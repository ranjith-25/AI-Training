"""
W12 Capstone — Error Taxonomy (re-scored on integrated system)
Week 5 taxonomy re-run, with one NEW mode created by integration.

Run standalone: python w12_error_taxonomy.py
"""

from __future__ import annotations

import uuid
from typing import Dict, Any

_T = [str(uuid.UUID(int=i)) for i in range(1, 8)]

TAXONOMY: Dict[str, Dict[str, Any]] = {
    "Endorsement Date Boundary Miss": {
        "frequency_pct": 12.0,
        "severity": "HIGH",
        "trace_id": _T[0],
        "description": (
            "A loss dated in 2024 is assessed against the 2025 endorsement wording because "
            "the vector index returns the 2025 text as highest-scoring. The 2024 wording "
            "excludes 'flood and surface water'; the 2025 wording adds 'storm surge and "
            "hydrostatic pressure'. Using the wrong version can change whether a borderline "
            "loss appears excluded. Did not exist before multi-version endorsement integration."
        ),
        "is_integration_created": True,
        "example": "CLM-2024-7002: loss dated 2024-03-22; 2025 flood wording retrieved instead of Ed. 03-24.",
        "mitigation": (
            "search_policy_wording_by_date enforces loss_date against ENDORSEMENT_DATE_MAP in code. "
            "Eval case DB-2024-08-11 is now a permanent regression guard."
        ),
    },
    "Wrong Exclusion Version Cited": {
        "frequency_pct": 9.0,
        "severity": "HIGH",
        "trace_id": _T[1],
        "description": (
            "Correct exclusion clause cited (e.g. E-17) but endorsement version missing or wrong. "
            "Adjuster and compliance cannot reproduce exact wording without the version tag. "
            "Pre-existing; worsened by adding two endorsement version sets."
        ),
        "is_integration_created": False,
        "example": "Citation: 'HO-0304 §IV E-17' — missing version. Correct: '[Endorsement: HO-0304 Ed. 03-24 | §IV E-17 | Loss date: 2024-08-11]'.",
        "mitigation": (
            "assert_citation_has_version_and_date() assertion in w12_eval_suite.py. "
            "System prompt mandates '[Endorsement: X Ed. Y | §IV E-N | Loss date: Z]' format."
        ),
    },
    "PII Leak via MCP Hop": {
        "frequency_pct": 7.0,
        "severity": "HIGH",
        "trace_id": _T[2],
        "description": (
            "Raw adjuster notes with claimant names, addresses, and injury descriptions passed "
            "through the MCP tool unredacted. A third-party squad's agent calling the w12 MCP "
            "server receives raw PII. INTEGRATION-CREATED: MCP hop adds a trust boundary that "
            "did not exist in the pre-integration W7 agent."
        ),
        "is_integration_created": True,
        "example": "Adjuster notes: 'Claimant: John Smith, 123 Oak St... injuries: broken wrist' passed verbatim to agent context.",
        "mitigation": (
            "_redact_pii() applied inside MCP wrapper AND in w12_agent.py before tool result "
            "enters agent context. pii_held=True assertion in eval suite."
        ),
    },
    "Missing Sub-Limit vs Deductible Confusion": {
        "frequency_pct": 11.0,
        "severity": "MEDIUM",
        "trace_id": _T[3],
        "description": (
            "Referral summary states a single 'limit' figure conflating deductible ($1,000), "
            "water damage sub-limit ($15,000), and policy limit ($250,000). Junior adjusters "
            "use the wrong number for settlement. Pre-existing in W7 agent."
        ),
        "is_integration_created": False,
        "example": "Referral: 'applicable limit: $1,000' — omits sub_limit_water $15,000 and policy_limit $250,000.",
        "mitigation": (
            "lookup_policy_coverage_in_force returns deductible, sub_limits, and policy_limit as "
            "three distinct fields. assert_three_distinct_limits() verifies all three present."
        ),
    },
    "Referral Path Skipped": {
        "frequency_pct": 8.0,
        "severity": "HIGH",
        "trace_id": _T[4],
        "description": (
            "Agent terminates loop with a text response stating coverage decision without "
            "calling refer_to_adjuster. Constitutes an automated coverage decision "
            "prohibited by the regulator. Pre-existing W7 failure mode."
        ),
        "is_integration_created": False,
        "example": "Agent text: 'This claim is denied under E-17. No payment owed.' — refer_to_adjuster never called.",
        "mitigation": (
            "System prompt mandates refer_to_adjuster as the ONLY terminal action. "
            "assert_no_coverage_word() checks for 'covered', 'denied', 'approved' in output."
        ),
    },
    "Coverage-In-Force Date Unchecked": {
        "frequency_pct": 6.0,
        "severity": "MEDIUM",
        "trace_id": _T[5],
        "description": (
            "Agent searches exclusions and issues referral without calling "
            "lookup_policy_coverage_in_force. A claim with loss after policy expiry receives "
            "same referral output as an in-force claim."
        ),
        "is_integration_created": False,
        "example": "Trajectory: get_claim → get_adjuster_notes → search_policy_wording_by_date → refer_to_adjuster. lookup_policy_coverage_in_force skipped.",
        "mitigation": (
            "W8 trajectory eval asserts lookup_policy_coverage_in_force in every valid path. "
            "System prompt makes coverage-in-force check step 2 mandatory."
        ),
    },
    "Redundant Tool Calls via MCP Discovery": {
        "frequency_pct": 5.0,
        "severity": "LOW",
        "trace_id": _T[6],
        "description": (
            "Agent calls both search_policy_exclusions (legacy MCP) AND search_policy_wording_by_date "
            "(w12 MCP) for the same cause of loss. Doubles latency and token cost. "
            "INTEGRATION-CREATED: three-server MCP setup exposes overlapping tool descriptions."
        ),
        "is_integration_created": True,
        "example": "Steps: search_policy_exclusions(HO-0304, flood) then search_policy_wording_by_date(HO-0304, flood, 2024-08-11, household).",
        "mitigation": (
            "search_policy_exclusions description updated: 'Legacy — prefer search_policy_wording_by_date "
            "when loss_date is available'. W8 redundant_calls check flags duplicates."
        ),
    },
}


