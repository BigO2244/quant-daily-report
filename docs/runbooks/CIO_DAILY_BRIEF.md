# CIO Daily Brief

The brief is a decision surface, not a dashboard. It must fit on one screen and
must link to evidence rather than reproduce it.

## Required form

```text
CAERUS CIO BRIEF — YYYY-MM-DD

OPERATIONS: GREEN | YELLOW | RED
Trading Integrity Rate: N/20 (N%)
Exceptions: none | up to three control failures with artifact links

CAPITAL
Live: state and only meaningful change
Paper: state and only meaningful change
Shadow: state and only meaningful change

ALPHA
Improving: one independent hypothesis family, or none
Deteriorating: one independent hypothesis family, or none
New Shadow: one promotion, or none
Killed: one falsified hypothesis, or none

CIO ATTENTION (maximum three)
1. A decision, challenge, or explicit no-action statement
```

## Status rules

- `GREEN`: 20/20 sessions certified and no unresolved capital, broker, or
  research-integrity exception.
- `YELLOW`: no current-session execution integrity failure, but at least one
  bounded exception or evidence gap remains.
- `RED`: the current session is uncertified, lineage cannot be proved, broker
  reconciliation is not green, or a required control is unavailable. Trading
  fails closed.
- Unchanged state is omitted. Counts of builds, variants, and experiments are
  not operating metrics.
- Research throughput is terminal verdicts on credible independent hypothesis
  families per calendar month. Parameter variants do not count.
- Atlas must state a challenge when the evidence contradicts CIO intuition; an
  agreeable summary is not a substitute for the challenge.

