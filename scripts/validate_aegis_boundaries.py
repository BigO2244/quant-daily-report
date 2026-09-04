"""Fail-closed Aegis import and changed-path boundary scanner."""

from __future__ import annotations

import argparse
import ast
import subprocess
from pathlib import Path

FORBIDDEN_IMPORT_ROOTS = {"broker", "execution", "allocation", "scheduler", "paper", "pilot", "live", "capital", "anthropic", "openai"}
FORBIDDEN_CHANGED_PREFIXES = ("execution/", "core/allocation", "scripts/cron", "scripts/deploy", "scripts/live", "scripts/paper", "scripts/execute", "config/systemd", ".github/workflows/")


def scan(repo_root: Path, base: str) -> list[str]:
    violations: list[str] = []
    for path in sorted((repo_root / "aiops/aegis").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        modules.extend(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        for module in modules:
            if module.split(".")[0] in FORBIDDEN_IMPORT_ROOTS: violations.append(f"forbidden import {module} in {path.relative_to(repo_root)}")
    result = subprocess.run(["git", "diff", "--name-only", base, "--"], cwd=repo_root, capture_output=True, text=True, check=False)
    if result.returncode: violations.append(f"git diff failed: {result.stderr.strip()}")
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=repo_root, capture_output=True, text=True, check=False)
    if untracked.returncode: violations.append(f"git untracked scan failed: {untracked.stderr.strip()}")
    for changed in sorted(set(result.stdout.splitlines()) | set(untracked.stdout.splitlines())):
        if changed.startswith(FORBIDDEN_CHANGED_PREFIXES): violations.append(f"forbidden changed path: {changed}")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--repo-root", type=Path, default=Path(".")); parser.add_argument("--base", default="origin/agent/aegis-control-plane-166")
    args = parser.parse_args(argv); violations = scan(args.repo_root.resolve(), args.base)
    if violations:
        print("AEGIS_BOUNDARY_STATUS: VIOLATION"); print("\n".join(f"- {item}" for item in violations)); return 1
    print("AEGIS_BOUNDARY_STATUS: CLEAN"); return 0


if __name__ == "__main__": raise SystemExit(main())
