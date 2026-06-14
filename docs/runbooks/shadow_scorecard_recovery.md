# Shadow Scorecard Recovery Runbook

## Purpose

Use this runbook when the Shadow daily model scorecard is stale, valid days stop
advancing, or the Shadow NAV chain cannot move forward after a hydration or
artifact outage.

This is an operator procedure only. It must not change trading logic, broker
submission, active strategy selection, cron timing, or portfolio construction.

## Known Recovery Pattern

The 2026-05-12 incident followed this chain:

1. VM cron referenced a missing module:
   `python3 -m scripts.hydrate_price_cache_only`.
2. Price cache hydration stopped advancing.
3. The scorecard reported `PRICE_CACHE_STALE`.
4. After hydration was fixed, the canonical cache was current through
   `2026-05-11`.
5. The remaining blocker was Shadow NAV continuity after `2026-04-27`.
6. Controlled artifact-only backfill restored Shadow artifacts through
   `2026-05-11`.
7. Valid days advanced to `16`, `shadow_refresh.status` returned to `OK`, and
   the scorecard became Fresh.

Relevant recovery commits:

- `90c14b1 fix(shadow): add controlled artifact backfill`
- `a8f15cd fix(shadow): preserve signal lookback during backfill`
- `1f527b2 fix(shadow): seed backfill anchor from nav series`

## Canonical Operational NAV Convention

Owner decision on 2026-06-13:

- The operational Shadow scorecard must use dated same-day close-to-close
  observations as the canonical methodology:
  `dated_same_day_close_to_close_v1`.
- Legacy full-history/backtest-style `shadow_nav_series.csv` lineage is
  superseded for operational scorecards.
- Recovery must not fabricate pre-observation history. It may restate the
  operational NAV series from dated `shadow_performance.json` only after
  daily returns are independently validated against dated strategy weights and
  point-in-time price inputs.

Use the governed restatement utility for this path:

```bash
python3 scripts/restate_shadow_nav_same_day.py \
  --staging-dir outputs/recovery_staging/shadow_nav_same_day_<UTC_TIMESTAMP>
```

Replace active artifacts only after staging validation passes and expected
current artifact hashes match:

```bash
python3 scripts/restate_shadow_nav_same_day.py \
  --replace-active \
  --expected-existing-nav-sha256 <current-shadow_nav_series-sha256> \
  --expected-existing-summary-sha256 <current-shadow_summary-sha256>
```

The utility writes:

- staged `performance/shadow_nav_series.csv`;
- staged `performance/shadow_summary.json`;
- `daily_return_validation.json`;
- `recovery_manifest.json`;
- a pre-replacement backup under `outputs/recovery_backups/` when
  `--replace-active` is used;
- active `performance/shadow_nav_restatement_manifest.json` after replacement.

Do not use full historical backtest output as the operational scorecard repair
unless a future owner decision supersedes this convention.

## Post-Recovery Guardrail Recap

After the 2026-05-12 backfill restored the scorecard, the follow-up work added
read-only guardrails so the same failure pattern is easier to detect before it
affects promotion or operator decisions.

Edits made:

- Added `scripts/check_shadow_scorecard_health.py` to read existing scorecard,
  NAV, and hydration artifacts and write compact diagnostics under
  `outputs/diagnostics/`.
- Added baseline-aware health checks for the recovered state:
  `baseline-date=2026-05-11` and `baseline-valid-days=16`.
- Added strict-mode failures for stale scorecards, failed
  `shadow_refresh.status`, stale cache dates, data-through mismatches, NAV
  regression, valid-day regression after a new expected trading day, and new
  post-baseline `PRICE_CACHE_STALE`, `NO_PRIOR`, `NO_DATA`,
  `BROKEN_CHAIN`, or missing Shadow directory conditions.
- Added `Tests/test_shadow_scorecard_health.py` coverage for fresh, stale,
  regression, equal-baseline, failed-refresh, and artifact-writing cases.
- Added `scripts/audit_shadow_promotion_readiness.py` as a read-only governance
  audit for Polaris, Orion, and Lyra. It keeps Polaris as baseline and prevents
  Orion or Lyra from being treated as promotion-eligible from recovered
  historical artifacts alone.
- Added `Tests/test_shadow_promotion_readiness.py` coverage for minimum valid
  days, stale scorecards, failed Shadow refresh, watchlist classification,
  Polaris baseline handling, and forward-clean-day requirements.
- Added `scripts/validate_cron_commands.py` to catch repo-owned cron references
  to missing Python modules or scripts before deployment.
