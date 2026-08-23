# QS-004 — Phase 2 Data Foundry Authority Draft

**Status:** `DRAFT_NONCANONICAL / PHASE1_BLOCKED / NO AUTHORITY GRANTED`

This is a decision template, not approval, execution, procurement, contact, or
cloud initialization. No field below is a substitute for Brett's explicit
authorization.

## Preconditions to fill and sign later

- Phase 1 final release and signed-ledger publication state: `<exact approved head and receipt>`
- DABL v2 structural schema-manifest SHA-256:
  `a70874492b8bc58f558e3694eb1af3d146aa629c6623877823e4bb738d661429`.
  It identifies declared literal wire fields, full event/projection field
  metadata, ID regexes, enum values, canonicalization rules,
  event-to-class/projection mapping, chronology and authority invariant names,
  and source/runtime placeholders. Its hash is not proof that those behaviors
  executed or that an external verifier authenticated a binding. The reviewer
  must also record the exact reviewed source/release commit and replace
  placeholders from Phase 1.
- Exact dynamically signed post-Phase-1 census: `<signed projection hash>`,
  `<export receipt hash>`, `<ledger head>`, exact canonical census bytes/hash,
  and `<census artifact hash>`.
  The census must enumerate **13 lanes and 21 assets**; these counts are not a
  substitute for the exact names, IDs, version hashes, or candidate mapping.
  No placeholder can be approved.
- Exact source/asset-version inventory and immutable manifest hashes: `<inventory>`
- Independent review identity and pinned signed projection/export receipt: `<identity and hashes>`

## Authority split (must remain separate)

1. **Research authority** remains exclusively the authenticated global research
   ledger. DABL may only pin its IDs/head/projection/receipt hashes.
2. **Data Foundry preparation** may only describe prospective immutable-data
   contracts. Relevant records carry structural, unverified family/hypothesis
   bindings marked `EXTERNAL_VERIFIER_REQUIRED`; this package does not perform
   or grant that verification. Its certification objects are unverified drafts;
   it cannot ratify research, authenticate an export, run frozen evaluation, or
   make alpha/lifecycle claims.
3. **Owner authority** is required for paid/licensed terms, contacts,
   credentials, external writes, holdout access, any evaluator run, and every
   production boundary.
4. **Independent reviewer authority** validates fact evidence and identity
   separation; it is not a buyer, collector, or research ratifier.

## Default restrictions

- Zero spend; no vendor contact, purchase, license acceptance, credential use,
  holdout access, scheduler change, collector run, GCP/VM mutation, or trading.
- Existing `data_spine` remains an untrusted adapter until separately certified.
- No tier, including a future Tier A, can run a frozen evaluator or support
  alpha/lifecycle claims in this preparation package.
- Any future Tier A requires all eight fact categories with hash-backed evidence,
  a projected independent replay request and exact later receipt hash, reviewer/
  producer/certifier separation, accepted applicable license terms, and a
  currently nonblocked, nonrevoked, nonsuperseded, unexpired version. This draft
  does not activate that capability.
- An explicit `NO_DATA_ACQUISITION_JUSTIFIED` outcome is permitted when the
  signed snapshot shows that no route can honestly satisfy the frozen contract.
  It records the blocked claim and residual evidence gap; it does not weaken
  PIT/provenance gates or authorize a proxy as equivalent.

## Current legacy-adapter baseline (not certification)

This is a current inventory observation, not a DABL asset version or a
decision-grade certificate: 12,334 files / 187,658,764,499 bytes; 178 manifests
(147 data-spine, 28 options, 3 evaluator) across 44 namespaces; 66 gates and
three evaluators. The legacy inventory has 12 certification records (10
`READY`, two `BLOCKED`) and nine absent records. All 12 lack the required
schema/asset/owner/license/coverage/certifier/signature evidence; 128 of 147
data-spine manifests lack license fields. Thus every legacy readiness or
certification surface is an untrusted adapter for Phase 2.

The future signed census must preserve the live semantic distinctions: HYP003
Form 4 is already gate-passing and only its exact settlement condition remains
blocked; HYP011 lacks payout facts/transform; HYP012 has raw assets but lacks a
transform; HYP001 lacks the tape and has a historical-vs-replay semantic
blocker; and the 8-K inventory is 313,449/313,450 raw with no semantic product.

## Separate future GCP authority

If approved after the above preconditions, a separate named action may create
the DABL location **once** on the authoritative GCP root, with exact path,
service identity, immutable source manifest, expected genesis bytes/hash, and
rollback/read-only plan. Its candidate namespace is
`outputs/research/alpha_lab/data_foundry/ledger/<bundle-id>/`. QS-004 must not
silently authorize this create-only initialization.
