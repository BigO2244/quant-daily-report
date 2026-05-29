"""Coverage for the operator-facing research-MCP CLI bridge.

The tests cover three concerns:

1. Pure rendering — given a structured payload, the human and Markdown
   outputs are deterministic and contain the headline numbers the
   operator needs.
2. CLI behaviour — argument parsing, artifact writing, exit codes for
   each terminal status, ``--no-write`` and ``--raw-json`` paths.
3. End-to-end against the real MCP — the script routes through the
   actual ``call_tool`` and produces non-empty output without raising.

Nothing here writes outside ``tmp_path`` (or, for the end-to-end test,
the standard outputs root with a unique timestamp).
"""

from __future__ import annotations

import datetime as dt
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import research_mcp_ask as rma


REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Pure rendering
# ---------------------------------------------------------------------------


def _ok_payload() -> dict:
    return {
        "status": "OK",
        "tool": "answer_research_question",
        "question": "Does timing matter more in high VIX regimes?",
        "intent": "timing_by_vix_regime",
        "routed_to": "execution_timing_by_vix_regime",
        "warnings": [],
        "answer": {
            "status": "OK",
            "run_date": "2026-05-29",
            "baseline_offset": "T+5m",
            "cache_key_version": "intraday_bars_v1_iex_0925_1030",
            "offsets": ["T+0m", "T+1m", "T+5m", "T+10m"],
            "coverage": {"insufficient_sample_threshold": 5},
            "regime_aggregates": [
                {
                    "regime": "ELEVATED",
                    "n_days": 12,
                    "insufficient_sample": False,
                    "by_offset": {
                        "T+0m": {"mean_opportunity_usd": 5.50, "median_opportunity_usd": 5.10},
                        "T+1m": {"mean_opportunity_usd": 4.20, "median_opportunity_usd": 4.00},
                        "T+5m": {"mean_opportunity_usd": 0.0, "median_opportunity_usd": 0.0},
                        "T+10m": {"mean_opportunity_usd": -3.10, "median_opportunity_usd": -2.80},
                    },
                },
                {
                    "regime": "NORMAL",
                    "n_days": 3,
                    "insufficient_sample": True,
                    "by_offset": {
                        "T+0m": {"mean_opportunity_usd": 0.20, "median_opportunity_usd": 0.10},
                        "T+1m": {"mean_opportunity_usd": 0.10, "median_opportunity_usd": 0.05},
                        "T+5m": {"mean_opportunity_usd": 0.0, "median_opportunity_usd": 0.0},
                        "T+10m": {"mean_opportunity_usd": -0.50, "median_opportunity_usd": -0.40},
                    },
                },
            ],
        },
    }


def test_render_ok_payload_includes_headline_numbers_and_baseline():
    human, md = rma.render_human_and_markdown("Does timing matter?", _ok_payload())

    # Headline numbers visible in human view.
    assert "ELEVATED" in human
    assert "12" in human  # n_days
    assert "$5.50" in human  # T+0 mean for ELEVATED
    assert "T+5m" in human  # baseline marker
    # Insufficient-sample tag rendered for the small-N regime.
    assert "NORMAL *" in human or "* = insufficient" in human

    # Markdown carries the same content in a table format.
    assert "| regime |" in md or "| regime " in md
    assert "ELEVATED" in md
    assert "$5.50" in md
    assert "Per-regime opportunity" in md


def test_render_no_timing_data_includes_next_command():
    payload = {
        "status": "NO_TIMING_DATA",
        "tool": "answer_research_question",
        "warnings": ["execution_timing replay has not been run yet"],
        "answer": {
            "status": "NO_TIMING_DATA",
            "reason": "no timing-replay run found",
            "regime_aggregates": [],
        },
    }
    human, md = rma.render_human_and_markdown("Q", payload)
    assert "outputs/research/execution_timing" in human
    assert "scripts.research.execution_timing_replay" in human
    assert "Required artifact missing" in md


def test_render_bad_regime_schema_lists_missing_columns():
    payload = {
        "status": "BAD_REGIME_SCHEMA",
        "warnings": [],
        "answer": {
            "status": "BAD_REGIME_SCHEMA",
            "missing_columns": ["date|as_of|execution_date"],
            "regime_aggregates": [],
        },
    }
    human, _ = rma.render_human_and_markdown("Q", payload)
    assert "date|as_of|execution_date" in human
    assert "_REGIME_DATE_COLUMN_CANDIDATES" in human


def test_render_unsupported_intent_lists_supported_phrasings():
    payload = {
        "status": "UNSUPPORTED_INTENT",
        "intent": None,
        "warnings": [],
        "available_intents": [
            {
                "intent": "timing_by_vix_regime",
                "matches": ["timing + VIX", "timing + regime", "high VIX + timing"],
                "example_question": "Does execution timing matter more in high-VIX regimes?",
            }
        ],
    }
    human, md = rma.render_human_and_markdown("alpha?", payload)
    assert "timing + VIX" in human
    assert "Does execution timing matter more in high-VIX regimes?" in human
    assert "Supported phrasings" in md


