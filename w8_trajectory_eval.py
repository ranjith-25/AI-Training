"""
Week 8: Agent Failure Modes & Trajectory Evals (Task Set D)

Scores the agent's PATH, not just its final answer.
- Asserts expected tool sequences for 10 claims (with alternate-path sets)
- Computes 4 trajectory numbers: tool-choice accuracy, argument validity,
  step efficiency, cost (p50 AND max)
- Reports outcome-vs-trajectory gap
- Applies ONE mitigation, re-runs, reports before->after + price paid
- Regression check across all failure modes

Usage:
    python w8_trajectory_eval.py              # full baseline + mitigation run
    python w8_trajectory_eval.py --baseline   # baseline only
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import statistics
import sys
import time
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("W8TrajectoryEval")

# ---------------------------------------------------------------------------
# 1. FAILURE MODE TAXONOMY (the "Week 8 zoo")
# ---------------------------------------------------------------------------

class FailureMode(str, Enum):
    EXCLUSION_SKIP = "exclusion_skip"           # Right answer, never opened exclusions
    WRONG_TOOL_ORDER = "wrong_tool_order"       # Called tools in nonsensical order
    HALLUCINATED_ARGS = "hallucinated_args"     # Invented claim IDs or form numbers
    REDUNDANT_CALLS = "redundant_calls"         # Called same tool > 1x with same args
    BUDGET_EXCEEDED = "budget_exceeded"         # Hit a budget limit before finishing
    NONE = "none"                               # Clean trajectory


# ---------------------------------------------------------------------------
# 2. EXPECTED TOOL SEQUENCES FOR 10 CLAIMS
#    Alternate-path cases are asserted as SETS of valid sequences.
# ---------------------------------------------------------------------------

# The canonical 4-step path (most claims follow this)
def _canonical(claim_id: str, form: str, cause: str) -> List[str]:
    return ["get_claim", "get_adjuster_notes", "search_policy_exclusions", "compute_payout"]

def _pending(claim_id: str) -> List[str]:
    """Claims with pending inspection skip exclusion search."""
    return ["get_claim", "get_adjuster_notes", "compute_payout"]

# For CLM-2024-7006 (scheduled equipment) and CLM-2024-7008 (sub-deductible clean claim),
# the agent may legitimately skip exclusion search if it determines from adjuster notes
# that the equipment is explicitly scheduled / the claim is clearly covered.
# Both the full 4-step and the 3-step path are valid.

EXPECTED_SEQUENCES: Dict[str, Dict[str, Any]] = {
    "CLM-2024-7001": {
        "valid_paths": [
            ["get_claim", "get_adjuster_notes", "search_policy_exclusions", "compute_payout"],
        ],
        "alternate_path": False,
        "rationale": "Sudden pipe burst: must check exclusions to confirm E-17 does not apply.",
        "valid_claim_ids": {"CLM-2024-7001"},
        "valid_forms": {"HO-0304"},
        "valid_causes": {"sudden pipe burst", "pipe burst", "sudden rupture",
                         "plumbing pipe rupture", "sudden plumbing pipe rupture",
                         "burst pipe", "frozen pipe burst"},
    },
    "CLM-2024-7002": {
        "valid_paths": [
            ["get_claim", "get_adjuster_notes", "search_policy_exclusions", "compute_payout"],
        ],
        "alternate_path": False,
        "rationale": "Flood/surface water: must hit exclusion E-17.",
        "valid_claim_ids": {"CLM-2024-7002"},
        "valid_forms": {"HO-0304"},
        "valid_causes": {"flood and surface water", "flood", "surface water",
                         "flooding", "groundwater", "rising surface water",
                         "surface flood", "river overflow"},
    },
    "CLM-2024-7003": {
        "valid_paths": [
            ["get_claim", "get_adjuster_notes", "search_policy_exclusions", "compute_payout"],
        ],
        "alternate_path": False,
        "rationale": "Gradual seepage 8+ months: must hit exclusion E-15.",
        "valid_claim_ids": {"CLM-2024-7003"},
        "valid_forms": {"HO-0304"},
        "valid_causes": {"gradual seepage", "continuous seepage", "chronic seepage",
                         "gradual continuous seepage", "slow leak", "pinhole leak",
                         "continuous leakage"},
    },
    "CLM-2024-7004": {
        "valid_paths": [
            ["get_claim", "get_adjuster_notes", "search_policy_exclusions", "compute_payout"],
        ],
        "alternate_path": False,
        "rationale": "Unscheduled HVAC: must hit exclusion E-28.",
        "valid_claim_ids": {"CLM-2024-7004"},
        "valid_forms": {"HO-0308"},
        "valid_causes": {"unscheduled equipment", "unscheduled equipment breakdown",
                         "not scheduled", "equipment not on schedule",
                         "not on the schedule", "unscheduled"},
    },
    "CLM-2024-7005": {
        "valid_paths": [
            ["get_claim", "get_adjuster_notes", "search_policy_exclusions", "compute_payout"],
        ],
        "alternate_path": False,
        "rationale": "Sewer backup: must hit exclusion E-18.",
        "valid_claim_ids": {"CLM-2024-7005"},
        "valid_forms": {"HO-0304"},
        "valid_causes": {"sewer", "drain backup", "sewer backup", "sewer line drain backup",
                         "sewer water", "effluent", "storm sewer", "floor drain",
                         "municipal sewer", "lateral"},
    },
    "CLM-2024-7006": {
        # ALTERNATE PATH CASE: Agent may skip exclusion search because adjuster
        # explicitly confirms the boiler is on Schedule A. Both paths are valid.
        "valid_paths": [
            ["get_claim", "get_adjuster_notes", "search_policy_exclusions", "compute_payout"],
            ["get_claim", "get_adjuster_notes", "compute_payout"],  # legitimate short path
        ],
        "alternate_path": True,
        "rationale": "Scheduled boiler: adjuster confirms equipment is on Schedule A. "
                     "Agent may skip exclusions if notes confirm scheduled coverage, "
                     "OR may check exclusions to confirm no E-27/E-28 applies.",
        "valid_claim_ids": {"CLM-2024-7006"},
        "valid_forms": {"HO-0308"},
        "valid_causes": {"power surge", "electrical surge", "equipment breakdown",
                         "boiler breakdown", "boiler control board burnout",
                         "scheduled equipment breakdown"},
    },
    "CLM-2024-7007": {
        "valid_paths": [
            ["get_claim", "get_adjuster_notes", "search_policy_exclusions", "compute_payout"],
        ],
        "alternate_path": False,
        "rationale": "Partial: sudden rupture + pre-existing rot. Must check exclusions for E-16.",
        "valid_claim_ids": {"CLM-2024-7007"},
        "valid_forms": {"HO-0304"},
        "valid_causes": {"pre-existing wear and rot", "pre-existing rot", "rot",
                         "dry rot", "decay", "pre-existing deterioration",
                         "sudden plumbing pipe rupture", "pipe rupture"},
    },
    "CLM-2024-7008": {
        # ALTERNATE PATH CASE: Sub-deductible claim is covered but payout is $0.
        # Agent may skip exclusion search if adjuster notes confirm clean sudden
        # rupture with no exclusion triggers, OR may check exclusions for completeness.
        "valid_paths": [
            ["get_claim", "get_adjuster_notes", "search_policy_exclusions", "compute_payout"],
            ["get_claim", "get_adjuster_notes", "compute_payout"],  # legitimate if clean claim
        ],
        "alternate_path": True,
        "rationale": "Sub-deductible clean claim: $650 < $1000 deductible. "
                     "Agent may skip exclusions if notes confirm sudden accidental cause, "
                     "OR may check exclusions for audit completeness.",
        "valid_claim_ids": {"CLM-2024-7008"},
        "valid_forms": {"HO-0304"},
        "valid_causes": {"sudden plumbing pipe rupture", "pipe rupture", "p-trap failure",
                         "sudden rupture", "burst pipe", "plumbing failure"},
    },
    "CLM-2024-7009": {
        "valid_paths": [
            ["get_claim", "get_adjuster_notes", "compute_payout"],  # pending inspection
        ],
        "alternate_path": False,
        "rationale": "Pending inspection: no cause established, skip exclusion search.",
        "valid_claim_ids": {"CLM-2024-7009"},
        "valid_forms": {"HO-0304"},
        "valid_causes": set(),  # No exclusion search expected
    },
    "CLM-2024-7010": {
        "valid_paths": [
            ["get_claim", "get_adjuster_notes", "search_policy_exclusions", "compute_payout"],
        ],
        "alternate_path": False,
        "rationale": "Pair/set partial loss: must check E-31.",
        "valid_claim_ids": {"CLM-2024-7010"},
        "valid_forms": {"HO-0309"},
        "valid_causes": {"pair and set partial loss", "pairs and sets", "pair",
                         "set", "matched pair", "earring", "lost earring"},
    },
}


# ---------------------------------------------------------------------------
# 3. TRAJECTORY SCORING FUNCTIONS
# ---------------------------------------------------------------------------

@dataclass
class TrajectoryScore:
    """Score for a single claim's agent trajectory."""
    claim_id: str
    outcome_passed: bool           # Did the final answer match ground truth?
    trajectory_passed: bool        # Did the tool sequence match expected?
    tool_sequence: List[str]       # Actual tool names called
    tool_sequence_match: bool      # Does sequence match one of the valid paths?
    args_valid: bool               # Were all arguments real (not hallucinated)?
    arg_issues: List[str]          # List of argument issues found
    steps_taken: int               # Actual number of tool calls
    steps_needed: int              # Minimum valid path length for this claim
    step_efficiency: float         # steps_taken / steps_needed
    failure_modes: List[FailureMode]
    cost_usd: float
    latency_ms: float
    tokens_used: int
    raw_steps: List[str]           # Raw step strings from agent


