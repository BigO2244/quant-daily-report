# Research Review Packet

The Research Review Packet is a read-only operating report for Caerus model
oversight. It consolidates attribution, decision attribution, signal outcome,
execution, risk, regime, model review, and data-freshness artifacts into one
deterministic JSON, Markdown, and HTML packet.

Run manually:

```bash
.venv/bin/python scripts/build_research_review_packet.py --date YYYY-MM-DD
```

When `--date` is omitted, the builder selects the latest date that has both
Attribution Phase A and Phase B artifacts. If no core attribution artifacts are
available, it uses today's date and emits explicit reason codes.

Outputs:

```text
outputs/research_review/YYYY-MM-DD/
  cio_briefing.json
  research_review.json
  research_review.md
  research_review.html
  research_review_sources.json
  research_review_summary.json
```

The builder does not fetch data, call brokers, submit orders, change signals,
or alter portfolio construction. Missing optional artifacts are reported as
diagnostics, not treated as runtime failures.

## CIO Briefing

Each packet includes a deterministic CIO briefing in `cio_briefing.json`,
`research_review.json`, `research_review_summary.json`, and as the lead section
of the Markdown/HTML reports. The briefing is rule-based and uses existing
packet artifacts only.

The CIO briefing includes:

- CIO takeaway narrative.
- What changed since the prior available review packet.
- 30-second read.
- Strategy leaderboard.
- Attribution interpretation.
- Signal evidence assessment.
- Risk/blocker assessment.
- Primary and secondary CIO recommendations.

Prior-review comparison searches `outputs/research_review/YYYY-MM-DD/` for the
latest packet date before the current packet date. If none exists, it emits
`prior_review_missing` and keeps the packet build successful.

## Source Contract

Primary sources:

- `outputs/attribution/YYYY-MM-DD/attribution_summary.json`
- `outputs/decision_attribution/YYYY-MM-DD/strategy_decision_summary.json`
- `outputs/decision_attribution/YYYY-MM-DD/signal_outcome_summary.json`
- `research/model_review_YYYY-MM-DD.md` or `outputs/model_review/YYYY-MM-DD/`
- execution health artifacts under `outputs/health/`, `outputs/daily/`, or
  `outputs/latest_execution_summary.txt`
- canonical risk and concentration artifacts under
  `outputs/risk_summary/YYYY-MM-DD/`
- legacy risk fallback artifacts under `outputs/attribution/YYYY-MM-DD/`
- regime artifacts under `outputs/attribution/YYYY-MM-DD/` and
  `outputs/vix_regime/regime_current.json`

The report is useful with partial sources. Missing artifacts emit reason codes
such as `missing_attribution`, `missing_decision_attribution`,
`missing_model_review`, `missing_execution_summary`, `missing_risk_summary`,
and `missing_regime_summary`.

## VM Wrapper

Manual VM workflow:

```bash
scripts/run_research_review_packet.sh --date YYYY-MM-DD
```

The wrapper:

1. Resolves and activates the repo runtime virtualenv.
2. Builds position attribution for the selected date.
3. Runs the canonical price hydration script if the Phase A summary reports a
   stale price source.
4. Rebuilds position attribution after hydration when hydration was attempted.
5. Builds decision attribution.
6. Builds the research review packet.
7. Prints the output paths.

Hydration is best-effort in the wrapper. A hydration or optional artifact gap is
reported in the packet instead of hiding the issue.

Suggested cron line only; do not install unless explicitly requested:

```cron
# Weekly Monday 9:15 ET research review packet
15 9 * * 1 cd /home/brettolson/quant-daily-report && scripts/run_research_review_packet.sh >> logs/research_review_packet.log 2>&1
```

Set `RESEARCH_REVIEW_SKIP_HYDRATION=1` to force the wrapper to skip cache
hydration and build the packet from existing artifacts only.
