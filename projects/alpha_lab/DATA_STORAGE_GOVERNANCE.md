# Alpha Lab GCP Data Storage Governance

Status: Canonical for Alpha Lab research data
Classification: `RESEARCH_ONLY_NON_EXECUTIONAL`
Effective date: 2026-07-17
Machine-readable policy: `gcp_storage_policy.json`

## Decision

The dedicated GCP research disk is the sole authoritative hot store for Alpha
Lab data:

```text
GCP project: alpha-stack-490922
VM access: ssh caerus-vm
disk: disk-20260717-164633
mount: /mnt/disks/alpha-lab
repository root: /mnt/disks/alpha-lab/alpha-lab-project
data root: /mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab
```

The Mac tree at
`/Users/brettolson/Documents/Caerus/alpha-lab-project/outputs/research/alpha_lab`
is a frozen rollback copy. It is not a collection target, a merge peer, or an
authoritative source after cutover. Git remains authoritative for compact code,
policy, frozen hypothesis specifications, and verdict documents; it is not the
store for raw or generated research data.

This policy changes no trading, paper, broker, allocation, production cron,
deployment, strategy registry, returns, or frozen holdout behavior.

## Mandatory landing rules

1. Every collector runs with the GCP repository root as `--repo-root`; a
   collector must never run against the Mac rollback tree.
2. Before a collection, verify the research disk is mounted, the repository
   root resolves below that mount, at least 50 GiB is free, the temporary and
   cache directories are writable below the same mount, and no other Alpha Lab
   collector is active.
3. Run at most one collector at a time. Bounded, resumable batches are required
   for large sources.
4. New payloads are append-only. Write into `.staging/` or a deterministic
   checkpoint, flush and checksum the payload, atomically rename it into its
   final location, and write `manifest.json` last.
5. A finalized path is immutable. Repeating an identical write is idempotent;
   different bytes at an existing path fail closed. Corrections receive a new
   bundle or run ID and retain lineage to the superseded object.
6. An incomplete staging directory or checkpoint is operational state, not
   evidence. Research readers may consume only finalized artifacts identified
   by a valid manifest or by the frozen experiment contract.
7. Every persisted file record includes its relative name, byte count, and
   SHA-256. A source manifest also records retrieval time, source identity,
   schema version, licensing/redistribution constraints, point-in-time limits,
   and explicit confirmation that credentials and trading behavior were not
   persisted.
8. Provider observation time, source publication/acceptance time, effective
   date, and model-availability time must remain distinct when applicable.
   Unknown timing fails closed for point-in-time claims.
9. Credentials remain only in the mode-`600` research `.env`; broker and
   trading credentials are forbidden. Secrets, signed URLs, access tokens, and
   credential-bearing request headers must never enter a payload or manifest.
10. No source data is deleted after a copy or transformation. Derived datasets
    reference exact input hashes so a result can be reconstructed and audited.

## Canonical directory map

| Data class | Canonical relative path | Completion rule |
|---|---|---|
| External source capture | `data_spine/<source_id>/<bundle_id>/` | `manifest.json` exists and validates every listed payload |
| Frozen hypothesis run | `<HYP-ID>/<run_id>/` | Run packet contains frozen hypothesis/code/input hashes and terminal status |
| Options forward observation | `options_proxy_forward/<artifact_class>/<as_of_date>/<run_id>/` | Immutable artifact plus health and boundary evidence for the session |
| Shared working/index data | `shared/` | Explicitly marked checkpoint or finalized input; never inferred from filename alone |
| Provider readiness | `provider_readiness/` | Dated readiness artifact with provider and blocker state |
| Collector work in progress | `data_spine/.staging/` and named checkpoints | Incomplete; excluded from decision evidence and migration attestation |

`source_id` uses lowercase letters, numbers, dots, underscores, and hyphens.
Bundle IDs use `<UTC YYYYMMDDTHHMMSSZ>-<12 hex content hash>`. Experiment IDs
retain the frozen `HYP-YYYY-NNN` identity. Never organize canonical data by a
person's name, a laptop path, or an informal description.

## Manifest contract

A source bundle manifest must contain at least:

- `schema_version`, `classification`, `source_id`, `bundle_id`, and UTC
  `retrieved_at`;
- a `files` array with relative `name`, `bytes`, and `sha256` for every payload;
- source URL/API/table identity and applicable terms or redistribution limits;
- point-in-time limitations and relevant source/effective/availability times;
- upstream bundle or file hashes for transformed datasets;
- `credentials_persisted: false` and `trading_behavior_changed: false`.

