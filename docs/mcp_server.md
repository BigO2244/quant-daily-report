# Caerus MCP Server (Phase 7+)

> **Companion docs:**
> [`operator/research_mcp_operator_guide.md`](operator/research_mcp_operator_guide.md)
> for the one-command question gateway;
> [`architecture/research_mcp_current_state_2026-05-29.md`](architecture/research_mcp_current_state_2026-05-29.md)
> for the canonical capability matrix and maturity assessment;
> [`architecture/caerus_research_mcp_architecture.md`](architecture/caerus_research_mcp_architecture.md)
> for the aspirational design intent. Where this server doc and the
> aspirational architecture doc disagree, **this doc and the current-state
> assessment are authoritative for the implemented surface**.

## Scope

The Caerus MCP Server is the local read-only operator intelligence layer
over existing Caerus artifacts. Phase 6 answered "what artifacts exist";
Phase 7 added compact artifact-backed interpretation of operational
status, strategy leadership, promotion readiness, and anomalies; the
2026-05-29 capability extension added a deterministic capability router
plus dedicated tools for execution-timing analysis and shadow-strategy
comparison. It is not a trading, scheduling, or automation surface.

The server can index existing artifacts into a caller-specified disposable
SQLite registry DB. Direct artifact inspection commands read `outputs/` in
place and do not build, repair, or mutate runtime artifacts. Phase 7 historical
awareness is computed from existing artifact paths; it does not introduce a
database, vector store, hidden cache, or external infrastructure.

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

As of 2026-05-29 the server exposes **20 tools**. Grouped by purpose:

**Registry indexing / inspection (9):**

- `build_caerus_registry`
- `latest_runs`
- `run_health`
- `integrity_findings`
- `governance_open`
- `research_packet_status`
- `registry_summary`
- `query_registry`
- `lineage`

**Operator intelligence (6):**

- `daily_operator_brief`
- `artifact_status`
- `operator_daily_summary`
- `artifact_drilldown`
- `morning_cio_brief`
- `anomaly_report`

**Research-question tools (4):**

- `promotion_readiness`
- `execution_timing_by_vix_regime` — stratify timing-replay opportunities by VIX regime
- `execution_timing_summary` — aggregate per-offset timing summary with retain/earlier/insufficient recommendation
- `shadow_comparison` — per-strategy NAV / cumret / excess vs SPY / turnover panel + pairwise overlap

**Capability planner (1):**

- `answer_research_question` — deterministic capability-based router that classifies a natural-language question against the `CAPABILITY_REGISTRY`, checks required artifacts, and dispatches to the appropriate tool. Returns one of: `OK`, `NEEDS_DATA` (capability matched but artifacts missing), `NEEDS_CAPABILITY` (capability matched but no tool wired yet), `UNSUPPORTED_INTENT` (no capability matched; closest suggestions returned). See [`architecture/research_mcp_current_state_2026-05-29.md`](architecture/research_mcp_current_state_2026-05-29.md) for the full capability matrix.

Every tool returns a JSON object with `status`, `db_path`, `queried_at`,
`warnings`, and `findings`. The research-question tools and the planner
additionally carry `intent`, `routed_to`, and tool-specific payload
fields documented inline in `research_registry/mcp_server/schemas.py`.

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

`morning_cio_brief` composes the daily artifact summary with strategy
leadership, portfolio/exposure hints, regime context, anomaly count, and an
operator attention section. It never infers execution completion unless
`execution_results.json` exists.

`promotion_readiness` reviews recent shadow artifacts and reports current
leader, observation count, available excess/drawdown/turnover/concentration
metrics, confidence, and a conservative recommendation. It does not recommend
capital deployment without a sufficient artifact-backed observation window.
When FR-028 Phase C sidecars are present, it consumes
`promotion_readiness.json` and `longitudinal_metrics.json` from the latest
dated shadow folder; otherwise it falls back to conservative Phase 7 evidence
extraction and reports insufficient evidence where metrics are absent.

