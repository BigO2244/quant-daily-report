# Quant Dashboard

## Purpose

The **Quant Daily Executive Dashboard** provides an executive-facing, fast-scan summary of:

- run success/failure
- daily portfolio performance
- benchmark comparison and excess return
- risk posture and breaker state
- trading activity and notable portfolio changes
- exceptions and operational health checks

The page is static-file friendly and designed to open locally in a browser without a frontend framework or web server.

### Executive-Safe Run Selection

The dashboard defaults to the **latest successful completed run** for executive metrics, not necessarily the most recent attempted run. This ensures that:

- Portfolio value, trading activity, and performance charts reflect known-good data
- Failed or halted runs don't make the dashboard appear broken
- The latest attempted run status is still surfaced prominently if it differs from the selected run

Run selection logic:

1. **Discover all runs** in `outputs/runs/`
2. **Classify each run** as complete/successful (has health/integrity snapshots or quant_report) vs failed/halted (sparse artifacts or preflight_failure)
3. **Select latest successful completed run** for executive metrics
4. **Record latest attempted run** separately for status alerts
5. **Fall back** to latest attempted run if no successful run exists

Run selection metadata is included in `run_meta.latest_attempted_run` and `run_meta.selected_governed_run`.

### Warning Density Reduction

The builder filters warnings to focus on executive-level concerns:

- **Included:** preflight failures, suspicious broker values, missing critical position/ledger artifacts
- **Suppressed:** low-level artifact parsing errors, derived metric estimation notes, missing optional data

Executive warnings are in `builder_notes.warnings`; full diagnostics in `builder_notes.all_warnings`.

## File Structure

- `web/dashboard/quant_daily_executive.html`
- `web/dashboard/quant_daily_executive.css`
- `web/dashboard/quant_daily_executive.js`
- `web/dashboard/sample_dashboard_data.json`
- `web/dashboard/dashboard_data.json` (generated)
- `scripts/build_quant_dashboard.py`

## Data Schema

The dashboard consumes a normalized JSON model with these top-level keys:

```json
{
  "run_meta": {},
  "kpis": {},
  "perf_summary": {},
  "series": {
    "nav": [],
    "benchmark": [],
    "daily_returns": [],
    "excess_returns": [],
    "drawdown": [],
    "chart_metadata": {
      "nav_chart": {"title": "...", "x_axis_label": "Date", "y_axis_label": "Value", "note": "Indexed to 100 at inception"},
      "daily_returns_chart": {"title": "...", "x_axis_label": "Date", "y_axis_label": "Return (%)", "baseline": 0.0},
      "excess_returns_chart": {"title": "...", "x_axis_label": "Date", "y_axis_label": "Excess Return (%)", "baseline": 0.0}
    }
  },
  "risk": {},
  "activity": {},
  "governed_snapshot": {},
  "broker_snapshot": {},
  "data_freshness": {},
  "top_changes": [],
  "exceptions": [],
  "operating_checks": [],
  "sources": [],
  "builder_notes": {}
}
```

### Important field conventions

- returns and exposure fields are decimal fractions (`0.01` = `1%`)
- currency fields are numeric USD values
- chart series rows use:
  - `{"date": "YYYY-MM-DD", "value": <number>}`
- status fields use pass/warning/fail style semantics

### Chart Improvements

Charts now include:

- **Axis labels:** X-axis (Date) and Y-axis (Indexed Value, Return %, Excess Return %)
- **0-baseline marker:** Visible 0% line on daily returns and excess returns charts
- **Indexed NAV note:** NAV chart is indexed to Base=100 at inception
- **Chart metadata:** Structured metadata in `series.chart_metadata` for rendering context

### Activity Context

The `activity` section now includes:

- `source_run_id` — which run the activity data comes from
- `source_report_date` — report date of the source run
- `note` — human-readable context (e.g., "Activity from selected governed run" or "Activity from latest run")

This clarifies whether trade counts reflect the latest attempted run or an earlier successful run.

### Buying Power Handling

The `broker_snapshot.buying_power` field may be `null` if the broker source doesn't provide it.

- `buying_power_note` — included when `buying_power` is `null`, explaining why (e.g., "Not provided by broker source" or "Field not present in broker payload")
- Dashboard UI displays the note instead of generic "Data unavailable" when the field is explicitly unavailable from the broker

## Build JSON from Artifacts

Run from repo root (`/Users/brettolson/Documents/Caerus/quant-daily-report-main`):

```bash
cd /Users/brettolson/Documents/Caerus/quant-daily-report-main
python3 scripts/research/build_quant_dashboard.py
```

Default output path:

- `web/dashboard/dashboard_data.json`

Optional output path:

```bash
python3 scripts/research/build_quant_dashboard.py --output web/dashboard/dashboard_data.json
```

`web/dashboard/dashboard_data.json` is generated at build time and is not committed by default.

The builder is resilient to missing artifacts. It does not fail hard for absent files; it emits warnings and fills the JSON with null/default placeholders, plus explicit exception/check statuses.

Broker snapshot ingestion order is deterministic and self-healing:

1. Reuse the newest governed broker snapshot artifact if available.
2. Reuse governed reconciliation artifact account values when available.
3. Optionally fetch a live broker account snapshot during build when explicitly enabled.
4. Derive broker-state fields from governed artifacts only as a last resort.
5. Degrade gracefully to `missing` if none of the above succeeds.

Optional live fetch is disabled by default and only runs when:

- `DASHBOARD_FETCH_BROKER_SNAPSHOT=1`
- Alpaca credentials are present in environment.

You can also force a live broker fetch without setting the env toggle:

```bash
python3 scripts/research/build_quant_dashboard.py --fetch-live-broker
```

