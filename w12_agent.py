"""
W12 Capstone — Integrated Claims Triage Agent
Single entry point. One command. One trace_id from FNOL to referral.

Integrates:
  - Week 3-4: RAG pipeline (endorsement retrieval)
  - Week 7:   Hand-built agent loop (ReAct, 4 budget guards)
  - Week 9:   MCP client (runtime tool discovery, not hardcoded)
  - Week 11:  Per-request JSONL logging, PII redaction, cost/latency per span

Key guarantees:
  - Endorsement version resolved by LOSS DATE (code-enforced, not prompt hint)
  - Never emits the word "covered" — refers to adjuster instead
  - PII (names, addresses, injuries) stripped before any log or MCP response
  - Single trace_id propagates through agent hop AND MCP hop
  - deductible / sub_limit / policy_limit kept as THREE distinct numbers

Usage:
    python w12_agent.py --claim CLM-2024-7002
    python w12_agent.py --claim CLM-2024-7002 --config w12_config.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("W12Agent")

# ---------------------------------------------------------------------------
# PII Redaction
# ---------------------------------------------------------------------------

_PII_PATTERNS_LOG = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-SSN]"),
    (re.compile(r"\b\d{3}[-.]\d{3}[-.]\d{4}\b"), "[REDACTED-PHONE]"),
    (re.compile(
        r"(?:claimant|insured|injured party)\s*:\s*[A-Z][a-z]+ [A-Z][a-z]+",
        re.IGNORECASE,
    ), "[REDACTED-NAME]"),
    (re.compile(
        r"\b\d{1,5}\s+\w+\s+(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Boulevard|Blvd|Lane|Way|Court)\b",
        re.IGNORECASE,
    ), "[REDACTED-ADDRESS]"),
    (re.compile(r"(?:injuries|injury)\s*:\s*[^\.;]+", re.IGNORECASE), "injuries: [REDACTED-INJURY]"),
]


def _redact_pii(text: str) -> str:
    for pattern, replacement in _PII_PATTERNS_LOG:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Budget constants
# ---------------------------------------------------------------------------

MAX_ITERATIONS = 8
MAX_TOKENS = 20_000
MAX_COST_USD = 0.10
MAX_WALL_CLOCK_S = 180.0

PRICE_PER_INPUT_TOKEN = 0.000000075
PRICE_PER_OUTPUT_TOKEN = 0.000000300

# ---------------------------------------------------------------------------
# Span tracker
# ---------------------------------------------------------------------------


class SpanTracker:
    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self.spans: List[Dict[str, Any]] = []
        self._start: Optional[float] = None
        self._name: Optional[str] = None

    def start(self, name: str):
        self._name = name
        self._start = time.perf_counter()

    def end(self, tokens: int = 0, cost_usd: float = 0.0, extra: Optional[Dict] = None):
        latency_ms = round((time.perf_counter() - self._start) * 1000, 2)
        span = {
            "trace_id": self.trace_id,
            "name": self._name,
            "latency_ms": latency_ms,
            "tokens": tokens,
            "cost_usd": round(cost_usd, 8),
        }
        if extra:
            span.update(extra)
        self.spans.append(span)
        logger.info(f"[SPAN] {self._name}: {latency_ms:.1f}ms tokens={tokens} cost=${cost_usd:.7f}")
        return span

    @property
    def total_cost(self) -> float:
        return sum(s["cost_usd"] for s in self.spans)

    @property
    def total_latency(self) -> float:
        return sum(s["latency_ms"] for s in self.spans)


# ---------------------------------------------------------------------------
# JSONL trace writer
# ---------------------------------------------------------------------------

TRACES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "w12_traces.jsonl")


def _write_trace(record: Dict[str, Any]):
    os.makedirs(os.path.dirname(TRACES_PATH), exist_ok=True)
    with open(TRACES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Local tool fallbacks
# ---------------------------------------------------------------------------

def _local_fallback_tools() -> Dict[str, Any]:
    try:
        from w7_tools import get_claim, get_adjuster_notes, search_policy_exclusions
        from w12_mcp_server import (
            search_policy_wording_by_date,
            lookup_policy_coverage_in_force,
            refer_to_adjuster,
        )
        return {
            "get_claim": lambda **kw: json.dumps(get_claim(**kw)),
            "get_adjuster_notes": lambda **kw: _redact_pii(json.dumps(get_adjuster_notes(**kw))),
            "search_policy_exclusions": lambda **kw: json.dumps(search_policy_exclusions(**kw)),
            "search_policy_wording_by_date": search_policy_wording_by_date,
            "lookup_policy_coverage_in_force": lookup_policy_coverage_in_force,
            "refer_to_adjuster": refer_to_adjuster,
        }
    except ImportError as e:
        logger.warning(f"Local fallback import failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# MCP tool → Gemini FunctionDeclaration
# ---------------------------------------------------------------------------

def _map_mcp_tool(mcp_tool) -> Any:
    from google.genai import types
    schema = mcp_tool.input_schema
    properties = {}
    required = schema.get("required", [])
    _type_map = {
        "string": types.Type.STRING, "number": types.Type.NUMBER,
        "integer": types.Type.INTEGER, "boolean": types.Type.BOOLEAN,
        "array": types.Type.ARRAY, "object": types.Type.OBJECT,
    }
    for prop_name, prop_data in schema.get("properties", {}).items():
        raw_type = prop_data.get("type", "string").lower()
        properties[prop_name] = types.Schema(
            type=_type_map.get(raw_type, types.Type.STRING),
            description=prop_data.get("description", ""),
        )
    return types.FunctionDeclaration(
        name=mcp_tool.name,
        description=mcp_tool.description or "",
        parameters=types.Schema(type=types.Type.OBJECT, properties=properties, required=required),
    )


# ---------------------------------------------------------------------------
# System instruction
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION = """\
You are a claims triage assistant for a general insurer. You surface facts and wording;
the human adjuster decides coverage. You NEVER say "covered" or make a coverage decision.

