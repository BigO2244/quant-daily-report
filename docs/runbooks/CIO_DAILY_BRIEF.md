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
- `YELLOW`: no current-session transaction-lineage or broker-integrity failure,
  but at least one bounded exception, historical certification gap, or
  universe-pedigree limitation remains. No scaling occurs.
- `RED`: current-session decision/artifact/execution lineage cannot be proved,
  broker reconciliation is not green, or another applicable execution safety
  gate fails. The affected lane fails closed; other independently governed
  lanes are not inferred to be halted.
- The certification window is an assurance metric, not capital authority. A
  retrospective RED window does not by itself halt an active lane.
- Unchanged state is omitted. Counts of builds, variants, and experiments are
  not operating metrics.
- Research throughput is terminal verdicts on credible independent hypothesis
  families per calendar month. Parameter variants do not count.
- Atlas must state a challenge when the evidence contradicts CIO intuition; an
  agreeable summary is not a substitute for the challenge.
