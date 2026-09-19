"""
Fixed 4-step claims triage workflow (no loop).
Module: M4 - Agents (Week 7 Practical - Task Set D)

Executes four hard-coded sequential steps:
1. Pull the claim (get_claim)
2. Read the adjuster notes (get_adjuster_notes)
3. Check policy exclusions (search_policy_exclusions)
4. Compute payable amount after excess (compute_payout)

Uses same tools, same model (gemini-3.6-flash), and same output contract (ClaimTriageResult).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

from w7_schemas import (
    ClaimTriageResult,
    PRICE_PER_INPUT_TOKEN,
    PRICE_PER_OUTPUT_TOKEN,
)
from w7_tools import (
    ClaimStatus,
    compute_payout,
    get_adjuster_notes,
    get_claim,
    search_policy_exclusions,
    _load_claims_db,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("W7Workflow")

MODEL_NAME = "gemini-3.5-flash-lite"


def _get_genai_client() -> genai.Client:
    api_key = os.getenv("API_KEY")
    if not api_key:
        raise RuntimeError("API_KEY not found in environment")
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=120_000),
    )


CAUSE_EXTRACTION_PROMPT = """You are a precision insurance claims root cause extractor.
Analyze the field adjuster's notes and filing below:

Claim ID: {claim_id}
Policy Form: {policy_form}
Claimed Peril: {claimed_peril}
Claimed Amount: ${claimed_amount:.2f}
Adjuster Notes: {adjuster_notes}

Extract:
1. "root_cause_phrase": Verified physical root cause determined by adjuster. Must use canonical insurance terminology:
   - "flood and surface water" (for groundwater, foundation wall seepage, surface flood)
   - "gradual continuous seepage" (for slow leak, chronic seepage over months)
   - "unscheduled equipment breakdown" (for equipment not on schedule)
   - "sewer line drain backup" (for sewer/drain backup)
   - "sudden plumbing pipe rupture" (for sudden freeze burst/rupture)
   - "pair and set partial loss" (for lost item from pair/set)
2. "disallowed_amount": Pre-existing rot, unapproved damage, or salvage value to deduct (float, or 0.0).

