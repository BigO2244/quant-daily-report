# 2026-08-25 Lyra Live stale-effective-date block

Status: `EXPLAINED_FAIL_CLOSED_NO_BROKER_WRITE`

The Tuesday 09:35 recurring Lyra Live invocation consumed the Monday-dated path
`outputs/shadow_candidates/2026-08-24/caerus_lyra.json`. At execution time the
preserved pre-repair artifact had `trade_date=2026-08-24` but
`effective_trade_date=2026-08-21`, so the runner rejected target identity before
constructing a broker client or reading/writing the account.

- Pre-repair SHA-256: `5ca60caedae1288cb434bd6ebd9d1696d96d3586f4887945dd903f8da8b15014`
- Result: `BLOCKED`, `broker_write_performed=false`
- Root cause: the 2026-08-24 price hydration returned empty downloads and left
  the cache at 2026-08-21.
- Corrected source SHA-256: `ea279b63b74d4f3a5ac191bce878a6a49015dfe6e745b59ace73ee7dd8967e6c`
- Repair completed after the scheduled execution window. No late retry was
  authorized or inferred.

The code now distinguishes a stale/mismatched effective date from a strategy or
variant mismatch and persists every future blocked attempt as a hash-bound,
immutable artifact under the Lyra Live state root.

