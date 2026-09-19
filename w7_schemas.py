"""
Shared output contract and schemas for Week 7 Practical Task Set D.
Ensures both Agent Loop and Fixed Workflow produce the identical contract.
"""

from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class ClaimTriageResult(BaseModel):
    claim_id: str
    policy_id: str
    claim_status: str
    claimed_amount: float
    deductible: float
    disallowed_amount: float
    payable_amount: float
    exclusion_clause_cited: Optional[str] = None
    reason: str
    passed: bool
    tokens_used: int
    cost_usd: float
    latency_ms: float
    steps_executed: List[str]
    system_type: str = "agent"  # "agent" or "workflow"


# Gemini 3.6 Flash pricing (standard public tier)
# $0.075 per 1M input tokens, $0.30 per 1M output tokens
# Blended approximation: ~$0.15 per 1M tokens ($0.00000015 per token)
PRICE_PER_INPUT_TOKEN = 0.000000075
PRICE_PER_OUTPUT_TOKEN = 0.000000300
PRICE_PER_TOKEN_BLENDED = 0.000000150
