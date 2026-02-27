import json
import re

import pytest

from paper.run_manager import (
    collect_manifest,
    ensure_dir,
    file_sha256,
    safe_write_text,
    write_latest_pointer,
)


def test_safe_write_text_refuses_overwrite(tmp_path):
    path = tmp_path / "artifact.txt"

    safe_write_text(path, "first\n")

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        safe_write_text(path, "second\n")

    assert path.read_text(encoding="utf-8") == "first\n"


def test_write_latest_pointer_allows_overwrite(tmp_path):
    latest = tmp_path / "latest.json"

    write_latest_pointer(latest, {"run_id": "run-1", "mode": "shadow"})
    write_latest_pointer(latest, {"run_id": "run-2", "mode": "alpaca"})

    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-2"
    assert payload["mode"] == "alpaca"


def test_manifest_and_checksums_smoke(tmp_path):
    run_root = tmp_path / "outputs" / "runs" / "2026-02-27T092501-0500_abc1234"
    ensure_dir(run_root / "reports")
    ensure_dir(run_root / "ledger")

    safe_write_text(run_root / "reports" / "quant_report_2026-02-27.html", "<html></html>\n")
    safe_write_text(run_root / "ledger" / "ledger_write_2026-02-27.json", "{}\n")

    manifest = collect_manifest(run_root)

    assert manifest["file_count"] == 2
    assert {item["path"] for item in manifest["files"]} == {
        "ledger/ledger_write_2026-02-27.json",
        "reports/quant_report_2026-02-27.html",
    }
    for item in manifest["files"]:
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"])

    manifest_path = run_root / "manifest.json"
    checksums_path = run_root / "checksums.sha256"
    safe_write_text(manifest_path, json.dumps(manifest, indent=2) + "\n")

    lines = []
    for item in manifest["files"]:
        abs_path = run_root / item["path"]
        lines.append(f"{file_sha256(abs_path)}  {item['path']}")
    safe_write_text(checksums_path, "\n".join(lines) + "\n")

    assert manifest_path.exists()
    assert checksums_path.exists()
