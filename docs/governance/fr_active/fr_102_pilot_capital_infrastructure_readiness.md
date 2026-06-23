# FR-102 Pilot Capital Infrastructure Readiness

Status: `RESEARCH_ONLY_IMPLEMENTATION_STARTED`  
Date: `2026-06-19`  
Execution Impact: `NON_EXECUTIONAL`  
Capital Impact: `$0`

## Objective

Assess whether Caerus can safely separate:

- paper execution;
- shadow/research sleeves;
- future tiny live pilot capital execution.

FR-102 does not enable live trading. It adds observe-first guardrails so future
live-capital work cannot accidentally route through existing paper execution
paths.

## Current Infrastructure Assessment

| Surface | Current state | Assessment |
|---|---|---|
| Paper execution | `scripts/run_precomputed_alpaca_execution.py` calls `paper.paper_broker.run_paper_day`. `paper/config_paper.json` defaults to `paper`. | Paper execution is the only implemented order-submission workflow. |
| Shadow/research sleeves | Strategy registry and research manifests mark shadow/research outputs as `NON_EXECUTIONAL`. | Shadow/research remains separated by governance, but not by a broker account boundary because it should not submit orders. |
| Live pilot execution | `core.trading_mode` knows `live`, but `paper_broker.run_paper_day` refuses `live`. | Tiny live pilot execution is not supported today. |
| Live preflight | New `live_preflight` mode is recognized and forces no-submit behavior. | Safe for observe-only checks; not capital approval. |
| Alpaca endpoint | `brokers.alpaca_broker.load_alpaca_env` reads paper/live endpoint choice from env. | Credential mode must be guarded at submission time, not assumed from script name. |

## Where Alpaca Credentials Are Read

Credential sources:

- `ALPACA_API_KEY_ID`
- `ALPACA_API_SECRET_KEY`
- legacy `ALPACA_KEY_ID`
- legacy `ALPACA_SECRET_KEY`
- `ALPACA_PAPER`
- `ALPACA_BASE_URL`

Primary readers:

- `brokers/alpaca_broker.py`: `load_alpaca_env()` and `AlpacaBroker.from_env()`.
- `scripts/run_precomputed_alpaca_execution.py`: loads repo `.env` into process env before importing broker code.
- `scripts/export_alpaca_broker_snapshot.py`: reads env and supports REST fallback for account, positions, orders, and fills.
- `scripts/alpaca_smoke_test.py`: reads env for a paper account endpoint probe.
- `scripts/diag_alpaca_auth.py`: reads env for diagnostics and paper `/v2/account` probe.
- `scripts/backfill_portfolio_history.py`: imports `load_alpaca_env()` for read-only portfolio history.

No real API keys were added to the repository.

## Artifacts That Must Be Separated By Mode

Before live pilot setup, these artifact families need explicit mode/account
partitioning or fields:

- run roots: `outputs/runs/<RUN_ID>/`;
- broker snapshots: `outputs/broker/*` and `outputs/runs/<RUN_ID>/broker/*`;
- order ledgers: `outputs/orders_sent/orders_sent.csv`, run-local broker order files, and legacy paper ledgers;
- execution payloads/results: `execution_payload.json`, `execution_results.json`;
- operator surfaces: `operator_summary.json`, trading-day summary, dashboard payloads, email payloads;
- reconciliation: `broker/recon_posttrade_<TRADE_DATE>.json`;
- reliability: `audit/execution_reliability_report_<TRADE_DATE>.json`, `outputs/reliability/*`;
- target attainment: `audit/execution_target_attainment_<TRADE_DATE>.json`;
- performance/NAV: `outputs/perf/*`, broker-authoritative portfolio history, live-overlay artifacts;
- precompute: `outputs/precompute/<TRADE_DATE>/*`.

Future live pilot artifacts should include `account_mode`, `broker_base_url`,
`capital_cap_usd`, `approved_pilot_sleeve_id`, and `execution_account_id_hash`
without storing secrets.

## Accidental Live Execution Risk

Pre-FR-102 risk:

1. The high-level runtime is named paper, but `AlpacaBroker.from_env()` can load
   live Alpaca credentials when `ALPACA_PAPER=0`.
2. The submit methods previously blocked non-paper hosts only when
   `broker.paper=True`; a live broker object could reach `submit_order`.
3. Standalone legacy executor `scripts/execute_alpaca_orders.py` also uses
   `AlpacaBroker.from_env()`.
4. Artifact paths do not yet partition paper and live accounts by mode.

Post-FR-102 mitigation:

- live Alpaca submission is blocked unless the process resolves to
  `TRADING_MODE=live`;
- live submission also requires `CAERUS_ALLOW_LIVE_TRADING`,
  `CAERUS_LIVE_CAPITAL_CAP_USD`, and `CAERUS_APPROVED_PILOT_SLEEVE_ID`;
