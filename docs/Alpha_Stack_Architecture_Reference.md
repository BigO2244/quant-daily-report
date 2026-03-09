Alpha Stack Architecture Reference
Program baseline for design, documentation, research, shadow validation, and production-safe implementation.
Version	Owner / Sponsor	Status
v1.0	Brett / CIO program	Architecture baseline

Executive summary. The future model will be built as a parallel Alpha Stack program: a modular, multi-sleeve portfolio system with regime-aware allocation, robust attribution, and strict production isolation. The current live model remains frozen in production while Alpha Stack is researched, backtested, shadowed, and only then promoted.
1. Purpose and Governance
This document is the single architecture reference point for all Alpha Stack work. It should be used to align design decisions, Codex implementation plans, research tasks, documentation updates, and promotion gates.
•	Use this file as the program baseline for architecture, scope, and sequencing.
•	Do not treat this as a static memo; update versioned revisions as decisions change.
•	All major Codex tasks should cite this document as the architectural source of truth.
2. Non-Negotiable Guardrails
The program must improve model sophistication without interrupting the current production environment.
•	Freeze the current production model except for bug fixes and operational hardening.
•	Build Alpha Stack in a separate namespace, config path, workflow path, and output root.
•	Require promotion by stages: design → backtest → shadow → paper → cutover.
•	Do not let research experiments write to canonical production artifacts.
3. Target Operating Model
Alpha Stack is a layered portfolio platform rather than a single strategy script.
Target flow: raw data → normalized feature store → sleeve signal engines → regime engine → sleeve allocator → portfolio constructor → target book → execution translator → reconciliation / attribution.
•	The current model remains the live operating strategy until Alpha Stack earns promotion.
•	The future platform must explain where returns come from at the sleeve, regime, and portfolio levels.
4. Core Layers
Each layer has a distinct responsibility and interface.
Layer	Primary responsibility	Key output
Data	Ingest prices, fundamentals, macro, breadth, and later options/news	Point-in-time raw datasets
Features	Compute reusable signals and normalized metrics	Feature store keyed by symbol/date
Sleeves	Generate sleeve scores, candidates, and provisional targets	Sleeve-level target weights
Regime	Classify market state with hysteresis	Regime context object
Allocator	Map regime to sleeve budgets	Dynamic sleeve weights
Portfolio construction	Blend sleeves under portfolio controls	Final target book
Execution / audit	Translate targets into orders and track outcomes	Trades, recon, attribution
5. Sleeve Definitions
Alpha Stack v1 will begin with four sleeves and reserve overlays for later phases.
Sleeve	Role	Core signals	Sizing baseline	Status
Trend / Momentum	Primary return engine in favorable markets	12-1, 6-1, 3-1 momentum; 50/200 MA; sector-relative strength	ATR / volatility aware	Build first
Value	Style diversification and recovery capture	P/E or earnings yield, EV/EBITDA, P/B, shareholder yield; sector-relative	Equal weight	Rebuild after PIT fix
Quality	Durability and downside resilience	ROE / ROIC, profitability, leverage, margin stability, accrual quality	Equal weight, higher conviction allowed	Build after attribution
Mean reversion	Short-term tactical dislocations	RSI, Bollinger, short-term z-score, volume reversal, regime gate	Small equal weight	Build last in v1
Future overlays are intentionally deferred: covered calls, cash-secured puts, protective hedges, and event/news sleeves should be researched only after Alpha Stack v1 is stable.
6. Regime Engine
The allocator is governed by a rules-based state machine with hysteresis.
•	Trend regime: strong_up / weak_up / neutral / weak_down / strong_down.
•	Volatility regime: calm / normal / elevated / crisis.
•	Breadth regime: healthy / mixed / deteriorating / washed_out.
•	Macro regime: supportive / neutral / restrictive.
•	All regime transitions must use hysteresis or smoothing to prevent whipsaw.
Initial allocator design should be rules-based, interpretable, and explicitly documented before any optimization is attempted.
7. Portfolio Construction and Risk
Signal generation and risk management must remain separate.
•	Portfolio-level position cap: 8–10% starting range.
•	Sector cap: 30% starting range.
•	Net long exposure cap: 95% with a minimum cash reserve.
•	No leverage in v1.
•	Drawdown circuit breaker: reduce sleeve sizes if portfolio drawdown breaches threshold.
•	Weight changes should be smoothed over multiple trading days to control turnover.
8. Research and Attribution Standards
Every sleeve and allocator decision must be measurable.
•	Measure IC and IC stability by sleeve before trusting combined results.
•	Track sleeve Sharpe, combined Sharpe, turnover, and cost-adjusted returns.
•	Produce factor decay curves to determine the right rebalance cadence per sleeve.
•	Track rolling correlations across sleeve equity curves; diversification is not assumed.
•	Backtests must include transaction cost assumptions and shadow-vs-production comparisons.
9. Data Standards
Point-in-time correctness is mandatory for trustworthy research.
•	Implement a DataStore abstraction with as-of-date access semantics.
•	Separate raw stores from processed feature stores.
•	Macro inputs should begin with FRED and price-derived breadth metrics.
•	Fundamental data must include filed dates to avoid look-ahead bias.
•	Do not trust value sleeve backtests until point-in-time data is enforced.
10. Delivery Roadmap
The implementation sequence should reduce model risk and protect production.
Phase	Objective	Primary deliverables
0	Freeze and document production	Production contract, current workflow map
1	Build data foundation	DataStore, macro feeds, PIT cache plan
2	Implement regime engine	State machine, hysteresis, validation plots
3	Upgrade trend sleeve	Composite trend score, sizing, diagnostics
4	Rebuild value sleeve	PIT-safe metrics, composite score, tests
5	Build attribution lab	IC/IR, decay curves, turnover/cost reports
6	Add allocator v1	Static base weights with regime overrides and smoothing
7	Add quality sleeve	Signals, tests, attribution
8	Add mean reversion sleeve	Signals, regime gates, conservative sizing
9	Shadow Alpha Stack	60 trading days minimum, daily comparisons
10	Promotion decision	Go/no-go memo and cutover plan
11. Documentation Requirements
Documentation must be updated before and alongside code.
•	Update README / architecture overview before major implementation work begins.
•	Maintain a dedicated Alpha Stack spec document, research log, and change log.
•	Every new sleeve needs a signal spec, data dependency list, and promotion criteria.
•	Codex tasks should update docs in the same PR as implementation wherever possible.
•	No “mystery model” behavior: formulas, thresholds, and gates must be documented.
12. Definition of Success
Success means a stronger alpha engine and a safer operating model.
•	Production remains stable while research velocity increases.
•	Alpha Stack can explain returns by sleeve, regime, and allocation decision.
•	Backtests are point-in-time safe and cost-aware.
•	Shadow results prove operational readiness before cutover.
•	The system graduates from a simple strategy engine to a real portfolio platform.
Appendix A. Required documentation pack
Before major build-out, the program should maintain the following living documents:
•	Alpha Platform Overview — high-level architecture, scope, non-goals, promotion ladder.
•	Sleeve Specification Pack — formulas, universes, holding periods, and sizing logic for each sleeve.
•	Regime Allocator Spec — state machine, thresholds, hysteresis, and regime-to-weight mapping.
•	Portfolio Construction Spec — constraints, caps, turnover logic, and circuit breakers.
•	Data Standards Spec — raw feeds, as-of semantics, filed-date rules, and cache governance.
•	Research Validation Spec — IC/IR targets, cost model, shadow criteria, and cutover checklist.
Appendix B. Codex implementation principle
Codex should be used to update documentation first, then build the research architecture in parallel namespaces without touching production execution semantics. Every implementation task should explicitly preserve current workflows, canonical artifacts, and broker reconciliation assumptions.
