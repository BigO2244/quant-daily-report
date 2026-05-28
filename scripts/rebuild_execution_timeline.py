#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.execution_lifecycle_timeline import write_execution_lifecycle_timeline
from core.run_pointer import read_latest_run_pointer


REQUIRED_SOURCE_ARTIFACTS = (
    "operator_summary.json",
    "execution_payload.json",
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_run_root(*, repo_root: Path, run_id: str | None, latest: bool) -> tuple[Path | None, str | None, str | None]:
    if latest:
        pointer = read_latest_run_pointer(str(repo_root))
        if not pointer:
            return None, None, "latest_run_missing"
        raw = str(pointer.get("run_root") or pointer.get("path") or "").strip()
        if not raw:
            pointer_run_id = str(pointer.get("run_id") or "").strip()
            raw = f"outputs/runs/{pointer_run_id}" if pointer_run_id else ""
        if not raw:
            return None, None, "latest_run_missing_run_root"
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = repo_root / path
        return path, str(pointer.get("run_id") or path.name), None

    if not run_id:
        return None, None, "run_id_required"
    return repo_root / "outputs" / "runs" / str(run_id), str(run_id), None


def rebuild_execution_timeline(
    *,
    repo_root: str | Path = _REPO_ROOT,
    run_id: str | None = None,
    latest: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    run_root, resolved_run_id, error = _resolve_run_root(
        repo_root=root,
        run_id=run_id,
        latest=latest,
    )
    if error or run_root is None:
        return {
            "status": "NEEDS_OPERATOR",
            "reason": error or "run_root_unresolved",
            "run_id": resolved_run_id,
            "run_root": None,
            "written": False,
        }

    json_path = run_root / "execution_timeline.json"
    md_path = run_root / "execution_timeline.md"
    if not run_root.exists() or not run_root.is_dir():
        return {
            "status": "NEEDS_OPERATOR",
            "reason": "run_directory_missing",
            "run_id": resolved_run_id,
            "run_root": str(run_root),
            "written": False,
        }

    missing_required = [
        name for name in REQUIRED_SOURCE_ARTIFACTS if not (run_root / name).exists()
    ]
    if missing_required:
        return {
            "status": "NEEDS_OPERATOR",
            "reason": "required_source_artifacts_missing",
            "run_id": resolved_run_id,
            "run_root": str(run_root),
            "missing_required_artifacts": missing_required,
            "written": False,
        }

    existing = [str(path) for path in (json_path, md_path) if path.exists()]
    if existing and not force:
        return {
            "status": "REFUSED",
            "reason": "timeline_exists",
            "run_id": resolved_run_id,
            "run_root": str(run_root),
            "existing_artifacts": existing,
            "written": False,
        }

    operator_summary = _read_json(run_root / "operator_summary.json")
    execution_payload = _read_json(run_root / "execution_payload.json")
    trade_date = (
        str(
            execution_payload.get("trade_date")
            or operator_summary.get("trade_date")
            or ""
        ).strip()
        or None
    )
    timeline_run_id = (
        str(
            execution_payload.get("run_id")
            or operator_summary.get("run_id")
            or resolved_run_id
            or ""
        ).strip()
        or None
    )

    written_json, written_md = write_execution_lifecycle_timeline(
        run_root=run_root,
        trade_date=trade_date,
        run_id=timeline_run_id,
    )
    return {
        "status": "OK",
        "reason": "",
        "run_id": timeline_run_id,
        "trade_date": trade_date,
        "run_root": str(run_root),
        "written": True,
        "artifacts": {
            "execution_timeline_json": str(written_json),
            "execution_timeline_md": str(written_md),
        },
        "forced": bool(force),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild execution lifecycle timeline artifacts from an existing run directory."
    )
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--run-id", help="Run ID under outputs/runs/<RUN_ID>.")
    selector.add_argument("--latest", action="store_true", help="Use outputs/latest_run.json.")
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT)
    parser.add_argument("--force", action="store_true", help="Overwrite existing timeline artifacts.")
    parser.add_argument("--json", action="store_true", help="Emit JSON response.")
    return parser.parse_args(argv)


def _render_text(payload: dict[str, Any]) -> str:
    lines = [
        f"status: {payload.get('status')}",
        f"reason: {payload.get('reason') or 'none'}",
        f"run_id: {payload.get('run_id') or ''}",
        f"trade_date: {payload.get('trade_date') or ''}",
        f"run_root: {payload.get('run_root') or ''}",
        f"written: {str(bool(payload.get('written'))).lower()}",
    ]
    if payload.get("missing_required_artifacts"):
        lines.append(
            "missing_required_artifacts: "
            + ", ".join(str(item) for item in payload["missing_required_artifacts"])
        )
    if payload.get("existing_artifacts"):
        lines.append(
            "existing_artifacts: "
            + ", ".join(str(item) for item in payload["existing_artifacts"])
        )
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, dict):
        for name, path in artifacts.items():
            lines.append(f"{name}: {path}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = rebuild_execution_timeline(
        repo_root=args.repo_root,
        run_id=args.run_id,
        latest=bool(args.latest),
        force=bool(args.force),
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(_render_text(payload))
    return 0 if payload.get("status") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