The manifest is the inventory and integrity contract, not merely a README. If a
manifest does not validate, the bundle is incomplete and consumers fail closed.

## How to find and access data

Start a read-only session:

```bash
ssh caerus-vm
ROOT=/mnt/disks/alpha-lab/alpha-lab-project
DATA="$ROOT/outputs/research/alpha_lab"
test -d "$DATA"
df -h /mnt/disks/alpha-lab
```

List the top-level holdings and sizes:

```bash
du -sh "$DATA"/* 2>/dev/null | sort -h
find "$DATA" -mindepth 1 -maxdepth 1 -type d -print | sort
```

Find all finalized source bundles or the newest bundle for one source:

```bash
find "$DATA/data_spine" -name manifest.json -type f | sort
find "$DATA/data_spine/yfinance_analyst_proxy" -name manifest.json -type f | sort | tail -1
```

Inspect a manifest without modifying it:

```bash
python -m json.tool /absolute/path/to/manifest.json | less
sha256sum /absolute/path/to/payload
```

Find data for a frozen hypothesis or an options observation date:

```bash
find "$DATA/HYP-2026-004" -maxdepth 2 -type f | sort
find "$DATA/options_proxy_forward" -path '*2026-07-17*' -type f | sort
```

Use absolute GCP paths in evidence notes. Do not copy a working set back to the
Mac and silently treat it as current. If local analysis is necessary, record
the GCP source path, manifest hash, transfer time, and local destination; the
GCP object remains authoritative.

## Collector preflight

Run this read-only check before every acquisition:

```bash
ssh caerus-vm '
set -eu
ROOT=/mnt/disks/alpha-lab/alpha-lab-project
test -d "$ROOT/outputs/research/alpha_lab"
findmnt -T /mnt/disks/alpha-lab
test "$(df --output=avail -BG /mnt/disks/alpha-lab | tail -1 | tr -dc 0-9)" -ge 50
test -w /mnt/disks/alpha-lab/tmp
test -w /mnt/disks/alpha-lab/cache
! pgrep -af -- "-m projects[.]alpha_lab.data_spine.cli|-m projects[.]alpha_lab.options_proxy.cli" >/dev/null
'
```

If any check fails, do not start, restart, or overlap a collector. Diagnose the
storage condition first.

## Backup, recovery, and deletion

The disk is protected by resource policy `alpha-lab-weekly-snapshot`: Sunday at
06:00 UTC, 28-day scheduled-snapshot retention, with automatic snapshots kept
if the source disk is deleted. Snapshots are recovery media, not the ordinary
way to browse research data.

Finalized evidence and raw source captures have no automatic deletion policy.
Deletion, bulk relocation, lifecycle expiration, or snapshot-policy changes
require an explicit owner-approved retention decision plus a tested restore.
Never use cleanup to hide a failed or negative experiment.

Recovery order:

1. stop new research collection without touching production scheduling;
2. record disk, mount, free space, process state, and affected paths;
3. identify the newest valid manifest and snapshot before the incident;
4. restore into a separate recovery disk or prefix;
5. verify manifest hashes and file counts before changing authority;
6. record the cutover and preserve the damaged copy for forensics.

## Cloud Storage archive boundary

No GCS bucket is currently configured. The scheduler VM has read-only Cloud
Storage OAuth scope. Do not weaken that boundary, place a service-account key
on the VM, or claim object-store replication exists.

A future GCS archive requires a private bucket, uniform bucket-level access,
public-access prevention, a least-privilege writer that does not broaden the
scheduler VM, create-if-absent upload preconditions, checksum comparison,
retention/lifecycle approval, monitoring, and a successful restore test. Until
all of those are verified, the GCP persistent disk plus snapshots is the
canonical and honest storage design.

## Enforcement and review

- Recurring collection prompts must name the GCP root and explicitly forbid a
  Mac write.
- The daily `Alpha Lab GCP storage audit` performs a read-only checksum dry run
  and verifies the mount, free space, disk attachment, snapshot policy, and new
  manifests. Healthy runs remain quiet; anomalies identify exact paths.
- A daily operator check reviews free space, collector overlap, newest terminal
  artifacts, and staging age.
- A weekly integrity check verifies all new manifests and their file hashes,
  then confirms that the scheduled snapshot policy remains attached.
- A monthly review inventories source growth, stale checkpoints, provider
  licenses, recovery posture, and any artifact without a terminal manifest.
- Any missing hash, path escape, secret detection, overwrite attempt, duplicate
  current-date snapshot, or insufficient-space condition fails closed.
