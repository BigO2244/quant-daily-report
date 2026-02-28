"""Deterministic planning workflow for aiops."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from .git_meta import get_git_metadata
from .spec_parser import REQUIRED_PARSE_HEADERS, SpecValidationError, parse_headers, validate_headers
from .util import VALID_MODES, ensure_writable_dir, now_local

_SECTION_HEADING_PATTERN = re.compile(r"^##\s+(.+?)\s*$")


def make_plan_run_id(dt_local: datetime, short_sha: str) -> str:
    """Create plan run id in YYYYMMDD_HHMMSS_<short_sha> format."""

    suffix = short_sha or "nogit"
    return f"{dt_local.strftime('%Y%m%d_%H%M%S')}_{suffix}"


def _extract_section(spec_text: str, section_name: str) -> str:
    """Extract body under a level-2 markdown heading."""

    lines = spec_text.splitlines()
    start_idx: int | None = None

    for idx, line in enumerate(lines):
        match = _SECTION_HEADING_PATTERN.match(line.strip())
        if match and match.group(1).strip() == section_name:
            start_idx = idx + 1
            break

    if start_idx is None:
        return ""

    end_idx = len(lines)
    for idx in range(start_idx, len(lines)):
        match = _SECTION_HEADING_PATTERN.match(lines[idx].strip())
        if match:
            end_idx = idx
            break

    section_lines = lines[start_idx:end_idx]
    while section_lines and not section_lines[0].strip():
        section_lines.pop(0)
    while section_lines and not section_lines[-1].strip():
        section_lines.pop()
    return "\n".join(section_lines)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_plan_without_hash(
    *,
    spec_path: Path,
    mode: str,
    spec_hash: str,
    files_section: str,
    acceptance_criteria_section: str,
) -> str:
    """Render canonical plan body used for PLAN_HASH."""

    parts = [
        "PLAN_VERSION: 1",
        f"SPEC_PATH: {spec_path}",
        f"MODE: {mode}",
        f"SPEC_HASH: {spec_hash}",
        "",
        "FILES:",
        files_section,
        "",
        "ACCEPTANCE_CRITERIA:",
        acceptance_criteria_section,
        "",
    ]
    return "\n".join(parts)


def render_plan(
    *,
    spec_path: Path,
    mode: str,
    spec_hash: str,
    files_section: str,
    acceptance_criteria_section: str,
) -> tuple[str, str]:
    """Render full plan and return (plan_text, plan_hash)."""

    body = render_plan_without_hash(
        spec_path=spec_path,
        mode=mode,
        spec_hash=spec_hash,
        files_section=files_section,
        acceptance_criteria_section=acceptance_criteria_section,
    )
    plan_hash = _sha256_text(body)
    return f"{body}PLAN_HASH: {plan_hash}\n", plan_hash


def run_plan(spec_path: Path, mode_override: str | None = None) -> int:
    """Create deterministic planning artifacts for a spec."""

    repo_root = Path.cwd()

    try:
        headers = parse_headers(spec_path)
        validate_headers(headers, REQUIRED_PARSE_HEADERS)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 1
    except SpecValidationError as exc:
        print(f"ERROR: {exc}")
        return 1

    mode = mode_override or headers.get("MODE", "")
    if mode not in VALID_MODES:
        print(f"ERROR: Invalid MODE '{mode}'. Allowed values: {', '.join(VALID_MODES)}")
        return 1

    spec_text = spec_path.read_text(encoding="utf-8")
    spec_hash = hashlib.sha256(spec_text.encode("utf-8")).hexdigest()

    files_section = _extract_section(spec_text, "FILES")
    acceptance_section = _extract_section(spec_text, "ACCEPTANCE CRITERIA")
    plan_text, _ = render_plan(
        spec_path=spec_path,
        mode=mode,
        spec_hash=spec_hash,
        files_section=files_section,
        acceptance_criteria_section=acceptance_section,
    )

    git_meta = get_git_metadata(repo_root)
    short_sha = str(git_meta.get("short_sha", "nogit"))
    run_id = make_plan_run_id(now_local(), short_sha)

    reports_root = repo_root / "reports" / "ai_runs"
    run_dir = reports_root / run_id

    try:
        ensure_writable_dir(reports_root)
        ensure_writable_dir(run_dir)
    except OSError as exc:
        print(f"ERROR: Reports directory not writable: {run_dir} ({exc})")
        return 1

    (run_dir / "spec_snapshot.md").write_text(spec_text, encoding="utf-8")
    (run_dir / "plan.md").write_text(plan_text, encoding="utf-8")
    return 0
