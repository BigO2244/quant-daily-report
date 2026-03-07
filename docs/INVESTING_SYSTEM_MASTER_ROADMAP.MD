# INVESTING SYSTEM MASTER ROADMAP
Quantitative Alpha Platform Architecture

Owner: Brett Olson
Role: CIO / Portfolio Manager
System: Alpha Lab + Quant Execution Engine

------------------------------------------------------------

1. SYSTEM MISSION

The goal of this platform is to build a self-improving quantitative investing system that:

1. Discovers investment signals
2. Validates them empirically
3. Converts them into strategies
4. Executes trades automatically
5. Continuously measures alpha

The system operates as a closed research → trading → feedback loop.

------------------------------------------------------------

2. SYSTEM PHILOSOPHY

Principle 1 — Research must be measurable

Ideas are useless unless their performance can be measured historically and forward tested.

Principle 2 — Signals before strategies

All alpha begins as a signal.

Examples:

earnings surprise  
AI infrastructure spending  
insider buying  
momentum breakout

Signals become strategies only after validation.

Principle 3 — Modular sleeves

The portfolio is composed of independent alpha sleeves.

Example sleeves:

Trend sleeve  
Valuation sleeve  
Event-driven sleeve  
AI infrastructure sleeve  
Macro regime sleeve  

Each sleeve owns:

its signals  
its trading logic  
its performance attribution

Principle 4 — Deterministic execution

Every trading run must produce deterministic artifacts:

positions.json  
trades.json  
nav.json  
signals.json  
logs.json  

This ensures full auditability.

Principle 5 — Continuous alpha discovery

The system continuously:

discover → test → deploy → retire signals

Alpha generation becomes a permanent research process.

------------------------------------------------------------

3. SYSTEM ARCHITECTURE OVERVIEW

The investing system contains seven layers.

1. Research Layer  
2. Signal Layer  
3. Alpha Lab  
4. Strategy Layer  
5. Portfolio Layer  
6. Execution Layer  
7. Performance Layer  

------------------------------------------------------------

4. RESEARCH INTELLIGENCE ENGINE

Purpose:

Generate potential investment signals from research sources.

Examples:

news feeds  
earnings transcripts  
macro releases  
research reports  
twitter / X  
SEC filings  

Output:

signals_candidates.json

Example:

{
 "ticker": "SMCI",
 "theme": "AI infrastructure demand",
 "source": "earnings transcript",
 "confidence": 0.82
}

Artifacts:

research_digest.html  
research_items.json  
run_log.json  

------------------------------------------------------------

5. SIGNAL REGISTRY

Central database of all signals discovered.

Directory:

data/signals/

Example record:

signal_id  
date  
ticker  
signal_type  
source  
confidence  
entry_rule  
exit_rule  
holding_period  

Example:

2026-03-05  
SMCI  
AI_INFRASTRUCTURE  
earnings_call  
0.88  
enter_next_open  
hold_10_days  

Purpose:

Track signals so we can measure whether they produce alpha.

------------------------------------------------------------

6. ALPHA LAB

Alpha Lab evaluates signal performance.

Metrics calculated:

1-day return  
3-day return  
5-day return  
10-day return  
20-day return  
max drawdown  
volatility  
hit rate  
Sharpe ratio  

Dataset:

data/signal_performance/signals_performance.csv

Output:

alpha_report.md  
alpha_leaderboard.csv  

Example output:

Signal: AI_INFRASTRUCTURE  
Sharpe: 1.82  
Win Rate: 61%  

------------------------------------------------------------

7. STRATEGY CONSTRUCTION

Validated signals become trading strategies.

Example:

AI_INFRA_STRATEGY

Rules:

Entry  
signal_score > 0.75

Position size  
2%

Exit  
10 days OR -6% stop

Strategies live in:

strategies/

------------------------------------------------------------

8. PORTFOLIO ENGINE

Allocates capital across strategies.

Example portfolio:

Trend sleeve  
Event sleeve  
AI sleeve  
Macro sleeve  

Example allocation:

Trend sleeve: 35%  
AI sleeve: 25%  
Event sleeve: 25%  
Macro sleeve: 15%

Responsibilities:

position sizing  
risk budgeting  
exposure control  
turnover management  

------------------------------------------------------------

9. EXECUTION ENGINE

Live trading system.

Responsibilities:

generate orders  
route to broker  
track fills  
update positions  
reconcile portfolio  

Broker integrations:

Alpaca  
Interactive Brokers (future)

Artifacts:

orders.json  
fills.json  
positions.json  
ledger.json  

------------------------------------------------------------

10. PERFORMANCE TRUTH LAYER

Measures real performance.

Metrics:

portfolio return  
benchmark return  
alpha  
drawdown  
exposure  
turnover  

Storage:

outputs/performance/

Files:

portfolio_nav.csv  
strategy_returns.csv  
alpha_attribution.csv  

------------------------------------------------------------

11. FEEDBACK LOOP

Research → Signals → Alpha Lab → Strategies → Portfolio → Performance → Research refinement

This creates a self-improving investing system.

------------------------------------------------------------

12. AI ROLES

ChatGPT (CIO)

system architecture  
portfolio design  
risk framework  

Claude (Research Agent)

research ingestion  
signal discovery  
information extraction  

Codex (Implementation)

code generation  
pipeline building  
module integration  

AIOPS (Governance)

spec-driven development  
CI/CD validation  
artifact verification  

------------------------------------------------------------

13. LONG-TERM VISION

Build an autonomous alpha factory capable of:

continuous signal discovery  
automated backtesting  
strategy promotion  
capital allocation  
live trading  
performance attribution  

------------------------------------------------------------

14. GUIDING RULE

Never deploy a strategy that has not passed:

Signal validation  
Alpha Lab testing  
Portfolio risk evaluation

------------------------------------------------------------

15. NEAR-TERM ROADMAP (Q1 2026 and Beyond)

### Foundation Pass (Complete — March 6, 2026)

✓ VIX producer integration (`outputs/perf/vix_close_history.csv`)  
✓ Canonical performance layer with benchmark + VIX + analyzer scores  
✓ Alpha assessment build pipeline with producer automation  
✓ Overlay engine backtesting with no-lookahead rule  
✓ Analyzer validation and classifier metrics  
✓ Documentation pack refresh (README, CHANGELOG, runbooks)

### Next Build: Performance Dashboard

Purpose: Real-time visibility into portfolio performance, signal quality, and system health.

Components:

- **Web dashboard** (React/TypeScript or similar): daily performance cards, equity curve, alpha attribution, analyzer signal heatmap
- **Data backend** (Python): canonical performance CSV → JSON API
- **Metrics refresh**: daily NAV, returns, volatility, Sharpe, IC, drawdown
- **Alerts layer**: email/Slack notifications for regime changes, IC drops, reconciliation failures

Acceptance criteria:

- Dashboard loads latest canonical_performance.csv and renders equity curve
- Shows daily alpha breakdown (excess_return attribution)
- Displays current VIX regime and backtest analyzer signal accuracy
- Email alerts sent on reconciliation failures and IC warnings

Target: After Q1 data collection (April–May 2026)

### Beyond: Expanded Signal Discovery

- Alpha Lab v1 formalization (signal registry, promotion pipeline)
- Earnings-driven signal discovery (EPS surprise + growth)
- Macro regime signals (FOMC meeting proximity, yield curve inversions)
- Cross-sleeve rebalancing rules
- Interactive Brokers integration (parallel to Alpaca)
- Live trading safeguards (pre-trade chaos testing, drawdown circuit breaker hardening)