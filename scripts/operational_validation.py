#!/usr/bin/env python3
"""Wave-1 read-only operational validation for CI governance.

This validator is intentionally scoped to FR-009, FR-011, and FR-013:

- GitHub Actions references must be pinned to immutable 40-character SHAs.
- Workflow-scope ``contents: write`` is disallowed.
- Dependabot advisory monitoring must cover pip and GitHub Actions.

It does not run trading workflows, submit orders, reinstall cron, regenerate
broker artifacts, inspect dependency pins, or validate cache namespace policy.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - exercised when PyYAML is absent
    yaml = None


STATUS_ORDER = {"PASS": 0, "WARN": 1, "FAIL": 2}
SHA_RE = re.compile(r"uses:\s*([^\s]+)@([^\s]+)")


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _status(checks: list[Check]) -> str:
    if not checks:
        return "PASS"
    return max((check.status for check in checks), key=lambda value: STATUS_ORDER[value])


def _workflow_files(repo_root: Path) -> list[Path]:
    workflow_dir = repo_root / ".github" / "workflows"
    return sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml"))


def _check_workflows(repo_root: Path) -> list[Check]:
    checks: list[Check] = []
    workflow_files = _workflow_files(repo_root)
    if not workflow_files:
        return [Check("workflow_yaml", "WARN", "no workflow files found")]

    mutable: list[str] = []
    write_permission_gaps: list[str] = []
    for path in workflow_files:
        text = path.read_text(encoding="utf-8")
        parsed: Any = None
        rel = str(path.relative_to(repo_root))
        if yaml is None:
            checks.append(Check("workflow_yaml", "WARN", f"{rel}: PyYAML unavailable; syntax parse skipped"))
        else:
            try:
                parsed = yaml.safe_load(text)
            except Exception as exc:
                checks.append(Check("workflow_yaml", "FAIL", f"{rel}: {exc}"))
            else:
                checks.append(Check("workflow_yaml", "PASS", rel))

        if isinstance(parsed, dict):
            permissions = parsed.get("permissions")
            if isinstance(permissions, dict) and permissions.get("contents") == "write":
                write_permission_gaps.append(f"{rel}: workflow-scope contents: write")

        for lineno, line in enumerate(text.splitlines(), 1):
            match = SHA_RE.search(line)
            if not match:
                continue
            ref = match.group(2)
            if not re.fullmatch(r"[0-9a-f]{40}", ref):
                mutable.append(f"{rel}:{lineno}: {match.group(0)}")

    if mutable:
        checks.append(Check("workflow_action_pinning", "FAIL", "; ".join(mutable)))
    else:
        checks.append(Check("workflow_action_pinning", "PASS", "all workflow uses references are pinned to 40-character SHAs"))

    if write_permission_gaps:
        checks.append(Check("workflow_permissions", "FAIL", "; ".join(write_permission_gaps)))
    else:
        checks.append(Check("workflow_permissions", "PASS", "no workflow-scope contents: write permissions found"))

    return checks


def _check_dependabot(repo_root: Path) -> list[Check]:
    path = repo_root / ".github" / "dependabot.yml"
    if not path.exists():
        return [Check("dependabot", "WARN", ".github/dependabot.yml missing")]
    if yaml is None:
        return [Check("dependabot", "WARN", "PyYAML unavailable; dependabot syntax parse skipped")]
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [Check("dependabot", "FAIL", f".github/dependabot.yml: {exc}")]
    updates = payload.get("updates") if isinstance(payload, dict) else None
    ecosystems = {str(item.get("package-ecosystem")) for item in updates if isinstance(item, dict)} if isinstance(updates, list) else set()
    missing = sorted({"pip", "github-actions"} - ecosystems)
    if missing:
        return [Check("dependabot", "FAIL", f"missing ecosystems: {missing}")]
    raw = path.read_text(encoding="utf-8").lower()
    if "auto-merge" in raw or "automerge" in raw:
        return [Check("dependabot", "FAIL", "dependabot config appears to enable auto-merge")]
    return [Check("dependabot", "PASS", "pip and github-actions monitoring configured without auto-merge")]


def build_payload(*, repo_root: Path) -> dict[str, Any]:
    checks: list[Check] = []
    checks.extend(_check_workflows(repo_root))
    checks.extend(_check_dependabot(repo_root))
    return {
        "status": _status(checks),
        "checks": [asdict(check) for check in checks],
        "summary": {
            "pass": sum(1 for check in checks if check.status == "PASS"),
            "warn": sum(1 for check in checks if check.status == "WARN"),
            "fail": sum(1 for check in checks if check.status == "FAIL"),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Wave-1 read-only CI governance validation checks.")
    parser.add_argument("--repo-root", default=str(_repo_root()))
    parser.add_argument("--json", action="store_true", help="Print JSON payload instead of text summary.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_payload(repo_root=Path(args.repo_root).resolve())
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"[OPERATIONAL_VALIDATION][{payload['status']}] {payload['summary']}")
        for check in payload["checks"]:
            print(f"{check['status']}\t{check['name']}\t{check['detail']}")
    return 1 if payload["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