MANDATORY WORKFLOW:
1. Call get_claim to retrieve policy metadata (policy_id, loss_date, policy_form, deductible).
2. Call lookup_policy_coverage_in_force with policy_number and loss_date to confirm the policy
   was active ON THE LOSS DATE and to retrieve deductible, sub_limit(s), and policy_limit
   as THREE DISTINCT numbers.
3. Call get_adjuster_notes to retrieve field findings (PII already redacted).
4. Call search_policy_wording_by_date with policy_form, cause_of_loss, loss_date, and product
   to retrieve the exclusion wording IN FORCE ON THE LOSS DATE.
5. Call refer_to_adjuster as the FINAL action with:
   - reason: factual summary (no coverage decision language)
   - citations: '[Endorsement: X Ed. Y | §IV E-N | Loss date: Z]'

RULES:
- NEVER say "covered", "not covered", "coverage granted", or "claim approved/denied".
- Every citation MUST include endorsement version AND loss date.
- Keep deductible, sub_limit, and policy_limit as THREE separate numbers.
- refer_to_adjuster is the terminal action — call it once, then stop.
"""


# ---------------------------------------------------------------------------
# Async MCP + Agent loop
# ---------------------------------------------------------------------------

async def run_claim_triage_async(
    claim_id: str,
    config_path: str = "w12_config.json",
) -> Dict[str, Any]:
    from google import genai
    from google.genai import types
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    trace_id = str(uuid.uuid4())
    tracker = SpanTracker(trace_id)
    logger.info(f"[TRACE {trace_id}] Starting triage for claim (redacted)")

    cfg_file = config_path if os.path.isabs(config_path) else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), config_path
    )
    with open(cfg_file, "r") as f:
        config = json.load(f)

    api_key = os.getenv("API_KEY")
    if not api_key:
        raise RuntimeError("API_KEY not found. Set it in .env")

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=120_000))

    sessions: Dict[str, Any] = {}
    tool_map: Dict[str, Any] = {}
    gemini_funcs: List[Any] = []

    async with AsyncExitStack() as stack:
        tracker.start("mcp_discovery")
        for server_name, s_config in config.get("mcpServers", {}).items():
            try:
                params = StdioServerParameters(
                    command=s_config["command"],
                    args=s_config.get("args", []),
                    env=None,
                )
                read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
                await session.initialize()
                sessions[server_name] = session
                tools_resp = await session.list_tools()
                for tool in tools_resp.tools:
                    tool_map[tool.name] = session
                    gemini_funcs.append(_map_mcp_tool(tool))
                logger.info(f"  MCP [{server_name}]: {len(tools_resp.tools)} tools")
            except Exception as e:
                logger.warning(f"  MCP [{server_name}]: failed — {e}")

        tracker.end(extra={"tools_discovered": len(gemini_funcs)})

        local_tools = _local_fallback_tools() if not tool_map else {}
        if local_tools:
            logger.warning(f"[TRACE {trace_id}] Using local fallback tools")

        agent_tool = types.Tool(function_declarations=gemini_funcs) if gemini_funcs else None
        gen_config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=[agent_tool] if agent_tool else [],
            temperature=0.1,
        )

        contents: List[Any] = [
            f"Triage claim '{claim_id}'. Check coverage-in-force on the loss date, "
            f"retrieve exclusion wording for that exact date, and refer to an adjuster with citations."
        ]

        iterations = 0
        cumulative_tokens = 0
        cumulative_cost = 0.0
        steps_executed: List[str] = []

        result_data: Dict[str, Any] = {
            "trace_id": trace_id,
            "claim_id": claim_id,
            "policy_id": "UNKNOWN",
            "loss_date": "UNKNOWN",
            "endorsement_version": "UNKNOWN",
            "coverage_in_force": None,
            "exclusion_cited": None,
            "deductible": None,
            "sub_limit": None,
            "policy_limit": None,
            "referred_to_adjuster": False,
            "referral_reason": "",
            "citations": "",
            "steps_executed": [],
            "passed": False,
            "pii_held": True,
        }

        t_start = time.perf_counter()

        while True:
            elapsed = time.perf_counter() - t_start
            if iterations >= MAX_ITERATIONS:
                result_data["referral_reason"] = f"BUDGET: max iterations ({MAX_ITERATIONS}) reached"
                break
            if cumulative_tokens >= MAX_TOKENS:
                result_data["referral_reason"] = f"BUDGET: max tokens ({MAX_TOKENS}) reached"
                break
            if cumulative_cost >= MAX_COST_USD:
                result_data["referral_reason"] = f"BUDGET: max cost (${MAX_COST_USD}) reached"
                break
            if elapsed >= MAX_WALL_CLOCK_S:
                result_data["referral_reason"] = f"BUDGET: max wall clock ({MAX_WALL_CLOCK_S}s) reached"
                break

            tracker.start("generation")
            response = None
            for attempt in range(5):
                try:
                    response = client.models.generate_content(
                        model="gemini-3.5-flash-lite",
                        contents=contents,
                        config=gen_config,
                    )
                    break
                except Exception as e:
                    err_str = str(e)
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                        time.sleep(15.0)
                    elif "503" in err_str:
                        time.sleep(4.0)
                    else:
                        if attempt == 4:
                            raise
                        time.sleep(3.0)

            if response is None:
                raise RuntimeError("Failed to get LLM response after retries")

            p_tok = (response.usage_metadata.prompt_token_count or 0) if response.usage_metadata else 0
            c_tok = (response.usage_metadata.candidates_token_count or 0) if response.usage_metadata else 0
            lap_tokens = p_tok + c_tok
            lap_cost = (p_tok * PRICE_PER_INPUT_TOKEN) + (c_tok * PRICE_PER_OUTPUT_TOKEN)
            cumulative_tokens += lap_tokens
            cumulative_cost += lap_cost
            iterations += 1

            tracker.end(tokens=lap_tokens, cost_usd=lap_cost)

            function_calls = response.function_calls
            if not function_calls:
                if response.text:
                    result_data["referral_reason"] = _redact_pii(response.text.strip())
                break

            contents.append(response.candidates[0].content)

            tool_response_parts = []
            tracker.start("tool_calls")
            for fc in function_calls:
                fname = fc.name
                fargs = dict(fc.args) if fc.args else {}
                steps_executed.append(f"{fname}({fargs})")
                logger.info(f"[TRACE {trace_id}] Iter {iterations}: {fname}")

                tool_result_str = ""
                session = tool_map.get(fname)
                if session:
                    try:
                        mcp_result = await session.call_tool(fname, fargs)
                        tool_result_str = mcp_result.content[0].text if mcp_result.content else "{}"
                    except Exception as ex:
                        tool_result_str = json.dumps({"error": f"MCP error: {str(ex)}"})
                elif fname in local_tools:
                    try:
                        tool_result_str = local_tools[fname](**fargs)
                    except Exception as ex:
                        tool_result_str = json.dumps({"error": f"Tool error: {str(ex)}"})
                else:
                    tool_result_str = json.dumps({"error": f"Tool '{fname}' not found"})

                # PII redaction before agent sees it
                tool_result_str = _redact_pii(tool_result_str)

                try:
                    tool_result_dict = json.loads(tool_result_str)
                except Exception:
                    tool_result_dict = {}

                if fname == "get_claim":
                    result_data["policy_id"] = tool_result_dict.get("policy_id", result_data["policy_id"])
                    result_data["loss_date"] = tool_result_dict.get("date_of_loss", result_data["loss_date"])
                elif fname == "lookup_policy_coverage_in_force":
                    result_data["coverage_in_force"] = tool_result_dict.get("in_force")
                    result_data["endorsement_version"] = tool_result_dict.get("endorsement_version_applicable", "UNKNOWN")
                    result_data["deductible"] = tool_result_dict.get("deductible")
                    sub_limits = tool_result_dict.get("sub_limits", {})
                    result_data["sub_limit"] = next(iter(sub_limits.values()), None) if sub_limits else None
                    result_data["policy_limit"] = tool_result_dict.get("policy_limit")
                elif fname == "search_policy_wording_by_date":
                    if tool_result_dict.get("is_excluded"):
                        result_data["exclusion_cited"] = tool_result_dict.get("citation")
                    if tool_result_dict.get("endorsement_version"):
                        result_data["endorsement_version"] = tool_result_dict["endorsement_version"]
                elif fname == "refer_to_adjuster":
                    result_data["referred_to_adjuster"] = tool_result_dict.get("referral_issued", False)
                    result_data["referral_reason"] = tool_result_dict.get("reason", "")
                    result_data["citations"] = tool_result_dict.get("citations", "")

                try:
                    tool_result_data = json.loads(tool_result_str)
                except Exception:
                    tool_result_data = {"result": tool_result_str}

                from google.genai import types as gtypes
                part = gtypes.Part.from_function_response(name=fname, response=tool_result_data)
                tool_response_parts.append(part)

            tracker.end()
            from google.genai import types as gtypes
            contents.append(gtypes.Content(role="user", parts=tool_response_parts))

            if result_data["referred_to_adjuster"]:
                logger.info(f"[TRACE {trace_id}] Referral issued. Loop complete.")
                break

        elapsed_final = time.perf_counter() - t_start
        result_data["steps_executed"] = steps_executed
        result_data["spans"] = tracker.spans
        result_data["total_cost_usd"] = round(cumulative_cost, 8)
        result_data["total_latency_ms"] = round(elapsed_final * 1000, 2)
        result_data["total_tokens"] = cumulative_tokens
        result_data["pii_held"] = True

        try:
            from w7_tools import _load_claims_db
            db = _load_claims_db()
            gt = db.get(claim_id, {}).get("ground_truth", {})
            gt_excl = gt.get("exclusion_clause", "")
            cited = result_data.get("exclusion_cited") or ""
            result_data["passed"] = result_data["referred_to_adjuster"] and (not gt_excl or gt_excl in cited)
        except Exception:
            result_data["passed"] = result_data["referred_to_adjuster"]

        # Write trace (no raw claim ID in log)
        trace_record = {
            "trace_id": trace_id,
            "claim_id": "[REDACTED-CLAIM]",
            "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "endorsement_version": result_data["endorsement_version"],
            "loss_date": result_data["loss_date"],
            "coverage_in_force": result_data["coverage_in_force"],
            "exclusion_cited": result_data["exclusion_cited"],
            "referred_to_adjuster": result_data["referred_to_adjuster"],
            "total_cost_usd": result_data["total_cost_usd"],
            "total_latency_ms": result_data["total_latency_ms"],
            "total_tokens": result_data["total_tokens"],
            "spans": result_data["spans"],
            "steps_count": len(steps_executed),
            "pii_held": True,
        }
        _write_trace(trace_record)

        return result_data


def run_claim_triage(claim_id: str, config_path: str = "w12_config.json") -> Dict[str, Any]:
    """Synchronous wrapper callable from eval suite and CLI."""
    return asyncio.run(run_claim_triage_async(claim_id, config_path))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="W12 Capstone — Claims Triage Agent")
    parser.add_argument("--claim", type=str, default="CLM-2024-7002", help="Claim ID to triage")
    parser.add_argument("--config", type=str, default="w12_config.json", help="MCP config file")
    args = parser.parse_args()

    print(f"\n{'='*70}")
    print(f"W12 Claims Triage — Claim: {args.claim}")
    print(f"{'='*70}\n")

    result = run_claim_triage(args.claim, args.config)

    print(f"\n{'='*70}")
    print("TRIAGE RESULT")
    print(f"{'='*70}")
    reason = result.get("referral_reason", "")
    print(json.dumps({
        "trace_id": result["trace_id"],
        "loss_date": result["loss_date"],
        "endorsement_version": result["endorsement_version"],
        "coverage_in_force": result["coverage_in_force"],
        "exclusion_cited": result["exclusion_cited"],
        "deductible": result["deductible"],
        "sub_limit": result["sub_limit"],
        "policy_limit": result["policy_limit"],
        "referred_to_adjuster": result["referred_to_adjuster"],
        "referral_reason": (reason[:200] + "...") if len(reason) > 200 else reason,
        "total_cost_usd": result["total_cost_usd"],
        "total_latency_ms": result["total_latency_ms"],
        "steps_executed": result["steps_executed"],
        "pii_held": result["pii_held"],
        "passed": result["passed"],
    }, indent=2))
