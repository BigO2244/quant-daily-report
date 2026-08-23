# Authenticated Research Identities

Status: `RESEARCH_ONLY_CONTROL`  
Decision-grade effect: fail closed until the enrolled identity release, the
ledger activation boundary, and all required attestations verify.

## What is signed

Each detached Ed25519 attestation binds exactly one immutable SHA-256 artifact,
one accountable role, an RFC3339 UTC timestamp, the preceding ledger head, and
the exact public-registry hash.  The eligible roles are:

- `OWNER_RATIFIER` for a wave/family or migration-plan decision;
- `PREREGISTRATION_AUTHOR` for a frozen experiment;
- `DATA_CERTIFIER` for the certified input snapshot of each model/challenge
  trial; and
- `INDEPENDENT_REVIEWER` for the final review receipt.
- `LEDGER_EXPORTER` for a detached signed projection export. This is a
  lifetime-separated control role and its active public key may not be reused
  for any other governance role.

Names, true booleans, standing user authorization, or a Git commit are not a
signature. Private keys, seed phrases, passphrases, access tokens, and signing
service credentials must never enter Git, a ledger, prompt, log, manifest, or
research artifact.

## Registry trust and version history

The public directory is not trusted merely because it is present on disk. A
registry release carries a root-Ed25519 signature over its exact directory hash
and monotonically increasing version. The root public key and the current
registry hash are supplied separately from protected external state. Neither
the root copied inside the history file nor a pin copied into that file may
authenticate itself. The
runtime rejects an unanchored registry, a substituted directory, an unavailable
historical registry hash, a non-newest release, or a rollback that conflicts
with that external pin.

Every key record has an externally anchored `subject_id`; `identity_id` is only
the credential alias. One subject may not hold more than one of the author,
Data Foundry certifier, or independent-reviewer roles. Public keys may not be
shared across identities.

Rotation adds a new public key in a signed higher registry release and marks
the prior key `RETIRED` or `REVOKED` with a replacement key ID. Revoked and
retired keys cannot issue new attestations. Historical attestation validation
resolves the registry hash named in the attestation, so a legitimate old event
does not become unverifiable merely because a later key rotates. Lost-key
recovery is a new, root-approved `recovery_issued` key release; it never
restores the retired key.

The registry audit reports active, revoked, recovery-issued, and soon-expiring
keys without exposing a secret. Any identity incident freezes new decision-grade
events, records the affected hashes and subjects, revokes/releases a replacement
directory, replays the ledger, and requires a fresh independent review where
the incident could affect a claim.

## Ledger activation and legacy migration

Identity enrollment has an explicit immutable `identity_activation_head_hash`.
Events at or before that head are loaded as `LEGACY_UNAUTHENTICATED` and remain
non-decision-grade; they are never retroactively decorated with new signatures.
Every child event after the boundary must verify against an anchored registry.

Migration uses one deterministic, owner-signed migration-plan artifact that
lists the complete receipts, 13 family mappings, 66/8/8/0 census, expected
child-event hashes, and the legacy terminal head. That one attestation binds
the whole batch; it does not create per-event historical authentication or
decision-grade status. The importer must reject a migration plan whose signed
artifact hash, intended genesis/boundary head, registry release, or deterministic
child plan differs from the reviewed plan. Until the importer is invoked with
that anchored plan verifier, it remains in legacy-only mode and must not claim
authenticated cutover.

Use the public-only ceremony in `RESEARCH_SIGNING_CEREMONY.md`. It creates the
complete migration definition from the exact owner packet and fresh receipts,
validates full anchored public history, emits deterministic event-plan bytes,
and finalizes the detached owner signature. A migration signature ratifies the
legacy plan; it does not publish it. A distinct owner-signed QS-003 artifact
must bind the signed-plan hash, canonical path, expected bytes/head, fresh
receipt-set hash, create-only mode, time, active registry, and prior `GENESIS`.
Only then may the importer perform one canonical GCP create-only publication.
The importer returns `LEGACY_IMPORTED_UNAUTHENTICATED` and the exact
`identity_activation_head_hash`; initialize subsequent ledger work with that
head so only post-import events require authenticated signatures.

## Operational use

The signer is an externally managed, individually authenticated Ed25519
credential or hardware-backed service. The ledger only receives the public
registry release and detached signature. Registration validates the current
head before appending; a stale-head signature, wrong role, wrong artifact hash,
expired/revoked/retired key, registry substitution, author–certifier overlap,
or author–reviewer overlap fails closed.

This control does not authorize GCP writes, purchases, holdout access,
promotion, allocation, scheduling, deployment, broker behavior, or trading.

## External-signer operator surface

The installed module entry point is:

```bash
python -m projects.alpha_lab.factory.ceremony --help
```

Registry, attestation, migration, publication, and projection subcommands all
emit exact canonical signing bytes, accept detached signatures only, and
immediately verify them. All histories use
`caerus_alpha_lab_identity_registry_history_v1` and carry complete contiguous
signed releases plus the declared external pin. Operational commands require
the protected root trust-anchor file and external registry hash separately.
See `RESEARCH_SIGNING_CEREMONY.md` for the complete ceremony and KMS examples.

Every authenticated control-plane invocation that supplies `--ledger` must
also supply all three inputs below:

```text
--identity-bundle /protected-review/control_plane_identity_bundle.json
--identity-trust-anchor /protected-pin/root_trust_anchor.json
--identity-registry-pin <active-registry-hash>
```

The loader compares the external anchor byte-for-byte with the public copy in
the bundle before it verifies every registry release, then compares the
separate pin with the signed activation plan and active registry directory.

## Signed projection exports

A `caerus_alpha_lab_signed_projection_export_v1` is a research-only handoff,
not a lifecycle or capital decision. Its detached `LEDGER_EXPORTER`
attestation covers the complete unsigned context: the strict canonical
projection, normalized repository-relative event-store path, source-ledger
byte receipt, replay evidence, and active anchored registry release. The
unsigned form is always classified `LINEAGE_ONLY_NON_DECISION_GRADE`; it may
not be used as a decision artifact. Export verification remains public-key
only and never handles a private credential.

The unsigned context and signed envelope serialize the same `exported_at` UTC
timestamp. The detached attestation must use that exact time, which must be no
earlier than the latest source event recording time and the active release.
The ledger receipt is formed under one shared file lock across semantic replay,
record enumeration, and exact byte hashing; a signed export refuses a missing
store rather than racing its creation. It also carries `head_by_event_count`,
including count zero and the current chain head, for ancestry-only handoff.
