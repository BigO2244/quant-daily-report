# Generic Live Cutover Runbook

Status: design and rehearsal only. Generic PAPER is not cut over, legacy PAPER
remains unchanged, and legacy Live remains code-level disabled.

## Safety boundary

The generic scheduler is off by default and cannot submit orders. Its enabled
mode only runs the generic v4 submission-disabled OMS rehearsal. A Live rehearsal
requires a sealed `READY_FOR_SEPARATE_ACTIVATION` cutover preflight. Neither that
preflight nor its manifests grants execution, approval, or activation authority.

Never import or call `scripts/live_pilot_execute.py` from the generic path. Do not
remove either `live_capital_disabled_by_owner_policy` stop until a separate owner
activation change has passed the full runbook and a read-only observation period.

## Current legacy gates that must remain effective

- `$HOME/.caerus/live_pilot.env`
- `CAERUS_LIVE_PILOT_KILL_SWITCH` (engaged during every rehearsal)
- `CAERUS_LIVE_PILOT_APPROVED`
- `CAERUS_LIVE_PILOT_SUBMIT_APPROVED`
- `CAERUS_LIVE_PILOT_CRON_APPROVED`
- `CAERUS_LIVE_PILOT_SCHEDULE_ENABLED`
- `CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH`
- `CAERUS_LIVE_PILOT_CAPITAL_CAP`
- `CAERUS_LIVE_PILOT_MAX_ORDERS`
- `config/research/strategy_registry.json#/sleeve_control_plane/paper_allocation_policy/governance/live_enabled`
- the structural disable blocks in `scripts/cron_live_pilot_execute.sh` and
  `scripts/live_pilot_execute.py`

The read-only inventory hashes these files, records only the listed gate values,
and never records credentials. A missing environment file, disengaged kill switch,
changed source hash, or missing structural stop blocks the cutover candidate.

## Rehearsal sequence

1. Build and validate an exact v4 Live plan from explicit Risk, broker snapshot,
   and governed lane policy artifacts.
2. Build PAPER and LIVE environment bindings and prove they use the same generic
   adapter contract, implementation ID, version, and capabilities.
3. Build read-only Live gate inventory. Keep the kill switch engaged and all
   owner, submit, and schedule gates disarmed.
4. Run safety, no-write OMS, PASS reconciliation, and no-write accounting
   rehearsals.
5. Validate the exact owner decision, capital ceiling, effective session, and
   rollback version; then build the generic cutover preflight.
6. Build the deployment replacement template, rollback manifest, and immutable
   preflight manifest. These are inputs to a later owner policy compilation—not
   active configuration.
7. Invoke `scripts/run_generic_lane_scheduler_dry_run.py` without
   `--enable-advisory-scheduler` first. Expected status: `DISABLED_NO_ACTION`.
8. An explicitly enabled rehearsal may return `VALIDATED_NO_SUBMIT`; it still
   performs no broker call and changes no schedule, configuration, or journal.

## Separate owner actions still required

- Compile and approve a `PENDING` `caerus.lane_deployment_policy.v1` artifact.
- Review rollback hashes and triggers.
- Migrate configuration references with the kill switch engaged.
- Observe the generic Live adapter read-only.
- Separately authorize activation.
- Only after observation, separately authorize retirement of legacy wrappers.

The redacted environment template is
`config/templates/generic_live.env.example`. It intentionally contains no API
keys, account IDs, or usable capital authorization.
