# Adaptive Shadow v1 — owner decision brief

Status: `PENDING_OWNER_APPROVAL`. This proposal is Shadow-only and cannot
produce an executable target or change Paper, Live, promotion, or capital.

## Recommendation

Approve a conservative 20-session Shadow observation beginning only after both
Polaris and Lyra have at least 60 valid causal sessions, 20 consecutive green
lineage sessions, fresh signals, and green capacity/liquidity gates. Orion is
excluded because the owner restricted it to the frozen legacy comparison role;
benchmarks and Research sleeves are also excluded.

The initial modeled sleeve mix is 50% Polaris / 50% Lyra. Each sleeve is bounded
to 40–60%, so the allocator can tilt only 10 percentage points from neutral per
session. Missing or stale evidence never causes renormalization; the runner
holds the static Polaris Shadow baseline and escalates.

## Objective and limits

The normalized objective is 45% expected SPY-relative return, less 25% forecast
volatility, 15% 95% expected shortfall, 10% turnover cost, and 5% holdings
overlap. All inputs must be causal and end before the decision as-of.

The principal limits are 10% maximum one-way allocation turnover per session,
25% over 20 sessions, 70% maximum pairwise holdings overlap, 25% maximum
aggregate security weight, 40% maximum industry weight, order size below 1% of
ADV, liquidation within one day at 5% ADV, and estimated capacity at least 20
times reference capital. Leverage and shorting are forbidden.

## Evidence returned for later review

After 20 completed adaptive Shadow sessions with zero unresolved lineage or
reconciliation breaks, the system returns modeled NAV, risk, cost, turnover,
overlap, concentration, capacity, every constraint decision, and comparison to
the static Polaris Shadow fallback. Any Paper proposal requires a new immutable
owner decision; this approval would not grant it.

The exact machine candidate is
`adaptive_shadow_v1_policy_candidate.json`. Its content hash is recorded inside
that artifact as
`0ee486a14972fe1c3a16c19d5f275c7dafc6d1c06405bc4790d088d85749d46e`.
