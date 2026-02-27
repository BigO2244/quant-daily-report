# Archiving Policy

## Rules
- No deletions for legacy strategy code.
- Legacy code is moved under `archive/`.
- Production/runtime modules must not import from `archive/`.
- Archived code may remain for audit/recovery but is out of production path.

## Archive layout
- `archive/sleeves/`: archived strategy sleeves and sleeve entry modules.
- `archive/experiments/`: one-off experiments, legacy test harnesses, old runners.
- `archive/legacy_reports/`: legacy reporting scripts not used by active workflows.
- `archive/legacy_configs/`: configs for archived modules.
- `archive/notes/`: inventory notes and follow-up candidates.

## Restore process
1. Create a branch for restore work.
2. Move target files from `archive/` back to active module locations.
3. Reconnect imports/workflows explicitly.
4. Run tests and smoke run before merge.

## Safety
- Keep production workflows pointed only to active runtime modules.
- Do not re-enable archived sleeves by default.
