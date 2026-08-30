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

## Deterministic builder

Build the reporting-only artifact explicitly; it is not yet scheduled or sent
by email:

```bash
python3 scripts/build_cio_daily_brief.py --report-date YYYY-MM-DD
```

The command reads the dated trading-integrity certification, current compiled
operating truth, the prior dated brief, and the canonical Alpha Lab research
projection when available. It writes an immutable, hash-bound bundle under
`outputs/governance/cio_daily_brief/YYYY-MM-DD/` containing `brief.json`,
`brief.md`, and `manifest.json`. Rebuilding identical inputs is idempotent;
different bytes at the same date fail closed.

Missing or malformed certification or operating truth degrades explicitly and
never changes capital authority. If the owner-ratified canonical research
projection is absent, or lacks family resolution timestamps, research
throughput is `UNAVAILABLE`, not zero. The builder never creates a parallel
research ledger and counts one terminal verdict per credible family, not
strategy wrappers, experiments, or parameter variants.

Email and cron integration remain separate, unimplemented steps. Any future
sender must consume the exact persisted bundle rather than recompute it.
