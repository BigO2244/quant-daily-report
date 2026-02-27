"""Approval pack writers."""

from __future__ import annotations

import json
from pathlib import Path

from .util import CommandResult


def write_commands_log(path: Path, command_results: list[CommandResult]) -> None:
    """Write detailed command execution logs."""

    lines: list[str] = []
    for idx, result in enumerate(command_results, start=1):
        lines.append(f"[{idx}] COMMAND: {' '.join(result.command)}")
        lines.append(f"RETURN CODE: {result.returncode}")
        if result.error:
            lines.append(f"ERROR: {result.error}")
        lines.append("STDOUT:")
        lines.append(result.stdout.rstrip() or "<empty>")
        lines.append("STDERR:")
        lines.append(result.stderr.rstrip() or "<empty>")
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_spec_json(path: Path, parsed_spec: dict[str, str]) -> None:
    """Write parsed spec headers for debugging."""

    path.write_text(json.dumps(parsed_spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_approval_markdown(
    spec_path: Path,
    mode: str,
    run_id: str,
    ts_local: str,
    ts_utc: str,
    git_meta: dict[str, str | bool],
    parsed_headers: dict[str, str],
    command_results: list[CommandResult],
    gate_outcomes: list[tuple[str, bool, str]],
    risk_checklist: list[str],
    next_actions: list[str],
    rollback_notes: list[str],
) -> str:
    """Build approval markdown with required sections."""

    lines: list[str] = []
    lines.append("# Approval Pack")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Spec Path: `{spec_path}`")
    lines.append(f"- Mode: `{mode}`")
    lines.append(f"- Run ID: `{run_id}`")
    lines.append(f"- Timestamp (Local): `{ts_local}`")
    lines.append(f"- Timestamp (UTC): `{ts_utc}`")
    lines.append("")

    lines.append("## Git Metadata")
    if git_meta.get("available"):
        lines.append(f"- Branch: `{git_meta['branch']}`")
        lines.append(f"- HEAD SHA: `{git_meta['head_sha']}`")
        lines.append(f"- Dirty: `{git_meta['dirty']}`")
    else:
        lines.append("- Git metadata unavailable")
    lines.append("")

    lines.append("## Parsed Spec Headers")
    for key in ("MODE", "PROJECT_TYPE", "RISK_TIER", "OBJECTIVE"):
        lines.append(f"- {key}: `{parsed_headers.get(key, 'MISSING')}`")
    lines.append("")

    lines.append("## Commands Run + Results")
    if command_results:
        for result in command_results:
            lines.append(f"- `{' '.join(result.command)}` -> exit `{result.returncode}`")
    else:
        lines.append("- No subprocess commands executed")
    lines.append("")

    lines.append("## Gate Outcomes")
    for name, ok, detail in gate_outcomes:
        status = "PASS" if ok else "FAIL"
        lines.append(f"- [{status}] {name}: {detail}")
    if mode == "HARDEN":
        harden_ok = all(ok for _, ok, _ in gate_outcomes)
        explicit = "HARDEN gate passed" if harden_ok else "HARDEN gate failed"
        lines.append(f"- {explicit}")
    lines.append("")

    lines.append("## Risk Checklist")
    if risk_checklist:
        for item in risk_checklist:
            lines.append(f"- {item}")
    else:
        lines.append("- Not required for this mode")
    lines.append("")

    lines.append("## Next Actions")
    for action in next_actions:
        lines.append(f"- {action}")
    lines.append("")

    lines.append("## Rollback Notes")
    for note in rollback_notes:
        lines.append(f"- {note}")

    return "\n".join(lines).rstrip() + "\n"


def write_approval(path: Path, content: str) -> None:
    """Write approval markdown file."""

    path.write_text(content, encoding="utf-8")
