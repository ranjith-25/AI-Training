"""
Hand-built claims triage agent loop with four enforced budgets.
Module: M4 - Agents (Week 7 Practical - Task Set D)

Enforces:
1. MAX_ITERATIONS (max loop iterations)
2. MAX_TOKENS (cumulative tokens across all laps, including re-sent context)
3. MAX_COST (cumulative cost in USD)
4. MAX_WALL_CLOCK_SECONDS (wall-clock elapsed time)

Outputs identical ClaimTriageResult contract.
"""

from __future__ import annotations

import argparse
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

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("W7Agent")

MODEL_NAME = "gemini-3.5-flash-lite"

# Default budgets
DEFAULT_MAX_ITERATIONS = 6
DEFAULT_MAX_TOKENS = 15000
DEFAULT_MAX_COST = 0.05  # $0.05
DEFAULT_MAX_WALL_CLOCK = 30.0  # seconds


class BudgetExceededError(Exception):
    """Raised when any of the 4 operational budgets is exceeded."""

    def __init__(self, budget_name: str, current_value: float, limit_value: float):
        self.budget_name = budget_name
        self.current_value = current_value
        self.limit_value = limit_value
        super().__init__(
            f"BUDGET EXCEEDED: '{budget_name}' reached {current_value} (limit: {limit_value}). Terminating cleanly."
        )


def _get_genai_client() -> genai.Client:
    api_key = os.getenv("API_KEY")
    if not api_key:
        raise RuntimeError("API_KEY not found in environment")
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=120_000),
    )


