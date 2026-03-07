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
    "drawdown": []
  },
  "risk": {},
  "activity": {},
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

## Build JSON from Artifacts

Run from repo root (`/Users/brettolson/Documents/Caerus/quant-daily-report-main`):

```bash
cd /Users/brettolson/Documents/Caerus/quant-daily-report-main
python3 scripts/build_quant_dashboard.py
```

Default output path:

- `web/dashboard/dashboard_data.json`

Optional output path:

```bash
python3 scripts/build_quant_dashboard.py --output web/dashboard/dashboard_data.json
```

`web/dashboard/dashboard_data.json` is generated at build time and is not committed by default.

The builder is resilient to missing artifacts. It does not fail hard for absent files; it emits warnings and fills the JSON with null/default placeholders, plus explicit exception/check statuses.

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
python3 scripts/build_quant_dashboard.py
```

2. Start a local static server from repo root:

```bash
cd /Users/brettolson/Documents/Caerus/quant-daily-report-main
python3 -m http.server 8765
```

3. Open with query parameter (uses generated real JSON at `web/dashboard/dashboard_data.json`):

- `http://localhost:8765/web/dashboard/quant_daily_executive.html?data=dashboard_data.json`

How `?data=` is resolved:

- `?data=dashboard_data.json` loads `web/dashboard/dashboard_data.json` relative to the dashboard HTML path.
- This is the canonical and most reliable query-string form for local serve mode.
- `?data=sample_dashboard_data.json` forces sample JSON mode while still using the local server.

## Source Artifacts Used

The builder attempts to read the following canonical sources:

- `outputs/latest.json`
- `outputs/alpha_assessment/canonical_performance.csv`
- `outputs/perf/nav_timeseries.csv`
- `outputs/perf/benchmark_close_history.csv`
- `outputs/perf/vix_close_history.csv`
- `outputs/ledger/trades.csv`
- `outputs/execution_email/<report_date>.json` (or latest usable file)
- `outputs/runs/<run_id>/snapshots/health_<report_date>.json`
- `outputs/runs/<run_id>/snapshots/integrity_<report_date>.json`
- `outputs/paper_state/canonical_positions.json` (preferred)
- `canonical-model-snapshot/canonical_positions.json` (legacy fallback)

The generated JSON includes a `sources` section listing presence/usage status.

## Missing Data Behavior

If data is absent or malformed:

- cards show `Data unavailable` or `Not generated`
- charts render inline empty-state messages
- exceptions panel surfaces missing critical sources
- operating checks mark warning/fail where applicable
- builder diagnostics are included in `builder_notes`

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
