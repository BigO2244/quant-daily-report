# 05 Final Summary

## Executive Conclusion

The active VM Shadow scorecard artifacts were recovered under the owner-approved
Option 3 methodology.

Canonical operational method:

- `dated_same_day_close_to_close_v1`

Active scorecard health after replacement:

- `Fresh`
- NAV integrity: `OK`
- `SHADOW_NAV_CHAIN_RESET`: cleared

## Replacement Status

- Active artifacts replaced: yes
- Replacement date/time: `2026-06-14T01:59:54Z`
- Observation start: `2026-05-12`
- Observation end: `2026-06-12`
- NAV rows: `23`
- Daily-return validation rows: `92`
- Non-trading skipped date: `2026-05-25`

## Corrected Scorecard Snapshot

From post-replacement dry run:

| Model | Daily | 7-Day | Since Observation Inception (2026-05-12) | Excess vs SPY |
|---|---:|---:|---:|---:|
| Polaris | +3.12% | +1.32% | +11.41% | +10.93% |
| Orion | +3.97% | +3.50% | +19.19% | +18.70% |
| Lyra | +4.04% | -0.24% | +12.58% | +12.10% |
| SPY | +0.54% | -2.03% | +0.48% | +0.00% |

The scorecard may display promotion-candidate labels after integrity passes,
but no model promotion, model retirement, allocation change, or strategy
lifecycle change was made by this recovery.

## Source Control

- Main code/reports deployed to VM: `0884a2a6b1ff6f3f09cae977864473aa06f73c8a`
- Recovery branch retained: `codex/shadow-nav-historical-recovery`

## Safety

- No broker order was submitted.
- No trading workflow was run.
- No execution, allocation, portfolio construction, model, strategy, promotion,
  retirement, or cron behavior changed.
- No secrets were committed.
- Original corrupt artifacts remain preserved in:
  `outputs/recovery_backups/shadow_nav_incident_20260613T181114Z/`
- Immediate pre-replacement artifacts are preserved in:
  `outputs/recovery_backups/shadow_nav_same_day_restatement_20260614T015954Z/`
