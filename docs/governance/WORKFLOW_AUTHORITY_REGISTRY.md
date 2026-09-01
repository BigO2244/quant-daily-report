# Caerus Workflow Authority Registry

Status: `CANONICAL_DISCOVERY_AND_RECONCILIATION_RULE`

Last reconciled: 2026-09-01

## Purpose

No single scheduler or repository describes the complete Caerus operating
workflow. Before declaring a workflow present, absent, active, failed, or
unauthorized, inspect all four authorities below and report conflicts rather
than silently choosing one.

## Required authorities

1. **Codex automations** — `$CODEX_HOME/automations/*/automation.toml`, including
   active status, recurrence, project/CWD, prompt boundary, and run memory.
2. **Workspace and project operating files** — the nearest applicable
   `AGENTS.md`, README, current-state document, and referenced governance.
3. **Deployed runtime** — VM cron, deployed Git SHA and cleanliness, runtime
   gates, current artifacts, broker evidence, and automation logs.
4. **GitHub** — canonical branch heads, workflows, reviewed contracts, and
   rollback history.

An app-level automation does not need a duplicate GitHub Action. GitHub records
its approved contract and authority; Codex executes it. Production cron remains
the authority for production trading schedules.

## Approved external research cadence

| Automation ID | Schedule (America/New_York) | Current state | Project | Authority |
|---|---|---|---|---|
| `alpha-lab-weekend-research-cycle` | Sunday 00:05; target completion 05:00 | `PAUSED` | Alpha Lab | Screen at most three candidates, select at most one, and produce one weekly research outcome. No unrecorded formal trial, holdout access, lifecycle change, or capital authority. |
| `alpha-lab-cio-review-queue` | Weekdays 07:15 | `PAUSED` | Alpha Lab | Surface only new or materially changed owner decisions and integrity failures. No lifecycle or production mutation. |
| `alpha-lab-analyst-proxy-daily` | Weekdays 18:10 | `PAUSED` | Alpha Lab / GCP research root | Research-only analyst-proxy evidence collection. |
| `caerus-options-proxy-daily-observation` | Weekdays 16:20 | `PAUSED` | Alpha Lab / GCP research root | Forward, non-executing options-proxy observation and maturation. |
| `alpha-lab-gcp-storage-audit` | Daily 19:00 | `PAUSED` | Alpha Lab / GCP research root | Read-only storage, manifest, and checksum assurance. |
| `alpha-lab-free-data-completion` | Hourly heartbeat while active | `PAUSED` | Alpha Lab / GCP research root | Bounded free-data work subject to storage, lock, and incident guards. |

These automations were paused during the September 1 production recovery
because their executable prompts use `ssh caerus-vm` and could consume the
958 MiB production scheduler's CPU, memory, network, or research disk. Their
research authority is preserved, but schedule authority is inactive. Re-enable
only after a research-only Mac/external-data execution contract passes its
capacity and isolation tests.

The executable prompt remains in Codex automation state. The Alpha Lab branch
holds the durable investment-research contract, state transitions, evidence
requirements, and rollback rules.

## Production VM cadence

The installed production schedule is `scripts/crontab.txt`. As of this
reconciliation it contains security-master refresh, precompute, Orion PAPER
execution and confirmation, independently governed Lyra Live execution,
post-close Shadow hydration/reporting, broker ledgers, portfolio audit, and the
weekly model review.

The 06:30 research digest is advisory, runs on the Mac Studio under launchd
label `com.caerus.quant-research`, and has no active canonical-precompute
consumer. It is intentionally absent from the production VM crontab. There is
no deployed 01:00 overnight-agent job. Historical designs or reports must not
be represented as current runtime behavior.

The dashboard refresh is a separate systemd timer, not cron. It runs every 15
minutes after the prior activation, is limited to 120 seconds, 320 MB, 64
tasks, low CPU/I/O priority, and skips all declared production-critical
windows. Ubuntu package metadata and automatic upgrades remain enabled in
tracked 01:15 and 02:15 ET quiet windows with at most 15 minutes of jitter.

## Conflict policy

- Deployed broker and artifact evidence controls capital-lane operating truth.
- Owner decisions and runtime gates control authority.
- Alpha Lab ledger and manifested evidence control research results.
- Atlas controls cross-repository program phase and approved cadence changes.
- GitHub controls durable code, governance, and rollback history.

A conflict makes the affected claim unproved until reconciled. It does not
automatically halt an unrelated capital lane whose authority and evidence are
otherwise intact.