`anomaly_report` surfaces stale research packets, missing artifact families,
limited continuity, stale shadow/research surfaces, missing execution integrity,
missing execution results, and empty run folders with deterministic severity
labels: `INFO`, `WARNING`, or `NEEDS_OPERATOR`.

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

Run Phase 7 operator intelligence commands:

```bash
.venv/bin/python3 scripts/research_registry_cli.py morning-brief --outputs-root outputs --json
.venv/bin/python3 scripts/research_registry_cli.py morning-brief --outputs-root outputs --markdown
.venv/bin/python3 scripts/research_registry_cli.py promotion-readiness --outputs-root outputs --json
.venv/bin/python3 scripts/research_registry_cli.py promotion-readiness --outputs-root outputs --markdown
.venv/bin/python3 scripts/research_registry_cli.py anomaly-report --outputs-root outputs --json
.venv/bin/python3 scripts/research_registry_cli.py anomaly-report --outputs-root outputs --markdown
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
ssh caerus-vm
cd ~/quant-daily-report
git status --short
./scripts/deploy.sh
source venv/bin/activate
python3 scripts/research_registry_mcp_server.py --help
python3 scripts/research_registry_mcp_server.py smoke \
  --db /tmp/caerus-mcp-server-smoke.db \
  --runs-root outputs/runs \
  --packets-root outputs/research_packets \
  --docs-root docs/governance \
  --limit 5
```

Do not install cron or deploy services as part of Phase 7.

Direct SSH by external IP is ephemeral and non-authoritative. If a direct
connection is unavoidable, resolve the current IP first:

```bash
gcloud compute instances describe alpha-stack-scheduler --zone us-central1-a --format="get(networkInterfaces[0].accessConfigs[0].natIP)"
```

## Security Boundary

The server imports only the research registry and artifact ingestion/query
layers. It does not import broker modules, execution runners, cron wrappers, or
dashboard publishers. It does not read or print environment secrets. The server
rejects registry DB paths under repo `outputs/` so generated SQLite indexes do
not contaminate runtime artifact directories.

Direct artifact inspection uses compact probes and selected status metadata. It
does not print raw artifact payloads and is designed to avoid exposing large
broker, portfolio, or environment-derived payloads through the MCP interface.
Phase 7 intelligence is artifact-backed only: missing evidence is reported as
unavailable or `NEEDS_OPERATOR`; it does not fill gaps with model calls,
external APIs, broker calls, or strategy assumptions.

## Known Limitations

- Artifact freshness is evaluated from latest persisted paths, not from broker
  or scheduler APIs.
- `broker_recon_present` confirms that broker/reconciliation artifacts exist; it
  does not call Alpaca or verify live account state.
- Missing artifacts are reported as `NEEDS_OPERATOR`; Phase 7 intentionally does
  not self-heal or trigger workflows.
- Direct artifact status does not require a registry DB, so its `db_path` field
  is informational for MCP response consistency.
- Strategy leadership and promotion readiness depend on fields exposed by
  shadow comparison artifacts. When those metrics are absent, the commands
  report insufficient evidence.
- FR-028 Phase C sidecars improve leadership/readiness evidence, but MCP remains
  a read-only consumer and does not create, repair, promote, allocate, or
  execute.
- Promotion readiness is advisory operator intelligence only. It is not an
  allocator, promotion gate, scheduler, or execution trigger.

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

## Phase 7 Deployment Notes

Phase 7 deploys by the same git-only fast-forward flow. Validation should run
the existing MCP/FR-031 tests plus `morning-brief`, `promotion-readiness`, and
`anomaly-report` in both JSON and Markdown against the VM `outputs/` tree.

Rollback is source-only via `git revert <phase-7-commit>` followed by a VM
fast-forward pull. No generated registry DB or runtime output rollback is
required because Phase 7 does not write source artifacts or production outputs.
