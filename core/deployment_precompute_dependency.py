"""Read actual completed-session inputs before publishing a deployment."""
from __future__ import annotations
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo


def dependency_report_date(now: dt.datetime | None = None) -> str:
    from paper.trading_calendar import is_trading_day, next_trading_day, market_session_status
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError('timezone required')
    local = now.astimezone(ZoneInfo('America/New_York'))
    day = local.date().isoformat()
    if is_trading_day(day):
        session = market_session_status(day, local, '16:00')
        if local < session.session_close_et:
            return day
    return next_trading_day(day)


def validate_candidate_dependency(*, runtime_root: Path, evidence_root: Path,
                                  candidate_sha: str, now: dt.datetime | None = None) -> dict:
    from core.orion_precompute_guard import validate_orion_precompute_dependency
    # This hypothetical attestation is used only inside prepublication validation.
    # No runtime CLI flag/environment setting can replace its real attestation.
    state = {'schema_version': 'caerus.deploy_state.v2', 'deployed_sha': candidate_sha,
             'validated_sha': candidate_sha, 'target_sha': candidate_sha, 'validation_status': 'PASS'}
    return validate_orion_precompute_dependency(
        repo_root=evidence_root, runtime_root=runtime_root,
        report_date=dependency_report_date(now), candidate_deploy_state=state)
