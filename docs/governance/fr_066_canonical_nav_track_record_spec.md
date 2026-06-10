# FR-066 Canonical NAV Track Record Integrity Specification

Status: DEPLOYED_OBSERVING
Owner: Caerus Research Program
Last Updated: 2026-06-10
Governance Label: OPERATIONAL_TELEMETRY
Execution Impact: NON_EXECUTIONAL (no broker submission, order routing, cron
execution-phase, strategy, allocation, or promotion behavior changes)

## Purpose

Establish one canonical, continuously maintained, broker-authoritative daily
NAV and benchmark series for the live paper book, from inception
(2026-03-03) forward, with loud failure on any gap. Every downstream consumer
(dashboard, attribution, operational drag, model tournament, promotion
readiness, Argo) reads this single series instead of recomputing or sampling
its own.

This is the highest-priority operational FR in the program. Without it, no
strategy evidence is decision-grade, and the fund cannot answer the most basic
question a fund must answer: what did the book return, against what benchmark,
every day since inception.

## Problem Statement (verified evidence before FR-066 deployment, 2026-06-10)

1. Before deployment, the canonical NAV artifact `outputs/portfolio_history/nav.csv`
   covered only 2026-03-03 through 2026-04-08 (26 rows). The
   builder (`scripts/build_portfolio_history.py`) was last run for trade date
   2026-04-09 and was never scheduled.
2. The 2026-06-08 freshness audit (`portfolio_history_freshness.json`) reports
   `freshness_status: STALE`, reason `PORTFOLIO_HISTORY_STALE`, with downstream
   confidence LOW for attribution, operational drag, tournament, and promotion
   readiness.
3. The 2026-04-19 dashboard work (`fabcb0e`) made the dashboard
   broker-authoritative via the Alpaca portfolio-history endpoint. This fixed
   the *view* but persists no canonical local record. The view is rebuilt from
   the broker on each refresh and is only as durable as Alpaca's endpoint
   retention and our credentials.
4. The FR-058A audit found an Alpaca 401 silent freeze from 2026-05-20 to
   2026-06-04: the dashboard refresh exited 0 with a swallowed warning while
   broker telemetry was frozen for roughly two weeks. FR-059 (loud broker
   telemetry failure) is in progress and is a sibling of this FR, not a
   substitute: FR-059 makes the refresh fail loudly; FR-066 ensures a durable
   canonical record exists independent of the refresh.
5. Known broker equity points existed as isolated dashboard/snapshot evidence.
   No daily series connected these points, and no SPY-relative or beta-adjusted
   record existed anywhere in the system.

## Deployment Note (2026-06-10)

FR-066 was deployed to the VM after owner approval. The inception backfill dry-run
and write used the VM `.env` without printing credentials. After correcting Alpaca
1D portfolio-history timestamp alignment to the New York session date, the
canonical series is continuous from 2026-03-03 and the daily builder extended it
through 2026-06-10. The Apr 8 canonical Alpaca portfolio-history row is
`$9,751.97`; the older `$9,715.45` figure remains a historical baseline
discrepancy and is not source truth. SPY and beta-adjusted columns are populated
subject to rolling-window availability. The 7:15 PM ET builder/escalation cron is
installed on the VM and writes to `logs/portfolio_history.cron.log`.

Backfill reconciliation is clean against overlapping `nav.csv` rows. Broker
snapshot reconciliation remains a provenance caveat because snapshots are
point-in-time account captures and are not the same EOD portfolio-history source.

## Design

### 1. Scheduled canonical build (daily)

- Add a post-close phase to the VM schedule (weekdays, after the 6:30 PM ET
  price hydration; proposed 7:15 PM ET):
  `python3 scripts/build_portfolio_history.py --trade-date $(TZ=America/New_York date +%F)`
- The job appends one row per trading day to the canonical artifacts under
  `outputs/portfolio_history/` (nav, positions, transactions, attribution,
  summary).
- Cron addition is a scheduling change to a read-only builder, not to any
  execution phase. It must be validated by
  `scripts/validate_cron_commands.py` before deployment.

### 2. One-time backfill to inception

- Pull Alpaca `GET /v2/account/portfolio/history` (period covering
  2026-03-03 to present, timeframe 1D) and reconstruct the full daily equity
  series.
- Reconcile overlapping dates against the existing 26 rows of `nav.csv`
  (tolerance: 1 bp of equity) and against all persisted broker snapshots
  (2026-04-08, 2026-04-09, 2026-05-18 fixtures). Discrepancies are recorded,
  not silently overwritten.