- `TRADING_MODE=live_preflight` always blocks order submission;
- mode ambiguity between `MODE` and `TRADING_MODE` raises before submission;
- paper execution now refuses live Alpaca credentials/endpoints.

## LIVE_PREFLIGHT Design

`LIVE_PREFLIGHT` is an observe-only mode for a future pilot review. It may inspect
configuration and read-only account state, but it must not submit orders.

Required preflight fields:

- requested mode;
- broker paper/live endpoint status;
- explicit live trading flag status;
- positive pilot capital cap;
- approved pilot sleeve id;
- live order allowance, always `false` in preflight;
- operator action.

Generated artifact in the precomputed execution runner:

`outputs/runs/<RUN_ID>/audit/live_pilot_preflight.json`

Operator summary fields:

- `live_pilot_preflight_status`
- `live_pilot_preflight_reason`
- `live_pilot_preflight_artifact`
- `live_orders_allowed`

## Controls Added

| Control | File | Behavior |
|---|---|---|
| Canonical `live_preflight` mode | `core/trading_mode.py` | Normalizes `live_preflight`, `live-preflight`, `preflight_live`, and `preflight-live`. |
| Live pilot guardrail contract | `core/live_pilot_preflight.py` | Centralizes mode, endpoint, flag, cap, and sleeve checks. |
| Submission-layer live guard | `brokers/alpaca_broker.py` | Blocks live endpoint order submission unless live mode, flag, cap, and sleeve are explicit. |
| Paper path live-credential refusal | `paper/paper_broker.py` | Paper execution refuses live broker credentials/endpoints. |
| LIVE_PREFLIGHT no-submit behavior | `paper/paper_broker.py` | Forces `plan_only` when mode is `live_preflight`. |
| LIVE_PREFLIGHT artifact | `scripts/run_precomputed_alpaca_execution.py` | Writes observe-only preflight evidence in run audit artifacts. |
| Targeted tests | `Tests/test_live_pilot_preflight.py` | Covers paper pass-through, live endpoint refusal, explicit live requirements, mode ambiguity, and no-submit preflight. |

## Is Live Pilot Currently Supported?

FR-102 by itself does not support live pilot execution. It makes future live
pilot setup safer, but it is not a capital approval.

The only permitted live-pilot execution lane is the separate FR-104 manual path,
if and only if its cap, approval, dry-run, account, artifact isolation, and
broker-truth controls pass.

Reasons:

- `paper_broker.run_paper_day` still refuses `TRADING_MODE=live`;
- live artifacts are not yet mode/account partitioned;
- no approved sleeve/cap/rollback/kill packet exists;
- no real API keys should be added to scheduled or paper runtimes;
- FR-104 may use externally supplied live credentials for an approved manual
  Level 2.5 evidence-collection run, with no secrets in git and no cron;
- no live executor has been designed, reviewed, or validated.

## Gaps Before API Key Setup

1. For Level 3 readiness, complete FR-101 forward `FULL_EVIDENCE` paper window.
2. For any Level 2.5 FR-104 evidence run, enforce manual approval, cap,
   dry-run-first sequencing, artifact isolation, and broker-truth capture.
3. Add mode/account partitioning for all execution, broker, reconciliation,
   reliability, target-attainment, and performance artifacts.
4. Produce a signed pilot packet naming sleeve, cap, account, allowed
   instruments, duration, rollback, kill criteria, and manual approvers.
5. Add read-only live account preflight that hashes account identity and proves
   correct endpoint without exposing secrets.
6. Add live-pilot dry-run tests that validate every live-bound order remains
   no-submit until explicit approval.
7. Add an operator checklist that verifies `TRADING_MODE`, `ALPACA_PAPER`,
   endpoint, cap, sleeve, and run root before any future live submission path.

## Recommended Live Pilot Sequence

1. For Level 2.5 evidence collection, require FR-104 manual approval, cap,
   dry-run, artifact isolation, and no cron.
2. For Level 3 readiness, finish FR-101 evidence window and obtain
   `RELIABILITY_GREEN + FULL_EVIDENCE` for the required paper run streak.
3. Select exactly one pilot sleeve and dollar cap under FR-084 or FR-104,
   depending on whether the purpose is readiness conclusion or evidence
   collection.
4. Add mode/account-separated artifact paths and tests.
5. Run `TRADING_MODE=live_preflight` with read-only live credentials only after
   human approval to set up credentials outside git.
6. Review preflight artifact, hashed account identity, cap, sleeve, and no-submit
   proof.
7. Build a separate live executor behind the same FR-102 guardrails only if
   Brett/CIO signs the pilot packet.
8. Start with one tiny manually reviewed order batch, preserve all broker truth,
   and demote immediately on any RED/YELLOW, artifact gap, recon mismatch, or
   target-attainment miss.

## Rollback

Revert FR-102 code and tests if needed. Existing paper execution should continue
to refuse `TRADING_MODE=live`; no live keys, cron, capital, strategy, sizing, or
broker behavior were added.
