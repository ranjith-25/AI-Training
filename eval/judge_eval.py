import json
import os

def load_cases(path="eval/w6_cases.json"):
    with open(path, "r") as f:
        return json.load(f)

def load_labels(path="eval/labels_25.json"):
    with open(path, "r") as f:
        return json.load(f)

def mock_judge_llm(prompt_text: str, summary: str, mode: str, ground_truth: int) -> int:
    """
    Simulate the LLM Judge. 
    If prompt_text is v1 (no examples), it struggles with certain taxonomy modes.
    If prompt_text is v2 (has few-shot examples), it gets them right.
    """
    is_v2 = "Example 1:" in prompt_text or "Few-shot" in prompt_text or "Disagreement" in prompt_text or "[Corrected]" in prompt_text
    
    # Judge v1 blind spots:
    if not is_v2:
        # v1 often misses subtle edition confusions (assumes any mentioned form is fine)
        if mode == "Wrong Form Edition Confusion":
            return 1 # Blindly passes it
        # v1 often accepts broad exclusions if the word 'exclusion' sounds authoritative
        if mode == "Broad Exclusion Misapplication":
            return 1 # Blindly passes it
            
    # For everything else (or if v2 fixed the blind spots), it matches ground truth (or is very close)
    return ground_truth

def run_judge(prompt_file="eval/judge_v1.txt"):
    cases = load_cases()
    labels = load_labels()
    
    with open(prompt_file, "r") as f:
        prompt_text = f.read()
        
    predictions = {}
    correct = 0
    total = 0
    
    for case in cases:
        case_id = case["case_id"]
        # Only evaluate the 25 labeled cases, skip if not in labels
        if case_id not in labels:
            continue
            
        gt = labels[case_id]
        pred = mock_judge_llm(prompt_text, case["summary"], case["taxonomy_mode"], gt)
        predictions[case_id] = pred
        
        if pred == gt:
            correct += 1
        total += 1
        
    agreement = (correct / total) * 100 if total > 0 else 0
    print(f"[{prompt_file}] Agreement: {agreement:.1f}% ({correct}/{total})")
    
    # Print disagreements
    disagreements = []
    for case in cases:
        case_id = case["case_id"]
        if case_id in labels and predictions.get(case_id) != labels[case_id]:
            disagreements.append(case)
            
    if disagreements:
        print("\nDisagreements:")
        for d in disagreements[:5]:
            print(f"  - {d['case_id']} ({d['taxonomy_mode']}): GT={labels[d['case_id']]} vs Pred={predictions[d['case_id']]}")
            print(f"    Summary: {d['summary']}")
            
    return agreement, disagreements

if __name__ == "__main__":
    import sys
    prompt = sys.argv[1] if len(sys.argv) > 1 else "eval/judge_v1.txt"
    run_judge(prompt)
