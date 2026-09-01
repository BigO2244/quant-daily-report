from __future__ import annotations

from pathlib import Path

from scripts.validate_cron_commands import validate_cron_text
from scripts.manage_lyra_live_cron import WEEKLY_LINE, WEEKLY_MARKER, render


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _failed(results):
    return [result for result in results if not result.passed]


def test_valid_python_module_passes(tmp_path: Path) -> None:
    _write(tmp_path / "scripts" / "__init__.py", "")
    _write(tmp_path / "scripts" / "example_job.py", "def main():\n    return 0\n")

    results = validate_cron_text("0 1 * * * cd $HOME/quant-daily-report && python3 -m scripts.example_job\n", repo_root=tmp_path)

    assert not _failed(results)
    assert any(result.kind == "python-module" and result.target == "scripts.example_job" for result in results)


def test_missing_python_module_fails(tmp_path: Path) -> None:
    _write(tmp_path / "scripts" / "__init__.py", "")

    results = validate_cron_text("0 1 * * * cd $HOME/quant-daily-report && python3 -m scripts.missing_job\n", repo_root=tmp_path)

    assert any(not result.passed and result.kind == "python-module" and result.target == "scripts.missing_job" for result in results)


def test_valid_shell_script_passes_bash_n(tmp_path: Path) -> None:
    _write(tmp_path / "scripts" / "cron_ok.sh", "#!/usr/bin/env bash\necho ok\n")

    results = validate_cron_text("0 1 * * * $HOME/quant-daily-report/scripts/cron_ok.sh >> logs/x.log 2>&1\n", repo_root=tmp_path)

    assert not _failed(results)
    assert any(result.kind == "bash-n" and result.target == "scripts/cron_ok.sh" for result in results)


def test_missing_shell_script_fails(tmp_path: Path) -> None:
    results = validate_cron_text("0 1 * * * $HOME/quant-daily-report/scripts/missing.sh\n", repo_root=tmp_path)

    assert any(not result.passed and result.kind == "script" and result.target == "scripts/missing.sh" for result in results)


def test_comments_and_env_lines_are_ignored(tmp_path: Path) -> None:
    text = "\n".join(
        [
            "# python3 -m scripts.missing_comment",
            "CRON_TZ=America/New_York",
            "SHELL=/bin/bash",
            "PATH=/usr/local/bin:/usr/bin:/bin",
            "",
        ]
    )

    assert validate_cron_text(text, repo_root=tmp_path) == []


def test_canonical_crontab_preserves_exactly_one_lyra_live_schedule() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts/crontab.txt").read_text(encoding="utf-8")
    assert text.count(WEEKLY_MARKER) == 1
    assert WEEKLY_LINE in text
    assert render(text, install=True).count(WEEKLY_MARKER) == 1


def test_operating_truth_stays_strict_while_cio_email_is_best_effort() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts/crontab.txt").read_text(encoding="utf-8")
    line = next(
        item
        for item in text.splitlines()
        if "python3 scripts/build_operating_truth.py" in item
    )

    assert "build_operating_truth.py" in line
    assert "--strict && python3 -m scripts.send_shadow_cio_report --best-effort-send" in line
