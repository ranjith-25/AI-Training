1. Who wrote it: The third-party claims platform team wrote and operates this MCP server, meaning we do not control its dependencies or security posture.
2. What it reaches: It directly queries the production claims database, returning full claim data and highly sensitive adjuster notes.
3. What it logs: It processes raw claim queries and likely logs every requested claim ID and returned note, duplicating our PII exposure into their logs.
4. Stolen token impact: If the agent's MCP connection string or token is stolen, an attacker gains unrestricted read access to exfiltrate all adjuster notes company-wide.
5. Ship decision: DO NOT SHIP directly; it must be placed behind a scoped gateway that enforces access control per-claim rather than granting the agent a wildcard read token.
