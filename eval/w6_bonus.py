def run_bonus():
    # Simulate a "Wrong Form Edition Confusion" case
    # The summary perfectly and faithfully matches the retrieved chunks.
    # However, the retrieved chunks are from the WRONG edition of the policy (e.g. 2022 instead of 2024).
    
    # RAGAS metrics for this specific trace:
    faithfulness = 0.95
    context_precision = 0.20 # It retrieved the wrong form, so precision against ground-truth chunks is low.
    
    print("--- Week 6 Bonus Challenge: RAGAS Metrics ---")
    print("Scenario: 'Confidently, Faithfully Wrong'")
    print("A claim summary was generated using retrieved chunks from the 2022 edition of HO-0304, but the claim falls under the 2024 edition.")
    print("The summary perfectly aligns with the retrieved 2022 chunks (no hallucinations).")
    print(f"\nFaithfulness Score: {faithfulness:.2f}")
    print(f"Context Precision Score: {context_precision:.2f}")
    
    print("\nWhy the overall average hides this:")
    print("When averaging RAGAS scores across a dataset, a 0.95 Faithfulness looks excellent.")
    print("However, Faithfulness only measures if the summary matches the *retrieved context*.")
    print("If the retriever fetches the wrong context (low Context Precision), the LLM can faithfully")
    print("reproduce the wrong answer. Averaging these metrics hides the fact that high Faithfulness")
    print("is completely useless if Context Precision fails on critical edition-specific queries.")

if __name__ == "__main__":
    run_bonus()
