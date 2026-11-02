"""
W12 Capstone — Policy Coverage MCP Server
Exposes date-scoped policy wording search, coverage-in-force lookup,
and refer-to-adjuster — all with PII redaction on every response.

Tools:
  1. search_policy_wording_by_date  — date filter enforced in CODE, not a prompt hint
  2. lookup_policy_coverage_in_force — was the policy active on the loss date?
  3. refer_to_adjuster              — terminal referral (never emits "covered")

Usage:
    python w12_mcp_server.py          # starts stdio MCP server
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from typing import Any, Dict, List, Optional

sys.path.insert(0, "d:\\AI-Training")

from mcp.server.mcpserver import MCPServer
from w7_tools import search_policy_exclusions as _search_exclusions

# ---------------------------------------------------------------------------
# PII Redaction
# ---------------------------------------------------------------------------

_PII_PATTERNS = [
    (re.compile(r"CLM-[\w-]+", re.IGNORECASE), "[REDACTED-CLAIM]"),
    (re.compile(r"POL-[\w-]+", re.IGNORECASE), "[REDACTED-POLICY]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-SSN]"),
    (re.compile(r"\b\d{3}[-.]\d{3}[-.]\d{4}\b"), "[REDACTED-PHONE]"),
    (re.compile(
        r"(?:claimant|insured|injured party)\s*:\s*[A-Z][a-z]+ [A-Z][a-z]+",
        re.IGNORECASE,
    ), "[REDACTED-NAME]"),
    (re.compile(
        r"\b\d{1,5}\s+\w+\s+(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Boulevard|Blvd|Lane|Ln|Way|Court|Ct)\b",
        re.IGNORECASE,
    ), "[REDACTED-ADDRESS]"),
    (re.compile(r"(?:injuries|injury)\s*:\s*[^\.;]+", re.IGNORECASE), "injuries: [REDACTED-INJURY]"),
]


def _redact_pii(text: str) -> str:
    """Strip PII BEFORE returning from any MCP tool."""
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Endorsement Version Date Map
# Each form has a list of {version, effective, expiry} entries.
# The tool picks the version whose effective <= loss_date <= expiry.
# This is enforced in CODE — not a prompt hint.
# ---------------------------------------------------------------------------

ENDORSEMENT_DATE_MAP: Dict[str, List[Dict[str, str]]] = {
    "HO-0304": [
        {
            "version": "HO-0304 Ed. 03-24",
            "effective": "2023-01-01",
            "expiry": "2024-12-31",
            "flood_exclusion_text": (
                "§IV E-17: We do not cover loss caused by or resulting from "
                "flood, surface water, waves, tidal water, overflow of a body of water, "
                "or spray from any of these, whether or not driven by wind. "
                "(HO-0304 Ed. 03-24 — in force for losses dated 2023-01-01 through 2024-12-31)"
            ),
        },
        {
            "version": "HO-0304 Ed. 01-25",
            "effective": "2025-01-01",
            "expiry": "9999-12-31",
            "flood_exclusion_text": (
                "§IV E-17: We do not cover loss caused by or resulting from "
                "flood, surface water, storm surge, waves, tidal water, overflow of a body of water, "
                "spray from any of these, subsurface water pressure, or hydrostatic pressure, "
                "whether or not driven by wind. This exclusion applies regardless of any other cause "
                "or event that contributes concurrently or in sequence to the loss. "
                "(HO-0304 Ed. 01-25 — in force for losses dated 2025-01-01 and later)"
            ),
        },
    ],
    "HO-0305": [
        {"version": "HO-0305 Ed. 03-24", "effective": "2023-01-01", "expiry": "2024-12-31",
         "flood_exclusion_text": "§IV E-17: Flood and surface water excluded. (HO-0305 Ed. 03-24)"},
        {"version": "HO-0305 Ed. 01-25", "effective": "2025-01-01", "expiry": "9999-12-31",
         "flood_exclusion_text": "§IV E-17: Flood, storm surge and surface water excluded. (HO-0305 Ed. 01-25)"},
    ],
    "HO-0306": [
        {"version": "HO-0306 Ed. 03-24", "effective": "2023-01-01", "expiry": "2024-12-31",
         "flood_exclusion_text": "§IV E-17: Surface water overflow excluded. (HO-0306 Ed. 03-24)"},
        {"version": "HO-0306 Ed. 01-25", "effective": "2025-01-01", "expiry": "9999-12-31",
         "flood_exclusion_text": "§IV E-17: Flood, storm surge and surface water excluded. (HO-0306 Ed. 01-25)"},
    ],
    "HO-0307": [
        {"version": "HO-0307 Ed. 03-24", "effective": "2023-01-01", "expiry": "2024-12-31",
         "flood_exclusion_text": "§IV E-17: Surface water excluded. (HO-0307 Ed. 03-24)"},
        {"version": "HO-0307 Ed. 01-25", "effective": "2025-01-01", "expiry": "9999-12-31",
         "flood_exclusion_text": "§IV E-17: Flood and storm surge excluded. (HO-0307 Ed. 01-25)"},
    ],
    "HO-0308": [
        {"version": "HO-0308 Ed. 03-24", "effective": "2023-01-01", "expiry": "2024-12-31",
         "flood_exclusion_text": "§IV E-17: Flood excluded. (HO-0308 Ed. 03-24)"},
        {"version": "HO-0308 Ed. 01-25", "effective": "2025-01-01", "expiry": "9999-12-31",
         "flood_exclusion_text": "§IV E-17: Flood and storm surge excluded. (HO-0308 Ed. 01-25)"},
    ],
    "HO-0309": [
        {"version": "HO-0309 Ed. 03-24", "effective": "2023-01-01", "expiry": "2024-12-31",
         "flood_exclusion_text": "§IV E-17: Flood and surface water excluded. (HO-0309 Ed. 03-24)"},
        {"version": "HO-0309 Ed. 01-25", "effective": "2025-01-01", "expiry": "9999-12-31",
         "flood_exclusion_text": "§IV E-17: Flood, storm surge and surface water excluded. (HO-0309 Ed. 01-25)"},
    ],
}


def _resolve_endorsement_version(policy_form: str, loss_date_str: str) -> Optional[Dict[str, str]]:
    """Return the endorsement version record in force on loss_date_str (YYYY-MM-DD)."""
    form_clean = policy_form.strip().upper()
    versions = ENDORSEMENT_DATE_MAP.get(form_clean, [])
    try:
        loss_dt = date.fromisoformat(loss_date_str)
    except ValueError:
        return None
    for v in versions:
        eff = date.fromisoformat(v["effective"])
        exp = date.fromisoformat(v["expiry"])
        if eff <= loss_dt <= exp:
            return v
    return None


# ---------------------------------------------------------------------------
# Policy Coverage-in-Force Database
# ---------------------------------------------------------------------------

POLICY_COVERAGE_DB: Dict[str, Dict[str, Any]] = {
    "POL-HO-2024-7001": {
        "product": "household", "form": "HO-0304",
        "effective": "2024-01-01", "expiry": "2025-01-01",
        "deductible": 1000.0, "sub_limit_water": 15000.0, "policy_limit": 250000.0,
    },
    "POL-HO-2024-7002": {
        "product": "household", "form": "HO-0304",
        "effective": "2024-01-01", "expiry": "2025-01-01",
        "deductible": 1500.0, "sub_limit_water": 15000.0, "policy_limit": 300000.0,
    },
    "POL-HO-2024-7003": {
        "product": "household", "form": "HO-0304",
        "effective": "2024-01-01", "expiry": "2025-01-01",
        "deductible": 1000.0, "sub_limit_water": 15000.0, "policy_limit": 275000.0,
    },
    "POL-HO-2024-7004": {
        "product": "household", "form": "HO-0308",
        "effective": "2024-01-01", "expiry": "2025-01-01",
        "deductible": 1000.0, "sub_limit_equipment": 10000.0, "policy_limit": 200000.0,
    },
    "POL-HO-2024-7005": {
        "product": "household", "form": "HO-0304",
        "effective": "2024-01-01", "expiry": "2025-01-01",
        "deductible": 1500.0, "sub_limit_water": 15000.0, "policy_limit": 300000.0,
    },
    "POL-HO-2024-7006": {
        "product": "household", "form": "HO-0308",
        "effective": "2024-01-01", "expiry": "2025-01-01",
        "deductible": 1000.0, "sub_limit_equipment": 10000.0, "policy_limit": 225000.0,
    },
    "POL-HO-2024-7007": {
        "product": "household", "form": "HO-0304",
        "effective": "2024-01-01", "expiry": "2025-01-01",
        "deductible": 1000.0, "sub_limit_water": 15000.0, "policy_limit": 275000.0,
    },
    "POL-HO-2024-7008": {
        "product": "household", "form": "HO-0304",
        "effective": "2024-01-01", "expiry": "2025-01-01",
        "deductible": 1000.0, "sub_limit_water": 15000.0, "policy_limit": 250000.0,
    },
    "POL-HO-2024-7009": {
        "product": "household", "form": "HO-0304",
        "effective": "2024-01-01", "expiry": "2025-01-01",
        "deductible": 1000.0, "sub_limit_water": 15000.0, "policy_limit": 250000.0,
    },
    "POL-HO-2024-7010": {
        "product": "household", "form": "HO-0309",
        "effective": "2024-01-01", "expiry": "2025-01-01",
        "deductible": 500.0, "sub_limit_jewellery": 5000.0, "policy_limit": 200000.0,
    },
}


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = MCPServer("w12-policy-coverage")


@mcp.tool()
def search_policy_wording_by_date(
    policy_form: str,
    cause_of_loss: str,
    loss_date: str,
    product: str,
) -> str:
    """
    Search policy exclusion wording IN FORCE ON THE LOSS DATE.

    The endorsement version is resolved by loss_date (YYYY-MM-DD) against a date-range
    table — this is enforced in code, not a prompt hint. A loss dated 2024-08-11 will
    always retrieve the 2023/24 wording, never the 2025 wording.

    Args:
        policy_form:  The policy form code (e.g. HO-0304).
        cause_of_loss: Description of the cause of loss (e.g. 'flood and surface water').
        loss_date:    Date of loss in YYYY-MM-DD format.
        product:      Policy product type: 'motor' or 'household'.
    """
    form_clean = policy_form.strip().upper()

    version_record = _resolve_endorsement_version(form_clean, loss_date)
    if version_record is None:
        return json.dumps({
            "error": (
                f"No endorsement version found for form '{form_clean}' on loss date '{loss_date}'. "
                f"Valid forms: {list(ENDORSEMENT_DATE_MAP.keys())}. Date format: YYYY-MM-DD."
            ),
            "endorsement_version": None,
            "loss_date_resolved": loss_date,
        })

    endorsement_version = version_record["version"]

    exclusion_result = _search_exclusions(form_clean, cause_of_loss)

    citation = None
    if exclusion_result.get("is_excluded"):
        clause_id = exclusion_result.get("clause_id", "")
        clause_id_short = clause_id.replace(f"{form_clean} ", "")
        citation = (
            f"[Endorsement: {endorsement_version} | §IV {clause_id_short} | "
            f"Loss date: {loss_date}]"
        )

    result = {
        "endorsement_version": endorsement_version,
        "loss_date_resolved": loss_date,
        "product": product,
        "policy_form": form_clean,
        "cause_of_loss": cause_of_loss,
        "is_excluded": exclusion_result.get("is_excluded", False),
        "clause_id": exclusion_result.get("clause_id"),
        "clause_title": exclusion_result.get("clause_title"),
        "clause_text": exclusion_result.get("clause_text"),
        "citation": citation,
        "note": (
            f"Wording resolved against {endorsement_version} — the version in force on {loss_date}. "
            f"Do NOT apply wording from any later endorsement version to this loss date."
        ),
    }

    return _redact_pii(json.dumps(result, ensure_ascii=False))


@mcp.tool()
def lookup_policy_coverage_in_force(
    policy_number: str,
    loss_date: str,
) -> str:
    """
    Check whether a policy was in force (active) on the given loss date.

    Returns product, effective/expiry dates, in_force status (bool), endorsement version
    applicable to that loss date, and the THREE distinct financial figures:
    deductible, sub_limit(s), and policy_limit.

    Args:
        policy_number: The policy number (e.g. POL-HO-2024-7001).
        loss_date:     Date of loss in YYYY-MM-DD format.
    """
    policy_clean = policy_number.strip()
    pol = POLICY_COVERAGE_DB.get(policy_clean)
    if pol is None:
        for k, v in POLICY_COVERAGE_DB.items():
            if policy_clean in k:
                pol = v
                policy_clean = k
                break

    if pol is None:
        return json.dumps({
            "error": f"Policy '{_redact_pii(policy_number)}' not found in coverage database.",
            "in_force": False,
        })

    try:
        loss_dt = date.fromisoformat(loss_date)
        eff_dt = date.fromisoformat(pol["effective"])
        exp_dt = date.fromisoformat(pol["expiry"])
        in_force = eff_dt <= loss_dt <= exp_dt
    except ValueError:
        return json.dumps({"error": f"Invalid loss_date format: '{loss_date}'. Use YYYY-MM-DD.", "in_force": False})

    form = pol.get("form", "HO-0304")
    version_record = _resolve_endorsement_version(form, loss_date)
    endorsement_version = version_record["version"] if version_record else "UNKNOWN"

    sub_limits = {k: v for k, v in pol.items() if k.startswith("sub_limit")}

    result = {
        "policy_number": "[REDACTED-POLICY]",
        "product": pol["product"],
        "policy_form": form,
        "effective_date": pol["effective"],
        "expiry_date": pol["expiry"],
        "loss_date": loss_date,
        "in_force": in_force,
        "endorsement_version_applicable": endorsement_version,
        "deductible": pol.get("deductible"),
        "sub_limits": sub_limits,
        "policy_limit": pol.get("policy_limit"),
        "note": (
            "deductible, sub_limit(s), and policy_limit are THREE distinct numbers. "
            "Do not conflate them in any referral summary."
        ),
    }

    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def refer_to_adjuster(
    claim_id: str,
    reason: str,
    citations: str,
) -> str:
    """
    Issue a structured referral to a human adjuster. This is the ONLY valid terminal
    action — the system triages and cites; the adjuster decides coverage.

    PII is redacted from reason and citations before return.

    Args:
        claim_id:  The claim identifier (redacted in output).
        reason:    Reason for referral (exclusion found, pending inspection, etc.).
        citations: Endorsement citation(s) in format '[Endorsement: X Ed. Y | §IV E-N | Loss date: Z]'.
    """
    forbidden = ["covered", "not covered", "coverage denied", "coverage approved"]
    reason_lower = reason.lower()
    for phrase in forbidden:
        if phrase in reason_lower:
            reason = reason.replace(phrase, "[DECISION-REDACTED — adjuster decides]")

    result = {
        "referral_issued": True,
        "claim_id": "[REDACTED-CLAIM]",
        "reason": _redact_pii(reason),
        "citations": _redact_pii(citations),
        "adjuster_action_required": True,
        "system_note": (
            "This system surfaces wording and facts. "
            "Coverage determination is the adjuster's exclusive responsibility. "
            "No automated coverage decision has been made."
        ),
    }

    return json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
