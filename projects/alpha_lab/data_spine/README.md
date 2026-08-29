# Alpha Lab Point-in-Time Data Spine

Governance: RESEARCH_ONLY / NONEXECUTIONAL / NO_PRODUCTION_INTEGRATION

The canonical landing, integrity, naming, access, retention, and recovery rules
are in `../DATA_STORAGE_GOVERNANCE.md` and `../gcp_storage_policy.json`. The
commands below are collector syntax; after the 2026-07-17 cutover they must run
from the GCP repository root, never from the frozen Mac rollback tree.

This package acquires and validates the common external inputs needed by the
four frozen Alpha Lab experiments. It cannot submit orders, change strategy
state, integrate with production cron, or authorize an alpha claim.

## Common artifact contract

Every collector writes an immutable bundle under
outputs/research/alpha_lab/data_spine/<source>/<bundle-id>/ containing:

- raw source bytes where licensing permits;
- normalized research tables where the source schema is stable;
- byte counts and SHA-256 checksums;
- retrieval time and source-specific PIT limitations;
- no credential values; and
- an immutable manifest.

The collectors deliberately distinguish CAPTURED from provider READY. A
current-vintage file, incomplete schema, or unproven historical availability
lineage does not pass a frozen experiment's provider gate.

## Access requirements

| Source | Credential | Current rule |
|---|---|---|
| Sharadar | NASDAQ_DATA_LINK_API_KEY | Required to audit or capture TICKERS, SEP, ACTIONS, SF1, and DAILY. The value is never persisted. |
| SEC EDGAR | SEC_USER_AGENT | No API key. Must contain a client name and approved contact email. |
| FRED/ALFRED | FRED_API_KEY | Present in the current environment. |
| EIA | EIA_API_KEY optional | Petroleum and natural-gas bulk archives need no key. A free key is preferable for narrow electricity queries. |
| BEA | BEA_API_KEY optional | Public concordance/guide need no key. The free 36-character key enables InputOutput table capture. |
| Alpha Vantage | ALPHA_VANTAGE_API_KEY | Free key; bounded to the published daily allowance. Current aggregate forward proxy only. |
| yfinance analyst proxy | None | Current EPS/revenue aggregate estimates, trends, and revision counts; forward-only unofficial interface. |
| USAspending | None | Public federal-award API; exact-name government-customer proxy only. |
| French/AQR | None | Public research downloads; preserve every downloaded vintage and terms sheet. |
| OCC | None | Public web access currently blocks the automated client; use manual intake for contract-specific records. |

## Canonical local credential storage

The single canonical local credential file for this checkout is:

    /Users/brettolson/Documents/Caerus/alpha-lab-project/.env

## Authoritative acquisition storage

As of 2026-07-17, resumable free-data acquisition runs on the scheduler VM's
separate research disk, not on the Mac checkout or the VM boot disk:

    host: caerus-vm (alpha-stack-scheduler)
    disk resource: disk-20260717-164633 (500 GB Standard Persistent Disk)
    mount: /mnt/disks/alpha-lab
    research root: /mnt/disks/alpha-lab/alpha-lab-project
    research Python: /mnt/disks/alpha-lab/venvs/alpha-lab/bin/python
    research credentials: /mnt/disks/alpha-lab/alpha-lab-project/.env
    temporary files: /mnt/disks/alpha-lab/tmp
    research cache: /mnt/disks/alpha-lab/cache

The VM `.env` is mode `600` and contains research-only credentials. Broker and
trading credentials are excluded. The original Mac artifact tree is a frozen
rollback copy and must not be used for new acquisition after the cutover.

At cutover, the VM 8-K checkpoint had 37 complete partitions while the frozen
Mac rollback copy had 36. Therefore the VM checkpoint is authoritative. Form 4
was already finalized into immutable bundle
`20260716T223024Z-ec999cde7593`; USAspending had 136 clean checkpoint
partitions. Do not merge checkpoint directories in either direction.

All VM acquisition commands must first verify that no research collector is
active and that at least 50 GiB remains free on `/mnt/disks/alpha-lab`. Run at
most one bounded acquisition command at a time. This storage cutover does not
authorize changes to trading, paper, broker, allocation, production cron,
deployment, strategy state, returns, or frozen holdout data.

The disk has GCP labels `purpose=alpha-lab-data` and
`environment=research`. Resource policy `alpha-lab-weekly-snapshot` creates a
weekly snapshot on Sunday at 06:00 UTC, retains scheduled snapshots for 28
days, and keeps automatic snapshots if the source disk is deleted. Snapshot
labels are `purpose=alpha-lab-backup` and `environment=research`.

