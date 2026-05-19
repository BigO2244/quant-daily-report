# Orchestration Dependency Audit

Date: 2026-05-19

Scope: execution, hydration, shadow evaluation, analytics, learning diagnostics,
scoreboard/reporting, and email delivery. This audit is documentation for a
surgical sequencing fix only; it does not change trading logic, portfolio
construction, model ranking, broker execution, or reconciliation semantics.

## Execution Graph

```text
1:00 ET  cron_overnight.sh
  -> outputs/overnight_signals/<date>.json

6:30 ET  cron_research.sh
  -> quant_research_agent/outputs/digest_<date>.json

7:00 ET  cron_precompute.sh
  -> daily_quant_report.py
  -> outputs/precompute/<date>/*
  -> scripts/run_shadow_candidates_daily.sh
     -> research.shadow_tracking.run
     -> outputs/shadow_candidates/<date>/*
     -> outputs/shadow_candidates/latest/*
     -> outputs/workflow/<date>/shadow*.json

9:35 ET  cron_execute.sh
  -> scripts/run_precomputed_alpaca_execution.py
  -> Alpaca paper orders
  -> outputs/workflow/<date>/execution.json
  -> execution run artifacts

10:00 ET cron_confirm.sh
  -> scripts.send_trading_confirmation_email
  -> core.shadow_scoreboard.build_shadow_scoreboard
  -> confirmation email

18:30 ET hydrate_price_cache_only --refresh-shadow-artifacts --strict
  -> research.flow_detection.data.ensure_price_panel
  -> outputs/research/flow_detection_v1/price_panel.parquet
  -> scripts.refresh_shadow_scorecard_artifacts
  -> outputs/shadow_candidates/<date>/*
  -> outputs/shadow_candidates/latest/*
  -> outputs/price_hydration/<date>/status.json

21:00 ET send_shadow_cio_report
  -> outputs/shadow_candidates/latest/*
  -> outputs/shadow_candidates/performance/shadow_nav_series.csv
  -> Shadow CIO email
```

## Timing Sequence

```text
Precompute shadow lane at 07:00 asks for the current report date.
Current-date close prices are normally unavailable before market close.
research.shadow_tracking.run can therefore write:
  comparison.status = NO_DATA
  comparison.reason_code = PRICE_CACHE_STALE
  shadow_performance.data_status = NO_DATA
  shadow_evaluation.*.data_status = NO_DATA
  feedback_loop_summary learning readiness = LOW or partial

Confirmation at 10:00 can read those same-day diagnostic artifacts.

Post-close hydration at 18:30 refreshes the price cache and regenerates core
shadow scorecard artifacts.

Before this fix, the artifact-only refresh regenerated:
  strategy payloads
  delta.json
  summary.json
  comparison.json
  shadow_performance.json
  shadow_evaluation.json
  comparison.md
  shadow_nav_series.csv

But it did not regenerate:
  feedback_loop_summary.json
  per-strategy feedback artifacts
  feedback_loop_rolling_index.*

It also did not publish feedback_loop_summary.json to latest.
```

## Producer / Consumer Map

| Artifact | Producer | Consumers | Readiness assumption |
|---|---|---|---|
| `outputs/precompute/<date>/planned_execution_payload.json` | `daily_quant_report.py` | `cron_execute.sh`, execution runner | Precompute bundle validation passes. |
| `outputs/workflow/<date>/execution.json` | execution runner | `cron_confirm.sh` | Execution pointer is terminal before confirmation email. |
| `outputs/research/flow_detection_v1/price_panel.parquet` | `hydrate_price_cache_only.py`, shadow runner when downloads allowed | shadow generation, scorecard refresh, analytics | Max cache date must cover requested completed trading day. |
| `outputs/price_hydration/<date>/status.json` | `hydrate_price_cache_only.py` | learning report, health checks, CIO report diagnostics | Written after hydration and optional shadow refresh complete. |
| `outputs/shadow_candidates/<date>/comparison.json` | `research.shadow_tracking.run`, `refresh_shadow_scorecard_artifacts.py` | scoreboard, learning report, health checks | `status=OK` means current strategy comparison exists. |
| `outputs/shadow_candidates/<date>/shadow_performance.json` | shadow runner / refresh | evaluation, feedback loop, NAV series | `data_status=OK` means returns and target weights were available. |
| `outputs/shadow_candidates/<date>/shadow_evaluation.json` | shadow runner / refresh | confirmation scoreboard, CIO report, learning report | Strategies should not be `NO_DATA` for post-close hydrated dates. |
| `outputs/shadow_candidates/<date>/feedback_loop_summary.json` | `write_feedback_loop_artifacts` | confirmation scoreboard, learning report | Must match the same generation pass as evaluation/comparison. |
| `outputs/shadow_candidates/latest/*` | shadow wrapper / refresh | CIO report, operator tooling | Latest publication should be a coherent artifact set. |

