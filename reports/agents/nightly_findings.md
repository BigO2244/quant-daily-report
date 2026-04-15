# Nightly Findings

- Generated at: `2026-04-15T16:59:11+00:00`
- Headline: Broker state partially confirmed

## Summary
- Trade date: 2026-04-13.
- Broker trust level: MEDIUM.
- Pretrade status: UNKNOWN; posttrade reconciliation: UNKNOWN.
- Nightly digest schedule from audit: 0 12 * * 1-5, 0 11 * * 1-5 => both weekday entries target 7:00 AM ET across EST/EDT.

## Risks
- Pretrade broker snapshot was not confirmed in the latest available artifacts.
- The latest run did not confirm broker-authoritative post-trade state.
- Posttrade reconciliation status is UNKNOWN.

## Actions
- Sync the latest broker and run artifacts from the scheduler or CI before relying on the dashboard.