Do not search the VM or sibling Caerus checkouts for a second copy. The root
`.gitignore` excludes `.env` and `.env.*`, and data-spine artifacts never
persist credential values. The file is local-only and should have mode `600`.
The CLI loads simple `KEY=value` entries automatically and never overrides an
environment variable supplied by the caller.

Create it from the non-secret template and edit it without placing a key in
shell history:

    cd /Users/brettolson/Documents/Caerus/alpha-lab-project
    cp projects/alpha_lab/data_spine/env.example .env
    chmod 600 .env
    nano .env

The required Sharadar entry is `NASDAQ_DATA_LINK_API_KEY`. Manual `source` is
no longer required for data-spine CLI commands.

Never paste a credential into chat or directly into a shell command. Rotate a
credential before storing it here if it has appeared in either place.

## Commands

    python -m projects.alpha_lab.data_spine.cli --repo-root . status
    python -m projects.alpha_lab.data_spine.cli --repo-root . validate-boundary
    python -m projects.alpha_lab.data_spine.cli --repo-root . audit-sharadar
    python -m projects.alpha_lab.data_spine.cli --repo-root . factors
    python -m projects.alpha_lab.data_spine.cli --repo-root . fred
    python -m projects.alpha_lab.data_spine.cli --repo-root . eia-audit
    python -m projects.alpha_lab.data_spine.cli --repo-root . eia-bulk --datasets natural_gas,petroleum
    python -m projects.alpha_lab.data_spine.cli --repo-root . eia-large-bulk --dataset electricity
    python -m projects.alpha_lab.data_spine.cli --repo-root . materialize-eia-electricity
    python -m projects.alpha_lab.data_spine.cli --repo-root . bea-io-reference
    python -m projects.alpha_lab.data_spine.cli --repo-root . bea-io-api --table-ids 56 --years ALL
    python -m projects.alpha_lab.data_spine.cli --repo-root . alpha-vantage-free-proxies --tickers IBM,AAPL --max-tickers 20
    python -m projects.alpha_lab.data_spine.cli --repo-root . yfinance-analyst-proxy --tickers-file data/universe.csv --workers 4
    python -m projects.alpha_lab.data_spine.cli --repo-root . usaspending-government-customers --partition-size 100 --max-new-partitions 1

The yfinance analyst proxy is accumulated on weekdays at 18:10 ET by the local
Codex automation `Alpha Lab analyst proxy daily`, which connects through
`ssh caerus-vm` and runs the collector against the GCP research root. It writes
only immutable research bundles and is not installed in Caerus production
cron. The automation must skip a date that already has a successful bundle.
    python -m projects.alpha_lab.data_spine.cli --repo-root . occ
    python -m projects.alpha_lab.data_spine.cli --repo-root . occ-intake --directory /path/to/downloaded/occ/files
    python -m projects.alpha_lab.data_spine.cli --repo-root . materialize-identity
    python -m projects.alpha_lab.data_spine.cli --repo-root . materialize-controls
    python -m projects.alpha_lab.data_spine.cli --repo-root . materialize-sec-facts
    python -m projects.alpha_lab.data_spine.cli --repo-root . materialize-earnings-events
    python -m projects.alpha_lab.data_spine.cli --repo-root . prepare-earnings-hydration
    python -m projects.alpha_lab.data_spine.cli --repo-root . prepare-delisting-hydration
    python -m projects.alpha_lab.data_spine.cli --repo-root . prepare-combined-8k-hydration
    python -m projects.alpha_lab.data_spine.cli --repo-root . materialize-insiders

After an approved SEC contact is configured:

    export SEC_USER_AGENT="Caerus Research contact@example.com"

Capture the SEC bulk submissions archive, then build the conservative Item 2.02
earnings-results tape:

    python -m projects.alpha_lab.data_spine.cli --repo-root . sec-submissions
    python -m projects.alpha_lab.data_spine.cli --repo-root . materialize-earnings-events

This tape uses exact SEC acceptance time and waits until the next full regular
session. It identifies results announcements from 8-K Item 2.02 and joins filed
Company Facts by accession when available. It does not infer scheduled earnings
dates, analyst expectations, or guidance sentiment; those remain explicit
blockers rather than silently imputed fields.

The full original Form 4/4-A capture uses compressed 1,000-filing partitions
and deterministic checkpoints. Re-running the same command resumes completed
partitions instead of downloading them again:

    python -m projects.alpha_lab.data_spine.cli --repo-root . sec-original-stream \
      --index outputs/research/alpha_lab/shared/form4_purchase_hydration_index.csv \
      --forms 4,4/A --partition-size 1000

