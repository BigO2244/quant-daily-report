# FR-068 Certification Risk Matrix

Date: 2026-06-23
Scope: Governance review / certification architecture only
Runtime impact: none

## Summary

FR-068 is not a single binary control. Several risks are already materially mitigated, while large-cap membership remains unresolved under the current artifact. The current certification framework correctly blocks decision-grade promotion, but the blocker should be reframed from `PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP_MISSING` to a method-neutral date-effective membership requirement.

## Risk Matrix

| Risk | Severity if unmitigated | Current mitigation | Remaining exposure | Current status |
|---|---|---|---|---|
| A. Security existence survivorship | Severe | PIT security master from Sharadar TICKERS; `Universe(as_of_date)`; listing/delisting dates; no `data/universe.csv` fallback in certified replay | Security master coverage and source lineage still need routine validation, but the core existence control is present | PASS |
| B. Ticker identity contamination | High | `security_id` framework; canonical replay panel keyed by `date, security_id`; ticker recorded as display/source metadata | Historical ticker mapping and related-ticker edge cases can still affect non-canonical studies | PASS for canonical replay |
| C. Membership look-ahead | Severe | Resolver can load `caerus_large_cap` by date; current artifact has membership intervals | Size criterion is current `scalemarketcap`, not date-effective; can include/exclude securities based on future/current size status | FAIL |
| D. Current-family contamination | Severe | Current family includes 1,600 securities and 354 delisted names; SEP price hydration covers the current family | The family is still a current-scale slice, not a reconstructed historical large-cap opportunity set from the full PIT master | FAIL |
| E. Pricing contamination | High | Sharadar SEP adjusted-close cache; canonical price panel has source hashes, zero duplicate `date, security_id` keys, and no missing SEP files for current family | Terminal delisting return/action enrichment is not fully certified; coverage is only for the current family, not a future replacement membership universe | PARTIAL |

## Detailed Findings

### A. Security Existence Survivorship

Severity: Severe.

Current mitigation: FR-068 built a PIT security master with 20,618 securities, including 14,790 delisted. `Universe(as_of_date)` uses listing and delisting dates rather than the legacy static `data/universe.csv`.

Remaining exposure: routine lineage validation remains necessary, but the principal current-survivor risk is mitigated for certified paths.

Status: PASS.

### B. Ticker Identity Contamination

Severity: High.

Current mitigation: Canonical replay artifacts use `security_id`; ticker is not the identity key. The canonical price panel reports zero duplicate `date, security_id` rows.

Remaining exposure: non-canonical research can still regress to ticker-keyed joins if not blocked. Certification should continue rejecting ticker-keyed panels and prohibited paths.

Status: PASS for canonical replay.

### C. Membership Look-Ahead

Severity: Severe.

Current mitigation: the resolver and artifact path are deterministic and date-aware. This proves wiring, not PIT-exact membership logic.

Remaining exposure: `scale_source=scalemarketcap` is a current/vendor size bucket. It can project future/current large-cap status backward into earlier dates.

Status: FAIL.

### D. Current-Family Contamination

Severity: Severe.

Current mitigation: the current family is larger and less survivor-only than `data/universe.csv`, and it includes delisted members.

Remaining exposure: it is still selected using current scale. It may omit securities that were historically large and include securities that only later became large.

Status: FAIL.

### E. Pricing Contamination

Severity: High.

Current mitigation: the current family has SEP adjusted-close coverage and source hashes. Certification rejects prohibited price paths.

Remaining exposure: terminal delisting return/action handling is not fully certified. Future certified membership methods may require additional pricing hydration beyond the current 1,600-security family.

Status: PARTIAL.

## Matrix Conclusion

FR-068 should not be classified as fully decision-grade yet. It should be classified as PARTIAL:

- PASS: security existence, canonical identity, resolver wiring
- PARTIAL: pricing lineage for current family
- FAIL: date-effective large-cap membership

The exact remaining blocker is:

`PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED`

The old blocker name, `PIT_EXACT_LARGE_CAP_DAILY_MARKETCAP_MISSING`, should be retired or treated as one implementation-specific sub-blocker.