- Backfill runs once, writes a manifest
  (`outputs/portfolio_history/backfill_manifest.json`) with source, request
  window, row counts, and reconciliation results, and is never re-run
  automatically.
- Priority note: Alpaca paper endpoint retention is not guaranteed. Every day
  of delay risks permanent loss of the March-June record. This backfill should
  be executed before any other FR-066 work.

### 3. Benchmark and scoreboard columns

Extend the canonical NAV row schema (additive columns only):

- `spy_close` (adjusted), `spy_return_1d`, `benchmark_nav` (indexed to
  inception)
- `excess_return_1d` = portfolio return minus SPY return
- `rolling_beta_60d` (vs SPY, minimum 30 observations, else null)
- `beta_adjusted_excess_1d` = portfolio return minus rolling_beta * SPY return
- Derived in `summary.json`: cumulative excess, information ratio over 63/126/
  252-day windows, max drawdown, current drawdown.

The program scoreboard metric is the information ratio of beta-adjusted
excess return versus SPY. CAGR is reported but never used as a promotion or
review headline.

### 4. Freshness gate with escalation (fail-loud)

- Every trading day must have a row. A gap greater than one trading day emits
  reason code `NAV_GAP` and sends an operator email through the existing
  email path (same channel as the Shadow CIO report).
- Two consecutive failed builds escalate to subject-line prefix `[CAERUS NAV
  BROKEN]`. Silence is never a valid state; this is the lesson of the
  2026-05-20 -> 2026-06-04 freeze.
- Integrates with FR-059 `live_status` reason codes (`alpaca_auth_failed`,
  `nav_artifact_stale`) rather than duplicating them.

### 5. Immutability and restatement rules

- Canonical artifacts are append-only. Any restatement of a historical row
  requires a logged entry in `outputs/portfolio_history/restatements.json`
  (date, old value, new value, reason, source artifact).
- A checksum manifest is updated on each append; the freshness audit verifies
  it.

### 6. Single-reader rule

- `portfolio_history_freshness.py`, operational drag (FR-055/058), the model
  tournament, promotion readiness, the dashboard history panel, and Argo all
  consume `outputs/portfolio_history/nav.csv` (or `summary.json`) as the only
  source of live NAV truth. The dashboard may continue to fetch Alpaca live
  intraday state for current-day panels, but historical series rendering moves
  to the canonical artifact.
- No consumer recomputes NAV from fills, positions, or its own broker pulls.

## Acceptance Criteria (pre-registered)

1. Continuous daily NAV rows from 2026-03-03 to current trade date, no gaps on
   trading days, reconciled to broker equity within 1 bp on snapshot dates.
2. `portfolio_history_freshness.json` reports `freshness_status: FRESH` for 10
   consecutive trading days after deployment.
3. SPY-relative and beta-adjusted columns present and non-null for >= 95% of
   rows (nulls only where the rolling window is not yet filled).
4. A forced failure test (revoked key in a staging run) produces the escalation
   email within one scheduled cycle.
5. Promotion-readiness and operational-drag confidence for portfolio history
   moves from LOW to at least MEDIUM.

## Rollback

Remove the cron line; revert the builder/schema commits; delete
`backfill_manifest.json` and added columns. Existing 26-row `nav.csv` rows are
never deleted. No execution, broker submission, allocation, strategy, or
promotion behavior is in scope, so rollback risk is limited to telemetry.

## Proposed Implementation File List

- `scripts/build_portfolio_history.py` (extend: benchmark columns, append-only
  guard, manifest)
- `scripts/backfill_portfolio_history.py` (new, one-time, dry-run default)
- `scripts/crontab.txt` (one added post-close line)
- `core/portfolio_history_escalation.py` (new, email escalation)
- `research/portfolio_history_freshness.py` (extend: checksum + gap checks)
- `Tests/test_portfolio_history_builder.py`
- `Tests/test_portfolio_history_backfill.py`
- `Tests/test_portfolio_history_escalation.py`

## Risks And Open Questions

- Alpaca paper portfolio-history retention for March-May 2026 is unverified;
  if the early window is no longer served, the canonical series begins at the
  earliest recoverable date and the gap is documented in the manifest. Check
  immediately.
- Paper-account cash adjustments (if any were made manually) would appear as
  NAV jumps; the backfill reconciliation must flag returns exceeding 5% on a
  single day for operator review.
- Decision: should weekends/holidays carry forward rows (flat NAV) or be
  absent? Proposed: absent; consumers use the trading calendar
  (`paper/trading_calendar.py`).
