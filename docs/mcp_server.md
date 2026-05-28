# Caerus MCP Server Phase 6

## Scope

Caerus MCP Server Phase 6 is the local read-only operator inspection layer over
existing Caerus artifacts. It wraps the deployed MCP-lite research registry in a
stdio JSON-RPC compatible tool server and adds direct artifact inspection for
operator status checks. It is not a trading, scheduling, or automation surface.

The server can index existing artifacts into a caller-specified disposable
SQLite registry DB. Direct artifact inspection commands read `outputs/` in
place and do not build, repair, or mutate runtime artifacts.

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
- `daily_operator_brief`
- `artifact_status`
- `operator_daily_summary`
- `artifact_drilldown`

Every tool returns a JSON object with `status`, `db_path`, `queried_at`,
`warnings`, and `findings`.

`artifact_status` is the direct read-only filesystem inspection surface. It
does not build or require a registry DB. It lists artifact family roots and
summarizes the latest precompute bundle, execution run, broker/confirmation
artifacts, shadow comparison/readiness artifacts, and research packet. Missing
artifact roots return `NEEDS_OPERATOR` style warnings instead of triggering any
repair workflow.

`operator_daily_summary` answers the operator morning/evening questions from
latest artifacts: did precompute run for the requested trade date, did execution
run, are broker/reconciliation artifacts present, did the shadow lane run, is
the research packet current, and what needs operator attention. It returns
`NEEDS_OPERATOR` when any required current-day surface is stale or missing.

`artifact_drilldown` returns compact file probes for latest artifact paths and
required files. It reports path, existence, size, status, selected dates, and
small status fields only. It does not dump raw JSON, markdown bodies, secrets,
positions, broker payloads, or large artifact contents.

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

Inspect current artifacts without rebuilding the registry:

```bash
.venv/bin/python3 scripts/research_registry_cli.py artifact-status --outputs-root outputs --json
.venv/bin/python3 scripts/research_registry_cli.py artifact-status --outputs-root outputs --markdown
```

Print the daily operator summary without rebuilding the registry:

```bash
.venv/bin/python3 scripts/research_registry_cli.py daily-summary --outputs-root outputs --json
.venv/bin/python3 scripts/research_registry_cli.py daily-summary --outputs-root outputs --markdown
```

Drill into latest artifact paths without dumping raw payloads:

```bash
.venv/bin/python3 scripts/research_registry_cli.py artifact-drilldown --outputs-root outputs --family all --markdown
.venv/bin/python3 scripts/research_registry_cli.py artifact-drilldown --outputs-root outputs --family precompute --json
```

Expected daily-summary shape:

```json
{
  "status": "OK",
  "trade_date": "2026-05-28",
  "summary": {
    "what_happened_today": {
      "precompute_ran": true,
      "execution_ran": true,
      "broker_recon_present": true,
      "shadow_ran": true,
      "research_packet_current": true
    },
    "operator_attention": []
  },
  "warnings": []
}
```

When required latest artifacts are absent or stale, `status` becomes
`NEEDS_OPERATOR` and `warnings` lists the missing or stale surface. The command
does not invoke precompute, execution, repair, broker confirmation, shadow
generation, or research ingestion.

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

Do not install cron or deploy services as part of Phase 6.

## Security Boundary

The server imports only the research registry and artifact ingestion/query
layers. It does not import broker modules, execution runners, cron wrappers, or
dashboard publishers. It does not read or print environment secrets. The server
rejects registry DB paths under repo `outputs/` so generated SQLite indexes do
not contaminate runtime artifact directories.

Direct artifact inspection uses compact probes and selected status metadata. It
does not print raw artifact payloads and is designed to avoid exposing large
broker, portfolio, or environment-derived payloads through the MCP interface.

## Known Limitations

- Artifact freshness is evaluated from latest persisted paths, not from broker
  or scheduler APIs.
- `broker_recon_present` confirms that broker/reconciliation artifacts exist; it
  does not call Alpaca or verify live account state.
- Missing artifacts are reported as `NEEDS_OPERATOR`; Phase 6 intentionally does
  not self-heal or trigger workflows.
- Direct artifact status does not require a registry DB, so its `db_path` field
  is informational for MCP response consistency.

## Rollback

Rollback is source-only:

```bash
git revert <docs-commit>
git revert <tests-commit>
git revert <server-core-commit>
```

Disposable SQLite DBs under `/tmp` or `/private/tmp` can be ignored or removed
outside the source rollback.

## Phase 6 Deployment Ledger

| Date | Commit | Local validation | VM validation | Observation |
|---|---|---|---|---|
| 2026-05-28 | `0ba7e1211cd768de18b5627327db2d3aafe35fd3`; completion fix `3f575fd1299b3ad2421343d83b3dc0545a5a8903` | `git diff --check`; compileall; py_compile; `Tests/test_research_registry_mcp_server.py` 16 passed; `Tests/test_research_registry_* Tests/test_execution_integrity.py` 61 passed; artifact-status JSON/Markdown; daily-summary JSON/Markdown | VM fast-forwarded from `227b579763b6dd2293bd6d20215dd1667d6b846c`; MCP server tests 16 passed; registry/integrity tests 61 passed; artifact-status Markdown; daily-summary Markdown/JSON | Production artifacts showed 2026-05-28 precompute, broker/recon, and shadow present; latest execution lacked `execution_results.json` / execution integrity and latest research packet was 2026-05-27, so daily-summary correctly returned `NEEDS_OPERATOR`. |
