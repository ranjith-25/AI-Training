# Race: Multi-Agent vs Single Agent

**Eval Cases (10 Week-6 Cases):** CASE-01, CASE-02, CASE-03, CASE-04, CASE-05, CASE-06, CASE-07, CASE-08, CASE-09, CASE-10

| Metric | Single Agent | Orchestrator (Multi-Agent) |
|---|---|---|
| Pass Rate | 90% | 90% |
| p50 Latency | 2.1s | 5.8s |
| p99 Latency | 3.2s | 9.5s |
| Total Tokens | 15,200 | 46,360 |
| Cost per claim | $0.05 | $0.15 |

**Multiplier Line:** Context re-send multiplier: 3.1x (46360 / 15200 tokens). 
Dominant hand-off: orchestrator -> exclusions worker resend, 41% of all tokens.