- Added `Tests/test_cron_command_validation.py` coverage for valid and missing
  `python -m` modules, shell script references, and ignored comments/env lines.
- Added this recovery runbook to preserve the hydration, NAV continuity, and
  artifact-only backfill operating pattern.

These tools are diagnostic only. They do not change cron timing, submit orders,
promote strategies, alter portfolio construction, or mutate historical artifacts
unless the explicit backfill command is run by an operator.

## Symptoms

Investigate with this runbook when any of these appear:

- Daily scorecard says `PRICE_CACHE_STALE`.
- `shadow_refresh.status` is `FAILED`.
- `shadow_refresh.reason` is `NO_PRIOR`, `NO_DATA`, `BROKEN_CHAIN`, or similar.
- Scorecard status is Stale.
- Data-through date is behind the latest source date.
- `7-Day through` or `YTD through` is older than the current completed trade date.
- `shadow_nav_series.csv` latest date is behind latest `shadow_evaluation.json`.
- Polaris, Orion, or Lyra valid-day counts stop advancing.
- Latest Shadow directory exists but performance remains unavailable.

## Primary Artifacts

- Hydration status:
  `outputs/price_hydration/YYYY-MM-DD/status.json`
- Canonical price cache:
  `outputs/research/flow_detection_v1/price_panel.parquet`
- Daily Shadow artifacts:
  `outputs/shadow_candidates/YYYY-MM-DD/`
- NAV series:
  `outputs/shadow_candidates/performance/shadow_nav_series.csv`
- Diagnostics:
  `outputs/diagnostics/`
- Backfill backups:
  `outputs/recovery_backups/`

## Diagnostic Commands

Run from the VM repo root:

```bash
cd ~/quant-daily-report
git status
git log -1 --oneline
```

Check hydration status:

```bash
cat outputs/price_hydration/YYYY-MM-DD/status.json
```

Check the canonical cache:

```bash
ls -lh outputs/research/flow_detection_v1/price_panel.parquet
stat outputs/research/flow_detection_v1/price_panel.parquet
```

List recent hydration and Shadow artifacts:

```bash
find outputs/price_hydration -maxdepth 2 -type f -name status.json | sort | tail -10
find outputs/shadow_candidates -maxdepth 2 -name shadow_evaluation.json | sort | tail -20
find outputs/shadow_candidates -maxdepth 2 -name shadow_performance.json | sort | tail -20
find outputs/shadow_candidates -maxdepth 2 -name comparison.json | sort | tail -20
```

Inspect NAV series coverage:

```bash
python3 - <<'PY'
from pathlib import Path
import csv

path = Path("outputs/shadow_candidates/performance/shadow_nav_series.csv")
rows = list(csv.DictReader(path.open()))
print("rows", len(rows))
print("first", rows[:3])
print("last", rows[-10:])
PY
```

Validate cron-owned command references:

```bash
python3 scripts/validate_cron_commands.py scripts/crontab.txt
```

Run the read-only scorecard health check:

```bash
python3 scripts/check_shadow_scorecard_health.py \
  --baseline-date 2026-05-11 \
  --baseline-valid-days 16 \
  --strict
```

Run the read-only promotion readiness audit:

```bash
python3 scripts/audit_shadow_promotion_readiness.py
```

## Decision Tree

1. Is the scorecard stale because of `PRICE_CACHE_STALE`?
   - If yes, inspect hydration status and cron logs first.
   - Do not attempt NAV backfill until the canonical price cache is current.

2. Is hydration cron running?
   - Check `logs/price_hydration.cron.log`.
   - Check the latest `outputs/price_hydration/YYYY-MM-DD/status.json`.
   - Confirm `status` is `OK` and `max_cache_date` is the latest completed
     trading day.

3. Does cron reference an existing module or script?
   - Run `python3 scripts/validate_cron_commands.py scripts/crontab.txt`.
   - Stop if any repo-owned module or script check fails.

4. Is the canonical price cache current?
   - Confirm `canonical_cache_path` points to
     `outputs/research/flow_detection_v1/price_panel.parquet`.
   - Confirm `max_cache_date` is not behind the expected completed trade date.

5. Is the NAV series stale?
   - Compare latest `shadow_evaluation.json` date with the latest date in
     `shadow_nav_series.csv`.
   - If the evaluation date is newer but NAV is stale, inspect
     `shadow_refresh.status` and `shadow_refresh.reason`.

6. Is there a continuity gap?
   - Identify the last valid NAV anchor date.
   - Identify the first missing, stale, `NO_PRIOR`, `NO_DATA`, or
     `BROKEN_CHAIN` date after the anchor.
   - Do not skip over a broken date and mark later dates valid.

