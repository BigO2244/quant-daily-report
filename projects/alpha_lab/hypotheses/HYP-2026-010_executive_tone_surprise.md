# HYP-2026-010 — Executive and Managerial Tone Surprise

State: `FROZEN`

Experiment: `EXP-2026-0010`

Classification: `ALPHA_CANDIDATE`

## Question and mechanism

Does an unexpected change in executive language, relative to the same
executive's own history and contemporaneous fundamentals, predict post-event
returns? Managers may reveal private information through changes in
uncertainty, specificity, evasiveness, and forward-looking tone.

## Point-in-time data contract

Require licensed or permissioned historical transcripts with speaker identity,
role, prepared-versus-Q&A segmentation, publication timestamp, correction
lineage, stable text hash, and issuer/security mapping. SEC acceptance or
headline sentiment is not a transcript substitute. The current checkout lacks
this asset and must stop `BLOCKED_DATA`.

## Frozen experiment

- Discovery 2012–2018; validation 2019–2024; untouched challenge
  2025-01-01 through 2026-06-30.
- Primary: within-executive tone-change residual after contemporaneous earnings
  surprise, guidance, sector, and market controls.
- Diagnostics: Q&A-only uncertainty surprise and specificity surprise.
- Horizon: next-session open through 60 sessions.
- Primary metric: `validation_60d_factor_residual_car_after_costs`.
- Maximum variants: three.
- Costs: 15 bps one-way base and 30 bps stress.
- Holm 5% across eight families; regimes secondary only.

## Pass and kill criteria

Pass only with exact PIT text lineage, positive corrected locked-validation
CAR, stability across executives/years, positive stress-cost return, and no
single issuer over 20% of active return. Kill on revision leakage, speaker
identity failure, model-version drift, non-positive validation, or
concentration.

## Freeze record

- Frozen by: Brett Olson, CIO, via explicit `FREEZE HYPOTHESIS`; drafted by Codex.
- Frozen at: 2026-07-23, America/New_York.
- Spec hash: `sha256:3159bd8d5eda2292d9f9dd1ae58395d22655cfb8ac4cc8e71805a7353b22dd16` (all bytes before `## Freeze record`).
- Code hash: no HYP-2026-010 evaluator existed at freeze; repository baseline `da5add9`.
- Data snapshot/hash: `NOT_ACQUIRED`; certified transcript history is required.