Use `--max-new-partitions N` for a bounded batch. Re-run the same command to
continue; the immutable bundle is finalized only after every partition exists.
The finalized manifest's `candidate_count` is a discovery-row count, not a
unique-filing count: 316,822 discovery rows resolve to 155,253 distinct source
payloads and 155,245 unique accessions. Materialization verifies inventory and
archive order plus every source SHA-256, deduplicates globally by source
payload, and includes the source hash in event/control IDs so the eight
historical accession collisions remain distinct without creating duplicate
events.

After the Form 4 capture completes, hydrate the combined earnings/delisting 8-K
queue without downloading overlapping filings twice:

    python -m projects.alpha_lab.data_spine.cli --repo-root . sec-original-stream \
      --index outputs/research/alpha_lab/shared/combined_8k_hydration_index.csv \
      --forms 8-K,8-K/A --partition-size 1000 --max-new-partitions 10 --request-workers 4

The delisting queue is discovery evidence. Nearby 8-K presence does not prove
cash/share consideration, contingent-value rights, bankruptcy recovery, or a
terminal return; original exhibits must be parsed and case-specific evidence
must pass before any settlement is certified.
    python -m projects.alpha_lab.data_spine.cli --repo-root . sec-reference
    python -m projects.alpha_lab.data_spine.cli --repo-root . sec-index --start-year 2012 --end-year 2026
    python -m projects.alpha_lab.data_spine.cli --repo-root . sec-companyfacts
    python -m projects.alpha_lab.data_spine.cli --repo-root . sec-insiders --start-year 2012 --end-year 2026
    python -m projects.alpha_lab.data_spine.cli --repo-root . prepare-insider-hydration
    python -m projects.alpha_lab.data_spine.cli --repo-root . sec-hydrate --index outputs/research/alpha_lab/shared/form4_purchase_hydration_index.csv --forms 4,4/A --limit 500
    python -m projects.alpha_lab.data_spine.cli --repo-root . audit-insider-hydration
    python -m projects.alpha_lab.data_spine.cli --repo-root . materialize-original-insiders

The SEC hydrator retains each original submission and converts the EDGAR
acceptance timestamp from America/New_York to UTC. `materialize-original-insiders`
builds the canonical research event tape directly from ownership XML, uses the
quarterly flat file only for candidate discovery, joins effective-dated security
identity, and assigns conservative next-session availability. A finalized
500-filing bundle is labeled pilot-only; it never certifies full history. Form
4/A lineage fails closed without guessing: if an issuer has any captured
amendment, every original and amended event for that issuer is excluded from
the eligible tape. The quality artifact reports the resulting row and purchase
coverage loss. This package creates research evidence, not a second strategy
implementation.

For the current Sharadar capture:

    python -m projects.alpha_lab.data_spine.cli --repo-root . audit-sharadar
    python -m projects.alpha_lab.data_spine.cli --repo-root . capture-sharadar --table TICKERS
    python -m projects.alpha_lab.data_spine.cli --repo-root . capture-sharadar --table ACTIONS
    python -m projects.alpha_lab.data_spine.cli --repo-root . capture-sharadar-stream --table SEP --columns ticker,date,open,high,low,close,volume,closeadj,closeunadj,lastupdated --tickers-file outputs/research/alpha_lab/shared/sep_capture_universe.txt --ticker-chunk-size 200 --start-date 2011-01-01 --end-date 2026-06-30

The SEP stream is cursor-paginated, compressed, retried after transient network
failures, and checkpointed at deterministic ticker-chunk boundaries. A rerun
resumes complete chunks. After it completes, compile the research panels with:

    python -m projects.alpha_lab.data_spine.cli --repo-root . materialize-market --sep-manifest /absolute/path/to/sharadar_sep_stream/.../manifest.json

The v3 market panel retains the provider's final observed daily return as
`last_observed_total_return`; it leaves `delisting_return` and
`terminal_return` null until settlement proceeds are independently verified.
For honest discovery sensitivity—not settlement certification—build the
immutable two-scenario envelope:

    python -m projects.alpha_lab.data_spine.cli --repo-root . terminal-return-sensitivity

The envelope reports a pessimistic further 100% loss and a zero-incremental
return scenario for every terminated security. Both must be reported when used.
Neither is a verified point estimate, satisfies the frozen terminal-return
provider gate, or permits an alpha claim by itself.

Do not use DAILY or SF1 from the current entitlement for historical inference:
strong ticker/date probes show that both are sample-only, even though a one-row
access probe succeeds.

Vendor trial extracts are checked without copying licensed sample content:

    python -m projects.alpha_lab.data_spine.cli --repo-root . validate-vendor --kind analyst_estimates --sample /absolute/path/to/sample.csv
    python -m projects.alpha_lab.data_spine.cli --repo-root . validate-vendor --kind supply_chain --sample /absolute/path/to/sample.csv

Passing the physical schema gate still leaves timestamp, amendment, identifier,
coverage, and licensing audits outstanding.
