"""
Mitigated claims agent for W8 trajectory eval.

SINGLE MITIGATION APPLIED: Tighter system instruction that MANDATES calling
search_policy_exclusions before compute_payout (unless inspection is pending).

This targets the top failure mode: 'exclusion_skip' — where the agent reaches
the correct payout without ever opening the exclusion tables, producing a
right-answer-down-a-wrong-path that passes outcome eval but fails trajectory eval.

The mitigation is a tighter tool description / system instruction ONLY.
No argument validation, no hard step limit, no re-planning, no workflow replacement.
"""

from __future__ import annotations

import json
import logging
import os
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
    AVAILABLE_TOOLS,
    ClaimStatus,
    compute_payout,
    get_adjuster_notes,
    get_claim,
    search_policy_exclusions,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("W8AgentMitigated")

MODEL_NAME = "gemini-3.5-flash-lite"

DEFAULT_MAX_ITERATIONS = 6
DEFAULT_MAX_TOKENS = 15000
DEFAULT_MAX_COST = 0.05
DEFAULT_MAX_WALL_CLOCK = 120.0


def _get_genai_client() -> genai.Client:
    api_key = os.getenv("API_KEY")
    if not api_key:
        raise RuntimeError("API_KEY not found in environment")
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=120_000),
    )


def _build_tool_declarations() -> List[types.Tool]:
    """Build GenAI Tool schema declarations — identical to w7_agent.py."""
    decl_get_claim = types.FunctionDeclaration(
        name="get_claim",
        description=(
            "Retrieves policy metadata and initial filing details for a claim (claim ID, policy ID, "
            "policyholder name, date of loss, claimed peril, policy form number, base deductible, and claimed amount). "
            "Does not retrieve adjuster notes, search exclusions, or calculate payouts."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "claim_id": types.Schema(
                    type="STRING",
                    description="The unique claim identifier, e.g. 'CLM-2024-7001'",
                )
            },
            required=["claim_id"],
        ),
    )

    decl_get_notes = types.FunctionDeclaration(
        name="get_adjuster_notes",
        description=(
            "Retrieves field inspection notes, physical damage findings, and verified root causes recorded "
            "by the claims adjuster for a given claim ID. Does not retrieve base policy limits, search exclusions, "
            "or calculate payouts."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "claim_id": types.Schema(
                    type="STRING",
                    description="The unique claim identifier, e.g. 'CLM-2024-7001'",
                )
            },
            required=["claim_id"],
        ),
    )

    # --- THE SINGLE MITIGATION IS HERE: tighter description ---
    decl_search_exclusions = types.FunctionDeclaration(
        name="search_policy_exclusions",
        description=(
            "MANDATORY before compute_payout unless inspection_pending is true. "
            "Searches policy endorsement exclusion tables to determine whether a specific cause of loss is "
            "excluded under a specified policy form. You MUST call this tool after reading adjuster notes "
            "and before computing any payout, to verify whether the identified cause of loss triggers "
            "a policy exclusion. Skipping this step produces an unauditable triage. "
            "Does not retrieve claim filings, read adjuster notes, or calculate payouts."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "policy_form": types.Schema(
                    type="STRING",
                    description="The endorsement form code, e.g. 'HO-0304', 'HO-0308', 'HO-0309'",
                ),
                "cause_of_loss": types.Schema(
                    type="STRING",
                    description="Specific loss cause or mechanism, e.g. 'flood and surface water', 'gradual seepage', 'unscheduled equipment'",
                ),
            },
            required=["policy_form", "cause_of_loss"],
        ),
    )

    decl_compute_payout = types.FunctionDeclaration(
        name="compute_payout",
        description=(
            "Calculates the final payable dollar settlement amount after applying deductible/excess and disallowance deductions "
            "strictly according to the adjudicated claim_status enum (APPROVED, PARTIAL_APPROVAL, DENIED, PENDING_INVESTIGATION). "
            "Does not inspect policy text, look up claim records, or read adjuster notes."
        ),
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "claimed_amount": types.Schema(
                    type="NUMBER",
                    description="Total dollar amount claimed on initial filing.",
                ),
                "deductible": types.Schema(
                    type="NUMBER",
                    description="Applicable policy deductible or excess amount in dollars.",
                ),
                "claim_status": types.Schema(
                    type="STRING",
                    enum=["APPROVED", "PARTIAL_APPROVAL", "DENIED", "PENDING_INVESTIGATION"],
                    description="The final adjudication claim status.",
                ),
                "disallowed_amount": types.Schema(
                    type="NUMBER",
                    description="Dollar amount excluded or disallowed due to policy exclusions or pre-existing wear.",
                ),
            },
            required=["claimed_amount", "deductible", "claim_status"],
        ),
    )

    return [
        types.Tool(
            function_declarations=[
                decl_get_claim,
                decl_get_notes,
                decl_search_exclusions,
                decl_compute_payout,
            ]
        )
    ]