7. Is backfill safe?
   - Safe only if price data exists for each date, prior continuity can be
     reconstructed chronologically, and price reads are bounded to the artifact
     date.
   - If look-ahead bounds cannot be proven, stop.

8. Should the observation window restart instead?
   - Restart forward observation when continuity cannot be reconstructed without
     fabrication or when required source artifacts are missing.
   - Do not promote Orion or Lyra from restored historical artifacts alone.

## Safe Recovery Protocol

Use artifact-only recovery. Do not submit trades or call broker/order code.

Required controls:

- Run dry-run first.
- Create backups before overwriting historical artifacts.
- Write a recovery manifest before mutation.
- Process dates chronologically only.
- Use the canonical price cache only.
- Bound price reads to `<= artifact date`.
- Preserve signal lookback behavior.
- Seed from the last valid NAV anchor only when the anchor exists.
- Stop on the first unsafe continuity failure in strict mode.
- Preserve generated diagnostics and backup manifests.

## Backfill Commands

Dry-run:

```bash
python3 -m scripts.backfill_shadow_artifacts \
  --start-date 2026-04-28 \
  --end-date 2026-05-11 \
  --anchor-date 2026-04-27 \
  --artifact-only \
  --strict \
  --dry-run
```

Actual run, only after dry-run passes:

```bash
python3 -m scripts.backfill_shadow_artifacts \
  --start-date 2026-04-28 \
  --end-date 2026-05-11 \
  --anchor-date 2026-04-27 \
  --artifact-only \
  --strict
```

Expected recovery outputs:

- `outputs/diagnostics/shadow_backfill_plan_YYYY-MM-DD.csv`
- `outputs/diagnostics/shadow_backfill_plan_YYYY-MM-DD.md`
- `outputs/diagnostics/shadow_backfill_dry_run_YYYY-MM-DD.md`
- `outputs/diagnostics/shadow_backfill_result_YYYY-MM-DD.csv`
- `outputs/diagnostics/shadow_backfill_result_YYYY-MM-DD.md`
- `outputs/recovery_backups/<RUN_ID>/manifest.json`

## Post-Recovery Validation

Run targeted validation only:

```bash
python3 -m py_compile \
  core/price_hydration.py \
  scripts/hydrate_price_cache_only.py \
  scripts/refresh_shadow_scorecard_artifacts.py \
  scripts/send_shadow_cio_report.py \
  scripts/backfill_shadow_artifacts.py \
  scripts/check_shadow_scorecard_health.py \
  scripts/audit_shadow_promotion_readiness.py \
  scripts/validate_cron_commands.py
```

```bash
.venv/bin/pytest -q \
  Tests/test_price_cache_only_hydrator.py \
  Tests/test_price_hydration_wrapper.py \
  Tests/test_shadow_backfill_artifacts.py \
  Tests/test_shadow_scorecard_health.py \
  Tests/test_shadow_promotion_readiness.py \
  Tests/test_cron_command_validation.py
```

Confirm:

- Scorecard status is Fresh.
- `shadow_refresh.status` is `OK`.
- `shadow_refresh.reason` is absent or `None`.
- `max_cache_date` equals the latest completed trade date.
- Data-through, `7-Day through`, and `YTD through` dates agree.
- NAV series latest date is current.
- Polaris, Orion, and Lyra valid days are advancing.
- No new `PRICE_CACHE_STALE`, `NO_PRIOR`, `NO_DATA`, or `BROKEN_CHAIN` appears.
- The next forward scheduled run increments valid days when a new completed
  trading day is available.

## What Not To Do

- Do not fabricate NAV continuity.
- Do not manually edit scorecard outputs without a manifest.
- Do not overwrite historical artifacts without backups.
- Do not use current or future prices to backfill prior dates.
- Do not skip a broken date and mark later dates valid.
- Do not rerun broad trading workflows for scorecard recovery.
- Do not call broker/order submission code.
- Do not change portfolio construction.
- Do not reinstall or retime cron unless cron itself is the confirmed defect and
  the change is explicitly approved.
- Do not promote Orion or Lyra because recovery succeeded.
- Do not confuse the 7-day performance window with full valid observation count.

## Operator Recommendation

After recovery, keep Polaris as the paper baseline. Orion and Lyra may resume
promotion-window counting only after clean forward observation days accumulate
after the recovery date. Use the promotion readiness audit for governance
classification; recovery alone is not promotion evidence.
