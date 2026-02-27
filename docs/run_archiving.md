# Run Archiving

## Overview

Each execution run should be treated as immutable. Canonical artifacts are written under:

`outputs/runs/<RUN_ID>/`

`RUN_ID` comes from `RUN_ID` env when provided (CI sets it), otherwise it is generated at runtime.

## Canonical vs View Paths

Canonical (immutable):
- `outputs/runs/<RUN_ID>/reports/...`
- `outputs/runs/<RUN_ID>/broker/...`
- `outputs/runs/<RUN_ID>/ledger/...`
- `outputs/runs/<RUN_ID>/snapshots/...`
- `outputs/runs/<RUN_ID>/meta.json`
- `outputs/runs/<RUN_ID>/manifest.json`
- `outputs/runs/<RUN_ID>/checksums.sha256`

Convenience views (mutable pointers/copies):
- `outputs/latest.json` points to the most recent run
- `outputs/daily/...` contains view files for legacy consumers
- `outputs/ledger/...` may contain pointer views

## Reproduce a Run

1. Download the artifact for the run (`outputs/runs/<RUN_ID>/`).
2. Read `meta.json` for mode/report date/git SHA.
3. Validate integrity with `checksums.sha256`.
4. Open artifacts directly from `reports/`, `broker/`, `ledger/`, and `snapshots/`.

## CI Behavior

Workflows set a unique `RUN_ID` per attempt and upload `outputs/runs/<RUN_ID>/` as the canonical artifact so reruns do not overwrite previous runs.
