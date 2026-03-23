# Alpha Stack Regime Allocator Specification

## Current Intent

The regime layer is intended to classify market state across four dimensions:

- trend
- volatility
- breadth
- macro

That regime context is intended to feed portfolio construction and sleeve weighting.

## Current Status

- The four-dimension classifier is part of the architecture.
- A full state machine with explicit thresholds and hysteresis rules is still planned work.
- Current baseline allocation remains static at 80% Sleeve 1 / 20% Sleeve 2 in `core/portfolio_alloc.py`.

## Near-Term Goal

Build a rules-based regime state machine before any optimization-led allocator work.

Required qualities:

- explicit thresholds
- explicit state boundaries
- deterministic transitions
- hysteresis to reduce whipsaw
- operator-readable outputs

## Interaction With Portfolio Construction

Once implemented, the regime layer should:

- classify the daily state
- adjust sleeve budgets
- preserve explicit reason codes for overrides
- remain separate from execution logic

## Planned Sequence

1. Add FRED macro inputs.
2. Define threshold logic for trend, volatility, breadth, and macro.
3. Add hysteresis and state persistence rules.
4. Introduce regime overrides on top of the current static allocator.

## Known Gaps

1. No explicit regime thresholds are finalized in code.
2. No hysteresis-driven state transitions are finalized in code.
3. No promotion-grade regime attribution outputs are yet available.
