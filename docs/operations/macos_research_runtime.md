# Mac Studio Advisory Research Runtime

Status: active as of 2026-09-01  
Scheduler: launchd label `com.caerus.quant-research`  
Cadence: 06:30 America/New_York, Monday-Friday  
Capital authority: none

The advisory digest no longer runs on the production scheduler VM. Its Mac
runtime is deliberately outside the iCloud-synced `Documents` tree:

- source snapshot: `~/.caerus/research-runtime/source/quant_research_agent`;
- Python environment: `~/.caerus/venvs/quant-research`;
- outputs: `~/.caerus/research-runtime/outputs`;
- logs: `~/.caerus/research-runtime/logs`;
- pinned source: `~/.caerus/research-runtime/SOURCE_SHA`;
- installed entry point: `~/.caerus/research-runtime/run_research.sh`.

The runtime contains research-only credentials with mode `0600`. It does not
contain Alpaca credentials, cannot submit orders, and is not consumed by
canonical Orion precompute. launchd applies background process class, I/O low
priority, and nice level 10. A directory lock suppresses overlapping runs.

## Verification

```bash
plutil -lint ~/Library/LaunchAgents/com.caerus.quant-research.plist
launchctl print gui/$(id -u)/com.caerus.quant-research
~/.caerus/research-runtime/run_research.sh --dry-run --source macro --no-dedup
```

Acceptance requires a loaded five-weekday 06:30 schedule, exit code zero from
the dry-run entry point, a digest artifact outside `Documents`, and no VM
crontab entry containing `scripts/cron_research.sh`.

## Refresh and rollback

Refresh the source snapshot only from an accepted Git SHA, update
`SOURCE_SHA`, preserve the mode-0600 `.env`, rerun the 34 research-agent tests,
and dry-run the installed entry point before the next scheduled launch.

Rollback is recoverable: boot out `com.caerus.quant-research` and move its
plist out of `~/Library/LaunchAgents`. Do not restore the VM research cron
unless a separately authorized capacity review proves it cannot contend with
precompute or trading services.
