import copy
import json
from pathlib import Path

import pytest

from core.governed_universe_freeze import (
    GovernedUniverseFreezeError,
    build_governed_universe_freeze,
    validate_governed_universe_freeze,
)


ROOT = Path(__file__).resolve().parents[1]


def test_persisted_freeze_binds_exact_current_bytes_and_membership() -> None:
    payload = json.loads(
        (ROOT / "docs/evidence/lyra_governed_universe_freeze_2026-08-19.json").read_text()
    )
    checked = validate_governed_universe_freeze(
        payload, universe_path=ROOT / "data/universe.csv",
        session_as_of="2026-08-19T07:00:00-04:00",
    )
    assert checked["source_sha256"] == "b9b21f2311ef249facf4f7f699fe916b06f4c778262772a3ac2f63e1bfb8cc21"
    assert checked["member_count"] == 200
    assert checked["membership_economics_changed"] is False


def test_builder_is_deterministic_and_matches_persisted_freeze() -> None:
    built = build_governed_universe_freeze(
        universe_path=ROOT / "data/universe.csv",
        generated_at="2026-08-19T02:50:00+00:00",
        effective_from="2026-08-19T00:00:00-04:00",
        source_revision="1b397d004b4d75bbcc1a7efb0e1b2ad55613fdac",
        no_retroactive_use_before="2026-08-19",
    )
    persisted = json.loads(
        (ROOT / "docs/evidence/lyra_governed_universe_freeze_2026-08-19.json").read_text()
    )
    assert built == persisted


def test_file_drift_and_resealed_tamper_fail_closed(tmp_path) -> None:
    payload = json.loads(
        (ROOT / "docs/evidence/lyra_governed_universe_freeze_2026-08-19.json").read_text()
    )
    drifted = tmp_path / "universe.csv"
    drifted.write_bytes((ROOT / "data/universe.csv").read_bytes() + b"FAKE,Unknown\n")
    with pytest.raises(GovernedUniverseFreezeError, match="bytes differ"):
        validate_governed_universe_freeze(payload, universe_path=drifted)
    tampered = copy.deepcopy(payload)
    tampered["member_count"] = 201
    with pytest.raises(GovernedUniverseFreezeError, match="content_hash mismatch"):
        validate_governed_universe_freeze(tampered)


def test_pre_freeze_session_is_rejected() -> None:
    payload = json.loads(
        (ROOT / "docs/evidence/lyra_governed_universe_freeze_2026-08-19.json").read_text()
    )
    with pytest.raises(GovernedUniverseFreezeError, match="predates"):
        validate_governed_universe_freeze(
            payload, universe_path=ROOT / "data/universe.csv",
            session_as_of="2026-08-18T07:00:00-04:00",
        )
