from __future__ import annotations

import hashlib
from pathlib import Path

from core.generic_live_v1_ops import install_config_with_backup
import pytest


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
    assert "CAERUS_GENERIC_LIVE_COMPLETE_ORDER_HISTORY_REQUIRED=1" in text
    assert "CAERUS_GENERIC_LIVE_COMPLETE_FILL_HISTORY_REQUIRED=1" in text
    assert "CAERUS_GENERIC_LIVE_MINIMUM_TRADE_USD=1" in text
    assert "CAERUS_GENERIC_LIVE_WHOLE_SHARE_ONLY=0" in text
    assert "CAERUS_GENERIC_LIVE_QUANTITY_PRECISION=6" in text
    assert "CAERUS_GENERIC_LIVE_GOVERNED_FEE_SCHEDULE_HASH=REPLACE_WITH_" in text


def test_dynamic_disabled_config_cannot_install_until_fee_schedule_is_pinned(tmp_path: Path):
    protected = tmp_path / "protected"
    protected.mkdir(mode=0o700)
    candidate = protected / "candidate.env"
    candidate.write_bytes(TEMPLATE.read_bytes())
    candidate.chmod(0o600)
    active = protected / "active.env"
    backup = protected / "active.env.rollback"
    with pytest.raises(Exception, match="unresolved template token"):
        install_config_with_backup(
            candidate_path=candidate,
            active_path=active,
            backup_path=backup,
            allowed_roots=[protected],
            expected_candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
        )
    assert not active.exists()