def test_status_to_exit_code_mapping():
    assert rma.status_to_exit_code("OK") == 0
    assert rma.status_to_exit_code("NO_TIMING_DATA") == 2
    assert rma.status_to_exit_code("NO_REGIME_DATA") == 2
    assert rma.status_to_exit_code("BAD_REGIME_SCHEMA") == 2
    assert rma.status_to_exit_code("UNSUPPORTED_INTENT") == 3
    assert rma.status_to_exit_code("WEIRD_FUTURE_STATUS") == 0  # clean default


def test_now_stamp_is_filesystem_safe():
    stamp = rma._now_stamp(dt.datetime(2026, 5, 29, 16, 35, 12, tzinfo=dt.timezone.utc))
    assert stamp == "2026-05-29T16-35-12Z"
    assert ":" not in stamp


# ---------------------------------------------------------------------------
# CLI behaviour
# ---------------------------------------------------------------------------


def _run_cli(argv: list[str], capsys, monkeypatch=None) -> tuple[int, str, str]:
    rc = rma.main(argv)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_cli_writes_artifacts_for_unsupported_intent(tmp_path, capsys):
    rc, out, err = _run_cli(
        [
            "What was the alpha last quarter?",
            "--output-root", str(tmp_path / "out"),
            "--timestamp", "2026-05-29T16-35-12Z",
        ],
        capsys,
    )
    assert rc == 3
    assert "UNSUPPORTED_INTENT" in out
    artifact_dir = tmp_path / "out" / "2026-05-29T16-35-12Z"
    assert (artifact_dir / "answer.json").exists()
    assert (artifact_dir / "answer.md").exists()
    # answer.json round-trips as JSON.
    payload = json.loads((artifact_dir / "answer.json").read_text())
    assert payload["status"] == "UNSUPPORTED_INTENT"
    md = (artifact_dir / "answer.md").read_text()
    assert "Supported phrasings" in md


def test_cli_routes_timing_question_against_real_mcp_when_no_data(tmp_path, capsys):
    """The MCP knows the timing artifact is absent in tmp_path's pseudo-root.
    The script must still complete cleanly and write its artifacts."""
    rc, out, err = _run_cli(
        [
            "Does timing matter more in high VIX regimes?",
            "--output-root", str(tmp_path / "out"),
            "--timestamp", "2026-05-29T16-35-12Z",
        ],
        capsys,
    )
    # Exit 2 because on a clean machine outputs/research/execution_timing is
    # absent; on the VM with a populated cache this would be 0.
    assert rc in {0, 2}
    assert "Question:" in out
    assert "Status:" in out
    artifact_dir = tmp_path / "out" / "2026-05-29T16-35-12Z"
    payload = json.loads((artifact_dir / "answer.json").read_text())
    assert payload["intent"] == "timing_by_vix_regime"


def test_cli_no_write_skips_artifacts(tmp_path, capsys):
    rc, out, err = _run_cli(
        [
            "What was the alpha last quarter?",
            "--output-root", str(tmp_path / "out"),
            "--no-write",
        ],
        capsys,
    )
    assert rc == 3
    assert not (tmp_path / "out").exists()
    assert "Artifacts:" not in out  # no artifact section when --no-write


def test_cli_raw_json_emits_parseable_json(tmp_path, capsys):
    rc, out, err = _run_cli(
        [
            "What was the alpha last quarter?",
            "--output-root", str(tmp_path / "out"),
            "--timestamp", "2026-05-29T16-35-12Z",
            "--raw-json",
        ],
        capsys,
    )
    payload = json.loads(out)
    assert payload["status"] == "UNSUPPORTED_INTENT"


def test_cli_empty_question_is_rejected(tmp_path, capsys):
    rc, out, err = _run_cli(
        [
            "   ",
            "--output-root", str(tmp_path / "out"),
            "--no-write",
        ],
        capsys,
    )
    assert rc == 1
    assert "must not be empty" in err


def test_cli_unknown_tool_reports_error(tmp_path, capsys):
    """If --tool overrides to something that doesn't accept 'question', the
    script reports a clean error and exits 1 — it doesn't crash."""
    rc, out, err = _run_cli(
        [
            "Does timing matter more in high VIX regimes?",
            "--output-root", str(tmp_path / "out"),
            "--no-write",
            "--tool", "execution_timing_by_vix_regime",  # does NOT accept question
        ],
        capsys,
    )
    assert rc == 1
    assert "did not accept 'question'" in err


# ---------------------------------------------------------------------------
# Subprocess smoke — exercise the module entrypoint exactly as an operator
# would type it on the VM.
# ---------------------------------------------------------------------------


def test_module_entrypoint_runs_via_python_m(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "scripts.research_mcp_ask",
            "Does timing matter more in high VIX regimes?",
            "--output-root", str(tmp_path / "out"),
            "--timestamp", "2026-05-29T16-35-12Z",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode in {0, 2}
    assert "Question:" in result.stdout
    assert (tmp_path / "out" / "2026-05-29T16-35-12Z" / "answer.json").exists()
    assert (tmp_path / "out" / "2026-05-29T16-35-12Z" / "answer.md").exists()
