MODE: BUILD
PROJECT_TYPE: quant-ops
RISK_TIER: medium
OBJECTIVE: Consolidate Quant Daily email outputs to 2 scheduled emails/day + 1 conditional failure alert; eliminate duplicates and reduce noise.

## CONTEXT
Current behavior produces multiple emails that overlap in content and can duplicate.
Examples observed:
- Separate TRADE EXECUTION email(s) for the same day/run context (duplicate) and content focuses on PLANNED orders, turnover/cash metrics.  [oai_citation:3‡TRADE EXECUTION — 2026-02-27 (SHADOW).eml](sediment://file_000000009cec722fa6b354230a023387)  [oai_citation:4‡TRADE EXECUTION — 2026-02-27 (SHADOW) 2.eml](sediment://file_000000001034722f868d3a940fbf614e)
- MODEL & PERFORMANCE SNAPSHOT email includes broader portfolio/perf content and also includes paper execution/reconciliation sections and “summary unavailable” due to missing inputs.  [oai_citation:5‡MODEL & PERFORMANCE SNAPSHOT — 02-27-2026.eml](sediment://file_00000000a358722f8e78d784476471b5)

We want one clear PRE email (proposed trades) and one clear POST email (execution results), with a third email only when the pipeline fails.

## DESIGN GOALS
1) Reduce email volume and eliminate duplicates.
2) Enforce a consistent “operating loop”:
   - PRE: What will trade today?
   - POST: What actually happened + reconciliation?
3) Preserve important detail, but move deep tables to a collapsed/secondary section or attachment/log link (if applicable).
4) Never leak secrets to email content.

## SCHEDULE (ET)
- 06:30 — Email #1: Proposed Trades (PRE)
- 09:35 — Email #2: Execution & Reconciliation (POST)
- Conditional — Email #3: FAILURE ALERT (only if any hard gate fails)

## EMAIL DEFINITIONS

### Email #1 — Proposed Trades (PRE) @ 06:30
Subject format:
  "PROPOSED TRADES — {YYYY-MM-DD} ({MODE})"

Body (top section, max ~15 lines):
- Run context: MODE, trade date, run_id (if available), market status (open/closed), trading_mode
- Decision summary: {TRADE / NO TRADE}
- Count summary: # buy, # sell/close, est notional, target cash %, turnover requested vs cap
- Top 10 orders table (ticker, side, qty, est notional, entry/stop/target if relevant)

Body (details section):
- Full orders (if >10 tickers)
- Constraints hit / skipped orders and reasons (weight cap, turnover cap, cash constraints, etc.)
- Risk summary table (turnover, cash weight, exposures)

Notes:
- This consolidates today’s “TRADE EXECUTION … PLANNED” content into the PRE email, instead of sending it as its own separate email.  [oai_citation:6‡TRADE EXECUTION — 2026-02-27 (SHADOW).eml](sediment://file_000000009cec722fa6b354230a023387)

### Email #2 — Execution & Reconciliation (POST) @ 09:35
Subject format:
  "EXECUTION REPORT — {YYYY-MM-DD} ({MODE})"

Body (top section, max ~15 lines):
- Run context: MODE, trade date, run_id
- Execution summary:
  - orders submitted / filled / partial / rejected
  - total executed notional
  - estimated slippage metric (if available)
- Reconciliation summary:
  - broker vs ledger match status
  - achieved cash % vs target
  - any invariant failures (YES/NO)

Body (details section):
- Fills table (ticker, side, qty, avg fill, status, reject reason)
- Post-trade holdings summary (top positions + cash)
- Links/paths to artifacts (run archive path, ledger snapshots) if applicable

Notes:
- “Paper Trading Execution summary unavailable…” due to missing inputs should be expressed here as a clear POST failure state, not buried inside a combined model email.  [oai_citation:7‡MODEL & PERFORMANCE SNAPSHOT — 02-27-2026.eml](sediment://file_00000000a358722f8e78d784476471b5)

### Email #3 — Failure Alert (conditional only)
Trigger:
- Any of the following occurs in either PRE or POST pipeline step:
  - job fails (exception / non-zero exit)
  - required artifact missing (e.g., expected CSV outputs)
  - reconciliation mismatch
  - email generation step fails after retries

Subject format:
  "ALERT — Quant Daily pipeline failed — {YYYY-MM-DD} ({STEP})"

Body:
- Step failed: PRE / POST
- Error summary (first ~20 lines, redact secrets)
- Pointers:
  - run_id
  - workflow run URL (if available)
  - artifact paths
- Clear call-to-action: “No trades executed” OR “Trades may have executed; verify broker now” depending on failure point

## IMPLEMENTATION SCOPE

### Consolidation & Dedup
- Create a single “email orchestrator” that decides which of the 3 email types to send.
- Ensure only one email per type per trade date (idempotent):
  - Derive a deterministic idempotency key (trade_date + email_type + mode + run_id)
  - Store in run archive or a small state file and skip if already sent.

### Content Routing
- Move “TRADE EXECUTION (PLANNED)” content into PRE email only.
- Ensure model/performance snapshot content is NOT emailed separately in the morning unless explicitly requested later.
  - If you still want performance, include only a compact performance summary in the POST email.

### Safety
- Redact env/secrets in all emails.
- Ensure failures do not dump raw env to stdout or email body.

## FILES
modify:
- daily_quant_report.py (or the main entrypoint used by GitHub Actions)
- email_alpha_report.py (or current email sender module)
- any module producing TRADE EXECUTION email content
- any module producing MODEL & PERFORMANCE SNAPSHOT email content
- GitHub Actions workflow(s) that schedule/send these emails (daily.yml, execution_email.yml, etc.)

add:
- core/email_orchestrator.py (or similar) to unify decision logic and dedup
- tests/test_email_orchestrator.py (idempotency, routing, failure triggers)

## ACCEPTANCE CRITERIA
- Exactly two scheduled emails are sent on a normal day:
  1) 06:30 ET Proposed Trades (PRE)
  2) 09:35 ET Execution & Reconciliation (POST)
- No standalone “TRADE EXECUTION … (SHADOW)” email is sent separately anymore; its content appears in PRE.  [oai_citation:8‡TRADE EXECUTION — 2026-02-27 (SHADOW).eml](sediment://file_000000009cec722fa6b354230a023387)
- Duplicate emails for the same trade date/type do not occur (idempotency enforced).
- If required inputs/artifacts are missing for the POST report, the system sends a single ALERT email (and the POST email is either suppressed or clearly marked FAILED), matching the error state like “summary unavailable (missing inputs…)” but presented as a first-class failure.  [oai_citation:9‡MODEL & PERFORMANCE SNAPSHOT — 02-27-2026.eml](sediment://file_00000000a358722f8e78d784476471b5)
- Tests cover:
  - PRE routing when trades exist and when “no proposed trades”
  - POST routing when fills exist and when no fills
  - ALERT routing on raised exceptions / missing artifacts
  - Dedup (same idempotency key → only one email)
- Email bodies contain no secrets and are stable/consistent across runs.

## OUT OF SCOPE (FOR THIS SPEC)
- Changing trading logic, signal generation, or portfolio construction.
- Adding Slack/Discord notifications (can be a later spec).