# Trading integrity certification

## Purpose

Caerus certifies the latest 20 expected XNYS sessions against six binary
controls. A session passes only when all six controls pass; missing or ambiguous
evidence fails closed.

1. Required data is fresh and the universe is PIT-valid and decision-grade.
2. The model computation actually reran, proven by current and prior causal
   lineage plus changed upstream market, feature, or rank hashes.
3. The decision is the output of that certified computation and the same
   lineage is preserved across the decision surfaces.
4. The precompute bundle is sealed and every declared artifact hash verifies.
5. The executor used the hash-bound approved package and exact-plan path; a
   downstream target rebuild or missing source hash fails.
6. Paper-lane and broker reconciliation match intended positions and cash with
   no unexplained difference or manual intervention.

Trading Integrity Rate is:

`certified sessions / expected sessions`

The target is 20/20 sessions and 120/120 control observations. This is not an
average-quality score: 119/120 still means the window is RED.

## Run

```bash
python3 scripts/certify_trading_integrity.py \
  --repo-root . \
  --through-date YYYY-MM-DD \
  --sessions 20 \
  --output outputs/governance/trading_integrity/YYYY-MM-DD.json
```

The command is read-only unless `--output` is supplied. It exits zero only for
a fully GREEN window.

## Interpretation

- An unchanged target is not itself stale. It is certifiable when current
  market/features/ranks differ from the prior computation and the target is a
  deterministic output of the governed selection rule.
- `legacy_current_universe` and `NON_DECISION_GRADE_UNIVERSE` fail the Data/PIT
  control. Fresh prices cannot compensate for non-decision-grade membership.
- A correct fail-closed no-trade day is operationally safe, but it is not a
  certified session when the upstream decision or artifact did not exist.
- Historical sessions are never retroactively upgraded without their original
  immutable evidence.

## CIO brief contract

The daily brief should consume only this certification artifact for Operations:

```text
OPERATIONS — GREEN|YELLOW|RED
Trading integrity: <certified>/<expected>
Yesterday: PASS|FAIL (<passed controls>/6)
Exceptions: <control names and exact reason codes>
```

Capital, Alpha, and at most three CIO Attention items remain separate. A
dashboard or narrative may summarize this artifact but may not reinterpret a
failed binary control as green.
