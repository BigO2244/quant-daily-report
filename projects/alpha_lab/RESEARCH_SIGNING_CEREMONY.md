# Alpha Lab Public Signing Ceremony

Status: `RESEARCH_ONLY_CONTROL`  
Authority: public preparation and verification only

## Boundary

The ceremony command prepares exact canonical bytes for an external Ed25519
signer and verifies the detached signature afterward. It never creates, loads,
accepts, or stores a private key. A signing service, hardware device, or offline
owner credential receives only `signing_payload.json`.

Run all commands from the repository root:

```bash
python -m projects.alpha_lab.factory.ceremony --help
python -m projects.alpha_lab.factory.ceremony registry --help
python -m projects.alpha_lab.factory.ceremony attestation --help
python -m projects.alpha_lab.factory.ceremony migration --help
python -m projects.alpha_lab.factory.ceremony publication --help
python -m projects.alpha_lab.factory.ceremony projection --help
```

All JSON input is strict: duplicate keys, non-finite numbers, malformed public
keys, unknown signing fields, path substitutions, stale pins, and secret
material fail closed. Output files and preparation directories are create-only.

## Protected inputs

Two inputs must arrive separately from the reviewed history file:

- the root public trust-anchor JSON; and
- the active registry-directory SHA-256 external pin.

The canonical exported history schema is
`caerus_alpha_lab_identity_registry_history_v1` with exactly `trust_anchor`,
`registries`, `active_registry_hash`, and
`externally_pinned_registry_hash`. A history file cannot authenticate its own
embedded root or pin.

## 1. Registry release

Prepare a genesis release from a public-only unsigned directory:

```bash
python -m projects.alpha_lab.factory.ceremony registry prepare \
  --directory /protected-review/registry_directory.json \
  --trust-anchor /protected-pin/root_trust_anchor.json \
  --released-at 2026-08-22T20:00:00Z \
  --output-dir /protected-review/registry-release-1
```

Sign the exact bytes in
`registry-release-1/signing_payload.json` with the dedicated offline owner/root
credential. Do not reuse a GitHub or SSH key, and do not give the private key to
this command.

Retrieve an approved machine-role Cloud KMS public key without exporting a
credential:

```bash
gcloud kms keys versions get-public-key 1 \
  --location=global --keyring=caerus-research --key=research-author \
  --public-key-format=pem --output-file=/protected-review/research-author.pem
```

Finalize using the root anchor and registry hash from the protected pin, not
copies taken from the preparation directory:

```bash
python -m projects.alpha_lab.factory.ceremony registry finalize \
  --request /protected-review/registry-release-1/review_manifest.json \
  --signature /protected-review/registry-release-1/signature.b64 \
  --trust-anchor /protected-pin/root_trust_anchor.json \
  --external-pin <exact-registry-directory-sha256> \
  --output-dir /protected-review/registry-release-1-final
```

For rotation, `registry prepare` and `registry finalize` both additionally
receive `--previous-history` and `--previous-external-pin`. The new public pin
is still supplied through `--external-pin` at finalization.

## 2. Event and generic attestations

An event draft contains its exact ID, type, payload, occurred/recorded times,
and prior ledger head. Preparation derives the typed payload hash and append
context; callers do not reproduce those hashes manually:

```bash
python -m projects.alpha_lab.factory.ceremony attestation prepare \
  --identity-history /protected-review/identity_registry_history.json \
  --identity-trust-anchor /protected-pin/root_trust_anchor.json \
  --external-pin <active-registry-hash> \
  --identity-id research.author --key-id research.author.2026 \
  --role PREREGISTRATION_AUTHOR \
  --attested-at 2026-08-22T20:05:00Z \
  --event-draft /protected-review/event_draft.json \
  --output-dir /protected-review/event-attestation
```

After external signing, use `attestation finalize`; the result is the exact
`caerus_alpha_lab_control_plane_event_attestation_v1` wrapper consumed by the
authenticated ledger. `attestation verify` rechecks it from the request,
protected root, external pin, and complete history. The `--generic` preparation
form covers an already-derived immutable artifact hash, context hash, ledger
head, and recording time for an eligible role.