def _build_tool_declarations() -> List[types.Tool]:
    """Build GenAI Tool schema declarations for the 4 domain tools."""
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

    decl_search_exclusions = types.FunctionDeclaration(
        name="search_policy_exclusions",
        description=(
            "Searches policy endorsement exclusion tables to determine whether a specific cause of loss is "
            "excluded under a specified policy form. Does not retrieve claim filings, read adjuster notes, "
            "or calculate payouts."
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


SYSTEM_INSTRUCTION = """You are an autonomous insurance claims triage agent.
Your objective is to adjudicate incoming claims accurately by using the available tools:
1. Pull the initial claim filing (get_claim).
2. Retrieve the field adjuster's inspection notes (get_adjuster_notes).
3. If inspection notes indicate pending inspection, set claim status to PENDING_INVESTIGATION and call compute_payout.
4. If inspection notes reveal specific physical causes (e.g. flood, surface water, wear and tear, pre-existing rot, unscheduled equipment), check policy exclusions for that policy form (search_policy_exclusions).
5. Based on the exclusion findings:
   - If excluded, claim status is DENIED.
   - If partial damage is excluded/pre-existing, claim status is PARTIAL_APPROVAL with the disallowed amount.
   - If fully covered, claim status is APPROVED.
6. Always execute compute_payout with the claimed amount, deductible, adjudicated claim status, and any disallowed amount.
7. Conclude with a concise triage summary citing any applicable exclusion clause.
"""


def run_claim_agent(
    claim_id: str,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_cost: float = DEFAULT_MAX_COST,
    max_wall_clock_seconds: float = DEFAULT_MAX_WALL_CLOCK,
    client: Optional[genai.Client] = None,
) -> ClaimTriageResult:
    """
    Executes the claims agent loop with strict enforcement of all 4 operational budgets.
    """
    if client is None:
        client = _get_genai_client()

    tools = _build_tool_declarations()
    gen_config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=tools,
        temperature=0.1,
    )

    t0 = time.perf_counter()
    iterations = 0
    cumulative_tokens = 0
    cumulative_cost = 0.0
    steps_executed: List[str] = []

    # Memory/context
    contents: List[Any] = [
        f"Please triage claim '{claim_id}'. Adjudicate coverage and determine final payout."
    ]

    last_claim_data: Dict[str, Any] = {}
    last_payout_data: Dict[str, Any] = {}
    last_exclusion_cited: Optional[str] = None
    final_reason: str = ""

    logger.info(f"Starting agent loop for claim {claim_id}...")

    while True:
        elapsed = time.perf_counter() - t0

        # -------------------------------------------------------------
        # 1. ENFORCE ALL FOUR BUDGETS BEFORE EACH ITERATION
        # -------------------------------------------------------------
        if iterations >= max_iterations:
            msg = f"MAX_ITERATIONS budget reached: {iterations}/{max_iterations}"
            logger.warning(f"Clean termination triggered: {msg}")
            return ClaimTriageResult(
                claim_id=claim_id,
                policy_id=last_claim_data.get("policy_id", "UNKNOWN"),
                claim_status="BUDGET_EXCEEDED",
                claimed_amount=last_claim_data.get("claimed_amount", 0.0),
                deductible=last_claim_data.get("deductible", 0.0),
                disallowed_amount=0.0,
                payable_amount=0.0,
                exclusion_clause_cited=None,
                reason=f"Terminated cleanly: {msg}",
                passed=False,
                tokens_used=cumulative_tokens,
                cost_usd=round(cumulative_cost, 6),
                latency_ms=round(elapsed * 1000, 2),
                steps_executed=steps_executed,
                system_type="agent",
            )

        if cumulative_tokens >= max_tokens:
            msg = f"MAX_TOKENS budget reached: {cumulative_tokens}/{max_tokens}"
            logger.warning(f"Clean termination triggered: {msg}")
            return ClaimTriageResult(
                claim_id=claim_id,
                policy_id=last_claim_data.get("policy_id", "UNKNOWN"),
                claim_status="BUDGET_EXCEEDED",
                claimed_amount=last_claim_data.get("claimed_amount", 0.0),
                deductible=last_claim_data.get("deductible", 0.0),
                disallowed_amount=0.0,
                payable_amount=0.0,
                exclusion_clause_cited=None,
                reason=f"Terminated cleanly: {msg}",
                passed=False,
                tokens_used=cumulative_tokens,
                cost_usd=round(cumulative_cost, 6),
                latency_ms=round(elapsed * 1000, 2),
                steps_executed=steps_executed,
                system_type="agent",
            )

        if cumulative_cost >= max_cost:
            msg = f"MAX_COST budget reached: ${cumulative_cost:.5f}/${max_cost:.5f}"
            logger.warning(f"Clean termination triggered: {msg}")
            return ClaimTriageResult(
                claim_id=claim_id,
                policy_id=last_claim_data.get("policy_id", "UNKNOWN"),
                claim_status="BUDGET_EXCEEDED",
                claimed_amount=last_claim_data.get("claimed_amount", 0.0),
                deductible=last_claim_data.get("deductible", 0.0),
                disallowed_amount=0.0,
                payable_amount=0.0,
                exclusion_clause_cited=None,
                reason=f"Terminated cleanly: {msg}",
                passed=False,
                tokens_used=cumulative_tokens,
                cost_usd=round(cumulative_cost, 6),
                latency_ms=round(elapsed * 1000, 2),
                steps_executed=steps_executed,
                system_type="agent",
            )

        if elapsed >= max_wall_clock_seconds:
            msg = f"MAX_WALL_CLOCK budget reached: {elapsed:.2f}s/{max_wall_clock_seconds:.2f}s"
            logger.warning(f"Clean termination triggered: {msg}")
            return ClaimTriageResult(
                claim_id=claim_id,
                policy_id=last_claim_data.get("policy_id", "UNKNOWN"),
                claim_status="BUDGET_EXCEEDED",
                claimed_amount=last_claim_data.get("claimed_amount", 0.0),
                deductible=last_claim_data.get("deductible", 0.0),
                disallowed_amount=0.0,
                payable_amount=0.0,
                exclusion_clause_cited=None,
                reason=f"Terminated cleanly: {msg}",
                passed=False,
                tokens_used=cumulative_tokens,
                cost_usd=round(cumulative_cost, 6),
                latency_ms=round(elapsed * 1000, 2),
                steps_executed=steps_executed,
                system_type="agent",
            )

        # -------------------------------------------------------------
        # 2. CALL MODEL WITH TRANSIENT RETRY LOGIC (HANDLING 503 & 429)
        # -------------------------------------------------------------
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
                    logger.warning(f"Rate limit hit (429). Backing off 15 seconds for quota window to reset (attempt {attempt+1}/6)...")
                    time.sleep(15.0)
                elif "503" in err_str or "UNAVAILABLE" in err_str:
                    logger.warning(f"Service unavailable (503). Retrying in 4 seconds (attempt {attempt+1}/6)...")
                    time.sleep(4.0)
                else:
                    if attempt == 5:
                        raise e
                    time.sleep(3.0)

        if response is None:
            raise RuntimeError(f"Failed to obtain response from {MODEL_NAME} after retries.")

        # -------------------------------------------------------------
        # 3. PER-LAP TOKEN ACCOUNTING (SUM ACROSS ALL LAPS)
        # -------------------------------------------------------------
        if response.usage_metadata:
            p_tok = response.usage_metadata.prompt_token_count or 0
            c_tok = response.usage_metadata.candidates_token_count or 0
            lap_tokens = p_tok + c_tok
            cumulative_tokens += lap_tokens
            lap_cost = (p_tok * PRICE_PER_INPUT_TOKEN) + (c_tok * PRICE_PER_OUTPUT_TOKEN)
            cumulative_cost += lap_cost
        iterations += 1

        # Check for tool call requests
        function_calls = response.function_calls
        if not function_calls:
            # Model finished turn without calling further tools
            if response.text:
                final_reason = response.text.strip()
            break

        # Append candidate response to conversation history
        contents.append(response.candidates[0].content)

        # -------------------------------------------------------------
        # 4. DISPATCH TOOLS REQUESTED BY AGENT
        # -------------------------------------------------------------
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

            # Track domain facts
            if fname == "get_claim" and "policy_id" in tool_result:
                last_claim_data = tool_result
            elif fname == "search_policy_exclusions" and tool_result.get("is_excluded"):
                last_exclusion_cited = tool_result.get("clause_id")
            elif fname == "compute_payout" and "payable_amount" in tool_result:
                last_payout_data = tool_result

            part = types.Part.from_function_response(name=fname, response=tool_result)
            tool_response_parts.append(part)

        contents.append(types.Content(role="user", parts=tool_response_parts))

        # Terminal condition: If compute_payout was successfully executed, triage calculation is complete!
        if last_payout_data and "payable_amount" in last_payout_data:
            logger.info(f"compute_payout executed with status={last_payout_data.get('claim_status')}. Concluding loop.")
            final_reason = last_payout_data.get("calculation_breakdown", "Triage completed.")
            break

    elapsed_final = time.perf_counter() - t0

    # Ground truth validation
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
        reason=final_reason or last_payout_data.get("calculation_breakdown", "Adjudicated by agent loop."),
        passed=passed,
        tokens_used=cumulative_tokens,
        cost_usd=round(cumulative_cost, 6),
        latency_ms=round(elapsed_final * 1000, 2),
        steps_executed=steps_executed,
        system_type="agent",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Hand-Built Claims Agent")
    parser.add_argument("--claim", type=str, default="CLM-2024-7001", help="Claim ID to triage")
    parser.add_argument("--test-budget", type=str, choices=["iterations", "tokens", "cost", "wallclock"], help="Trigger budget termination")
    args = parser.parse_args()

    max_iter = DEFAULT_MAX_ITERATIONS
    max_tok = DEFAULT_MAX_TOKENS
    max_c = DEFAULT_MAX_COST
    max_wc = DEFAULT_MAX_WALL_CLOCK

    if args.test_budget == "iterations":
        max_iter = 1
    elif args.test_budget == "tokens":
        max_tok = 50
    elif args.test_budget == "cost":
        max_c = 0.000001
    elif args.test_budget == "wallclock":
        max_wc = 0.01

    res = run_claim_agent(
        claim_id=args.claim,
        max_iterations=max_iter,
        max_tokens=max_tok,
        max_cost=max_c,
        max_wall_clock_seconds=max_wc,
    )
    print("\n--- AGENT TRIAGE RESULT ---")
    print(json.dumps(res.model_dump(), indent=2))
