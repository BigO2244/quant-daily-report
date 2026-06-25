# FR-DH Runtime Credentials Setup

Status: DRAFT_RESEARCH / READ_ONLY

Owner / steward placeholder: Caerus Research Data Steward (TBD)

Runtime impact statement: Documentation and read-only data-access setup only.
This document does not change paper/live/shadow trading behavior, execution
logic, broker calls, model decisions, scheduler state, or sleeve consumption.

## Strategic Purpose

Provide safe, non-secret instructions for loading the existing Nasdaq Data Link
/ Sharadar credential into the runtime shell used by FR-DH hydration probes.

The goal is to make focused source probes reproducible without committing,
printing, or embedding secret values.

## Credential Names

Existing Caerus Sharadar/Nasdaq Data Link scripts resolve credentials in this
order:

1. `NASDAQ_DATA_LINK_API_KEY`
2. `QUANDL_API_KEY`

`NASDAQ_DATA_LINK_API_KEY` is the canonical variable. `QUANDL_API_KEY` is the
legacy fallback retained for compatibility with existing scripts.

## Safe Loading Patterns

Do not commit secrets to the repository. Do not paste secrets into logs,
artifacts, tickets, markdown, shell history shared with agents, or command
output. Disable shell tracing before loading credentials:

```bash
set +x
```

For a one-session local probe:

```bash
export NASDAQ_DATA_LINK_API_KEY=<key>
```

For a repo-approved env-file workflow, keep the file outside the repository,
restrict permissions, and source it only in the shell that will run probes:

```bash
mkdir -p ~/.caerus
chmod 700 ~/.caerus
printf 'export NASDAQ_DATA_LINK_API_KEY=<key>\n' > ~/.caerus/nasdaq_data_link.env
chmod 600 ~/.caerus/nasdaq_data_link.env
set +x
. ~/.caerus/nasdaq_data_link.env
```

The FR-DH swarm can also load that file directly:

```bash
.venv/bin/python scripts/data_hydration/run_data_hydration_swarm.py \
  --env-file ~/.caerus/nasdaq_data_link.env \
  --limit-sample \
  --datasets corporate_actions security_master_pit etf_index_constituents fundamentals_pit \
  --sources nasdaq_sharadar
```

## Presence Checks

Use presence-only checks. Do not print values:

```bash
test -n "${NASDAQ_DATA_LINK_API_KEY:-}" && echo NASDAQ_DATA_LINK_API_KEY=present || echo NASDAQ_DATA_LINK_API_KEY=missing
test -n "${QUANDL_API_KEY:-}" && echo QUANDL_API_KEY=present || echo QUANDL_API_KEY=missing
```

Expected classifications:

- Missing env var in current shell: `BLOCKED_CREDENTIALS`
- HTTP 401 or 403 from Nasdaq Data Link: `BLOCKED_AUTH_OR_ENTITLEMENT`
- HTTP 429 from Nasdaq Data Link: `RATE_LIMITED`
- Malformed response: `SCHEMA_ERROR`
- Empty response: `EMPTY_RESULT`
- Valid sample response: `PARTIAL`

## Focused Probe Commands

Credential-presence dry-run:

```bash
.venv/bin/python scripts/data_hydration/run_data_hydration_swarm.py \
  --dry-run \
  --datasets corporate_actions security_master_pit etf_index_constituents fundamentals_pit \
  --sources nasdaq_sharadar
```

Sharadar/Nasdaq focused sample probe after loading the key:

```bash
.venv/bin/python scripts/data_hydration/run_data_hydration_swarm.py \
  --limit-sample \
  --datasets corporate_actions security_master_pit etf_index_constituents fundamentals_pit \
  --sources nasdaq_sharadar
```

Single-dataset examples:

```bash
.venv/bin/python scripts/data_hydration/run_data_hydration_swarm.py --limit-sample --datasets corporate_actions --sources nasdaq_sharadar
.venv/bin/python scripts/data_hydration/run_data_hydration_swarm.py --limit-sample --datasets security_master_pit --sources nasdaq_sharadar
.venv/bin/python scripts/data_hydration/run_data_hydration_swarm.py --limit-sample --datasets etf_index_constituents --sources nasdaq_sharadar
.venv/bin/python scripts/data_hydration/run_data_hydration_swarm.py --limit-sample --datasets fundamentals_pit --sources nasdaq_sharadar
```

## Non-Interactive Runtime Notes

For VM or scheduler-style shells, configure the secret in the environment of
the process that launches the read-only probe. Keep the env file outside the
repository and outside generated artifacts. If the scheduler uses systemd or a
wrapper script, the only acceptable change is to load the credential for the
hydration probe process; do not change trading, broker, execution, or sleeve
runtime paths as part of this credential setup.

## Acceptance Criteria

- Credential values are never committed, printed, or persisted in artifacts.
- Focused probes can be run by dataset and source.
- Missing credentials remain non-fatal to the full hydration swarm.
- No trading or execution path imports or consumes the hydrated data.

## Recommended Next Step

Load `NASDAQ_DATA_LINK_API_KEY` in the intended runtime shell and run the
focused `nasdaq_sharadar` probe for P1/P2 Sharadar-backed datasets before
starting P1 canonical normalization.
