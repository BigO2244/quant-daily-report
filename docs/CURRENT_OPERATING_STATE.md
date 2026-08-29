# Current Operating State

This file is generated from `config/operations/operating_lane_registry.json`.
Runtime observations are written to `outputs/operating_state/current/`.
Do not hand-edit volatile lane claims.

Generated: 2026-08-29T14:00:00+00:00

| Lane | Strategy | State | Authority |
|---|---|---|---|
| SHADOW | Lyra, Orion, Orion Alpha, Polaris, Polaris Alpha | ACTIVE | PROVED |
| PAPER | Orion | ACTIVE | PROVED |
| LIVE | Lyra | ACTIVE_WITH_EXCEPTION | PROVED |
| LIVE | — | DISABLED (legacy FR-104) | PROVED |

Lyra Live remains funded and active. Its 2026-08-25 recurring attempt failed
closed before broker access because the Monday-dated source still had a Friday
effective date after a market-data hydration failure. The source was repaired
after the execution window; no late trade or automatic retry occurred.

A strategy may be observed in Shadow while operating in a separate capital
lane. The disabled legacy FR-104 lane does not disable Lyra Live. Broker evidence
governs positions and cash; narrative documents are presentation only.

