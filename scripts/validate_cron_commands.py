#!/usr/bin/env python3
"""Validate repo-owned command references in tracked cron files.

This is a read-only guard for deployment hygiene. It checks that cron entries do
not reference missing repo modules or scripts, and it syntax-checks shell/Python
targets where that is safe.
"""
from __future__ import annotations

import argparse
import py_compile
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


PYTHON_MODULE_RE = re.compile(r"\bpython(?:3)?\s+-m\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")
REPO_SCRIPT_RE = re.compile(r"(?:\$HOME/quant-daily-report/|~/quant-daily-report/|(?:\./)?)(scripts/[A-Za-z0-9_./-]+\.(?:sh|py))")
ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")


@dataclass(frozen=True)
class CheckResult:
    kind: str
    target: str
    passed: bool
    detail: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _is_ignored_line(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#") or ENV_ASSIGNMENT_RE.match(stripped) is not None


def _command_part(line: str) -> str | None:
    parts = line.split(maxsplit=5)
    if len(parts) < 6:
        return None
    return parts[5]


def _module_to_path(module: str, repo_root: Path) -> Path | None:
    parts = module.split(".")
    package_path = repo_root.joinpath(*parts, "__init__.py")
    module_path = repo_root.joinpath(*parts).with_suffix(".py")
    if module_path.exists():
        return module_path
    if package_path.exists():
        return package_path
    return None


def validate_python_module(module: str, repo_root: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    path = _module_to_path(module, repo_root)
    if path is None:
        return [CheckResult("python-module", module, False, "module path does not exist under repo root")]
    results.append(CheckResult("python-module", module, True, f"module path exists at {path.relative_to(repo_root)}"))
    try:
        py_compile.compile(str(path), doraise=True)
    except py_compile.PyCompileError as exc:
        results.append(CheckResult("py-compile", str(path.relative_to(repo_root)), False, str(exc)))
    else:
        results.append(CheckResult("py-compile", str(path.relative_to(repo_root)), True, "compiled"))
    return results


def validate_script(script: str, repo_root: Path) -> list[CheckResult]:
    path = repo_root / script
    if not path.exists():
        return [CheckResult("script", script, False, "script does not exist")]
    results = [CheckResult("script", script, True, "exists")]
    if path.suffix == ".sh":
        completed = subprocess.run(["bash", "-n", str(path)], cwd=repo_root, text=True, capture_output=True)
        results.append(CheckResult("bash-n", script, completed.returncode == 0, completed.stderr.strip() or "syntax ok"))
    elif path.suffix == ".py":
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            results.append(CheckResult("py-compile", script, False, str(exc)))
        else:
            results.append(CheckResult("py-compile", script, True, "compiled"))
    return results


def validate_cron_text(text: str, *, repo_root: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    seen: set[tuple[str, str]] = set()
    for raw_line in text.splitlines():
        if _is_ignored_line(raw_line):
            continue
        command = _command_part(raw_line)
        if not command:
            continue
        for module in PYTHON_MODULE_RE.findall(command):
            key = ("python-module", module)
            if key in seen:
                continue
            seen.add(key)
            results.extend(validate_python_module(module, repo_root))
        for script in REPO_SCRIPT_RE.findall(command):
            key = ("script", script)
            if key in seen:
                continue
            seen.add(key)
            results.extend(validate_script(script, repo_root))
    return results


def validate_cron_file(path: Path, *, repo_root: Path) -> list[CheckResult]:
    return validate_cron_text(path.read_text(encoding="utf-8"), repo_root=repo_root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate repo-owned script/module references in cron files.")
    parser.add_argument("cron_file", nargs="?", default="scripts/crontab.txt")
    parser.add_argument("--repo-root", default=str(_repo_root()))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    cron_file = Path(args.cron_file)
    if not cron_file.is_absolute():
        cron_file = repo_root / cron_file
    results = validate_cron_file(cron_file, repo_root=repo_root)
    failed = [result for result in results if not result.passed]
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status}\t{result.kind}\t{result.target}\t{result.detail}")
    if failed:
        print(f"[CRON_VALIDATE][FAIL] {len(failed)} failed checks", file=sys.stderr)
        return 1
    print(f"[CRON_VALIDATE][OK] {len(results)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
