# 03 VM Replacement

## VM Code State

- VM: `alpha-stack-scheduler`
- Repo path: `/home/brettolson/quant-daily-report`
- VM HEAD after code deploy: `0884a2a6b1ff6f3f09cae977864473aa06f73c8a`
- Branch: `main`
- Tracked working tree after code deploy: clean

## Staging

- Staging directory:
  `outputs/recovery_staging/shadow_nav_same_day_20260613T2101Z`
- Observation start: `2026-05-12`
- Observation end: `2026-06-12`
- Rows: `23`
- Daily-return validation rows: `92`
- Daily-return validation status: `PASS`
- Skipped non-trading artifact: `2026-05-25`

Staged hashes:

- NAV: `6a48b74c1c4b5a7af0a22210e21b70522bb24c0b84f6cd12dc11d1668a1b2de2`
- Summary: `266314b2abffbd49afc7b0eeb9dcb11dbc4f0fdd52b296228abd63786d4d2c7c`
- Manifest: `373527c1f95fd1f5359101cb6d056ffabbd8b369b460a64281d380fd06282165`
- Daily-return validation: `c23a2c152c576cf68b068fc973418db95fa4ffa9db31bebaa44c822140b4c628`

## Replacement

Active replacement was performed after staging and isolated health validation
passed.

Expected pre-replacement hashes:

- `shadow_nav_series.csv`:
  `5d59c987c07198c590a287e189a1224f2f783ba8bd55d5fdcb39d1de9e84dc1f`
- `shadow_summary.json`:
  `86a793550a73db81c8dedfe9c96618f4a08a788a150f1354c6542b5a6a65d67a`

Replacement backup:

- Backup directory:
  `outputs/recovery_backups/shadow_nav_same_day_restatement_20260614T015954Z`
- Backup manifest SHA-256:
  `0ffca3f2a8ad8844bdb802702183f021ae4b053828321b6b14fef6d2ad53c6a9`

Active post-replacement hashes:

- `shadow_nav_series.csv`:
  `6a48b74c1c4b5a7af0a22210e21b70522bb24c0b84f6cd12dc11d1668a1b2de2`
- `shadow_summary.json`:
  `266314b2abffbd49afc7b0eeb9dcb11dbc4f0fdd52b296228abd63786d4d2c7c`
- `shadow_nav_restatement_manifest.json`:
  `76cf50519e414772b1cfb8ab06967d979bb29f331e1691975fe94c837f999d81`
