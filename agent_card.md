# AgentCard: Claims Orchestrator

- **Skills:** Decompose complex claim questions, delegate to sub-agents (exclusions check, adjuster note summary), and synthesise a final coverage decision.
- **Input Mode:** Natural language claim question + claim metadata (JSON/YAML).
- **Output Mode:** Final coverage determination (covered/denied) + synthetic explanation.
- **Auth:** OAuth2 bearer tokens passed down to worker agents for downstream API system access.

## Failed Case Analysis (A2A Task Lifecycle)
The failed case (where the worker returned a 500) should have ended paused at `input-required`, awaiting the adjuster's confirmation on how to proceed, instead of proceeding to hallucinate a covered status.

**What A2A buys over a plain REST call:**
A2A allows the orchestrator to dynamically negotiate retries, decompose nuanced natural-language errors, and gracefully degrade to partial answers rather than failing completely upon an HTTP 500 like a brittle REST call would.
