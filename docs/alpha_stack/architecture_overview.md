# Alpha Stack Architecture Overview

Purpose
- Define the target Alpha Stack platform architecture and implementation sequence before coding.

Scope
- Covers layer responsibilities, interface contracts, state and artifact boundaries, and staged delivery.
- Does not modify production workflows, current canonical artifacts, or reconciliation assumptions.

Assumptions
- Source of truth is `docs/Alpha_Stack_Architecture_Reference.md`.
- Existing production engine remains the live strategy until explicit promotion.

Status
- Baseline architecture for implementation planning.

Future Work
- Add concrete module/package names after namespace review.
- Add portfolio construction spec and execution translator interface schema.

## 1. Operating Model

Target flow
- Raw data -> normalized feature store -> sleeve signal engines -> regime engine -> sleeve allocator -> portfolio constructor -> target book -> execution translator -> reconciliation/attribution.

Design principles
- Layer isolation: each layer has one responsibility and explicit inputs/outputs.
- Interpretable first: rules-based decisions before optimization.
- Point-in-time correctness over speed.
- Production isolation by default.

## 2. Production vs Alpha Stack Split

Production engine (current)
- Remains operational, unchanged except bug fixes/hardening.
- Uses existing workflow and canonical artifact contracts.

Alpha Stack program (new)
- Runs in separate namespace, config path, workflow path, and outputs.
- Generates independent research/shadow artifacts.
- Cannot write to production canonical files.

Required separation controls
- Distinct root output path: `outputs/alpha_stack/` (research/shadow only).
- Distinct run pointer: `outputs/alpha_stack/latest.json`.
- Distinct config root: `configs/alpha_stack/`.
- Distinct workflow entry points (no reuse of production write paths).

## 3. Core Layers and Contracts

Data layer
- Responsibility: ingest OHLCV, fundamentals, macro, breadth.
- Output contract: point-in-time raw datasets keyed by `(symbol, as_of_date, source_timestamp)`.
- Hard rule: all joins must honor `as_of_date`.

Feature layer
- Responsibility: transform raw data to reusable features.
- Output contract: feature store keyed by `(symbol, feature_date)` with provenance metadata.
- Hard rule: no forward-filled fundamentals past known filed date.

Sleeve layer
- Responsibility: compute sleeve scores, candidate sets, provisional target weights.
- Output contract per sleeve:
  - `score`
  - `rank`
  - `candidate_flag`
  - `provisional_weight`
  - `diagnostics`

Regime layer
- Responsibility: classify market regime with hysteresis.
- Output contract: regime context object containing trend/vol/breadth/macro states and confidence.

Allocator layer
- Responsibility: map regime context to sleeve budgets.
- Output contract: normalized sleeve weights summing to `<= 0.95` net long target.

Portfolio construction layer
- Responsibility: merge sleeve targets under caps and turnover rules.
- Output contract: final target book with symbol-level weights and constraint diagnostics.

Execution/audit layer
- Responsibility: translate target book to executable instructions and produce full attribution trail.
- Output contract: order intents, realized fills, recon status, sleeve/regime attribution.

## 4. Stage Promotion Ladder

Required progression
1. Design complete
2. Backtest validated
3. Shadow run validated
4. Paper promotion
5. Production cutover decision

Promotion rule
- No stage skip. Any failed gate reverts to prior stage until remediated.

## 5. Delivery Sequence (Baseline)

Phase 0
- Freeze and document production contract.

Phase 1
- Build DataStore and PIT data foundations.

Phase 2
- Implement regime state machine and hysteresis diagnostics.

Phase 3
- Upgrade trend sleeve.

Phase 4
- Rebuild value sleeve with PIT-safe fundamentals.

Phase 5
- Build attribution lab (IC/IR, decay, costs, turnover).

Phase 6
- Add allocator v1 (rules-based overrides + smoothing).

Phase 7
- Add quality sleeve.

Phase 8
- Add mean reversion sleeve.

Phase 9
- Shadow Alpha Stack for minimum 60 trading days.

Phase 10
- Promotion memo and cutover decision.

## 6. Deferred Capabilities

Future phases only
- Options overlays (covered calls, cash-secured puts, protective hedges)
- Event/news sleeves
- Optimization-led allocator search

These are intentionally deferred until v1 sleeves and allocator pass shadow gates.
