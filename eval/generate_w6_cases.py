import json

modes = [
    'Wrong Form Edition Confusion',
    'Broad Exclusion Misapplication',
    'Numeric Sub-Limit Blindness',
    'Single-Endorsement Tunnel Vision',
    'Definitional Tone Deafness',
    'Ambiguity Overconfidence'
]

cases = []
labels = {}

# We generate 26 cases: 24 regular + 2 regression
for i in range(1, 27):
    is_reg = (i >= 25)
    mode = modes[i % len(modes)]
    case_id = f"REG-{i-24:02d}" if is_reg else f"CASE-{i:02d}"
    
    # Let's write realistic but brief claim summaries. 
    # Some will correctly state coverage, some will misapply exclusions or sub-limits.
    
    claim_num = f"CLM-2024-{10000+i}"
    date_of_loss = f"2024-08-{i:02d}"
    deductible = f"${1000 + (i%5)*500}"
    
    # Make ground_truth 1 for even, 0 for odd
    ground_truth = 1 if i % 2 == 0 else 0
    labels[case_id] = ground_truth
    
    # Build a summary that incorporates the formatting strings we'll test for via assertions.
    # We will purposely omit formatting in 2 cases to ensure assertions actually catch them.
    
    if mode == 'Broad Exclusion Misapplication':
        # E.g. Failing: denied for wear and tear when it was a sudden pipe burst
        if ground_truth == 0:
            summary = f"Claim {claim_num} for loss on {date_of_loss}. Deductible {deductible} applies. The loss is denied due to the general deterioration exclusion (Form HO-0304), despite the adjuster noting a sudden pipe burst."
        else:
            summary = f"Claim {claim_num} for loss on {date_of_loss}. Deductible {deductible} applies. The loss is covered under HO-0304 as a sudden and accidental pipe burst."
            
    elif mode == 'Numeric Sub-Limit Blindness':
        # E.g. Failing: hallucinates a $100k limit when it should be $10k
        if ground_truth == 0:
            summary = f"Claim {claim_num} for loss on {date_of_loss}. Deductible {deductible} applies. We are paying the policy limit of $100,000 for water damage, disregarding the $10,000 sub-limit."
        else:
            summary = f"Claim {claim_num} for loss on {date_of_loss}. Deductible {deductible} applies. Payment is capped at the $10,000 per-occurrence sub-limit for water damage."
            
    elif mode == 'Wrong Form Edition Confusion':
        if ground_truth == 0:
            summary = f"Claim {claim_num} for loss on {date_of_loss}. Deductible {deductible} applies. The loss is denied citing the 14-day gradual seepage rule from the 2022 edition, though the 2024 edition applies (Form HO-0304)."
        else:
            summary = f"Claim {claim_num} for loss on {date_of_loss}. Deductible {deductible} applies. The loss is covered, correctly applying the current 2024 edition rules."
            
    elif mode == 'Single-Endorsement Tunnel Vision':
        if ground_truth == 0:
            summary = f"Claim {claim_num} for loss on {date_of_loss}. Deductible {deductible} applies. Claim denied based on HO-0304, ignoring the overriding coverage provided in HO-0309."
        else:
            summary = f"Claim {claim_num} for loss on {date_of_loss}. Deductible {deductible} applies. Coverage synthesized correctly across HO-0304 and HO-0309."
            
    elif mode == 'Definitional Tone Deafness':
        if ground_truth == 0:
            summary = f"Claim {claim_num} for loss on {date_of_loss}. Deductible {deductible} applies. Question asked for definition of 'Breakdown', but summary just states 'Yes, this is covered'."
        else:
            summary = f"Claim {claim_num} for loss on {date_of_loss}. Deductible {deductible} applies. The summary correctly defines 'Breakdown' according to HO-0308."
            
    elif mode == 'Ambiguity Overconfidence':
        if ground_truth == 0:
            summary = f"Claim {claim_num} for loss on {date_of_loss}. Deductible {deductible} applies. The summary confidently asserts the loss is fully covered despite missing information about the cause of loss."
        else:
            summary = f"Claim {claim_num} for loss on {date_of_loss}. Deductible {deductible} applies. The summary correctly notes that coverage determination requires clarification on the cause of loss."
            
    else:
        summary = f"Claim {claim_num} for loss on {date_of_loss}. Deductible {deductible} applies. Basic summary."
        
    # Inject a denial without exclusion citation for one case to fail the assertion
    if i == 5:
        summary = f"Claim {claim_num} for loss on {date_of_loss}. Deductible {deductible} applies. The claim is denied because it's not covered."
    # Break format for one case to fail the claim number assertion
    if i == 6:
        summary = summary.replace("CLM-", "CLAIM ")

    cases.append({
        "case_id": case_id,
        "claim_number": claim_num,
        "date_of_loss": date_of_loss,
        "deductible_amount": deductible,
        "taxonomy_mode": mode,
        "is_regression": is_reg,
        "summary": summary
    })

with open("eval/w6_cases.json", "w") as f:
    json.dump(cases, f, indent=2)

with open("eval/labels_25.json", "w") as f:
    json.dump(labels, f, indent=2)

print(f"Generated {len(cases)} cases and saved to w6_cases.json and labels_25.json")