When a broker snapshot is derived or fetched live, the builder persists a normalized artifact at:

- `outputs/broker/broker_snapshot_latest.json`

## Open Dashboard Locally

Recommended method (most reliable): run a local static server and use real-data mode with `?data=dashboard_data.json`.

### Sample mode (works immediately)

Open in browser:

- `file:///Users/brettolson/Documents/Caerus/quant-daily-report-main/web/dashboard/quant_daily_executive.html`

This defaults to `sample_dashboard_data.json`.
If the browser blocks local `fetch` for `file://` URLs, the page automatically falls back to an embedded sample payload so the dashboard still renders.

### Real artifact mode

1. Build dashboard JSON:

```bash
python3 scripts/research/build_quant_dashboard.py
```

2. Start a local static server from repo root:

```bash
cd /Users/brettolson/Documents/Caerus/quant-daily-report-main
python3 -m http.server 8765
```

3. Open with query parameter (uses generated real JSON at `web/dashboard/dashboard_data.json`):

- `http://localhost:8765/web/dashboard/quant_daily_executive.html?data=dashboard_data.json`
- `http://localhost:8765/web/dashboard/quant_daily_executive.html?data=dashboard_data.json&refresh=60`

## VM Dashboard Access

On the scheduler VM, the dashboard is served by nginx on port 80 at:

- `/dashboard/`
- `/dashboardDEV/`

Both routes use nginx basic auth with the credential file
`/etc/nginx/.htpasswd_dashboard`.

If the login is lost or needs rotation, run
`scripts/reset_dashboard_auth.sh` on the VM checkout. The script prompts for a
new username and password, updates the htpasswd file in place, and reloads
nginx. It does not print or commit secrets.

How `?data=` is resolved:

- `?data=dashboard_data.json` loads `web/dashboard/dashboard_data.json` relative to the dashboard HTML path.
- This is the canonical and most reliable query-string form for local serve mode.
- `?data=sample_dashboard_data.json` forces sample JSON mode while still using the local server.
- `?refresh=60` reloads the static JSON every 60 seconds using `cache: no-store`, which is the simplest way to get near-live updates when the VM rebuilds `dashboard_data.json` on a schedule.

## Source Artifacts Used

The builder attempts to read the following canonical sources:

- `outputs/latest.json`
- `outputs/alpha_assessment/canonical_performance.csv`
- `outputs/perf/nav_timeseries.csv`
- `outputs/perf/benchmark_close_history.csv`
- `outputs/perf/vix_close_history.csv`
- `outputs/ledger/trades.csv`
- `outputs/execution_email/<report_date>.json` (or latest usable file)
- `outputs/broker/broker_snapshot_latest.json` (canonical broker snapshot artifact)
- `outputs/runs/<run_id>/snapshots/health_<report_date>.json`
- `outputs/runs/<run_id>/snapshots/integrity_<report_date>.json`
- `outputs/paper_state/canonical_positions.json` (preferred)
- `canonical-model-snapshot/canonical_positions.json` (legacy fallback)

The generated JSON includes a `sources` section listing presence/usage status.

## Governed vs Broker Snapshot

The dashboard intentionally shows two account views:

- **Governed run snapshot**: values sourced from immutable run/performance artifacts.
- **Latest broker snapshot**: latest broker-state view from artifact reuse, derivation, or optional live fetch.

This keeps rendering fully artifact-driven in the browser while still surfacing recent broker reality.

## Freshness and Alignment

`data_freshness.broker_vs_run_alignment` is conservative and uses these classes:

- `aligned`: broker snapshot date matches run report date.
- `mismatch`: broker and run dates differ materially.
- `stale`: broker snapshot is older than threshold (`stale_threshold_hours`).
- `missing`: broker snapshot could not be resolved.

When alignment is not `aligned`, the dashboard adds warning-level operating checks/exceptions and an explicit snapshot alignment note.

## Trust Level and Sanity Guards

`broker_snapshot.trust_level` indicates confidence in broker values:

- `authoritative`: direct broker snapshot artifact or optional live fetch.
- `reconciled`: broker account values from governed reconciliation artifacts.
- `derived`: estimated from governed artifacts only.
- `missing`: no broker snapshot resolved.

When `trust_level=derived`, sanity guardrails run before display. If derived broker equity is implausibly far from governed run value (ratio outside conservative bounds), the dashboard flags it as suspicious, de-emphasizes the broker headline value, and surfaces warning diagnostics.

## Missing Data Behavior

If data is absent or malformed:

- cards show `Data unavailable` or `Not generated`
- charts render inline empty-state messages
- exceptions panel surfaces missing critical sources
- operating checks mark warning/fail where applicable
- builder diagnostics are included in `builder_notes`
- broker snapshot fields degrade to `missing` without breaking render

## Degraded Mode (Halted or Incomplete Runs)

When a run is halted or artifacts are incomplete, the dashboard remains renderable and enters a degraded state:

- unavailable metrics show a clean `Data unavailable` display
- exceptions/checks explain why values are degraded (missing inputs, insufficient history, halted execution)
- charts render available series only, with polished empty-state text where data is absent
- builder outputs `builder_notes.degraded_metrics` with explicit metric-level reasons

## Known Limitations

- no run selector/date selector yet (current behavior uses latest context)
- no drill-through links to raw artifacts in UI
- largest position weight may be unavailable when not present in source payloads
- turnover limit is currently a dashboard-configured default when no explicit artifact value exists

## v2 Enhancements

- operator view toggle with richer diagnostics
- date/run selector
- direct artifact links from exceptions/check rows
- richer reconciliation detail pane
- execution decision drilldown (intent vs fills)