def _parse_tool_name(step_str: str) -> str:
    """Extract the tool function name from a step string like 'get_claim({...})'."""
    match = re.match(r"(\w+)\(", step_str)
    return match.group(1) if match else step_str


def _parse_tool_args(step_str: str) -> Dict[str, Any]:
    """Extract arguments from step string."""
    match = re.search(r"\((.+)\)$", step_str, re.DOTALL)
    if not match:
        return {}
    arg_str = match.group(1)
    # Handle both formats: key='val' and {'key': 'val'}
    try:
        return eval(arg_str)  # Safe for our controlled format
    except Exception:
        # Parse key=val format
        result = {}
        for part in arg_str.split(", "):
            if "=" in part:
                k, v = part.split("=", 1)
                result[k.strip()] = v.strip().strip("'\"")
        return result


def _validate_arguments(
    claim_id: str,
    steps: List[str],
    expected: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    """Check if tool arguments are real (not hallucinated)."""
    issues = []
    valid_claim_ids = expected.get("valid_claim_ids", set())
    valid_forms = expected.get("valid_forms", set())
    valid_causes = expected.get("valid_causes", set())

    for step_str in steps:
        fname = _parse_tool_name(step_str)
        args = _parse_tool_args(step_str)

        if fname in ("get_claim", "get_adjuster_notes"):
            arg_cid = args.get("claim_id", "")
            if arg_cid and arg_cid not in valid_claim_ids:
                issues.append(f"{fname}: hallucinated claim_id '{arg_cid}' (expected one of {valid_claim_ids})")

        elif fname == "search_policy_exclusions":
            arg_form = args.get("policy_form", args.get("form", ""))
            
            if arg_form and arg_form not in valid_forms:
                issues.append(f"search_policy_exclusions: wrong form '{arg_form}' (expected {valid_forms})")

    return (len(issues) == 0, issues)


def score_trajectory(
    claim_id: str,
    agent_result: Dict[str, Any],
    expected: Dict[str, Any],
) -> TrajectoryScore:
    """Score a single claim's agent trajectory against expected sequences."""
    raw_steps = agent_result.get("steps_executed", [])
    actual_tools = [_parse_tool_name(s) for s in raw_steps]
    valid_paths = expected["valid_paths"]

    # Tool sequence match
    tool_seq_match = actual_tools in valid_paths

    # Argument validity
    args_valid, arg_issues = _validate_arguments(claim_id, raw_steps, expected)

    # Step efficiency
    min_path_len = min(len(p) for p in valid_paths)
    steps_taken = len(actual_tools)
    step_efficiency = steps_taken / min_path_len if min_path_len > 0 else float("inf")

    # Detect failure modes
    failure_modes = []

    # 1. EXCLUSION_SKIP: outcome correct but never called search_policy_exclusions
    #    when ALL valid paths require it
    all_paths_need_exclusions = all(
        "search_policy_exclusions" in p for p in valid_paths
    )
    skipped_exclusions = "search_policy_exclusions" not in actual_tools
    outcome_passed = agent_result.get("passed", False)

    if outcome_passed and skipped_exclusions and all_paths_need_exclusions:
        failure_modes.append(FailureMode.EXCLUSION_SKIP)

    # 2. WRONG_TOOL_ORDER: tools called but not in any valid path order
    if not tool_seq_match and steps_taken > 0:
        # Check if it's just extra calls vs wrong ordering
        if sorted(actual_tools) != sorted(actual_tools):
            failure_modes.append(FailureMode.WRONG_TOOL_ORDER)

    # 3. HALLUCINATED_ARGS
    if not args_valid:
        failure_modes.append(FailureMode.HALLUCINATED_ARGS)

    # 4. REDUNDANT_CALLS: same tool called with same effective args > 1x
    seen_calls = set()
    for s in raw_steps:
        canonical = _parse_tool_name(s) + str(sorted(_parse_tool_args(s).items()))
        if canonical in seen_calls:
            failure_modes.append(FailureMode.REDUNDANT_CALLS)
            break
        seen_calls.add(canonical)

    # 5. BUDGET_EXCEEDED
    if agent_result.get("claim_status") == "BUDGET_EXCEEDED":
        failure_modes.append(FailureMode.BUDGET_EXCEEDED)

    if not failure_modes:
        failure_modes.append(FailureMode.NONE)

    # Trajectory passes if tool sequence matches AND args are valid
    trajectory_passed = tool_seq_match and args_valid

    return TrajectoryScore(
        claim_id=claim_id,
        outcome_passed=outcome_passed,
        trajectory_passed=trajectory_passed,
        tool_sequence=actual_tools,
        tool_sequence_match=tool_seq_match,
        args_valid=args_valid,
        arg_issues=arg_issues,
        steps_taken=steps_taken,
        steps_needed=min_path_len,
        step_efficiency=round(step_efficiency, 2),
        failure_modes=failure_modes,
        cost_usd=agent_result.get("cost_usd", 0.0),
        latency_ms=agent_result.get("latency_ms", 0.0),
        tokens_used=agent_result.get("tokens_used", 0),
        raw_steps=raw_steps,
    )


# ---------------------------------------------------------------------------
# 4. RUN THE AGENT AND COLLECT TRAJECTORIES
# ---------------------------------------------------------------------------

def run_agent_trajectories(use_mitigation: bool = False) -> List[Dict[str, Any]]:
    """Run the agent over all 10 claims, return results as dicts."""
    from w7_schemas import ClaimTriageResult

    if use_mitigation:
        from w8_agent_mitigated import run_claim_agent_mitigated as run_fn
        system_label = "agent_mitigated"
    else:
        from w7_agent import run_claim_agent as run_fn
        system_label = "agent_baseline"

    claims_file = Path(__file__).parent / "data" / "w7_claims.json"
    with open(claims_file, "r", encoding="utf-8") as f:
        claims = json.load(f)

    results = []
    for idx, claim in enumerate(claims, 1):
        cid = claim["claim_id"]
        logger.info(f"[{idx}/{len(claims)}] {system_label}: triaging {cid}...")
        try:
            res = run_fn(cid)
            results.append(res.model_dump())
            logger.info(
                f"   -> status={res.claim_status}, payable=${res.payable_amount:,.2f}, "
                f"passed={res.passed}, steps={len(res.steps_executed)}"
            )
        except Exception as e:
            logger.error(f"   -> ERROR: {e}")
            results.append({
                "claim_id": cid,
                "claim_status": "ERROR",
                "passed": False,
                "steps_executed": [],
                "tokens_used": 0,
                "cost_usd": 0.0,
                "latency_ms": 0.0,
                "payable_amount": 0.0,
            })
        # Breathing space for API rate limits
        time.sleep(2.0)

    return results


# ---------------------------------------------------------------------------
# 5. COMPUTE & REPORT THE 4 TRAJECTORY NUMBERS
# ---------------------------------------------------------------------------

def compute_trajectory_metrics(scores: List[TrajectoryScore]) -> Dict[str, Any]:
    """Compute the 4 trajectory numbers from scored trajectories."""
    n = len(scores)

    # 1. Tool-Choice Accuracy: % of claims where the tool sequence matched a valid path
    tool_choice_acc = sum(1 for s in scores if s.tool_sequence_match) / n * 100

    # 2. Argument Validity Rate: % of claims where all args were real, not hallucinated
    arg_validity = sum(1 for s in scores if s.args_valid) / n * 100

    # 3. Step Efficiency: mean(steps_taken / steps_needed)
    efficiencies = [s.step_efficiency for s in scores if s.step_efficiency < float("inf")]
    step_eff_mean = statistics.mean(efficiencies) if efficiencies else 0.0

    # 4. Cost per claim: p50 AND max (not just the mean)
    costs = [s.cost_usd for s in scores]
    cost_p50 = statistics.median(costs) if costs else 0.0
    cost_max = max(costs) if costs else 0.0
    cost_mean = statistics.mean(costs) if costs else 0.0

    # Latency similarly
    latencies = [s.latency_ms for s in scores]
    latency_p50 = statistics.median(latencies) if latencies else 0.0
    latency_max = max(latencies) if latencies else 0.0

    return {
        "tool_choice_accuracy": round(tool_choice_acc, 1),
        "argument_validity_rate": round(arg_validity, 1),
        "step_efficiency_mean": round(step_eff_mean, 2),
        "cost_p50": round(cost_p50, 6),
        "cost_max": round(cost_max, 6),
        "cost_mean": round(cost_mean, 6),
        "latency_p50_ms": round(latency_p50, 1),
        "latency_max_ms": round(latency_max, 1),
    }


# ---------------------------------------------------------------------------
# 6. OUTCOME-VS-TRAJECTORY GAP
# ---------------------------------------------------------------------------

def compute_gap(scores: List[TrajectoryScore]) -> Dict[str, Any]:
    """Compute outcome pass rate - trajectory pass rate."""
    n = len(scores)
    outcome_pass_rate = sum(1 for s in scores if s.outcome_passed) / n * 100
    trajectory_pass_rate = sum(1 for s in scores if s.trajectory_passed) / n * 100
    gap = outcome_pass_rate - trajectory_pass_rate

    # Find the right-answer-wrong-path case(s)
    right_answer_wrong_path = [
        s for s in scores
        if s.outcome_passed and not s.trajectory_passed
    ]

    return {
        "outcome_pass_rate": round(outcome_pass_rate, 1),
        "trajectory_pass_rate": round(trajectory_pass_rate, 1),
        "gap": round(gap, 1),
        "right_answer_wrong_path_claims": [
            {
                "claim_id": s.claim_id,
                "tool_sequence": s.tool_sequence,
                "failure_modes": [m.value for m in s.failure_modes],
                "arg_issues": s.arg_issues,
            }
            for s in right_answer_wrong_path
        ],
    }


# ---------------------------------------------------------------------------
# 7. PER-MODE FAILURE COUNTS (for regression checking)
# ---------------------------------------------------------------------------

def count_failure_modes(scores: List[TrajectoryScore]) -> Dict[str, int]:
    """Count occurrences of each failure mode."""
    counts: Dict[str, int] = {m.value: 0 for m in FailureMode}
    for s in scores:
        for fm in s.failure_modes:
            counts[fm.value] += 1
    return counts


# ---------------------------------------------------------------------------
# 8. MAIN: Run baseline, score, apply mitigation, re-score, regression check
# ---------------------------------------------------------------------------

def print_trajectory_table(scores: List[TrajectoryScore], label: str):
    """Print a per-claim trajectory table."""
    print(f"\n{'='*100}")
    print(f"TRAJECTORY EVAL: {label} ({len(scores)} claims)")
    print(f"{'='*100}")
    header = (
        f"{'Claim ID':<16} | {'Outcome':>7} | {'Traj':>5} | "
        f"{'Seq Match':>9} | {'Args OK':>7} | {'Steps':>5} | "
        f"{'Eff':>5} | {'Failure Modes'}"
    )
    print(header)
    print("-" * 100)
    for s in scores:
        modes = ", ".join(m.value for m in s.failure_modes)
        print(
            f"{s.claim_id:<16} | {'PASS' if s.outcome_passed else 'FAIL':>7} | "
            f"{'PASS' if s.trajectory_passed else 'FAIL':>5} | "
            f"{'YES' if s.tool_sequence_match else 'NO':>9} | "
            f"{'YES' if s.args_valid else 'NO':>7} | "
            f"{s.steps_taken:>2}/{s.steps_needed:<2} | "
            f"{s.step_efficiency:>5.2f} | {modes}"
        )
    print("-" * 100)


def print_metrics_table(metrics: Dict[str, Any], label: str):
    """Print the 4 trajectory numbers."""
    print(f"\n--- Trajectory Metrics: {label} ---")
    print(f"  Tool-Choice Accuracy:    {metrics['tool_choice_accuracy']}%")
    print(f"  Argument Validity Rate:  {metrics['argument_validity_rate']}%")
    print(f"  Step Efficiency (mean):  {metrics['step_efficiency_mean']}")
    print(f"  Cost per Claim (p50):    ${metrics['cost_p50']:.6f}")
    print(f"  Cost per Claim (max):    ${metrics['cost_max']:.6f}")
    print(f"  Cost per Claim (mean):   ${metrics['cost_mean']:.6f}")
    print(f"  Latency p50:             {metrics['latency_p50_ms']:.1f} ms")
    print(f"  Latency max:             {metrics['latency_max_ms']:.1f} ms")


def print_gap(gap: Dict[str, Any]):
    """Print the outcome-vs-trajectory gap."""
    print(f"\n--- Outcome vs Trajectory Gap ---")
    print(f"  Outcome Pass Rate:     {gap['outcome_pass_rate']}%")
    print(f"  Trajectory Pass Rate:  {gap['trajectory_pass_rate']}%")
    print(f"  GAP:                   {gap['gap']} percentage points")
    if gap["right_answer_wrong_path_claims"]:
        print(f"\n  Right-answer-wrong-path claims:")
        for c in gap["right_answer_wrong_path_claims"]:
            print(f"    {c['claim_id']}:")
            print(f"      Tool sequence: {' -> '.join(c['tool_sequence'])}")
            print(f"      Failure modes: {', '.join(c['failure_modes'])}")
            if c["arg_issues"]:
                for issue in c["arg_issues"]:
                    print(f"      Arg issue: {issue}")


def print_regression_table(
    before_counts: Dict[str, int],
    after_counts: Dict[str, int],
):
    """Print per-mode regression table."""
    print(f"\n--- Per-Mode Regression Check ---")
    print(f"{'Failure Mode':<25} | {'Before':>6} | {'After':>6} | {'Delta':>6} | {'Status'}")
    print("-" * 75)
    all_modes = sorted(set(list(before_counts.keys()) + list(after_counts.keys())))
    regressions = []
    new_modes = []
    for mode in all_modes:
        b = before_counts.get(mode, 0)
        a = after_counts.get(mode, 0)
        delta = a - b
        if delta > 0 and mode != FailureMode.NONE.value:
            status = "REGRESSED"
            regressions.append(mode)
        elif delta < 0 and mode != FailureMode.NONE.value:
            status = "improved"
        elif b == 0 and a > 0 and mode != FailureMode.NONE.value:
            status = "NEW MODE"
            new_modes.append(mode)
        else:
            status = "unchanged"
        print(f"{mode:<25} | {b:>6} | {a:>6} | {delta:>+6} | {status}")
    print("-" * 75)
    if regressions:
        print(f"  WARNING: Modes that got worse: {regressions}")
    if new_modes:
        print(f"  WARNING: New modes introduced: {new_modes}")
    if not regressions and not new_modes:
        print(f"  No regressions detected. Modes checked: {[m for m in all_modes if m != 'none']}")


def main():
    parser = argparse.ArgumentParser(description="W8 Trajectory Eval")
    parser.add_argument("--baseline", action="store_true",
                        help="Run baseline only, skip mitigation")
    args = parser.parse_args()

    # =====================================================================
    # PHASE 1: BASELINE — Run agent over 10 claims, score trajectories
    # =====================================================================
    print("\n" + "=" * 100)
    print("PHASE 1: BASELINE AGENT TRAJECTORY EVAL")
    print("=" * 100)

    baseline_results = run_agent_trajectories(use_mitigation=False)

    # Score each trajectory
    baseline_scores = []
    for res in baseline_results:
        cid = res["claim_id"]
        if cid in EXPECTED_SEQUENCES:
            score = score_trajectory(cid, res, EXPECTED_SEQUENCES[cid])
            baseline_scores.append(score)

    # Print per-claim table
    print_trajectory_table(baseline_scores, "BASELINE")

    # Compute & print 4 trajectory numbers
    baseline_metrics = compute_trajectory_metrics(baseline_scores)
    print_metrics_table(baseline_metrics, "BASELINE")

    # Compute & print outcome-vs-trajectory gap
    baseline_gap = compute_gap(baseline_scores)
    print_gap(baseline_gap)

    # Count failure modes for baseline
    baseline_mode_counts = count_failure_modes(baseline_scores)
    print(f"\n--- Baseline Failure Mode Counts ---")
    for mode, count in sorted(baseline_mode_counts.items()):
        if count > 0:
            print(f"  {mode}: {count}")

    # Determine top failure mode (excluding 'none')
    active_modes = {k: v for k, v in baseline_mode_counts.items()
                    if k != FailureMode.NONE.value and v > 0}
    if active_modes:
        top_mode = max(active_modes, key=active_modes.get)
        print(f"\n  TOP FAILURE MODE: {top_mode} (count: {active_modes[top_mode]})")
    else:
        top_mode = None
        print("\n  No active failure modes detected in baseline.")

    # Save baseline results
    output_dir = Path(__file__).parent
    baseline_out = {
        "phase": "baseline",
        "per_claim": [
            {
                "claim_id": s.claim_id,
                "outcome_passed": s.outcome_passed,
                "trajectory_passed": s.trajectory_passed,
                "tool_sequence": s.tool_sequence,
                "tool_sequence_match": s.tool_sequence_match,
                "args_valid": s.args_valid,
                "arg_issues": s.arg_issues,
                "steps_taken": s.steps_taken,
                "steps_needed": s.steps_needed,
                "step_efficiency": s.step_efficiency,
                "failure_modes": [m.value for m in s.failure_modes],
                "cost_usd": s.cost_usd,
                "latency_ms": s.latency_ms,
                "tokens_used": s.tokens_used,
                "raw_steps": s.raw_steps,
            }
            for s in baseline_scores
        ],
        "metrics": baseline_metrics,
        "gap": baseline_gap,
        "failure_mode_counts": baseline_mode_counts,
        "top_failure_mode": top_mode,
    }
    with open(output_dir / "w8_baseline_results.json", "w", encoding="utf-8") as f:
        json.dump(baseline_out, f, indent=2, ensure_ascii=False)
    print(f"\nBaseline results saved to w8_baseline_results.json")

    if args.baseline:
        print("\n--baseline flag set. Skipping mitigation phase.")
        return

    # =====================================================================
    # PHASE 2: MITIGATION — Apply ONE fix, re-run, measure price paid
    # =====================================================================
    print("\n\n" + "=" * 100)
    print("PHASE 2: MITIGATED AGENT TRAJECTORY EVAL")
    print(f"Mitigation applied: tighter tool description for search_policy_exclusions")
    print(f"Target failure mode: {top_mode}")
    print("=" * 100)

    mitigated_results = run_agent_trajectories(use_mitigation=True)

    # Score mitigated trajectories
    mitigated_scores = []
    for res in mitigated_results:
        cid = res["claim_id"]
        if cid in EXPECTED_SEQUENCES:
            score = score_trajectory(cid, res, EXPECTED_SEQUENCES[cid])
            mitigated_scores.append(score)

    print_trajectory_table(mitigated_scores, "MITIGATED")

    mitigated_metrics = compute_trajectory_metrics(mitigated_scores)
    print_metrics_table(mitigated_metrics, "MITIGATED")

    mitigated_gap = compute_gap(mitigated_scores)
    print_gap(mitigated_gap)

    mitigated_mode_counts = count_failure_modes(mitigated_scores)

    # =====================================================================
    # PHASE 3: REPORT — Before->After for top mode + price paid + regression
    # =====================================================================
    print("\n\n" + "=" * 100)
    print("PHASE 3: MITIGATION IMPACT REPORT")
    print("=" * 100)

    if top_mode:
        before_count = baseline_mode_counts.get(top_mode, 0)
        after_count = mitigated_mode_counts.get(top_mode, 0)
        print(f"\n  Top Failure Mode: {top_mode}")
        print(f"  Before -> After: {before_count} -> {after_count}")

    # Price paid
    print(f"\n  --- Price Paid for Mitigation ---")
    print(f"  {'Metric':<30} | {'Baseline':>12} | {'Mitigated':>12} | {'Delta':>12}")
    print(f"  {'-'*70}")

    lat_delta = mitigated_metrics["latency_p50_ms"] - baseline_metrics["latency_p50_ms"]
    cost_delta = mitigated_metrics["cost_p50"] - baseline_metrics["cost_p50"]
    eff_delta = mitigated_metrics["step_efficiency_mean"] - baseline_metrics["step_efficiency_mean"]

    print(f"  {'Latency p50 (ms)':<30} | {baseline_metrics['latency_p50_ms']:>12.1f} | {mitigated_metrics['latency_p50_ms']:>12.1f} | {lat_delta:>+12.1f}")
    print(f"  {'Cost p50 ($)':<30} | {baseline_metrics['cost_p50']:>12.6f} | {mitigated_metrics['cost_p50']:>12.6f} | {cost_delta:>+12.6f}")
    print(f"  {'Step Efficiency (mean)':<30} | {baseline_metrics['step_efficiency_mean']:>12.2f} | {mitigated_metrics['step_efficiency_mean']:>12.2f} | {eff_delta:>+12.2f}")
    print(f"  {'Tool-Choice Accuracy (%)':<30} | {baseline_metrics['tool_choice_accuracy']:>12.1f} | {mitigated_metrics['tool_choice_accuracy']:>12.1f} | {mitigated_metrics['tool_choice_accuracy'] - baseline_metrics['tool_choice_accuracy']:>+12.1f}")

    # Regression check
    print_regression_table(baseline_mode_counts, mitigated_mode_counts)

    # Save all results
    full_out = {
        "baseline": baseline_out,
        "mitigated": {
            "phase": "mitigated",
            "mitigation": "Tighter tool description for search_policy_exclusions: "
                          "added MANDATORY instruction to always call this tool before "
                          "compute_payout unless inspection is pending.",
            "target_mode": top_mode,
            "per_claim": [
                {
                    "claim_id": s.claim_id,
                    "outcome_passed": s.outcome_passed,
                    "trajectory_passed": s.trajectory_passed,
                    "tool_sequence": s.tool_sequence,
                    "tool_sequence_match": s.tool_sequence_match,
                    "args_valid": s.args_valid,
                    "arg_issues": s.arg_issues,
                    "steps_taken": s.steps_taken,
                    "steps_needed": s.steps_needed,
                    "step_efficiency": s.step_efficiency,
                    "failure_modes": [m.value for m in s.failure_modes],
                    "cost_usd": s.cost_usd,
                    "latency_ms": s.latency_ms,
                    "tokens_used": s.tokens_used,
                    "raw_steps": s.raw_steps,
                }
                for s in mitigated_scores
            ],
            "metrics": mitigated_metrics,
            "gap": mitigated_gap,
            "failure_mode_counts": mitigated_mode_counts,
        },
        "regression": {
            "before_counts": baseline_mode_counts,
            "after_counts": mitigated_mode_counts,
        },
        "price_paid": {
            "latency_p50_delta_ms": round(lat_delta, 1),
            "cost_p50_delta_usd": round(cost_delta, 6),
            "step_efficiency_delta": round(eff_delta, 2),
        },
    }
    with open(output_dir / "w8_trajectory_results.json", "w", encoding="utf-8") as f:
        json.dump(full_out, f, indent=2, ensure_ascii=False)
    print(f"\nFull trajectory eval results saved to w8_trajectory_results.json")


if __name__ == "__main__":
    main()
