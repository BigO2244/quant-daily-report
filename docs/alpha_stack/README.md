# Alpha Stack Documentation Baseline

Purpose
- Establish the program baseline for Alpha Stack research, shadow validation, and staged promotion without changing current production behavior.

Scope
- Defines what Alpha Stack is, what gets built first, and what is intentionally deferred.
- Covers architecture, sleeves, regime allocator, data standards, and research validation.
- Applies only to the Alpha Stack namespace and research/shadow workflows.

Assumptions
- Source of truth is `docs/Alpha_Stack_Architecture_Reference.md`.
- Current production engine remains active and frozen except bug fixes and operational hardening.
- Canonical production artifacts and broker reconciliation assumptions remain unchanged.

Status
- Baseline documentation complete.
- Implementation pending.

Future Work
- Add portfolio construction spec as a separate document before allocator implementation.
- Add change log and decision record for threshold revisions.

## Program Boundaries

Production engine (current)
- Live paper execution path is unchanged.
- Existing workflows, artifacts, and reconciliation contracts remain authoritative.

Alpha Stack program (new)
- Separate namespace, config path, workflow path, and output root.
- Research-first delivery path: design -> backtest -> shadow -> paper -> cutover.
- No writes to production canonical artifacts during research or shadow.

## Documentation Map

- `docs/alpha_stack/architecture_overview.md`: Layered system design, interfaces, and phase order.
- `docs/alpha_stack/sleeve_specifications.md`: Sleeve formulas, thresholds, holding logic, and promotion criteria.
- `docs/alpha_stack/regime_allocator_spec.md`: State machine, transition thresholds, hysteresis, and sleeve budget mapping.
- `docs/alpha_stack/data_standards.md`: Point-in-time data contracts, as-of semantics, and quality gates.
- `docs/alpha_stack/research_validation_spec.md`: Backtest/shadow validation metrics, promotion gates, and failure criteria.

## Build Order (Baseline)

1. Data foundation (PIT-safe stores, as-of APIs, data quality checks)
2. Regime engine (explicit state machine + hysteresis)
3. Trend sleeve upgrade
4. Value sleeve rebuild (after PIT enforcement)
5. Attribution lab (IC/IR, decay, turnover/cost)
6. Allocator v1 (rules-based, interpretable)
7. Quality sleeve
8. Mean reversion sleeve
9. 60+ trading day shadow run
10. Promotion decision and cutover plan

## Explicit Deferrals

Deferred to future phases, not current capabilities
- Options overlays: covered calls, cash-secured puts, protective hedges
- Event/news sleeves and discretionary overlays
- Optimization-first allocator (rules-first required)
- Any production cutover changes before shadow success criteria are met

## Production Safety Principles

- Never alter production execution semantics as part of Alpha Stack build-out.
- Never allow Alpha Stack research outputs to overwrite production canonical state.
- Keep Alpha Stack and production run artifacts physically separated.
- Require documented go/no-go decisions for every promotion stage.
