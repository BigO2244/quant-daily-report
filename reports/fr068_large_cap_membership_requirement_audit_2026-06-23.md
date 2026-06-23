# FR-068 Large-Cap Membership Requirement Audit

Date: 2026-06-23
Scope: Research-only audit of the `PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP` certification requirement
Runtime impact: none

## Executive Finding

The underlying requirement is valid: decision-grade replay needs a date-effective large-cap membership definition that does not use current size status to reconstruct historical candidate sets.

The current requirement name and implementation are too narrow. `PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP` encodes one acceptable implementation, not the only way to mitigate the research risk. The certification should require PIT-valid, survivorship-free, security-id keyed large-cap membership lineage, and should allow multiple certified membership methods.

Recommendation: replace requirement.

## A. Requirement Justification

### What The Current Gate Checks

The current replay panel builder classifies membership scale precision by `scale_source`:

- `scale_source == marketcap` -> `PIT_EXACT_SCALE`
- `scale_source == scalemarketcap` -> `PIT_APPROXIMATE_SCALE`
- `PIT_APPROXIMATE_SCALE` emits `PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP_MISSING`

The formal certifier turns that warning into a failure when decision-grade scale is required. This means certification currently treats Sharadar DAILY-style numeric market cap as the only passing scale source.

Primary code references:

- `research/canonical_replay_panel.py::_classify_scale_precision`
- `research/replay_certification.py::certify_security_id_price_panel`
- `research/pit_large_cap_family.py::classify_large_cap`

### Exact Research Risk Mitigated

The requirement mitigates universe-definition look-ahead and size survivorship contamination.

The specific risk is not bad pricing or ticker identity. It is that a current size label can decide whether a security belonged in a historical large-cap candidate universe. If a 2026 large/mega-cap classification is used for 2014 membership, the replay can:

- include securities before they were large enough to be in the intended sleeve universe;
- include later winners whose current size is partly the result of the post-decision return path being studied;
- exclude securities that were historically large but later shrank, merged, delisted, or disappeared;
- distort rank buckets, sleeve selections, target weights, turnover, cash drag, and allocator comparisons before performance attribution begins.

This matters directly to conviction allocation and sleeve promotion research. Those studies compare outcomes across ranked ideas and allocation variants. If the candidate set is contaminated, apparent stock-selection alpha or concentration benefit can be an artifact of the historical membership definition rather than the model or allocator.

### Why Current `scalemarketcap` Is Insufficient

The existing `caerus_large_cap` artifact is wired correctly and resolves through `Universe(as_of_date, "caerus_large_cap")`, but its size source is current Sharadar `scalemarketcap`.

That source is useful as a transparent approximation, but it is not date-effective. It does not prove that a security crossed the large-cap threshold as of each historical decision date. Prior certification correctly marks it as `PIT_APPROXIMATE_SCALE`.

## Existing FR-068 Artifact Coverage

| Risk | Existing FR-068 mitigation | Decision-grade for this risk? |
|---|---|---|
| Static `data/universe.csv` survivorship | `Universe(as_of_date)` and PIT security master exist; no certified replay path should rely on `data/universe.csv` | Yes for existence universe |
| Delisted security omission | PIT security master includes delisted securities; SEP cache exists for the current large-cap family | Mostly yes for the current family, subject to delisting-return/action caveats |
| Ticker identity drift | Replay panel and decision tapes are security-id keyed; ticker is display/source metadata | Yes |
| Price survivorship in current family | Sharadar SEP adjusted-close cache covers the 1,600-member current large-cap family | Mostly yes for price rows in that family |
| Large-cap membership as of date | Existing family uses `scalemarketcap`, a current/vendor size bucket | No |
| Full PIT size opportunity set | DAILY market-cap panel, complete shares-outstanding reconstruction, or equivalent membership evidence is not available locally | No |

Conclusion: FR-068 already mitigates security-existence survivorship, ticker-key risk, and much of the delisted-price coverage risk for the current family. It does not fully mitigate historical large-cap membership contamination.

## B. Alternative Architectures

### Option 1: Daily Numeric Market-Cap Panel

Build `caerus_large_cap` from a survivorship-free daily market-cap panel for the full PIT security master.

This is the current implementation target. It is strong because it directly answers the intended question: which securities were above the large-cap threshold on each decision date?

Certification conditions:

- security-id keyed or deterministically mapped to security_id;
- includes active and delisted securities;
- uses rows only on or before the decision date;
- has coverage diagnostics and fail-closed thresholds;
- records source hashes and lineage.

Verdict: valid implementation, but not uniquely required.