## Readiness Contracts

- Execution readiness is broker/precompute authoritative and is independent of
  shadow analytics.
- Shadow analytics readiness requires price/signal data for the requested trade
  date.
- `PRICE_CACHE_STALE` is valid before the completed trading day can be hydrated,
  but should not survive after `outputs/price_hydration/<date>/status.json`
  reports `status=OK` with `max_cache_date >= date`.
- Learning readiness must be computed from the same shadow artifact generation
  pass as `comparison.json` and `shadow_evaluation.json`.
- Latest shadow publication should not expose a mixed generation set.

## Freshness Validation Logic

- `research.shadow_tracking.run.classify_no_data_reason` emits
  `PRICE_CACHE_STALE` when the signal frame max date is earlier than the
  requested trade date and downloads are not allowed.
- `comparison.json` emits `status=NO_DATA` and `reason_code=PRICE_CACHE_STALE`
  when same-day shadow generation runs against a stale panel.
- `shadow_performance.json` carries `data_status=NO_DATA` and
  `data_reason=PRICE_CACHE_STALE`.
- `shadow_evaluation.json` propagates the same `data_status` and `data_reason`
  into each strategy.
- `core.shadow_scoreboard` marks artifact status `DEGRADED` when evaluation
  `data_status` is not `OK`.
- `core.portfolio_learning_report` treats `PRICE_CACHE_STALE` through hydration
  diagnostics. If hydration is OK and current cache covers the date, it
  suppresses the stale freshness warning but still depends on the actual shadow
  and feedback artifacts for learning fields.
- `core.feedback_loop_artifacts._learning_readiness` returns `LOW` when three or
  more required diagnostic dimensions are missing or unavailable.

## Identified Race Conditions

1. Same-day precompute shadow generation can publish a valid diagnostic
   `NO_DATA/PRICE_CACHE_STALE` artifact before market close. This is expected,
   but it is unsafe for downstream consumers to treat that artifact as final for
   post-close analytics.

2. Post-close artifact-only refresh updated core shadow artifacts but skipped
   feedback-loop diagnostics. This created a mixed artifact set where
   `comparison.json` and `shadow_evaluation.json` could be fresh while
   `feedback_loop_summary.json` remained stale, missing, or still described the
   earlier `NO_DATA` run.

3. Latest publication omitted `feedback_loop_summary.json`, so latest-based
   operator tooling could read fresh evaluation artifacts without matching
   learning diagnostics.

4. Partial artifact states are observable during refresh because files are
   written sequentially. The highest-impact observed gap was not file write
   atomicity itself; it was that one required downstream diagnostic artifact was
   never regenerated in the artifact-only refresh path.

## Exact Root Causes

- `PRICE_CACHE_STALE`: emitted by
  `research.shadow_tracking.run.classify_no_data_reason` when current trade-date
  prices are unavailable in the local signal frame and downloads are disabled.

- `NO_DATA`: emitted by `research.shadow_tracking.run.main` in the no-data branch
  and propagated into comparison, performance, and evaluation artifacts.

- `DEGRADED`: emitted by `core.shadow_scoreboard` when a strategy evaluation
  has `data_status != OK`, or when required scoreboard artifacts are missing.

- `LOW` learning readiness: emitted by
  `core.feedback_loop_artifacts._learning_readiness` when decision metadata,
  attribution, stability history, or regime diagnostics are unavailable. The
  stale `feedback_loop_summary.json` left this state behind after core shadow
  artifacts were refreshed.

- Unavailable turnover/concentration: these are read from `comparison.json`
  strategy payloads. In a `NO_DATA` comparison, strategies are empty, so
  turnover and top-3 concentration render unavailable.

## Remediation Options

1. **Regenerate feedback diagnostics inside post-close artifact refresh**
   - Blast radius: low
   - Effect: makes comparison, evaluation, feedback, rolling index, and latest
     publication coherent after hydration.
   - Selected.

2. Add a centralized shadow readiness manifest and make all consumers read it
   - Blast radius: medium
   - Effect: stronger contract, broader consumer changes.
   - Deferred.

3. Make shadow artifact writes fully atomic through staging directories
   - Blast radius: medium
   - Effect: removes transient partial-read windows.
   - Deferred; useful later with a readiness manifest.

4. Change morning shadow generation to target the previous completed trading day
   - Blast radius: medium to high
   - Effect: changes dated artifact semantics.
   - Rejected for this hot fix.

## Selected Fix

The post-close artifact-only shadow refresh now regenerates feedback-loop
artifacts from the same hydrated panel and publishes `feedback_loop_summary.json`
with the rest of the latest shadow scorecard bundle.

This preserves execution, reconciliation, model ranking, portfolio construction,
and broker behavior. It only hardens the analytics/reporting artifact sequence.
