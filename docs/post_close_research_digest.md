# Post-Close Research Digest

The post-close research digest is a VM-safe, read-only automation wrapper that
hydrates the canonical price cache, rebuilds attribution artifacts, builds the
Research Review Packet, and emails Brett a compact operating summary.

Run manually:

```bash
scripts/run_post_close_research_digest.sh --date YYYY-MM-DD
```

Dry run without email:

```bash
scripts/run_post_close_research_digest.sh --date YYYY-MM-DD --no-email
```

## Date Selection

When `--date YYYY-MM-DD` is supplied, that date is used for hydration,
attribution, decision attribution, packet generation, and email. Future dates
are rejected by the email helper.

When no date is supplied:

1. `scripts/hydrate_price_cache_only.py --strict` runs with its default latest
   completed trading-day resolution.
2. The digest selects the latest successful
   `outputs/price_hydration/YYYY-MM-DD/status.json` date.
3. If no successful price-hydration status exists, it falls back to the latest
   dated shadow candidate artifact.
4. If no shadow candidate exists, it falls back to the latest attribution or
   decision-attribution artifact date.
5. If no actionable artifact exists, it uses today's ET date and the downstream
   packet emits missing-artifact reason codes.

This avoids selecting future dates and keeps optional artifact gaps visible
instead of silently hiding them.

## Email

The digest uses `core.quant_report.send_email`, which resolves credentials via
`core.email_env.resolve_email_env` and existing aliases:

- `EMAIL_SENDER` or `SMTP_USER` or `REPORT_EMAIL_FROM`
- `EMAIL_APP_PASSWORD` or `SMTP_PASSWORD`
- `EMAIL_RECIPIENT` or `REPORT_TO_EMAIL` or `REPORT_EMAIL_TO`

Subject:

```text
[Alpha Stack] CIO Research Briefing — YYYY-MM-DD
```

The email starts with the CIO Briefing from the Research Review Packet:

- CIO takeaway.
- 30-second read.
- Strategy leaderboard.
- Key attribution notes.
- Signal evidence.
- Risks/blockers.
- Recommended action.

The artifact-style fields remain below that under `Technical Appendix`,
including readiness, confidence, attribution status, positions analyzed,
decisions analyzed, top contributors, top detractors, data freshness warnings,
recommended next actions, and generated packet paths.

## Suggested Cron

Do not install cron unless explicitly requested. Suggested VM line:

```cron
# Post-close research digest, Monday-Friday at 8:00 PM ET
0 20 * * 1-5 cd /home/brettolson/quant-daily-report && scripts/run_post_close_research_digest.sh >> logs/post_close_research_digest.log 2>&1
```

If the VM path is the project-standard home checkout, use:

```cron
0 20 * * 1-5 cd ~/quant-daily-report && scripts/run_post_close_research_digest.sh >> logs/post_close_research_digest.log 2>&1
```
