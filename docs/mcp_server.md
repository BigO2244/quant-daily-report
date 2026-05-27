# Caerus MCP Server Phase 6A

## Scope

Caerus MCP Server Phase 6A wraps the deployed MCP-lite research registry in a
local read-only, stdio JSON-RPC compatible tool server. It is an operator
research interface over registry artifacts. It is not a trading, scheduling, or
automation surface.

The server indexes existing artifacts into a caller-specified disposable SQLite
registry DB. Source artifacts are read only.

## Non-Goals

- No broker access.
- No order submission.
- No execution workflow triggering.
- No cron or scheduler changes.
- No autonomous agents.
- No dashboard deployment.
- No strategy, model, FR-028, or FR-029 semantic changes.
- No secret or environment inspection.

## Launch

Use a DB under `/tmp` or `/private/tmp` for local and VM operator sessions.

```bash
.venv/bin/python3 scripts/research_registry_mcp_server.py stdio \
  --db /private/tmp/caerus-research-registry.db \
  --runs-root outputs/runs \
  --packets-root outputs/research_packets \
  --docs-root docs/governance \
  --limit 10
```

Print tool definitions:

```bash
.venv/bin/python3 scripts/research_registry_mcp_server.py tools
```

Local smoke:

```bash
.venv/bin/python3 scripts/research_registry_mcp_server.py smoke \
  --db /private/tmp/caerus-mcp-server-smoke.db \
  --runs-root outputs/runs \
  --packets-root outputs/research_packets \
  --docs-root docs/governance \
  --limit 5
```

## Tools

- `build_caerus_registry`
- `latest_runs`
- `run_health`
- `integrity_findings`
- `governance_open`
- `research_packet_status`
- `registry_summary`
- `query_registry`
- `lineage`

Every tool returns a JSON object with `status`, `db_path`, `queried_at`,
`warnings`, and `findings`.

## Example JSON-RPC Calls

List tools:

```json
{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
```

Build a disposable registry:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "build_caerus_registry",
    "arguments": {
      "db_path": "/private/tmp/caerus-research-registry.db",
      "runs_root": "outputs/runs",
      "packets_root": "outputs/research_packets",
      "docs_root": "docs/governance",
      "limit": 10
    }
  }
}
```

Query current governance state:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "governance_open",
    "arguments": {
      "db_path": "/private/tmp/caerus-research-registry.db"
    }
  }
}
```

Debug raw governance duplicates:

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "governance_open",
    "arguments": {
      "db_path": "/private/tmp/caerus-research-registry.db",
      "show_duplicates": true
    }
  }
}
```

## Recommended VM Usage

After review, deploy by the normal deterministic source flow:

```bash
git push origin main
ssh brettolson@34.61.147.38
cd ~/quant-daily-report
git status --short
git pull --ff-only origin main
source venv/bin/activate
python3 scripts/research_registry_mcp_server.py --help
python3 scripts/research_registry_mcp_server.py smoke \
  --db /tmp/caerus-mcp-server-smoke.db \
  --runs-root outputs/runs \
  --packets-root outputs/research_packets \
  --docs-root docs/governance \
  --limit 5
```

Do not install cron or deploy services as part of Phase 6A.

## Security Boundary

The server imports only the research registry and artifact ingestion/query
layers. It does not import broker modules, execution runners, cron wrappers, or
dashboard publishers. It does not read or print environment secrets. The server
rejects registry DB paths under repo `outputs/` so generated SQLite indexes do
not contaminate runtime artifact directories.

## Rollback

Rollback is source-only:

```bash
git revert <docs-commit>
git revert <tests-commit>
git revert <server-core-commit>
```

Disposable SQLite DBs under `/tmp` or `/private/tmp` can be ignored or removed
outside the source rollback.
