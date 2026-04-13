# Nightly Findings

- Generated at: `2026-04-08T14:14:28+00:00`
- Headline: Broker-authoritative state not confirmed

## Summary
- Trade date: 2026-04-08.
- Broker trust level: LOW.
- Pretrade status: UNKNOWN; posttrade reconciliation: UNKNOWN.
- Nightly digest schedule from audit: 0 12 * * 1-5, 0 11 * * 1-5 => both weekday entries target 7:00 AM ET across EST/EDT.

## Risks
- Pretrade broker snapshot was not confirmed in the latest available artifacts.
- Posttrade broker snapshot was not confirmed in the latest available artifacts.
- The latest run did not confirm broker-authoritative post-trade state.
- Posttrade reconciliation status is UNKNOWN.

## Actions
- Sync the latest broker and run artifacts from the scheduler or CI before relying on the dashboard.
