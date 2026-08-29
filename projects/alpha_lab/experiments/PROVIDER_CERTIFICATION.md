# Provider readiness certification

A physical dataset is never enough to pass an Alpha Lab provider gate. Every
asset must have a certification at:

`outputs/research/alpha_lab/provider_readiness/<asset_id>.json`

The JSON object must contain:

- the exact `provider_id` and `dataset_id` from `catalog.py`;
- `status: "READY"`;
- `historical_point_in_time_verified: true`;
- `schema_validation_status: "PASS"`;
- `data_files`, exactly equal to the runner's sorted list of relative path,
  byte count, and SHA-256 for every matched physical file;
- `schema_manifest`, with one object per logical field containing
  `logical_field`, `source_path`, `physical_field`, and `data_type`;
- `blockers: []`; and
- `evidence_hash`, equal to the canonical SHA-256 of the complete certification
  object after removing only `evidence_hash`.

The runner derives `fields_available` from `schema_manifest`; a free-form field
claim is ignored. It also opens CSV, JSON/JSONL, or Parquet schemas and verifies
that each declared physical field exists in the file whose content hash was
certified. A file change, missing frozen or physical field, unsupported schema
format, different provider, failed PIT audit, invalid schema entry, declared
blocker, or evidence-hash mismatch fails closed.

Certification proves that a versioned physical input passed the data contract.
It does not prove alpha and does not authorize reading the locked holdout. A
separately reviewed frozen evaluator remains required after all provider gates
pass.