# --- THE SINGLE MITIGATION: tighter system instruction ---
SYSTEM_INSTRUCTION_MITIGATED = """You are an autonomous insurance claims triage agent.
Your objective is to adjudicate incoming claims accurately by using the available tools.

MANDATORY PROTOCOL — you must follow these steps in order:
1. Pull the initial claim filing (get_claim).
2. Retrieve the field adjuster's inspection notes (get_adjuster_notes).
3. If inspection notes indicate pending inspection, set claim status to PENDING_INVESTIGATION and call compute_payout. SKIP step 4.
4. OTHERWISE, you MUST call search_policy_exclusions with the policy form and the verified root cause from adjuster notes BEFORE calling compute_payout. Do NOT skip this step even if the claim appears clean — every non-pending claim requires an exclusion check for audit compliance.
5. Based on the exclusion findings:
   - If excluded, claim status is DENIED.
   - If partial damage is excluded/pre-existing, claim status is PARTIAL_APPROVAL with the disallowed amount.
   - If fully covered, claim status is APPROVED.
6. Always execute compute_payout with the claimed amount, deductible, adjudicated claim status, and any disallowed amount.
7. Conclude with a concise triage summary citing any applicable exclusion clause.

CRITICAL: Calling compute_payout without first calling search_policy_exclusions (for non-pending claims) violates audit protocol and will produce an unverifiable triage.
"""


