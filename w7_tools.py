import json
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ClaimStatus(str, Enum):
    APPROVED = "APPROVED"
    PARTIAL_APPROVAL = "PARTIAL_APPROVAL"
    DENIED = "DENIED"
    PENDING_INVESTIGATION = "PENDING_INVESTIGATION"


class ClaimRecord(BaseModel):
    claim_id: str
    policy_id: str
    policyholder: str
    date_of_loss: str
    policy_form: str
    claimed_peril: str
    claimed_amount: float
    deductible: float


class AdjusterNotesRecord(BaseModel):
    claim_id: str
    notes: str
    inspection_pending: bool = False


class ExclusionResult(BaseModel):
    policy_form: str
    query_cause: str
    is_excluded: bool
    clause_id: Optional[str] = None
    clause_title: Optional[str] = None
    clause_text: Optional[str] = None


class PayoutResult(BaseModel):
    claimed_amount: float
    deductible: float
    disallowed_amount: float
    claim_status: ClaimStatus
    payable_amount: float
    calculation_breakdown: str


_CLAIMS_DB: Dict[str, Dict[str, Any]] = {}
_ENDORSEMENTS_CACHE: Dict[str, str] = {}


def _load_claims_db() -> Dict[str, Dict[str, Any]]:
    global _CLAIMS_DB
    if not _CLAIMS_DB:
        claims_file = Path(__file__).parent / "data" / "w7_claims.json"
        if claims_file.exists():
            with open(claims_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                _CLAIMS_DB = {c["claim_id"]: c for c in data}
    return _CLAIMS_DB


def _load_endorsement(form_number: str) -> str:
    global _ENDORSEMENTS_CACHE
    form_clean = form_number.strip().upper()
    if form_clean in _ENDORSEMENTS_CACHE:
        return _ENDORSEMENTS_CACHE[form_clean]

    endorsements_dir = Path(__file__).parent / "data" / "endorsements"
    matches = list(endorsements_dir.glob(f"{form_clean}*.txt"))
    if not matches:
        return ""
    with open(matches[0], "r", encoding="utf-8") as f:
        content = f.read()
        _ENDORSEMENTS_CACHE[form_clean] = content
        return content


# ---------------------------------------------------------------------------
# Tool 1: get_claim
# ---------------------------------------------------------------------------
def get_claim(claim_id: str) -> Dict[str, Any]:
    """
    Retrieves policy metadata and initial filing details for a claim (claim ID,
    policy ID, policyholder name, date of loss, claimed peril, policy form number,
    base deductible, and claimed amount). Does not retrieve adjuster notes, search
    policy exclusions, or calculate payouts.
    """
    db = _load_claims_db()
    if claim_id not in db:
        return {"error": f"Claim ID '{claim_id}' not found in records."}

    raw = db[claim_id]
    rec = ClaimRecord(
        claim_id=raw["claim_id"],
        policy_id=raw["policy_id"],
        policyholder=raw["policyholder"],
        date_of_loss=raw["date_of_loss"],
        policy_form=raw["policy_form"],
        claimed_peril=raw["claimed_peril"],
        claimed_amount=float(raw["claimed_amount"]),
        deductible=float(raw["deductible"]),
    )
    return rec.model_dump()


# ---------------------------------------------------------------------------
# Tool 2: get_adjuster_notes
# ---------------------------------------------------------------------------
def get_adjuster_notes(claim_id: str) -> Dict[str, Any]:
    """
    Retrieves field inspection notes, physical damage findings, and verified root
    causes recorded by the claims adjuster for a given claim ID. Does not retrieve
    base policy limits, search exclusions, or calculate payouts.
    """
    db = _load_claims_db()
    if claim_id not in db:
        return {"error": f"Claim ID '{claim_id}' not found in records."}

    raw = db[claim_id]
    notes = raw.get("adjuster_notes", "")
    is_pending = "PENDING_INSPECTION" in notes or not notes.strip()
    return AdjusterNotesRecord(
        claim_id=claim_id,
        notes=notes,
        inspection_pending=is_pending,
    ).model_dump()


# ---------------------------------------------------------------------------
# Tool 3: search_policy_exclusions
# ---------------------------------------------------------------------------
def search_policy_exclusions(policy_form: str, cause_of_loss: str) -> Dict[str, Any]:
    """
    Searches policy endorsement exclusion tables to determine whether a specific cause
    of loss is excluded under a specified policy form. Does not retrieve claim filings,
    read adjuster notes, or calculate payouts.
    """
    content = _load_endorsement(policy_form)
    if not content:
        return {
            "policy_form": policy_form,
            "query_cause": cause_of_loss,
            "is_excluded": False,
            "error": f"Endorsement form '{policy_form}' not found on file.",
        }

    # Parse exclusions table: | E-XX | Title | Description |
    # Format: | E-15 | Gradual Seepage or Continuous Leakage | ...
    pattern = re.compile(r"\|\s*(E-\d{2})\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|")
    exclusions = []
    for line in content.splitlines():
        match = pattern.search(line)
        if match:
            exclusions.append({
                "clause_id": match.group(1).strip(),
                "clause_title": match.group(2).strip(),
                "clause_text": match.group(3).strip(),
            })

    cause_lower = cause_of_loss.lower()
    
    # Keyword taxonomy mapping for authentic policy exclusions
    keywords_map = {
        "E-15": ["seepage", "continuous", "gradual", "leakage", "chronic", "slow leak", "pinhole leak"],
        "E-16": ["unmaintained", "neglect", "failure to maintain", "pre-existing", "dry rot", "rot", "decay", "predated"],
        "E-17": ["flood", "surface water", "river", "overflow", "groundwater", "hydrostatic", "foundation seepage", "rising surface"],
        "E-18": ["sewer", "drain backup", "floor drain", "storm sewer", "lateral", "effluent", "sewer water"],
        "E-27": ["wear and tear", "mechanical wear", "corrosion", "rust", "deterioration"],
        "E-28": ["unscheduled", "not on the schedule", "not scheduled", "not listed", "never scheduled"],
        "E-29": ["cosmetic", "appearance only"],
        "E-31": ["pair", "pairs", "set", "sets", "matched", "earring"],
    }

    matched_clause = None
    for excl in exclusions:
        cid = excl["clause_id"]
        triggers = keywords_map.get(cid, [])
        if any(kw in cause_lower for kw in triggers):
            matched_clause = excl
            break

    if matched_clause:
        return ExclusionResult(
            policy_form=policy_form,
            query_cause=cause_of_loss,
            is_excluded=True,
            clause_id=f"{policy_form} {matched_clause['clause_id']}",
            clause_title=matched_clause["clause_title"],
            clause_text=matched_clause["clause_text"],
        ).model_dump()

    return ExclusionResult(
        policy_form=policy_form,
        query_cause=cause_of_loss,
        is_excluded=False,
        clause_id=None,
        clause_title=None,
        clause_text=None,
    ).model_dump()


# ---------------------------------------------------------------------------
# Tool 4: compute_payout (The 3rd/Adjudication tool with ClaimStatus Enum)
# ---------------------------------------------------------------------------
def compute_payout(
    claimed_amount: float,
    deductible: float,
    claim_status: ClaimStatus,
    disallowed_amount: float = 0.0,
) -> Dict[str, Any]:
    """
    Calculates the final payable dollar settlement amount after applying deductible/excess
    and disallowance deductions strictly according to the adjudicated claim_status enum
    (APPROVED, PARTIAL_APPROVAL, DENIED, PENDING_INVESTIGATION). Does not inspect policy text,
    look up claim records, or read adjuster notes.
    """
    if isinstance(claim_status, str):
        try:
            claim_status = ClaimStatus(claim_status)
        except ValueError:
            return {"error": f"Invalid claim_status: '{claim_status}'. Must be one of {[s.value for s in ClaimStatus]}"}

    if claim_status == ClaimStatus.DENIED:
        payable = 0.00
        calc = f"Status is DENIED. Net payable amount is $0.00 (Disallowed: ${disallowed_amount:,.2f})."
    elif claim_status == ClaimStatus.PENDING_INVESTIGATION:
        payable = 0.00
        calc = "Status is PENDING_INVESTIGATION. Payment held at $0.00 pending adjuster inspection."
    elif claim_status == ClaimStatus.PARTIAL_APPROVAL:
        base = max(0.0, float(claimed_amount) - float(disallowed_amount))
        payable = max(0.0, base - float(deductible))
        calc = (
            f"Partial approval: (${claimed_amount:,.2f} claimed - ${disallowed_amount:,.2f} disallowed) "
            f"- ${deductible:,.2f} deductible = ${payable:,.2f} payable."
        )
    elif claim_status == ClaimStatus.APPROVED:
        base = max(0.0, float(claimed_amount) - float(disallowed_amount))
        payable = max(0.0, base - float(deductible))
        calc = f"Full approval: ${claimed_amount:,.2f} claimed - ${deductible:,.2f} deductible = ${payable:,.2f} payable."
    else:
        payable = 0.00
        calc = "Unknown status."

    res = PayoutResult(
        claimed_amount=round(float(claimed_amount), 2),
        deductible=round(float(deductible), 2),
        disallowed_amount=round(float(disallowed_amount), 2),
        claim_status=claim_status,
        payable_amount=round(payable, 2),
        calculation_breakdown=calc,
    )
    return res.model_dump()


# ---------------------------------------------------------------------------
# Tool Schema Declarations for Function Calling in Agent Loop
# ---------------------------------------------------------------------------
TOOL_DEFINITIONS = [
    {
        "name": "get_claim",
        "description": (
            "Retrieves policy metadata and initial filing details for a claim (claim ID, policy ID, "
            "policyholder name, date of loss, claimed peril, policy form number, base deductible, and claimed amount). "
            "Does not retrieve adjuster notes, search exclusions, or calculate payouts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "claim_id": {
                    "type": "string",
                    "description": "The unique claim identifier, e.g. 'CLM-2024-7001'",
                }
            },
            "required": ["claim_id"],
        },
    },
    {
        "name": "get_adjuster_notes",
        "description": (
            "Retrieves field inspection notes, physical damage findings, and verified root causes recorded "
            "by the claims adjuster for a given claim ID. Does not retrieve base policy limits, search exclusions, "
            "or calculate payouts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "claim_id": {
                    "type": "string",
                    "description": "The unique claim identifier, e.g. 'CLM-2024-7001'",
                }
            },
            "required": ["claim_id"],
        },
    },
    {
        "name": "search_policy_exclusions",
        "description": (
            "Searches policy endorsement exclusion tables to determine whether a specific cause of loss is "
            "excluded under a specified policy form. Does not retrieve claim filings, read adjuster notes, "
            "or calculate payouts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "policy_form": {
                    "type": "string",
                    "description": "The endorsement form code, e.g. 'HO-0304', 'HO-0308', 'HO-0309'",
                },
                "cause_of_loss": {
                    "type": "string",
                    "description": "Specific loss cause or mechanism, e.g. 'flood and surface water', 'gradual seepage', 'equipment not on schedule'",
                },
            },
            "required": ["policy_form", "cause_of_loss"],
        },
    },
    {
        "name": "compute_payout",
        "description": (
            "Calculates the final payable dollar settlement amount after applying deductible/excess and disallowance deductions "
            "strictly according to the adjudicated claim_status enum (APPROVED, PARTIAL_APPROVAL, DENIED, PENDING_INVESTIGATION). "
            "Does not inspect policy text, look up claim records, or read adjuster notes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "claimed_amount": {
                    "type": "number",
                    "description": "Total dollar amount claimed on initial filing.",
                },
                "deductible": {
                    "type": "number",
                    "description": "Applicable policy deductible or excess amount in dollars.",
                },
                "claim_status": {
                    "type": "string",
                    "enum": ["APPROVED", "PARTIAL_APPROVAL", "DENIED", "PENDING_INVESTIGATION"],
                    "description": "The final adjudication claim status.",
                },
                "disallowed_amount": {
                    "type": "number",
                    "description": "Dollar amount excluded or disallowed due to policy exclusions or pre-existing wear.",
                },
            },
            "required": ["claimed_amount", "deductible", "claim_status"],
        },
    },
]

AVAILABLE_TOOLS = {
    "get_claim": get_claim,
    "get_adjuster_notes": get_adjuster_notes,
    "search_policy_exclusions": search_policy_exclusions,
    "compute_payout": compute_payout,
}