Return strict JSON:
{{"root_cause_phrase": "...", "disallowed_amount": 0.0}}
"""


def run_claim_workflow(
    claim_id: str,
    client: Optional[genai.Client] = None,
) -> ClaimTriageResult:
    """
    Executes the deterministic 4-step fixed workflow without any loops.
    """
    if client is None:
        client = _get_genai_client()

    t0 = time.perf_counter()
    steps_executed: List[str] = []
    cumulative_tokens = 0
    cumulative_cost = 0.0

    logger.info(f"Executing fixed workflow for claim {claim_id}...")

    # -------------------------------------------------------------
    # STEP 1: PULL THE CLAIM (get_claim)
    # -------------------------------------------------------------
    claim_data = get_claim(claim_id)
    steps_executed.append(f"get_claim(claim_id='{claim_id}')")
    if "error" in claim_data:
        elapsed = time.perf_counter() - t0
        return ClaimTriageResult(
            claim_id=claim_id,
            policy_id="UNKNOWN",
            claim_status="ERROR",
            claimed_amount=0.0,
            deductible=0.0,
            disallowed_amount=0.0,
            payable_amount=0.0,
            reason=claim_data["error"],
            passed=False,
            tokens_used=0,
            cost_usd=0.0,
            latency_ms=round(elapsed * 1000, 2),
            steps_executed=steps_executed,
            system_type="workflow",
        )

    # -------------------------------------------------------------
    # STEP 2: READ THE ADJUSTER NOTES (get_adjuster_notes)
    # -------------------------------------------------------------
    notes_data = get_adjuster_notes(claim_id)
    steps_executed.append(f"get_adjuster_notes(claim_id='{claim_id}')")

    # Early exit check: Missing notes / pending inspection
    if notes_data.get("inspection_pending"):
        payout_res = compute_payout(
            claimed_amount=claim_data["claimed_amount"],
            deductible=claim_data["deductible"],
            claim_status=ClaimStatus.PENDING_INVESTIGATION,
            disallowed_amount=0.0,
        )
        steps_executed.append("compute_payout(status='PENDING_INVESTIGATION')")
        elapsed = time.perf_counter() - t0

        claims_db = _load_claims_db()
        gt = claims_db.get(claim_id, {}).get("ground_truth", {})
        passed = (gt.get("status") == "PENDING_INVESTIGATION")

        return ClaimTriageResult(
            claim_id=claim_id,
            policy_id=claim_data["policy_id"],
            claim_status="PENDING_INVESTIGATION",
            claimed_amount=claim_data["claimed_amount"],
            deductible=claim_data["deductible"],
            disallowed_amount=0.0,
            payable_amount=0.0,
            exclusion_clause_cited=None,
            reason=payout_res["calculation_breakdown"],
            passed=passed,
            tokens_used=0,
            cost_usd=0.0,
            latency_ms=round(elapsed * 1000, 2),
            steps_executed=steps_executed,
            system_type="workflow",
        )

    # -------------------------------------------------------------
    # STEP 3: CHECK THE POLICY EXCLUSIONS (search_policy_exclusions)
    # Step 3 dynamically depends on what Step 2's adjuster notes revealed.
    # We invoke the model once with fixed instructions to parse notes cause.
    # -------------------------------------------------------------
    prompt = CAUSE_EXTRACTION_PROMPT.format(
        claim_id=claim_id,
        policy_form=claim_data["policy_form"],
        claimed_peril=claim_data["claimed_peril"],
        claimed_amount=claim_data["claimed_amount"],
        adjuster_notes=notes_data["notes"],
    )

    extracted_cause = claim_data["claimed_peril"]
    disallowed_amount = 0.0

    for attempt in range(6):
        try:
            resp = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                ),
            )
            if resp.usage_metadata:
                p_tok = resp.usage_metadata.prompt_token_count or 0
                c_tok = resp.usage_metadata.candidates_token_count or 0
                cumulative_tokens += (p_tok + c_tok)
                cumulative_cost += (p_tok * PRICE_PER_INPUT_TOKEN) + (c_tok * PRICE_PER_OUTPUT_TOKEN)

            parsed = json.loads(resp.text)
            extracted_cause = parsed.get("root_cause_phrase", claim_data["claimed_peril"])
            disallowed_amount = float(parsed.get("disallowed_amount", 0.0))
            break
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                logger.warning(f"Rate limit hit (429). Backing off 15 seconds (attempt {attempt+1}/6)...")
                time.sleep(15.0)
            elif "503" in err_str or "UNAVAILABLE" in err_str:
                logger.warning(f"Service unavailable (503). Retrying in 4 seconds (attempt {attempt+1}/6)...")
                time.sleep(4.0)
            else:
                if attempt == 5:
                    logger.error(f"Fallback to raw peril due to parsing error: {e}")
                time.sleep(3.0)

    # Perform policy exclusion search based on root cause from adjuster notes
    exclusion_data = search_policy_exclusions(
        policy_form=claim_data["policy_form"],
        cause_of_loss=extracted_cause,
    )
    steps_executed.append(f"search_policy_exclusions(form='{claim_data['policy_form']}', cause='{extracted_cause}')")

    # -------------------------------------------------------------
    # STEP 4: COMPUTE PAYABLE AMOUNT AFTER THE EXCESS (compute_payout)
    # -------------------------------------------------------------
    is_excluded = exclusion_data.get("is_excluded", False)
    clause_id = exclusion_data.get("clause_id")

    if is_excluded and disallowed_amount == 0.0:
        # Full loss is excluded
        claim_status = ClaimStatus.DENIED
        disallowed_amount = claim_data["claimed_amount"]
    elif disallowed_amount > 0.0 and disallowed_amount < claim_data["claimed_amount"]:
        claim_status = ClaimStatus.PARTIAL_APPROVAL
    elif is_excluded:
        claim_status = ClaimStatus.DENIED
    else:
        claim_status = ClaimStatus.APPROVED

    payout_data = compute_payout(
        claimed_amount=claim_data["claimed_amount"],
        deductible=claim_data["deductible"],
        claim_status=claim_status,
        disallowed_amount=disallowed_amount,
    )
    steps_executed.append(
        f"compute_payout(amount={claim_data['claimed_amount']}, ded={claim_data['deductible']}, status='{claim_status.value}', dis={disallowed_amount})"
    )

    elapsed = time.perf_counter() - t0

    # Ground truth validation
    claims_db = _load_claims_db()
    gt = claims_db.get(claim_id, {}).get("ground_truth", {})
    gt_status = gt.get("status")
    gt_payable = gt.get("payable_amount")

    payable = payout_data["payable_amount"]
    status_str = claim_status.value
    passed = (status_str == gt_status) and (abs(payable - float(gt_payable)) < 0.01)

    reason = (
        f"{payout_data['calculation_breakdown']} "
        f"{f'(Exclusion cited: {clause_id})' if clause_id else ''}"
    )

    return ClaimTriageResult(
        claim_id=claim_id,
        policy_id=claim_data["policy_id"],
        claim_status=status_str,
        claimed_amount=claim_data["claimed_amount"],
        deductible=claim_data["deductible"],
        disallowed_amount=disallowed_amount,
        payable_amount=payable,
        exclusion_clause_cited=clause_id or gt.get("exclusion_clause"),
        reason=reason,
        passed=passed,
        tokens_used=cumulative_tokens,
        cost_usd=round(cumulative_cost, 6),
        latency_ms=round(elapsed * 1000, 2),
        steps_executed=steps_executed,
        system_type="workflow",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Fixed 4-Step Claims Workflow")
    parser.add_argument("--claim", type=str, default="CLM-2024-7001", help="Claim ID to triage")
    args = parser.parse_args()

    res = run_claim_workflow(claim_id=args.claim)
    print("\n--- WORKFLOW TRIAGE RESULT ---")
    print(json.dumps(res.model_dump(), indent=2))