For an approved Cloud KMS Ed25519 machine-role key, omit
`--digest-algorithm`; Ed25519 signs the exact message bytes directly:

```bash
gcloud kms asymmetric-sign \
  --location=global --keyring=caerus-research \
  --key=research-author --version=1 \
  --input-file=/protected-review/event-attestation/signing_payload.json \
  --signature-file=/protected-review/event-attestation/signature.b64
```

## 3. Migration plan and QS-003

`migration definition` converts the exact unsigned owner packet and a fresh
canonical audit into the complete definition. `migration prepare` repeats that
audit, validates the full pinned public history, deterministically builds every
legacy event and expected ledger byte, and emits the owner signing bytes:

```bash
python -m projects.alpha_lab.factory.ceremony migration prepare \
  --repo-root /mnt/disks/alpha-lab/alpha-lab-project \
  --data-root /mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab \
  --owner-packet projects/alpha_lab/templates/MIGRATION_OWNER_SIGNING_PACKET.json \
  --recorded-at 2026-08-22T20:10:00Z \
  --identity-history /protected-review/identity_registry_history.json \
  --identity-trust-anchor /protected-pin/root_trust_anchor.json \
  --external-pin <active-registry-hash> \
  --owner-identity-id owner.ratifier --owner-key-id owner.ratifier.2026 \
  --output-dir /protected-review/migration-plan
```

`migration finalize` ingests only the detached owner signature and immediately
verifies the signed plan. `migration verify` can repeat the complete GCP source
audit before any later step. `migration identity-bundle` composes the exact
`caerus_alpha_lab_control_plane_identity_bundle_v1` consumed by authenticated
Alpha control-plane commands from the verified history and signed plan; no
manual JSON assembly is required. The protected root and external pin remain
separate command inputs even though their public copies appear in the bundle.

The migration signature ratifies the exact legacy plan but does **not**
authorize publication. Prepare a distinct QS-003 artifact after the signed plan
exists:

```bash
python -m projects.alpha_lab.factory.ceremony publication prepare \
  --repo-root /mnt/disks/alpha-lab/alpha-lab-project \
  --data-root /mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab \
  --signed-plan /protected-review/signed_migration_plan.json \
  --authorized-at 2026-08-22T20:15:00Z \
  --identity-history /protected-review/identity_registry_history.json \
  --identity-trust-anchor /protected-pin/root_trust_anchor.json \
  --external-pin <active-registry-hash> \
  --owner-identity-id owner.ratifier --owner-key-id owner.ratifier.2026 \
  --output-dir /protected-review/publication-authorization
```

QS-003 binds the signed-plan hash, exact canonical ledger path, expected bytes,
event count and head, fresh receipt-set hash, create-only mode, authorization
time, active registry and pin, and prior `GENESIS`. `publication publish` is a
verification-only dry run by default. `--write` is accepted only on canonical
GCP and only with both valid owner-signed artifacts; the publisher uses a
create-only atomic hard link and never repairs or overwrites an existing ledger.

## 4. Projection export

`projection prepare` opens the authenticated canonical ledger, verifies the
signed activation plan and complete legacy prefix, snapshots the ledger under
lock, and emits exact `LEDGER_EXPORTER` signing bytes. `projection finalize`
reopens and replays the ledger before accepting the detached signature.
`projection verify` replays it again against the final envelope.

```bash
python -m projects.alpha_lab.factory.ceremony projection prepare \
  --ledger /mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab/ledger/research_events.v1.jsonl \
  --research-root /mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab \
  --repo-root /mnt/disks/alpha-lab/alpha-lab-project \
  --signed-migration-plan /protected-review/signed_migration_plan.json \
  --identity-history /protected-review/identity_registry_history.json \
  --identity-trust-anchor /protected-pin/root_trust_anchor.json \
  --external-pin <active-registry-hash> \
  --exported-at 2026-08-22T20:20:00Z \
  --exporter-identity-id ledger.exporter --exporter-key-id ledger.exporter.2026 \
  --output-dir /protected-review/projection-export
```

No command in this runbook authorizes data purchase, holdout access, Shadow,
Paper, capital, deployment, broker behavior, scheduling, or trading.
