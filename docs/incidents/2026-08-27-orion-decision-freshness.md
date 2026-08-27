# Orion decision-freshness incident — 2026-08-27

## Summary

The repeated Orion holdings were not evidence of a frozen feature, rank, or
target computation. A replay from the production price panel shows that market,
feature, and rank hashes changed on every completed session while the
rank-decay model legitimately retained the same five holdings.

The operational failure occurred one stage earlier. The 2026-08-24 post-close
price hydration returned empty downloads for the required universe, left the
cache at 2026-08-21, and produced a `PARTIAL` status. No decision-eligible
2026-08-24 Orion source was created, so the 2026-08-25 morning precompute failed
closed. Hydration and source production recovered later on 2026-08-25.

The repair closes two latent integrity gaps exposed by the incident:

- global cache freshness could mask missing or stale individual symbols; and
- a dated Orion artifact did not prove the complete price-to-target causal
  computation or bind morning precompute to a successful post-close producer.

## Preserved evidence

The production VM state and affected artifacts were captured before repair at:

`outputs/incidents/2026-08-27_orion_stale_target/`

The VM and GitHub `main` were both clean at
`48e5f362557398e565c76580ce294cee37fecb4c` before changes.

## Completed-session replay evidence

The table below was regenerated offline from a copy of the production price
panel. Hashes are SHA-256 prefixes. The 2026-08-27 morning decision consumes the
latest completed XNYS session, 2026-08-26; a 2026-08-27 close does not exist
before that session completes.

| Effective session | Market hash | Feature hash | Rank hash | Target hash | Top five ranks |
|---|---|---|---|---|---|
| 2026-08-20 | `0a9e45a524a0` | `087aac24ff53` | `cf53032d2944` | `e4e392e2305c` | MU, WDC, STX, DELL, INTC |
| 2026-08-21 | `f6020345f7a9` | `dfcd3d3a74d0` | `ab5b86bf25f3` | `e4e392e2305c` | MU, WDC, STX, INTC, DELL |
| 2026-08-24 | `90dfd0de6a1c` | `a3774b296a30` | `551ee4d380c3` | `e4e392e2305c` | MU, WDC, STX, DELL, INTC |
| 2026-08-25 | `bf0f9a84a80d` | `562f700fdaba` | `fb6cada9568b` | `e4e392e2305c` | MU, WDC, STX, DELL, INTC |
| 2026-08-26 | `ac64ecf3e5c6` | `7ea92bdff334` | `2baae408895a` | `e4e392e2305c` | MU, WDC, STX, DELL, INTC |

The target remained WDC, STX, MU, LRCX, and INTC at 20% each. LRCX was rank 8
on 2026-08-26 and was retained by the governed rank-decay exit rule; the other
four incumbents were ranks 1, 2, 3, and 5. This is a legitimate unchanged
target supported by freshly recomputed upstream evidence.

## Repair

- Validate current-session and exact 1/3/21/126/252-session anchors for every
  required symbol.
- Retry individual symbols when a successful batch response omits them.
- Emit a full causal lineage object and six-stage diagnostics for market data,
  normalized panel, features, full rank history, current rank table, and target
  weights.
- Require an immediately prior lineaged Orion source; missing or legacy prior
  evidence fails closed. Production migration uses an explicitly ineligible,
  exact-next-session `PRIOR_LINEAGE_TRUST_ANCHOR` before creating the current
  readiness marker.
- Require a clean, committed runtime and a successful, hash-bound post-close
  readiness marker before morning precompute.
- Preserve and independently cross-check lineage through the sealed PAPER
  target, handoff, contract, bundle validation, and operator email.
- Report `STALE_DECISION_SUSPECTED` whenever causal freshness cannot be proven.

## Rollback

Rollback must use the normal deployment script to return the VM to the last
known-good merged Git SHA. Do not hand-edit production Python. Removing the
repair also removes the readiness contract, so PAPER precompute must remain
blocked until the deployed code and scheduler contract are reconciled.
