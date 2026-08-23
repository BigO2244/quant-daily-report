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

## Gate A: clean release dependencies

Gate A must pass on the release candidate before any registry, migration,
publication, or projection preparation. Gate A targets exactly Ubuntu 22.04
x86_64, glibc 2.35, and CPython 3.10.12. The dependency contract declares
Alpha's runtime imports in `requirements.in`, locks every test dependency to
one hashed binary wheel, and records each wheel's filename, size, SHA-256,
metadata hashes, target tags, and target dependency closure. The wheel
manifest's `dependency_resolution_base_commit` records resolver lineage only;
it is not the release source identity. The separately reviewed source archive,
source manifest, file manifest, and canonical release-input manifest bind the
final committed source.
The source verifier also requires the `git archive` global PAX `comment` to
equal the declared commit and reconstructs the complete Git tree OID from the
exact file modes and bytes; commit and tree labels are therefore checked
provenance, not caller assertions.

The operator receives these reviewed, absolute-path inputs:

- the exact `git archive` tar, canonical source manifest, and canonical
  file-only manifest for the final reviewed commit;
- the 25 binary wheels named by
  `phase1-cp310-linux-x86_64-wheel-manifest.json`;
- the canonical `caerus_alpha_lab_clean_release_input_v1` manifest; and
- the approved release parent. Wheel binaries and generated release/source
  manifests are ceremony inputs and must not enter Git.

Download the target wheels into a temporary wheelhouse before entering Gate A.
This is the only network-enabled dependency preparation step:

```bash
WHEELHOUSE="$(mktemp -d /tmp/caerus-alpha-phase1-wheelhouse.XXXXXX)"

python3.10 -m pip download \
  --dest "$WHEELHOUSE" \
  --require-hashes \
  --only-binary=:all: \
  --platform manylinux_2_34_x86_64 \
  --platform manylinux_2_28_x86_64 \
  --platform manylinux_2_17_x86_64 \
  --platform manylinux2014_x86_64 \
  --python-version 3.10 \
  --implementation cp \
  --abi cp310 \
  -r projects/alpha_lab/release/phase1-cp310-linux-x86_64.lock
```

The following placeholders must be replaced by the exact paths and digest from
the reviewed release-input packet. The packet also delivers
`gate_a_bootstrap.py` as a separate byte-identical artifact and records its
owner-approved SHA-256. That minimal standard-library trust root authenticates
its own bytes against both the explicit authorization and source file manifest,
then verifies and extracts the complete archive using create-only,
descriptor-relative no-follow operations. It imports no code from a checkout.

Run its dry verification first, then repeat with `--write` to create and seal
`$RELEASE_PARENT/bootstrap/sha256/$SOURCE_ARCHIVE_SHA256/app`. Existing valid
content is verified idempotently; incomplete or divergent content is never
repaired, deleted, or reused:

```bash
RELEASE_PARENT=/approved/release/parent
SOURCE_ARCHIVE_SHA256=<EXACT_SOURCE_ARCHIVE_SHA256>
BOOTSTRAP_SHA256=<EXACT_OWNER_APPROVED_BOOTSTRAP_SHA256>
BOOTSTRAP_TOOL=/protected-release-input/gate_a_bootstrap.py

python3.10 -I -S -B "$BOOTSTRAP_TOOL" \
  --source-archive /absolute/path/to/source.tar \
  --source-manifest /absolute/path/to/source_manifest.json \
  --file-manifest /absolute/path/to/file_manifest.json \
  --release-parent "$RELEASE_PARENT" \
  --authorized-source-archive-sha256 "$SOURCE_ARCHIVE_SHA256" \
  --authorized-bootstrap-sha256 "$BOOTSTRAP_SHA256"

python3.10 -I -S -B "$BOOTSTRAP_TOOL" \
  --source-archive /absolute/path/to/source.tar \
  --source-manifest /absolute/path/to/source_manifest.json \
  --file-manifest /absolute/path/to/file_manifest.json \
  --release-parent "$RELEASE_PARENT" \
  --authorized-source-archive-sha256 "$SOURCE_ARCHIVE_SHA256" \
  --authorized-bootstrap-sha256 "$BOOTSTRAP_SHA256" \
  --write

BOOTSTRAP_ROOT="$RELEASE_PARENT/bootstrap/sha256/$SOURCE_ARCHIVE_SHA256/app"
GATE_A_BUILDER="$BOOTSTRAP_ROOT/projects/alpha_lab/factory/release_build.py"
```

Gate A refuses write mode unless the loaded builder and colocated dependency
validator are at that exact content-addressed path and their bytes match the
source file manifest. Never import the builder from a mutable legacy checkout,
ambient `PYTHONPATH`, user site, or a stale VM module. Invoke the exact file
with `-I -S -B`, which disables ambient Python paths, site initialization, and
bytecode writes. First run the non-mutating preflight and default dry build:

```bash
python3.10 -I -S -B "$GATE_A_BUILDER" preflight \
  --repo-root "$BOOTSTRAP_ROOT" \
  --source-archive /absolute/path/to/source.tar \
  --source-manifest /absolute/path/to/source_manifest.json \
  --file-manifest /absolute/path/to/file_manifest.json \
  --wheelhouse "$WHEELHOUSE" \
  --release-input-manifest /absolute/path/to/release_input_manifest.json \
  --release-parent "$RELEASE_PARENT"

python3.10 -I -S -B "$GATE_A_BUILDER" build \
  --repo-root "$BOOTSTRAP_ROOT" \
  --source-archive /absolute/path/to/source.tar \
  --source-manifest /absolute/path/to/source_manifest.json \
  --file-manifest /absolute/path/to/file_manifest.json \
  --wheelhouse "$WHEELHOUSE" \
  --release-input-manifest /absolute/path/to/release_input_manifest.json \
  --release-parent "$RELEASE_PARENT"
```

