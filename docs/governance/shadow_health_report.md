# Daily Shadow health report

The September 11 remediation adds a read-only daily aggregation of the existing
strategy registry, dated Shadow evaluation/comparison, sleeve snapshots, feedback
artifacts, and advisory promotion-readiness artifact. It creates no registry,
performance series, promotion gate, or capital authority.

Run after the post-close Shadow refresh:

```sh
python3 scripts/build_shadow_health_report.py --trade-date YYYY-MM-DD --strict
```

The command writes `outputs/shadow_health/YYYY-MM-DD/shadow_health_report.json`
atomically and exits nonzero on `PIPELINE_FAILURE`. A daily hook must propagate
this failure into Shadow workflow health without coupling it to broker execution.
Local source availability is not deployment evidence; deployment remains a
separate owner decision.

The registry's existing `active_in_shadow_tracking` rule defines required
sleeves. All other registered non-benchmark entries receive an explicit
`NOT_EXPECTED` disposition, including Research-stage Phoenix, Cygnus,
Cassiopeia, and Argo. No absent Research performance is fabricated. Aquila's
PAPER authority does not imply Shadow tracking.

For each expected sleeve the report includes artifact checks and hashes,
modeled holdings, daily/cumulative returns and return convention, SPY benchmark,
observation counts, missing/invalid dates, modeled position changes, learning
readiness and gap, and advisory promotion readiness and reasons. Position changes
are not broker fills; unavailable prior positions produce unknown trades rather
than zero. Existing source observation counts are retained separately from
counts independently verified through dated evaluations.

The canonical observation window defaults to May 12, 2026, constrained further
by each registry entry's observation start. XNYS calendar sessions define gaps;
weekends and exchange holidays do not. `--observation-start` supports explicit
bounded diagnostic windows, which must not be represented as full-history
certification. Source artifact dates must match their directory. Missing expected
sleeves, dates, invalid numbers, absent benchmark evidence, unavailable modeled
trades, and missing required artifacts are pipeline failures. Readiness is blocked
when a sleeve has incomplete pipeline evidence. Existing advisory readiness is
preserved as source evidence; `promotion_authorized` is always false.

This surveillance report does not certify historical return methodology or
research alpha. Existing provenance and promotion governance remain binding.
No price hydration, backfill, source repair, deployment, model promotion, or
capital increase is performed by this command.

## Forensic implementation note

Before modification, inspection found registry-aware portfolio learning reporting,
scorecard health, and separate promotion-readiness diagnostics. They provided most
metrics but no single dated mandatory report with an explicit failure per missing
expected sleeve/artifact and missing session. This change reads their canonical
source artifacts rather than replacing their calculations. The local checkout
has no September 11 Shadow artifacts, so its September 11 report fails explicitly;
current runtime evidence must be materialized before judging production health.
