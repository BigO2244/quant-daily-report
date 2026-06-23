# Exposure-Matched Framework

Date: 2026-06-22
Program: Decision-Grade PIT Research Infrastructure Completion
Phase: 5 - Exposure-Matched Framework
Governance label: RESEARCH_ONLY / NON_EXECUTIONAL
Runtime impact: none
Gate result: PASS

## Objective

Add reusable methodology to separate:

- sizing effect; and
- deployment/cash-drag effect.

## Artifact

`research/exposure_matched.py`

## Methods

| Function | Purpose |
| --- | --- |
| `daily_gross_exposure` | Compute daily deployed gross exposure |
| `exposure_match_weights` | Scale candidate daily gross exposure to baseline daily gross exposure |
| `exposure_metrics` | Report average gross, cash, holdings count, and HHI |
| `portfolio_returns` | Compute daily return contribution from weights and forward returns |
| `attribution_decomposition` | Decompose candidate-vs-baseline into sizing and deployment effects |

## Interpretation

- Matched exposure isolates sizing/security-selection differences at the same daily gross exposure.
- Unmatched exposure preserves original deployment and therefore includes cash-drag/deployment effects.
- Deployment effect = unmatched candidate return minus matched candidate return.
- Sizing effect = matched candidate return minus baseline return.

## Validation

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -m py_compile research/exposure_matched.py

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -m pytest -p no:cacheprovider \
  Tests/test_exposure_matched.py \
  Tests/test_canonical_decision_tape.py \
  Tests/test_replay_certification.py
```

Result:

```text
11 passed
```

## Gate

PASS. Future allocator studies can independently measure sizing and deployment/cash-drag effects.