Only after the preflight output has been independently reviewed may the
operator add both write controls:

```bash
python3.10 -I -S -B "$GATE_A_BUILDER" build \
  --repo-root "$BOOTSTRAP_ROOT" \
  --source-archive /absolute/path/to/source.tar \
  --source-manifest /absolute/path/to/source_manifest.json \
  --file-manifest /absolute/path/to/file_manifest.json \
  --wheelhouse "$WHEELHOUSE" \
  --release-input-manifest /absolute/path/to/release_input_manifest.json \
  --release-parent "$RELEASE_PARENT" \
  --interpreter /usr/bin/python3.10 \
  --write \
  --authorized-release-input-sha256 <EXACT_RELEASE_INPUT_SHA256>
```

The build is create-only and content-addressed. It copies the verified source
and wheels, extracts the source file-only with descriptor-relative no-follow
operations, creates a copied venv, installs with `--no-index` and
`--require-hashes`, and executes dependency validation, `pip check`, the exact
357-test inventory, and the 355-pass/2-DuckDB-skip suite inside a proven Linux
user/network namespace. It binds all 25 locked distributions, the explicit
`pip`/`setuptools` venv bootstrap allowance, the external base interpreter and
complete stdlib-tree identities, and the sealed app/venv/wheel/lock tree.
Caches, JUnit output, and temporary state remain outside the release.

The external-runtime receipt also binds Ubuntu's OS-release bytes, every shared
object loaded by the interpreter, and the exact single-link `/usr/bin/git`
binary. The versioned
`caerus_alpha_lab_atlas_gate_e_runtime_receipt_v1` inside
`verification_receipt.json` is the direct Atlas provenance surface. It binds
the release-input, build, and built-manifest identities; copied Python bytes;
the canonical complete site-packages subtree; lock, wheel-manifest, and
installed-distribution closure; and the release/app/venv owner and mode census.
`READY.verification_receipt_sha256` binds that whole receipt, while independent
verification reports the exact `READY` SHA-256. No caller-derived runtime hash
is an alternative authority.

`verification_receipt.json` and canonical `READY` are written last. A crash
before durable `READY` leaves an unreferenced directory that is never repaired,
deleted, or reused automatically. An existing valid address is idempotently
verified; any incomplete or divergent collision fails closed.

Verify independently immediately before every ceremony invocation:

```bash
python3.10 -I -S -B "$GATE_A_BUILDER" verify \
  --release-dir /approved/release/parent/releases/sha256/<EXACT_RELEASE_INPUT_SHA256>

python3.10 -I -S -B "$GATE_A_BUILDER" ceremony \
  --release-dir /approved/release/parent/releases/sha256/<EXACT_RELEASE_INPUT_SHA256> \
  --ceremony-output-root /protected-review/approved-output-workspace \
  -- registry verify-history \
  --history /protected-review/identity_registry_history.json \
  --trust-anchor /protected-pin/root_trust_anchor.json \
  --external-pin <active-registry-hash>
```

Mode `0555`/`0444` is tamper-evident but is not an adversarial seal against the
same Unix owner, which can restore write permission. Before any Gate E ceremony,
an administrator must place the release and bootstrap hierarchy under a
different non-writing principal or a read-only mount. The launcher fails closed
for root, a same-owner caller, writable ancestors, group/other-write modes, or
a descriptor-relative ACL-aware effective principal that can write any
protected release/bootstrap file or directory. It records the current effective
UID/GID and canonical protected-tree write-denial census, then runs
a second complete release/base-runtime verification before execution. This
separate-principal/read-only control is an external production Gate E action;
until it is proven on the exact Ubuntu host, release work remains in progress.

The launcher executes only `projects.alpha_lab.factory.ceremony`, uses the
exact app and interpreter paths from `READY`, forbids relative paths and
publication `--write`, applies OS-level network isolation, and re-verifies the
sealed release after the command. Outputs must be create-only and beneath the
separately approved, preexisting no-symlink ceremony-output root, which must be
outside the release, authoritative checkout/data roots, and protected inputs.

The only permitted dependency-driven skips are the two named DuckDB tests.
DuckDB is not needed by the signing ceremony and remains an explicitly excluded
optional materialization engine. The yfinance adapter is injected by tests and
causes no skip. Any other skip, sdist, unpinned or unhashed requirement,
wheel-set difference, import-contract change, incompatible platform/ABI,
dependency-closure difference, external-runtime drift, cache, extra file,
mutable mode, source-link member, or metadata/hash-chain drift fails Gate A.
Do not continue to signing on a failure.

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
  --location="<APPROVED_LOCATION>" \
  --keyring=caerus-research --key=research-author \
  --public-key-format=pem --output-file=/protected-review/research-author.pem
```

Finalize using the root anchor and registry hash from the protected pin, not
copies taken from the preparation directory:

```bash
python -m projects.alpha_lab.factory.ceremony registry finalize \
  --request /protected-review/registry-release-1/review_manifest.json \
  --signature /protected-review/registry-release-1/signature.raw \
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
  --location="<APPROVED_LOCATION>" --keyring=caerus-research \
  --key=research-author --version=1 \
  --input-file=/protected-review/event-attestation/signing_payload.json \
  --signature-file=/protected-review/event-attestation/signature.raw
```

Cloud KMS writes the detached Ed25519 signature as raw bytes. Ceremony finalize
commands accept that exact 64-byte `.raw` file directly; base64 conversion is
neither required nor preferred.

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
