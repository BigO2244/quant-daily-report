from __future__ import annotations

import hashlib
from pathlib import Path

from core.generic_live_v1_ops import install_config_with_backup


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "config/templates/generic_live_v1_dynamic_20260824.disabled.env"


def test_dynamic_disabled_config_has_no_fixed_ceiling_or_execution_enable():
    text = TEMPLATE.read_text()
    assert "CAERUS_GENERIC_LIVE_NOMINAL_CAPITAL_CEILING_USD=NONE" in text
    assert "CAERUS_GENERIC_LIVE_CAPITAL_CEILING_USD=460" not in text
    assert "CAERUS_GENERIC_LIVE_SUBMIT_APPROVED=0" in text
    assert "CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED=0" in text
    assert "CAERUS_GENERIC_LIVE_POSTTRADE_OBSERVATION_ENABLED=0" in text
    assert "CAERUS_GENERIC_LIVE_BUYING_POWER_ALLOWED=0" in text
    assert "CAERUS_GENERIC_LIVE_PENDING_TRANSFERS_REQUIRED_ZERO=1" in text
    assert "REPLACE_WITH_" not in text


def test_dynamic_disabled_config_passes_protected_installer(tmp_path: Path):
    protected = tmp_path / "protected"
    protected.mkdir(mode=0o700)
    candidate = protected / "candidate.env"
    candidate.write_bytes(TEMPLATE.read_bytes())
    candidate.chmod(0o600)
    active = protected / "active.env"
    backup = protected / "active.env.rollback"
    result = install_config_with_backup(
        candidate_path=candidate,
        active_path=active,
        backup_path=backup,
        allowed_roots=[protected],
        expected_candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
    )
    assert result["backup_created"] is False
    assert active.read_bytes() == TEMPLATE.read_bytes()
    assert active.stat().st_mode & 0o777 == 0o600
