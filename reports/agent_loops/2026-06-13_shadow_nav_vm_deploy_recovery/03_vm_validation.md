# VM Validation

## Code Validation

VM virtualenv:

- `~/.venvs/quant-daily-report/bin/python3`

Commands:

```bash
~/.venvs/quant-daily-report/bin/python3 -m pytest \
  Tests/test_shadow_scorecard_refresh_continuity.py \
  Tests/test_shadow_tracking.py \
  Tests/test_shadow_cio_report.py \
  Tests/test_shadow_scorecard_health.py \
  Tests/test_shadow_backfill_artifacts.py -q
```

Result:

- `46 passed, 22 warnings in 120.28s`

```bash
~/.venvs/quant-daily-report/bin/python3 -m py_compile \
  research/shadow_tracking/run.py \
  scripts/refresh_shadow_scorecard_artifacts.py \
  scripts/send_shadow_cio_report.py \
  scripts/check_shadow_scorecard_health.py
```

Result:

- Passed.

## Report Dry Run

Command:

```bash
~/.venvs/quant-daily-report/bin/python3 scripts/send_shadow_cio_report.py --dry-run
```

Result:

- Data through: `2026-06-12`
- Data health: `Fresh but corrupt`
- Reason: `SHADOW_NAV_CHAIN_RESET`
- Offending date: `2026-06-09`
- Rankings: suppressed
- Daily / seven-day / YTD / excess windows: suppressed as `N/A`
- Promotion signal: suppressed with `SHADOW_PERFORMANCE_SUPPRESSED`

## Strict Health

Command:

```bash
~/.venvs/quant-daily-report/bin/python3 scripts/check_shadow_scorecard_health.py \
  --baseline-date 2026-06-05 \
  --baseline-valid-days 0 \
  --expected-date 2026-06-12 \
  --diagnostics-dir /tmp/caerus_shadow_health_check \
  --strict
```

Result:

- Exit code: `1`
- Status: `FAIL`
- `performance_integrity.status`: `CORRUPT`
- `performance_integrity.reason_code`: `SHADOW_NAV_CHAIN_RESET`
- Detail: `simultaneous implausible NAV ratio on 2026-06-09: caerus_polaris, caerus_orion, caerus_lyra, spy_benchmark`

Diagnostics were written to `/tmp/caerus_shadow_health_check`, not active repo artifact paths.