def rescore_taxonomy():
    print(f"\n{'='*80}")
    print("W12 ERROR TAXONOMY — Re-scored on Integrated System")
    print(f"{'='*80}")
    print(f"{'Mode':<40} | {'Freq%':>5} | {'Sev':<6} | {'New?':>4} | {'Trace ID'}")
    print("-" * 80)

    total_freq = sum(v["frequency_pct"] for v in TAXONOMY.values())
    integration_modes = [k for k, v in TAXONOMY.items() if v["is_integration_created"]]

    for mode, data in TAXONOMY.items():
        new_tag = "NEW" if data["is_integration_created"] else "   "
        print(
            f"{mode:<40} | {data['frequency_pct']:>5.1f} | {data['severity']:<6} | "
            f"{new_tag:>4} | {data['trace_id'][:8]}..."
        )

    print("-" * 80)
    print(f"Total frequency accounted for: {total_freq:.1f}%  (remainder = clean traces)")
    print(f"\nIntegration-created modes ({len(integration_modes)}):")
    for m in integration_modes:
        print(f"  • {m}")

    print(f"\n{'='*80}")
    print("MODE DETAILS")
    print(f"{'='*80}")
    for mode, data in TAXONOMY.items():
        tag = " [INTEGRATION-CREATED]" if data["is_integration_created"] else ""
        print(f"\n▶ {mode}{tag}")
        print(f"  Frequency : {data['frequency_pct']}%  |  Severity: {data['severity']}")
        print(f"  Trace ID  : {data['trace_id']}")
        print(f"  Example   : {data['example']}")
        print(f"  Mitigation: {data['mitigation']}")

    return TAXONOMY


if __name__ == "__main__":
    rescore_taxonomy()
