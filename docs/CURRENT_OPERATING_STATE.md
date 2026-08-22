# Current Operating State

As of 2026-08-22, Caerus has three concurrent equity lanes. A strategy may be
observed in Shadow while also operating in a separately governed capital lane;
the single `status` field in the research strategy registry is not a complete
system-wide execution-state model.

| Lane | Strategy | State | Authority |
|---|---|---|---|
| Shadow | Polaris, Orion, Lyra, Polaris_Alpha, Orion_Alpha | Active modeled comparison | Research registry and dated Shadow artifacts |
| PAPER | Orion | Active | PAPER control plane, immutable session packages, PAPER broker artifacts |
| Live | Lyra | Active, funded, recurring | Owner decision, Lyra Live runtime gates, schedule, immutable Live plan/result, Live broker state |

Lyra Live was owner-approved on 2026-08-19. Its initialization session on
2026-08-20 submitted five Alpaca Live buys; all five filled and post-trade
reconciliation was `ALIGNED`. The recurring cadence consumes the Monday-close
`h1_weekly_h6_top5` Lyra target and executes on the next expected Tuesday at
09:35 ET, subject to its independent fail-closed gates.

The legacy FR-104 Live pilot and generic Live v1 lane remain disabled. Those
facts do not disable the separate Lyra Live portfolio. Orion remains the sole
PAPER sleeve; Lyra continuing in Shadow does not make it Shadow-only.

The existing dashboard `live_pilot` section is scoped to legacy FR-104
artifacts and does not yet ingest the Lyra Live plan/result or broker ledger.
Its labels now say `Legacy FR-104`; its idle or blocked status must not be read
as Lyra Live status.

Canonical evidence:

- Owner authority: `docs/governance/decision_records/lyra_live_owner_decision_20260819.json`
- Current read-only operating observation: `docs/evidence/lyra_live_operating_truth_2026-08-22.json`
- Executor: `scripts/run_lyra_live_portfolio.py`
- Recurring wrapper: `scripts/cron_lyra_live_portfolio.sh`

Truth precedence for an operating-status claim is: explicit owner decision,
deployed policy and runtime gates, immutable execution artifacts, current broker
state, then reporting and narrative surfaces. A research-registry field cannot
negate higher-priority execution evidence outside its declared lane.