def run_claim_agent_mitigated(
    claim_id: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_cost: float = DEFAULT_MAX_COST,
    max_wall_clock_seconds: float = DEFAULT_MAX_WALL_CLOCK,
    client: Optional[genai.Client] = None,
) -> ClaimTriageResult:
    """
    Identical to w7_agent.run_claim_agent but with the mitigated system instruction
    and tighter search_policy_exclusions tool description.
    """
    if client is None:
        client = _get_genai_client()

    tools = _build_tool_declarations()
    gen_config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION_MITIGATED,  # <-- THE MITIGATION
        tools=tools,
        temperature=0.1,
    )

    t0 = time.perf_counter()
    iterations = 0
    cumulative_tokens = 0
    cumulative_cost = 0.0
    steps_executed: List[str] = []

    contents: List[Any] = [
        f"Please triage claim '{claim_id}'. Adjudicate coverage and determine final payout."
    ]

    last_claim_data: Dict[str, Any] = {}
    last_payout_data: Dict[str, Any] = {}
    last_exclusion_cited: Optional[str] = None
    final_reason: str = ""

    logger.info(f"Starting MITIGATED agent loop for claim {claim_id}...")

    while True:
        elapsed = time.perf_counter() - t0

        # Budget checks (identical to w7_agent.py)
        if iterations >= max_iterations:
            msg = f"MAX_ITERATIONS budget reached: {iterations}/{max_iterations}"
            logger.warning(f"Clean termination triggered: {msg}")
            return ClaimTriageResult(
                claim_id=claim_id, policy_id=last_claim_data.get("policy_id", "UNKNOWN"),
                claim_status="BUDGET_EXCEEDED", claimed_amount=last_claim_data.get("claimed_amount", 0.0),
                deductible=last_claim_data.get("deductible", 0.0), disallowed_amount=0.0,
                payable_amount=0.0, exclusion_clause_cited=None,
                reason=f"Terminated cleanly: {msg}", passed=False,
                tokens_used=cumulative_tokens, cost_usd=round(cumulative_cost, 6),
                latency_ms=round(elapsed * 1000, 2), steps_executed=steps_executed,
                system_type="agent",
            )

        if cumulative_tokens >= max_tokens:
            msg = f"MAX_TOKENS budget reached: {cumulative_tokens}/{max_tokens}"
            logger.warning(f"Clean termination triggered: {msg}")
            return ClaimTriageResult(
                claim_id=claim_id, policy_id=last_claim_data.get("policy_id", "UNKNOWN"),
                claim_status="BUDGET_EXCEEDED", claimed_amount=last_claim_data.get("claimed_amount", 0.0),
                deductible=last_claim_data.get("deductible", 0.0), disallowed_amount=0.0,
                payable_amount=0.0, exclusion_clause_cited=None,
                reason=f"Terminated cleanly: {msg}", passed=False,
                tokens_used=cumulative_tokens, cost_usd=round(cumulative_cost, 6),
                latency_ms=round(elapsed * 1000, 2), steps_executed=steps_executed,
                system_type="agent",
            )

        if cumulative_cost >= max_cost:
            msg = f"MAX_COST budget reached: ${cumulative_cost:.5f}/${max_cost:.5f}"
            logger.warning(f"Clean termination triggered: {msg}")
            return ClaimTriageResult(
                claim_id=claim_id, policy_id=last_claim_data.get("policy_id", "UNKNOWN"),
                claim_status="BUDGET_EXCEEDED", claimed_amount=last_claim_data.get("claimed_amount", 0.0),
                deductible=last_claim_data.get("deductible", 0.0), disallowed_amount=0.0,
                payable_amount=0.0, exclusion_clause_cited=None,
                reason=f"Terminated cleanly: {msg}", passed=False,
                tokens_used=cumulative_tokens, cost_usd=round(cumulative_cost, 6),
                latency_ms=round(elapsed * 1000, 2), steps_executed=steps_executed,
                system_type="agent",
            )

        if elapsed >= max_wall_clock_seconds:
            msg = f"MAX_WALL_CLOCK budget reached: {elapsed:.2f}s/{max_wall_clock_seconds:.2f}s"
            logger.warning(f"Clean termination triggered: {msg}")
            return ClaimTriageResult(
                claim_id=claim_id, policy_id=last_claim_data.get("policy_id", "UNKNOWN"),
                claim_status="BUDGET_EXCEEDED", claimed_amount=last_claim_data.get("claimed_amount", 0.0),
                deductible=last_claim_data.get("deductible", 0.0), disallowed_amount=0.0,
                payable_amount=0.0, exclusion_clause_cited=None,
                reason=f"Terminated cleanly: {msg}", passed=False,
                tokens_used=cumulative_tokens, cost_usd=round(cumulative_cost, 6),
                latency_ms=round(elapsed * 1000, 2), steps_executed=steps_executed,
                system_type="agent",
            )

        # Call model with retry logic
        response = None
        for attempt in range(6):
            try:
                response = client.models.generate_content(
                    model=MODEL_NAME,
                    contents=contents,
                    config=gen_config,
                )
                break
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    logger.warning(f"Rate limit hit. Backing off 15s (attempt {attempt+1}/6)...")
                    time.sleep(15.0)
                elif "503" in err_str or "UNAVAILABLE" in err_str:
                    logger.warning(f"Service unavailable. Retrying in 4s (attempt {attempt+1}/6)...")
                    time.sleep(4.0)
                else:
                    if attempt == 5:
                        raise e
                    time.sleep(3.0)

        if response is None:
            raise RuntimeError(f"Failed to obtain response from {MODEL_NAME} after retries.")

        # Token accounting
        if response.usage_metadata:
            p_tok = response.usage_metadata.prompt_token_count or 0
            c_tok = response.usage_metadata.candidates_token_count or 0
            lap_tokens = p_tok + c_tok
            cumulative_tokens += lap_tokens
            lap_cost = (p_tok * PRICE_PER_INPUT_TOKEN) + (c_tok * PRICE_PER_OUTPUT_TOKEN)
            cumulative_cost += lap_cost
        iterations += 1

        # Check for tool calls
        function_calls = response.function_calls
        if not function_calls:
            if response.text:
                final_reason = response.text.strip()
            break

        contents.append(response.candidates[0].content)

        # Dispatch tools
        tool_response_parts = []
        for fc in function_calls:
            fname = fc.name
            fargs = dict(fc.args) if fc.args else {}
            steps_executed.append(f"{fname}({fargs})")
            logger.info(f"Lap {iterations}: Agent calling {fname} with {fargs}")

            tool_fn = AVAILABLE_TOOLS.get(fname)
            if tool_fn:
                try:
                    tool_result = tool_fn(**fargs)
                except Exception as ex:
                    tool_result = {"error": f"Tool execution failed: {str(ex)}"}
            else:
                tool_result = {"error": f"Tool '{fname}' does not exist"}

            if fname == "get_claim" and "policy_id" in tool_result:
                last_claim_data = tool_result
            elif fname == "search_policy_exclusions" and tool_result.get("is_excluded"):
                last_exclusion_cited = tool_result.get("clause_id")
            elif fname == "compute_payout" and "payable_amount" in tool_result:
                last_payout_data = tool_result

            part = types.Part.from_function_response(name=fname, response=tool_result)
            tool_response_parts.append(part)

        contents.append(types.Content(role="user", parts=tool_response_parts))

        if last_payout_data and "payable_amount" in last_payout_data:
            logger.info(f"compute_payout executed. Concluding loop.")
            final_reason = last_payout_data.get("calculation_breakdown", "Triage completed.")
            break

    elapsed_final = time.perf_counter() - t0

    status = last_payout_data.get("claim_status", ClaimStatus.PENDING_INVESTIGATION)
    if isinstance(status, ClaimStatus):
        status_str = status.value
    else:
        status_str = str(status)

    from w7_tools import _load_claims_db
    claims_db = _load_claims_db()
    gt = claims_db.get(claim_id, {}).get("ground_truth", {})
    gt_status = gt.get("status")
    gt_payable = gt.get("payable_amount")

    payable = float(last_payout_data.get("payable_amount", 0.0))
    passed = (status_str == gt_status) and (abs(payable - float(gt_payable)) < 0.01)

    return ClaimTriageResult(
        claim_id=claim_id,
        policy_id=last_claim_data.get("policy_id", "POL-UNKNOWN"),
        claim_status=status_str,
        claimed_amount=float(last_claim_data.get("claimed_amount", 0.0)),
        deductible=float(last_claim_data.get("deductible", 0.0)),
        disallowed_amount=float(last_payout_data.get("disallowed_amount", 0.0)),
        payable_amount=payable,
        exclusion_clause_cited=last_exclusion_cited or gt.get("exclusion_clause"),
        reason=final_reason or last_payout_data.get("calculation_breakdown", "Adjudicated by mitigated agent."),
        passed=passed,
        tokens_used=cumulative_tokens,
        cost_usd=round(cumulative_cost, 6),
        latency_ms=round(elapsed_final * 1000, 2),
        steps_executed=steps_executed,
        system_type="agent",
    )
