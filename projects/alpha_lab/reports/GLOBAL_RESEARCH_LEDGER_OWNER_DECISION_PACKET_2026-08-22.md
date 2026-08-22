# Global Research Ledger — Owner Decision Packet

Date: 2026-08-22
Classification: `RESEARCH_ONLY_NON_EXECUTIONAL`
Status: `OWNER_RATIFICATION_REQUIRED_BEFORE_CANONICAL_GCP_WRITE`

## Verified legacy inventory

- 66 data-gate attempts.
- 60 `BLOCKED_DATA` outcomes.
- 6 `READY_FOR_FROZEN_EVALUATOR` outcomes.
- 8 statistical model variants: HYP-2026-006 (3), HYP-2026-007 (2), and
  HYP-2026-008 (3).
- 8 prespecified robustness records, each covering the exact 2-window ×
  2-cost × 2-terminal-assumption grid (64 cells total).
- 0 challenge-period reads and 0 challenge confirmations.
- HYP-2026-006, HYP-2026-007, and HYP-2026-008 are honest non-positive
  validation evidence. They are not data-blocked, statistically confirmed, or
  eligible for handoff.

The importer does not turn data gates, windows, cost scenarios, or robustness
cells into extra statistical trials. It does not fabricate p-values or locked
holdout evidence.

The canonical write path now fails closed unless that complete census is still
present. It also performs the entire append plan against a scratch ledger before
publication, then atomically creates the canonical ledger without overwriting an
existing path. A bad family definition or budget cannot strand an irreversible
partial migration, and an existing partial ledger is reported for manual
reconciliation rather than silently repaired.

## Recommended family decision

Start with one family generation per registered hypothesis. This is the
conservative mapping because the existing hypotheses were frozen separately
and often use different mechanisms or primary metrics. Merge hypotheses only
when the owner can affirm that they test the same economic mechanism under one
shared family-wide error budget. Similar tickers, data sources, or technique
labels are not sufficient grounds for a merge.

For every owner-ratified family, freeze:

1. family name and economic mechanism;
2. constituent hypothesis IDs and parent lineage;
3. primary metric and benchmark;
4. expected direction, null value, and minimum economic hurdle;
5. frozen primary variant ID;
6. maximum statistical trial units and nested-selection budget;
7. verified within-family method and family alpha; and
8. exploratory wave membership, correction method, and alpha/q.

New families should use the implemented Holm-Bonferroni within-family engine.
Legacy Romano-Wolf contracts may be preserved as historical policy, but they
remain blocked from decision-grade status until a joint-resampling verifier is
implemented.

The imported legacy wave is permanently descriptive. Owner ratification records
its provenance and family accounting; it cannot make the legacy evidence
promotion-eligible. Promotion would require a newly frozen, policy-compliant
wave and new evidence collected under that wave.

## Ratification boundary

The canonical importer requires a reviewed JSON ratification artifact that:

- names Brett Olson as owner;
- records `RATIFY_GLOBAL_RESEARCH_LEDGER_MIGRATION`;
- binds the fresh GCP audit receipts;
- resolves all 13 registered hypotheses to explicit family IDs;
- supplies every substantive family-definition field;
- freezes the legacy wave and challenge-epoch identities; and
- carries its canonical hash, computed with `artifact_sha256` omitted.

The importer reloads that exact file, reruns the canonical GCP audit, compares
the fresh inventory with the reviewed inventory, and fails on any conflicting
existing event. No canonical ledger write has been performed as part of this
packet.

Owner identity, preregistration authorship, research-author identity, and
independent-reviewer separation are not yet backed by an authenticated identity
service. The ledger therefore keeps authenticated owner ratification,
preregistration, and independent-review gates closed even when the referenced
artifacts are present and hash-valid. This is an intentional fail-closed
boundary, not missing research evidence.

## What ratification does not authorize

Ratification does not approve a model, open a challenge epoch, create a Shadow
handoff, alter Paper or Live trading, or submit an order. The imported legacy
families remain non-decision-grade because corrected family inference, a
single-use locked challenge, and an independent final review do not exist.
