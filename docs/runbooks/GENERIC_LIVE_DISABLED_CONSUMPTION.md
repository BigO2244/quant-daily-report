# Generic Live Disabled Consumption and Owner Review

Status: isolated staging only; no active scheduler, dashboard, notification
sender, configuration, or execution integration.

## Verified Live facts

The redacted read-only observation in
`docs/evidence/generic_live_account_observation_2026-08-18.json` was produced by
one `GET /v2/account` request to the protected Live endpoint. It records:

- observed Live equity and cash: `$460.90`;
- account status: `ACTIVE`, with broker trading/account blocks false;
- the SHA-256 account pin only; no raw account number or credentials;
- no broker write, order submission, configuration mutation, or remote file write.

The disabled candidate in
`docs/evidence/generic_live_disabled_candidate_config_2026-08-18.json` applies
the lower of the owner's `$460` ceiling and observed Live equity, so its
effective ceiling is `$460`. It retains the stricter legacy transition limits:
one order maximum, a `$100` minimum trade, 95% maximum gross, whole shares,
long-only, no leverage, and no shorting.

## Safe consumption boundaries

- `core/owner_notification_outbox.py` can validate an owner destination hash and
  append advisory inbox items only when `write_enabled=True`. It has no sender
  or network integration, and every item remains `PENDING_SEND_DISABLED`.
- `core/dashboard_truth_consumer.py` always requires the canonical sealed truth
  projection. By default it validates and returns no cards. An explicit
  `consumer_enabled=True` may build the existing UI-neutral truth payload in
  memory, but still cannot publish a dashboard or use fallback data.
- `core/generic_paper_live_rehearsal.py` derives structural adapter parity only
  from the sealed scheduled-pipeline receipt and validates both nested execution
  dry runs. Its classification is
  `STRUCTURAL_REHEARSAL_NOT_BROKER_FACTUAL`.
- `core/generic_live_candidate_config.py` emits only disabled candidate and
  blocked preflight artifacts. It cannot change the protected Live environment.

## Remaining external gates

No cutover is authorized. The final candidate preflight must remain blocked
until the active checkout has the generic path, an account pin/capital ceiling/
one-order limit are installed through a separately approved deployment policy,
owner/submission/schedule approvals are recorded, and an active generic schedule
is separately authorized. The legacy Live executor must remain disabled and the
kill switch must remain armed through staging and observation.

Notification delivery, dashboard publication, active config changes, schedule
installation, kill-switch disengagement, and order submission are deliberately
outside this tranche.
