from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.refresh_shadow_scorecard_artifacts import (
    _append_nav_series,
    _load_same_date_broker_context,
    _publish_latest,
)


ALL_NAV_FIELDS = [
    "date",
    "caerus_polaris",
    "caerus_polaris_alpha",
    "caerus_orion",
    "caerus_orion_alpha",
    "caerus_lyra",
    "spy_benchmark",
]
ESTABLISHED_NAV_SLUGS = ("caerus_polaris", "caerus_orion", "caerus_lyra", "spy_benchmark")
ALPHA_NAV_SLUGS = ("caerus_polaris_alpha", "caerus_orion_alpha")


def _write_nav(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["date", "caerus_polaris", "caerus_orion", "caerus_lyra", "spy_benchmark"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_dates(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [row["date"] for row in csv.DictReader(handle)]


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _performance_payload(trade_date: str, *, nav: float, previous_nav: float, daily_return: float) -> dict:
    strategies = {
        slug: {
            "nav": nav,
            "previous_nav": previous_nav,
            "daily_return": daily_return,
        }
        for slug in ("caerus_polaris", "caerus_orion", "caerus_lyra", "spy_benchmark")
    }
    strategies["caerus_polaris_alpha"] = {
        "nav": nav,
        "previous_nav": previous_nav,
        "daily_return": daily_return,
    }
    strategies["caerus_orion_alpha"] = {
        "nav": nav,
        "previous_nav": previous_nav,
        "daily_return": daily_return,
    }
    return {
        "trade_date": trade_date,
        "status": "OK",
        "data_status": "OK",
        "strategies": strategies,
    }


def _established_only_payload(trade_date: str, *, nav: float, previous_nav: float, daily_return: float) -> dict:
    return {
        "trade_date": trade_date,
        "status": "OK",
        "data_status": "OK",
        "strategies": {
            slug: {
                "nav": nav,
                "previous_nav": previous_nav,
                "daily_return": daily_return,
            }
            for slug in ESTABLISHED_NAV_SLUGS
        },
    }


def test_append_rejects_nav_continuity_mismatch_and_preserves_history(tmp_path: Path) -> None:
    output_root = tmp_path / "shadow"
    nav_path = output_root / "performance" / "shadow_nav_series.csv"
    _write_nav(
        nav_path,
        [
            {
                "date": "2026-06-04",
                "caerus_polaris": "36.0",
                "caerus_orion": "158.0",
                "caerus_lyra": "159.0",
                "spy_benchmark": "4.96",
            }
        ],
    )
    before = nav_path.read_text(encoding="utf-8")

    status = _append_nav_series(
        output_root=output_root,
        shadow_performance=_performance_payload(
            "2026-06-05",
            nav=1.01,
            previous_nav=1.0,
            daily_return=0.01,
        ),
    )

    assert status["status"] == "REJECTED"
    assert status["reason_code"] == "SHADOW_NAV_CONTINUITY_MISMATCH"
    assert nav_path.read_text(encoding="utf-8") == before


def test_append_rejects_existing_date_without_restatement_mode(tmp_path: Path) -> None:
    output_root = tmp_path / "shadow"
    nav_path = output_root / "performance" / "shadow_nav_series.csv"
    _write_nav(
        nav_path,
        [
            {
                "date": "2026-06-04",
                "caerus_polaris": "36.0",
                "caerus_orion": "158.0",
                "caerus_lyra": "159.0",
                "spy_benchmark": "4.96",
            }
        ],
    )
    before = nav_path.read_text(encoding="utf-8")

    status = _append_nav_series(
        output_root=output_root,
        shadow_performance=_performance_payload(
            "2026-06-04",
            nav=36.1,
            previous_nav=36.0,
            daily_return=0.0027777778,
        ),
    )

    assert status["status"] == "REJECTED"
    assert status["reason_code"] == "SHADOW_EXISTING_DATE_RESTATEMENT_BLOCKED"
    assert nav_path.read_text(encoding="utf-8") == before


def test_append_accepts_identical_existing_date_as_idempotent_refresh(tmp_path: Path) -> None:
    output_root = tmp_path / "shadow"
    nav_path = output_root / "performance" / "shadow_nav_series.csv"
    _write_nav(
        nav_path,
        [
            {
                "date": "2026-06-04",
                "caerus_polaris": "1.0",
                "caerus_orion": "1.0",
                "caerus_lyra": "1.0",
                "spy_benchmark": "1.0",
            }
        ],
    )
    before = nav_path.read_text(encoding="utf-8")

    status = _append_nav_series(
        output_root=output_root,
        shadow_performance=_performance_payload(
            "2026-06-04",
            nav=1.0,
            previous_nav=1.0,
            daily_return=0.0,
        ),
    )

    assert status["status"] == "OK"
    assert status["reason_code"] == "SHADOW_EXISTING_DATE_IDENTICAL"
    assert status["idempotent"] is True
    assert nav_path.read_text(encoding="utf-8") == before


def test_append_rejects_existing_history_with_missing_strategy_column(tmp_path: Path) -> None:
    output_root = tmp_path / "shadow"
    nav_path = output_root / "performance" / "shadow_nav_series.csv"
    nav_path.parent.mkdir(parents=True, exist_ok=True)
    nav_path.write_text(
        "date,caerus_polaris,caerus_orion,spy_benchmark\n"
        "2026-06-04,36.0,158.0,4.96\n",
        encoding="utf-8",
    )
    before = nav_path.read_text(encoding="utf-8")

    payload = _performance_payload(
        "2026-06-05",
        nav=36.36,
        previous_nav=36.0,
        daily_return=0.01,
    )
    payload["strategies"]["caerus_orion"].update({"nav": 159.58, "previous_nav": 158.0})
    payload["strategies"]["spy_benchmark"].update({"nav": 5.0096, "previous_nav": 4.96})

    status = _append_nav_series(output_root=output_root, shadow_performance=payload)

    assert status["status"] == "REJECTED"
    assert status["reason_code"] == "SHADOW_NAV_SCHEMA_MISMATCH"
    assert nav_path.read_text(encoding="utf-8") == before


def test_append_accepts_legitimate_inception_and_incremental_rows(tmp_path: Path) -> None:
    output_root = tmp_path / "shadow"
    nav_path = output_root / "performance" / "shadow_nav_series.csv"

    first = _append_nav_series(
        output_root=output_root,
        shadow_performance=_performance_payload(
            "2026-01-02",
            nav=1.01,
            previous_nav=1.0,
            daily_return=0.01,
        ),
    )
    second = _append_nav_series(
        output_root=output_root,
        shadow_performance=_performance_payload(
            "2026-01-05",
            nav=1.0302,
            previous_nav=1.01,
            daily_return=0.02,
        ),
    )

    assert first["status"] == "OK"
    assert second["status"] == "OK"
    assert _read_dates(nav_path) == ["2026-01-02", "2026-01-05"]


def test_append_allows_missing_alpha_navs_before_observation_start(tmp_path: Path) -> None:
    output_root = tmp_path / "shadow"
    nav_path = output_root / "performance" / "shadow_nav_series.csv"
    _write_nav(
        nav_path,
        [
            {
                "date": "2026-06-12",
                "caerus_polaris": "1.0",
                "caerus_orion": "1.0",
                "caerus_lyra": "1.0",
                "spy_benchmark": "1.0",
            }
        ],
    )

    status = _append_nav_series(
        output_root=output_root,
        shadow_performance=_established_only_payload(
            "2026-06-15",
            nav=1.01,
            previous_nav=1.0,
            daily_return=0.01,
        ),
    )

    rows = _read_rows(nav_path)
    assert status["status"] == "OK"
    assert rows[-1]["date"] == "2026-06-15"
    assert rows[-1]["caerus_polaris_alpha"] == ""
    assert rows[-1]["caerus_orion_alpha"] == ""


def test_append_accepts_first_alpha_nav_after_blank_pre_observation_row(tmp_path: Path) -> None:
    output_root = tmp_path / "shadow"
    nav_path = output_root / "performance" / "shadow_nav_series.csv"
    nav_path.parent.mkdir(parents=True, exist_ok=True)
    with nav_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ALL_NAV_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "date": "2026-06-22",
                "caerus_polaris": "1.0",
                "caerus_polaris_alpha": "",
                "caerus_orion": "1.0",
                "caerus_orion_alpha": "",
                "caerus_lyra": "1.0",
                "spy_benchmark": "1.0",
            }
        )
    payload = _performance_payload("2026-06-23", nav=1.01, previous_nav=1.0, daily_return=0.01)
    for slug in ALPHA_NAV_SLUGS:
        payload["strategies"][slug] = {
            "nav": 0.934,
            "previous_nav": 1.0,
            "daily_return": -0.066,
        }

    status = _append_nav_series(output_root=output_root, shadow_performance=payload)

    rows = _read_rows(nav_path)
    assert status["status"] == "OK"
    assert rows[-1]["date"] == "2026-06-23"
    assert rows[-1]["caerus_polaris_alpha"] == "0.934"
    assert rows[-1]["caerus_orion_alpha"] == "0.934"


def _write_publish_bundle(output_root: Path, trade_date: str) -> list[str]:
    required = [
        "comparison.md",
        "comparison.json",
        "delta.json",
        "shadow_performance.json",
        "shadow_evaluation.json",
        "longitudinal_metrics.json",
        "stability_surface.json",
        "promotion_readiness.json",
        "promotion_readiness.md",
        "feedback_loop_summary.json",
    ]
    dated = output_root / trade_date
    dated.mkdir(parents=True)
    for artifact in required:
        content = f"dated:{trade_date}:{artifact}\n"
        if artifact.endswith(".json"):
            content = json.dumps({"trade_date": trade_date, "artifact": artifact})
        (dated / artifact).write_text(content, encoding="utf-8")
    return required


def test_publish_latest_replaces_complete_bundle_including_readiness(tmp_path: Path) -> None:
    output_root = tmp_path / "shadow"
    latest = output_root / "latest"
    latest.mkdir(parents=True)
    (latest / "promotion_readiness.json").write_text('{"trade_date":"2026-06-22"}', encoding="utf-8")
    required = _write_publish_bundle(output_root, "2026-06-23")

    status = _publish_latest(output_root, "2026-06-23")

    assert status["status"] == "OK"
    assert sorted(path.name for path in latest.iterdir()) == sorted(required)
    for artifact in required:
        assert (latest / artifact).read_bytes() == (output_root / "2026-06-23" / artifact).read_bytes()
    assert json.loads((latest / "promotion_readiness.json").read_text())["trade_date"] == "2026-06-23"


def test_publish_latest_leaves_existing_bundle_untouched_when_dated_bundle_incomplete(tmp_path: Path) -> None:
    output_root = tmp_path / "shadow"
    latest = output_root / "latest"
    latest.mkdir(parents=True)
    marker = latest / "promotion_readiness.json"
    marker.write_text('{"trade_date":"2026-06-22"}', encoding="utf-8")
    _write_publish_bundle(output_root, "2026-06-23")
    (output_root / "2026-06-23" / "longitudinal_metrics.json").unlink()

    status = _publish_latest(output_root, "2026-06-23")

    assert status["status"] == "WITHHELD"
    assert status["published_artifacts"] == []
    assert status["missing_artifacts"] == ["longitudinal_metrics.json"]
    assert marker.read_text(encoding="utf-8") == '{"trade_date":"2026-06-22"}'


def test_refresh_preserves_only_same_date_broker_context(tmp_path: Path) -> None:
    dated = tmp_path / "2026-06-23"
    dated.mkdir()
    context = {
        "trade_date": "2026-06-23",
        "positions_count": 7,
        "strategy_overlap": {"caerus_polaris": {"overlap_names_count": 0}},
    }
    (dated / "comparison.json").write_text(
        json.dumps({"trade_date": "2026-06-23", "broker_context": context}),
        encoding="utf-8",
    )

    assert _load_same_date_broker_context(dated, "2026-06-23") == context
    assert _load_same_date_broker_context(dated, "2026-06-24") is None