### Option 2: PIT Index-Membership Events

Use historical constituent add/remove events for a relevant benchmark universe, such as S&P 500, Russell 1000, or another explicitly approved large-cap proxy.

This can satisfy the same objective if the research question is framed as replay over that benchmark universe rather than all securities above a numeric market-cap threshold.

Certification conditions:

- effective dates for additions and removals;
- delisted and acquired names retained historically;
- source lineage and coverage checks;
- explicit statement that the universe is index membership, not numeric market-cap rank.

Verdict: valid for index-universe research; not equivalent if the sleeve mandate is numeric large-cap exposure.

### Option 3: PIT Shares Outstanding Times Daily Close

Reconstruct market cap as:

`market_cap = PIT shares outstanding * unadjusted close`

This can satisfy the same objective if shares outstanding are complete, date-effective by filing or vendor effective date, split-adjusted correctly, and available for active and delisted securities across the full PIT security master.

Current local FR-068 data does not meet those conditions. The existing filing-based coverage audit only reconstructed about 9.7% to 11.8% of the already-narrow current family checkpoints, so it cannot certify the canonical family now.

Verdict: conceptually valid, currently unavailable.

### Option 4: Actual Historical Decision-Time Artifacts

If historical production or paper artifacts contain the exact candidate universe, ranks, scores, and target weights used at each decision date, those artifacts can be the membership authority for replaying actual historical decisions.

This satisfies allocator replay for names that were actually considered. It may not satisfy counterfactual research that asks whether omitted historical large-cap names should have entered the sleeve, because the artifact only contains the historical decision set.

Certification conditions:

- immutable decision-time artifacts;
- generated before or at the decision date;
- security-id mapping;
- complete rank/score/target lineage;
- no regenerated current-universe joins.

Verdict: valid for actual-decision replay; incomplete for full counterfactual universe reconstruction.

### Option 5: Reconstitution-Date Membership

Use a date-effective membership file that updates on scheduled reconstitution dates rather than every trading day.

This can satisfy the objective if the sleeve or benchmark explicitly reconstitutes on that cadence. Daily replay can then carry the last certified membership forward until the next effective membership date.

Certification conditions:

- the cadence is pre-specified;
- effective-date history is PIT;
- no current constituent backfill;
- lineage shows which membership file controlled each trade date.

Verdict: valid if it matches the intended universe policy; weaker if the target is daily numeric market-cap thresholding.

### Option 6: Full PIT Common-Stock Universe

Use all PIT common stocks with listing/delisting filters and no large-cap filter.

This avoids current-size contamination, but it changes the research question. It turns a large-cap sleeve replay into an all-cap replay and can change liquidity, capacity, model behavior, and portfolio risk.

Verdict: valid as a robustness study, not a replacement for large-cap certification.

### Option 7: Current `scalemarketcap`

Use current Sharadar `scalemarketcap` as the large-cap filter.

This is deterministic and includes delisted names that still carry the scale label, but it is not date-effective and can project later size status backward.

Verdict: acceptable for diagnostics only; not decision-grade.

## C. Recommendation

Replace the requirement.

Keep the decision-grade gate that rejects current-scale `scalemarketcap` as sufficient evidence. Do not relax certification to allow the current family to pass.

Replace the implementation-specific blocker:

`PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP_MISSING`

with a broader requirement:

`PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED`

The replacement requirement should certify the objective, not the data vendor field. Acceptable certified methods should include:

- daily numeric market cap from Sharadar DAILY or another survivorship-free vendor;
- PIT index-membership event history when the study explicitly uses an index universe;
- complete PIT shares-outstanding times unadjusted close reconstruction;
- immutable historical decision-time candidate tapes for actual-decision replay;
- scheduled reconstitution membership when that cadence is the declared universe policy.

The certifier should record the membership method explicitly, for example:

- `PIT_DAILY_MARKETCAP`
- `PIT_INDEX_MEMBERSHIP`
- `PIT_SHARES_PRICE_RECONSTRUCTION`
- `PIT_DECISION_TAPE`
- `PIT_RECONSTITUTION_MEMBERSHIP`
- `CURRENT_SCALE_APPROXIMATION`

Only the first five should be eligible for decision-grade PASS, and only when coverage, survivorship, security-id keying, no-look-ahead, and lineage checks pass. `CURRENT_SCALE_APPROXIMATION` should remain PARTIAL or FAIL for allocator and promotion certification.

## Final Classification

The current requirement protects a real and material research risk, but the exact DAILY-marketcap formulation is an implementation choice.

Final recommendation: replace requirement.
