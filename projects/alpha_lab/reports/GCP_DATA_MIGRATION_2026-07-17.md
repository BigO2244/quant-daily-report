# Alpha Lab GCP Data Migration Attestation — 2026-07-17

Classification: `RESEARCH_ONLY_NON_EXECUTIONAL`

## Outcome

The dedicated GCP research disk is authoritative for Alpha Lab data. The live
inventory after the final migration and the concurrent bounded SEC batch
contained approximately 17 GiB across 3,481 files and 93 finalized
`manifest.json` files under:

```text
/mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab
```

The largest namespaces were approximately 15 GiB in `data_spine/`, 1.1 GiB in
`shared/`, and 14 MiB in `options_proxy_forward/`.

## Migration completeness evidence

The frozen Mac tree contained approximately 1.8 GiB. A checksum-based rsync
dry run compared every non-staging, non-lock file in the Mac Alpha Lab data
tree with the same relative path on `caerus-vm`:

```bash
rsync -ani --checksum \
  --exclude='.staging/' \
  --exclude='locks/' \
  outputs/research/alpha_lab/ \
  caerus-vm:/mnt/disks/alpha-lab/alpha-lab-project/outputs/research/alpha_lab/
```

Result: no output. Therefore no finalized or ordinary non-staging Mac file was
missing from or byte-different on the GCP disk at the time of validation. The
GCP tree is larger because collection continued there after cutover.

## Final closure

A later audit found six finalized files from the 2026-07-17 options-proxy run
that had been created on the Mac before its recurring automation was redirected
to GCP. After the active GCP SEC collector exited, those six files were copied
with create-only behavior. No existing GCP file was overwritten.

The post-copy checksum rsync dry run returned zero file differences and zero
output lines after excluding only `.staging/`, collector locks, and `.DS_Store`.
Independent SHA-256 comparison of every 2026-07-17 options-proxy file returned
exact parity between the Mac rollback tree and GCP.

The recurring `Alpha Lab analyst proxy daily` and `Caerus options proxy daily
observation` automations now run collection through `ssh caerus-vm` against the
GCP root. A read-only daily `Alpha Lab GCP storage audit` automation checks the
mount, free space, disk attachment and snapshot policy, recent manifests, and
checksum parity; it notifies only on an anomaly.

Staging checkpoints and lock files were intentionally excluded. They are
host-local operational state, not finalized evidence, and must not be merged
between the Mac and VM. The VM 8-K and USAspending checkpoints remain
authoritative.

## GCP resource evidence

- Project: `alpha-stack-490922`
- Instance: `alpha-stack-scheduler`
- SSH alias: `caerus-vm`
- Disk: `disk-20260717-164633`, 500 GB `pd-standard`, status `READY`
- Labels: `purpose=alpha-lab-data`, `environment=research`
- Attached snapshot policy: `alpha-lab-weekly-snapshot`
- Filesystem at validation: approximately 492 GiB total, 17 GiB used, 475 GiB
  available
- Active Alpha Lab collectors at validation: none
- Final data-spine status: `READY_FOR_DATA_GATES`, with no blockers
- Final production-boundary status: `CLEAN`, with no findings

No files were deleted from the Mac. It remains a frozen rollback copy.

## Explicit non-changes

This migration did not change or invoke trading, paper, live, broker,
allocation, production cron, deployment, strategy registry, returns, or frozen
holdout behavior. No GCS bucket or object-replication claim was created. The VM
Cloud Storage write boundary remains unchanged.
