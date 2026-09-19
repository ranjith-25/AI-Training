"""
Race Harness: Race Claims Agent Against Fixed Workflow across 10 Benchmark Claims.
Module: M4 - Agents (Week 7 Practical - Task Set D)

Reports 4 numbers for both systems (8 numbers total):
1. Pass Rate (%)
2. p50 Latency (median ms)
3. Total Tokens
4. Cost per Claim (USD)

Outputs 'race.csv' and formatted comparison table.
"""

from __future__ import annotations

import csv
import json
import logging
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from w7_agent import run_claim_agent
from w7_schemas import ClaimTriageResult
from w7_workflow import run_claim_workflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("W7Race")


def load_benchmark_claims() -> List[Dict[str, Any]]:
    claims_file = Path(__file__).parent / "data" / "w7_claims.json"
    with open(claims_file, "r", encoding="utf-8") as f:
        return json.load(f)


def run_race():
    claims = load_benchmark_claims()
    print("=" * 80)
    print(f"STARTING WEEK 7 BENCHMARK RACE: AGENT VS FIXED WORKFLOW ({len(claims)} CLAIMS)")
    print("=" * 80)

    workflow_results: List[ClaimTriageResult] = []
    agent_results: List[ClaimTriageResult] = []

    # 1. Run Fixed Workflow over all 10 claims
    print("\n>>> RACING FIXED WORKFLOW (4-Step Deterministic DAG)...")
    for idx, claim in enumerate(claims, 1):
        cid = claim["claim_id"]
        logger.info(f"[{idx}/{len(claims)}] Workflow triaging {cid}...")
        res = run_claim_workflow(cid)
        workflow_results.append(res)
        logger.info(
            f"   -> Result: status={res.claim_status}, payable=${res.payable_amount:,.2f}, "
            f"passed={res.passed}, tokens={res.tokens_used}, latency={res.latency_ms:.1f}ms"
        )
        # Small breath to ensure smooth API pacing
        time.sleep(1.0)

    # 2. Run Hand-Built Agent Loop over all 10 claims
    print("\n>>> RACING HAND-BUILT AGENT LOOP (Dynamic ReAct Loop)...")
    for idx, claim in enumerate(claims, 1):
        cid = claim["claim_id"]
        logger.info(f"[{idx}/{len(claims)}] Agent triaging {cid}...")
        res = run_claim_agent(cid)
        agent_results.append(res)
        logger.info(
            f"   -> Result: status={res.claim_status}, payable=${res.payable_amount:,.2f}, "
            f"passed={res.passed}, tokens={res.tokens_used}, latency={res.latency_ms:.1f}ms"
        )
        # 3-second breathing space between multi-lap agent runs
        time.sleep(3.0)

    # -----------------------------------------------------------------
    # Compute the 4 Core Numbers for each system (8 numbers total)
    # -----------------------------------------------------------------
    # 1. Pass Rate
    wf_pass_rate = (sum(1 for r in workflow_results if r.passed) / len(workflow_results)) * 100.0
    ag_pass_rate = (sum(1 for r in agent_results if r.passed) / len(agent_results)) * 100.0

    # 2. p50 Latency (median)
    wf_latencies = [r.latency_ms for r in workflow_results]
    ag_latencies = [r.latency_ms for r in agent_results]
    wf_p50_latency = statistics.median(wf_latencies)
    ag_p50_latency = statistics.median(ag_latencies)

    # 3. Total Tokens (Sum of tokens across all claims, all laps)
    wf_total_tokens = sum(r.tokens_used for r in workflow_results)
    ag_total_tokens = sum(r.tokens_used for r in agent_results)

    # 4. Cost per Claim (Average cost in USD)
    wf_cost_per_claim = sum(r.cost_usd for r in workflow_results) / len(workflow_results)
    ag_cost_per_claim = sum(r.cost_usd for r in agent_results) / len(agent_results)

    # -----------------------------------------------------------------
    # Print the Evaluation Rubric 8-Number Comparison Table
    # -----------------------------------------------------------------
    print("\n" + "=" * 80)
    print("FINAL RACE RESULTS: 8 COMPARATIVE METRICS OVER 10 BENCHMARK CLAIMS")
    print("=" * 80)
    header = f"{'Metric':<25} | {'Fixed Workflow (4 Steps)':<24} | {'Agent Loop (ReAct)':<22} | {'Delta (Agent / Workflow)'}"
    print(header)
    print("-" * 88)
    
    delta_pass = f"{ag_pass_rate - wf_pass_rate:+.1f}%"
    delta_lat = f"{ag_p50_latency / wf_p50_latency:.2f}x slower" if wf_p50_latency > 0 else "N/A"
    delta_tok = f"{ag_total_tokens / wf_total_tokens:.2f}x tokens" if wf_total_tokens > 0 else "N/A"
    delta_cost = f"{ag_cost_per_claim / wf_cost_per_claim:.2f}x cost" if wf_cost_per_claim > 0 else "N/A"

    print(f"{'1. Pass Rate':<25} | {wf_pass_rate:>21.1f}% | {ag_pass_rate:>19.1f}% | {delta_pass}")
    print(f"{'2. p50 Latency':<25} | {wf_p50_latency:>19.1f} ms | {ag_p50_latency:>17.1f} ms | {delta_lat}")
    print(f"{'3. Total Tokens':<25} | {wf_total_tokens:>22,d} | {ag_total_tokens:>20,d} | {delta_tok}")
    print(f"{'4. Cost per Claim':<25} | ${wf_cost_per_claim:>21.6f} | ${ag_cost_per_claim:>19.6f} | {delta_cost}")
    print("=" * 80)

    # -----------------------------------------------------------------
    # Write race.csv
    # -----------------------------------------------------------------
    csv_file = Path(__file__).parent / "race.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # Summary table
        writer.writerow(["SUMMARY METRICS", "Fixed Workflow", "Agent Loop", "Delta"])
        writer.writerow(["Pass Rate (%)", f"{wf_pass_rate:.1f}%", f"{ag_pass_rate:.1f}%", delta_pass])
        writer.writerow(["p50 Latency (ms)", f"{wf_p50_latency:.1f}", f"{ag_p50_latency:.1f}", delta_lat])
        writer.writerow(["Total Tokens", wf_total_tokens, ag_total_tokens, delta_tok])
        writer.writerow(["Cost per Claim ($)", f"{wf_cost_per_claim:.6f}", f"{ag_cost_per_claim:.6f}", delta_cost])
        writer.writerow([])
        # Detailed claim breakdown
        writer.writerow([
            "Claim ID", "System", "Status", "Claimed ($)", "Deductible ($)",
            "Disallowed ($)", "Payable ($)", "Passed", "Tokens Used", "Cost ($)",
            "Latency (ms)", "Steps Executed"
        ])
        for r in workflow_results:
            writer.writerow([
                r.claim_id, "Workflow", r.claim_status, r.claimed_amount, r.deductible,
                r.disallowed_amount, r.payable_amount, r.passed, r.tokens_used,
                f"{r.cost_usd:.6f}", f"{r.latency_ms:.1f}", "; ".join(r.steps_executed)
            ])
        for r in agent_results:
            writer.writerow([
                r.claim_id, "Agent", r.claim_status, r.claimed_amount, r.deductible,
                r.disallowed_amount, r.payable_amount, r.passed, r.tokens_used,
                f"{r.cost_usd:.6f}", f"{r.latency_ms:.1f}", "; ".join(r.steps_executed)
            ])

    print(f"\nMachine-readable benchmark results successfully exported to: {csv_file.resolve()}")


if __name__ == "__main__":
    run_race()
