# 01 Input Inventory

## Scope

This pass audited the VM Shadow artifacts for the recovery range anchored at `2026-06-05` with candidate affected trading dates through `2026-06-12`.

## VM State

- VM: `alpha-stack-scheduler`
- Repository path: `/home/brettolson/quant-daily-report`
- Branch: `main`
- VM HEAD: `75b51c6223921c69b4625b0e31a3abe2a32733f5`
- VM `origin/main`: `75b51c6223921c69b4625b0e31a3abe2a32733f5`
- Tracked VM working tree: clean
- Cron: inspected read-only; no changes made

## Preserved Evidence

- Incident backup: `outputs/recovery_backups/shadow_nav_incident_20260613T181114Z/`
- Backup manifest: `outputs/recovery_backups/shadow_nav_incident_20260613T181114Z/evidence_manifest.json`
- Backup manifest SHA-256: `fe69dddbb3845066ba65fe118a1a9eaf7622974a763a1f0b4d4f98f377b1805c`
- Preserved `shadow_nav_series.csv` SHA-256: `5d59c987c07198c590a287e189a1224f2f783ba8bd55d5fdcb39d1de9e84dc1f`
- Preserved `shadow_summary.json` SHA-256: `86a793550a73db81c8dedfe9c96618f4a08a788a150f1354c6542b5a6a65d67a`
- Active `shadow_nav_series.csv` SHA-256 at audit start: `5d59c987c07198c590a287e189a1224f2f783ba8bd55d5fdcb39d1de9e84dc1f`
- Active `shadow_summary.json` SHA-256 at audit start: `86a793550a73db81c8dedfe9c96618f4a08a788a150f1354c6542b5a6a65d67a`

## Source Inputs

- Price panel: `outputs/research/flow_detection_v1/price_panel.parquet`
- Price panel SHA-256: `a81559032d48a8c504784461afab3f0cff1b74d0d3e7488974693cf03452cb97`
- Price date range: `2014-01-02` through `2026-06-12`
- Price rows: `605364`
- Price tickers: `200`
- Strategy registry: `config/research/strategy_registry.json`
- Strategy registry SHA-256: `8a30c7ae153d4b87c358e641a4bd61fa7aa9b027d8350ff34a2b040f13db078c`
- Dated Shadow directories: `40`

## Date Inventory

| Date | Trading Day | Dated Artifacts | NAV CSV Row | Prior CSV Row | Performance Status | Previous Trade Date |
|---|---:|---:|---:|---|---|---|
| 2026-06-02 | yes | yes | yes | 2026-06-01 | OK | 2026-06-01 |
| 2026-06-03 | yes | yes | yes | 2026-06-02 | OK | 2026-06-02 |
| 2026-06-04 | yes | yes | yes | 2026-06-03 | OK | 2026-06-03 |
| 2026-06-05 | yes | yes | yes | 2026-06-04 | OK | 2026-06-04 |
| 2026-06-08 | yes | yes | no | 2026-06-05 | OK | 2026-06-05 |
| 2026-06-09 | yes | yes | yes | 2026-06-05 | OK | 2026-06-08 |
| 2026-06-10 | yes | yes | yes | 2026-06-09 | OK | 2026-06-09 |
| 2026-06-11 | yes | yes | yes | 2026-06-10 | OK | 2026-06-10 |
| 2026-06-12 | yes | yes | yes | 2026-06-11 | OK | 2026-06-11 |

## AIOPS

- Spec: `specs/2026-06-13_shadow_nav_historical_recovery.md`
- Run ID: `20260613_161841_75b51c6`
- Status: `FAILED`
- Exit code: `5`
- Stage blocker: dispatch failed with exit code `1`
- Preserved artifacts: `reports/ai_runs/20260613_161841_75b51c6/`

Because AIOPS dispatch failed, the equivalent deterministic audit was performed manually and recorded in this report set.
