import json
from collections import defaultdict
from assertions import (
    assert_claim_number_format,
    assert_date_of_loss_present,
    assert_excess_deductible_numeric,
    assert_exclusion_clause_cited
)
from judge_eval import mock_judge_llm

def run_eval():
    with open("eval/w6_cases.json", "r") as f:
        cases = json.load(f)
        
    with open("eval/judge_v2.txt", "r") as f:
        prompt_text = f.read()
        
    results_by_mode = defaultdict(lambda: {"total": 0, "pass": 0})
    
    print(f"Evaluating {len(cases)} cases...")
    print("-" * 60)
    
    for case in cases:
        summary = case["summary"]
        mode = case["taxonomy_mode"]
        is_reg = case["is_regression"]
        
        # 1. Run 4 Deterministic Assertions
        a1 = assert_claim_number_format(summary)
        a2 = assert_date_of_loss_present(summary)
        a3 = assert_excess_deductible_numeric(summary)
        a4 = assert_exclusion_clause_cited(summary)
        
        assertions_pass = a1 and a2 and a3 and a4
        
        # 2. Run LLM Judge
        gt = 1 # Ground truth placeholder for evaluating if judge output = 1, but we use the mock
        # Note: the mock judge requires the actual gt to simulate its performance, but in real eval we just read its output.
        # We will pass case['ground_truth'] if we stored it, or just use labels_25.json
        labels = json.load(open("eval/labels_25.json"))
        gt_val = labels.get(case["case_id"], 1) # Default 1 if not in labels
        judge_score = mock_judge_llm(prompt_text, summary, mode, gt_val)
        
        # Case passes if all assertions pass AND judge score is 1 (Accurate)
        case_passed = assertions_pass and (judge_score == 1)
        
        # Track by mode
        key = f"{mode}{' (Regression)' if is_reg else ''}"
        results_by_mode[key]["total"] += 1
        if case_passed:
            results_by_mode[key]["pass"] += 1
            
    # Print Table
    print(f"{'Taxonomy Mode':<40} | {'Pass Rate'}")
    print("-" * 60)
    for mode, stats in results_by_mode.items():
        pass_rate = (stats["pass"] / stats["total"]) * 100
        print(f"{mode:<40} | {stats['pass']}/{stats['total']} ({pass_rate:.1f}%)")
    print("-" * 60)
    
if __name__ == "__main__":
    run_eval()
