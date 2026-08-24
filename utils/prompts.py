"""
Prompt templates for the claims-assistant RAG system.
"""

# ---------------------------------------------------------------------------
# RAG system prompt — instructs the model to cite and refuse
# ---------------------------------------------------------------------------

RAG_SYSTEM_PROMPT = """\
You are a claims assistant for National Heritage Insurance Company.
Your ONLY knowledge source is the endorsement excerpts provided below.

RULES:
1. Answer ONLY using the provided context excerpts.
2. For every factual claim in your answer, include an inline citation in
   the format:  [Source: <chunk_id> | <form_number>, <clause>]
   where <clause> is the section or exclusion code (e.g. §IV E-17).
3. If the context does NOT contain enough information to answer the
   question, you MUST reply with EXACTLY this phrase and nothing else:
   "I cannot answer this question from the available endorsement documents."
4. Do NOT speculate, infer beyond the text, or use external knowledge.
5. Keep answers concise and precise.
"""

# ---------------------------------------------------------------------------
# RAG user prompt template
# ---------------------------------------------------------------------------

RAG_USER_TEMPLATE = """\
CONTEXT EXCERPTS:
{context}

QUESTION:
{question}
"""

# ---------------------------------------------------------------------------
# Refusal phrase (exact match for evaluation)
# ---------------------------------------------------------------------------

REFUSAL_PHRASE = "I cannot answer this question from the available endorsement documents."

# ---------------------------------------------------------------------------
# Unanswerable questions for refusal testing
# ---------------------------------------------------------------------------

UNANSWERABLE_QUESTIONS = [
    {
        "id": "U1",
        "question": "What is the reserve-setting threshold for claim CLM-2024-88431?",
        "reason": "Reserve-setting thresholds live in the adjuster management system, not in policy endorsements."
    },
    {
        "id": "U2",
        "question": "What is the current reinsurance treaty limit for catastrophic homeowners losses?",
        "reason": "Reinsurance treaty terms are in a separate reinsurance contract, not indexed endorsement wording."
    },
    {
        "id": "U3",
        "question": "Who is the assigned field adjuster for policy POL-HO-2024-55102?",
        "reason": "Adjuster assignments are in the claims management system, not in endorsement documents."
    },
]
