# Alpha Stack Research and Validation Specification

Purpose
- Define how Alpha Stack research is validated and promoted across backtest, shadow, and paper gates.

Scope
- Metric definitions, thresholds, validation windows, failure criteria, and promotion checklists.
- Applies to Alpha Stack program only.

Assumptions
- Production model remains active and unchanged until formal cutover decision.
- Validation includes explicit transaction costs and operational checks.

Status
- Baseline validation contract before implementation.

Future Work
- Add statistical power checks by sleeve and universe segment.
- Add post-cutover rollback triggers and monitoring dashboard spec.

## 1. Validation Stages

Stage 1: Design validation
- Specs complete for sleeves, regimes, allocator, data.
- No code promotion without documentation sign-off.

Stage 2: Backtest validation
- PIT-safe, cost-aware, reproducible backtests.

Stage 3: Shadow validation
- Minimum 60 trading days in shadow.
- Daily side-by-side with production baseline.

Stage 4: Paper promotion readiness
- Shadow criteria met with operational stability.

Stage 5: Cutover decision
- Formal go/no-go memo and rollback plan.

## 2. Required Metrics

Performance metrics
- CAGR
- Annualized volatility
- Sharpe ratio (cost-adjusted)
- Sortino ratio
- Max drawdown
- Calmar ratio

Attribution metrics
- Sleeve contribution to return
- Regime contribution to return
- Allocation effect vs selection effect

Signal quality metrics
- IC mean and standard deviation
- IC t-stat
- Hit rate by sleeve
- Factor decay curve (1d, 5d, 10d, 21d horizons)

Diversification metrics
- Rolling 126d sleeve correlation matrix
- Effective number of sleeves (entropy-based)

Execution realism metrics
- Turnover
- Estimated slippage cost
- ADV participation breaches

## 3. Minimum Thresholds (Initial Program Gates)

Backtest pass thresholds
- Combined cost-adjusted Sharpe >= 0.90
- Max drawdown <= baseline drawdown * 1.10
- Turnover <= 25% average daily equivalent at portfolio level
- IC mean >= 0.02 for each enabled sleeve, with no sleeve IC t-stat < 1.5

Shadow pass thresholds (>= 60 trading days)
- Tracking stability: no unexplained signal/output discontinuities
- Operational error rate: 0 critical failures, <= 2 noncritical incidents
- Cost-adjusted excess return vs production baseline >= 0 over window
- Sleeve attribution signs align with expected regime behavior in >= 70% of days

Paper promotion thresholds
- All backtest + shadow thresholds passed
- Data quality hard failures = 0 for the full shadow window
- Daily reconciliation and artifact generation pass rate >= 99%

## 4. Statistical Definitions

IC
- Spearman rank correlation between signal score at `t` and forward return `t+1` (or configured horizon).

IC stability
- `IC_stability = mean(IC) / std(IC)` measured on rolling windows.

Cost-adjusted return
- `r_net = r_gross - commission_cost - slippage_cost - spread_cost`

Turnover
- `turnover_t = 0.5 * sum_i |w_i(t) - w_i(t-1)|`

Drawdown
- `dd_t = NAV_t / cummax(NAV) - 1`

## 5. Experiment Governance

Required per experiment
- Hypothesis statement
- Parameter changes and rationale
- Data version and date range
- Reproducible seed/config
- Result summary with pass/fail against thresholds

Prohibited
- Parameter changes without recorded rationale
- Cherry-picked evaluation windows
- Promotion claims without cost-adjusted metrics

## 6. Promotion Checklist

Backtest -> Shadow
- PIT audit passed
- Sleeve specs implemented as documented
- Cost model enabled
- Attribution outputs complete

Shadow -> Paper
- >= 60 trading days complete
- Thresholds passed
- Operational incident review completed
- Go/no-go memo approved

Paper -> Cutover recommendation
- Stable paper behavior
- No unresolved data integrity issues
- Explicit rollback playbook prepared

## 7. Failure and Rollback Criteria

Immediate fail
- Any PIT violation discovered in active validation set
- Missing/invalid artifacts for required daily outputs
- Unexplained major attribution discrepancy

Rollback trigger (post-promotion testing)
- 20-day rolling underperformance beyond pre-defined tolerance
- Operational failure pattern indicating systemic instability

## 8. Explicit Deferrals

Future phase only
- Options overlay validation suite
- Intraday execution quality model
- ML hyperparameter search framework
