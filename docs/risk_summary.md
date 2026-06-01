# Risk Summary Artifacts

`scripts/build_risk_summary.py` builds deterministic, read-only risk and
concentration artifacts for the Research Review Packet.

Run:

```bash
.venv/bin/python scripts/build_risk_summary.py --date YYYY-MM-DD
```

Outputs:

```text
outputs/risk_summary/YYYY-MM-DD/
  risk_summary.json
  concentration_summary.json
  exposure_summary.json
```

The builder reads existing dated artifacts only. It prefers
`outputs/portfolio_history/YYYY-MM-DD/holdings_snapshot.json` for strategy
holdings, falls back to `outputs/shadow_candidates/YYYY-MM-DD/*.json`, then
falls back to Attribution Phase A
`outputs/attribution/YYYY-MM-DD/position_attribution.json`. It enriches sectors
from holding rows or `data/universe.csv`, and carries optional exposure metadata
from `outputs/attribution/YYYY-MM-DD/exposure_summary.json` when present.

Each artifact includes:

- `date`
- `strategies_covered`
- `position_count`
- `top_holdings`
- `max_position_weight`
- `top3_concentration`
- `top5_concentration`
- `sector_exposure`
- `missing_sector_coverage_count`
- `concentration_risk_level`
- `exposure_risk_level`
- `confidence`
- `reason_codes`
- `source_artifacts`

Missing holdings, weights, or sector data produce explicit `reason_codes`
instead of runtime failure. The builder does not fetch market data, submit
orders, change strategy selection, or mutate execution state.

The Research Review Packet consumes
`outputs/risk_summary/YYYY-MM-DD/risk_summary.json` first. If that artifact is
missing, it falls back to the older attribution/portfolio-history risk inputs.
