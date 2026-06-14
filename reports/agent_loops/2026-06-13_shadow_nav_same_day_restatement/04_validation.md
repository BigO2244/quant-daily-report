# 04 Validation

## Local Validation

Before merge to `main`:

```bash
./.venv/bin/python3 -m pytest \
  Tests/test_shadow_nav_same_day_restatement.py \
  Tests/test_shadow_scorecard_refresh_continuity.py \
  Tests/test_shadow_tracking.py \
  Tests/test_shadow_cio_report.py \
  Tests/test_shadow_scorecard_health.py \
  Tests/test_shadow_backfill_artifacts.py \
  Tests/test_shadow_promotion_readiness.py \
  Tests/test_research_registry_shadow_comparison.py \
  Tests/test_research_registry_strategy_behavior_differentiation.py -q
```

Result: `98 passed`

```bash
./.venv/bin/python3 -m pytest \
  Tests/test_research_registry_mcp_server.py \
  Tests/test_sleeve_manifest.py \
  Tests/test_strategy_registry.py \
  Tests/test_research_registry_promotion_readiness.py -q
```

Result: `67 passed`

```bash
./.venv/bin/python3 -m py_compile \
  scripts/restate_shadow_nav_same_day.py \
  scripts/refresh_shadow_scorecard_artifacts.py \
  scripts/send_shadow_cio_report.py \
  scripts/check_shadow_scorecard_health.py
git diff --check
```

Result: passed

## VM Validation

After deploy of `0884a2a`:

```bash
~/.venvs/quant-daily-report/bin/python3 -m pytest \
  Tests/test_shadow_nav_same_day_restatement.py \
  Tests/test_shadow_scorecard_refresh_continuity.py \
  Tests/test_shadow_cio_report.py \
  Tests/test_shadow_scorecard_health.py -q
```

Result before replacement: `24 passed`

After the non-trading skip patch:

```bash
~/.venvs/quant-daily-report/bin/python3 -m pytest \
  Tests/test_shadow_nav_same_day_restatement.py -q
```

Result: `4 passed`

After active replacement:

```bash
~/.venvs/quant-daily-report/bin/python3 scripts/check_shadow_scorecard_health.py \
  --baseline-date 2026-06-05 \
  --baseline-valid-days 0 \
  --expected-date 2026-06-12 \
  --diagnostics-dir /tmp/caerus_shadow_health_after_same_day_restatement \
  --strict
```

Result: `OK`

Final health:

- `scorecard_data_health`: `Fresh`
- `performance_integrity.status`: `OK`
- `performance_integrity.reason_code`: `null`
- `nav_series_latest_date`: `2026-06-12`
- `data_through_date`: `2026-06-12`
- `post_baseline_issues`: `[]`

Post-replacement Shadow tests:

```bash
~/.venvs/quant-daily-report/bin/python3 -m pytest \
  Tests/test_shadow_nav_same_day_restatement.py \
  Tests/test_shadow_scorecard_refresh_continuity.py \
  Tests/test_shadow_cio_report.py \
  Tests/test_shadow_scorecard_health.py \
  Tests/test_shadow_backfill_artifacts.py -q
```

Result: `31 passed`

Post-replacement registry/MCP/differentiation tests:

```bash
~/.venvs/quant-daily-report/bin/python3 -m pytest \
  Tests/test_shadow_promotion_readiness.py \
  Tests/test_research_registry_shadow_comparison.py \
  Tests/test_research_registry_strategy_behavior_differentiation.py \
  Tests/test_research_registry_mcp_server.py -q
```

Result: `75 passed`

Cron was read-only inspected and not modified.
