from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from core.workflow_status import workflow_status_dir


PRECOMPUTE_WORKFLOW_FILE = "daily-alpaca-precompute.yml"


def artifact_name_for_report_date(report_date: str) -> str:
    return f"alpaca-precompute-{report_date}"


def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True)


def _write_outputs(payload: dict[str, object]) -> None:
    target = str(os.getenv("GITHUB_OUTPUT", "")).strip()
    if not target:
        return
    with open(target, "a", encoding="utf-8") as fh:
        for key, value in payload.items():
            if isinstance(value, bool):
                rendered = str(value).lower()
            else:
                rendered = str(value)
            fh.write(f"{key}={rendered}\n")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)


def _find_download_root(download_dir: Path, report_date: str) -> Path | None:
    contract_match = next(
        iter(download_dir.glob(f"**/outputs/precompute/{report_date}/contract.json")),
        None,
    )
    if contract_match is None:
        return None
    return contract_match.parents[3]


def fetch_precompute_artifact(
    *,
    report_date: str,
    destination: Path,
    repo: str,
    branch: str = "",
) -> dict[str, Any]:
    artifact_name = artifact_name_for_report_date(report_date)
    payload: dict[str, Any] = {
        "workflow_kind": "live",
        "report_date": str(report_date),
        "precompute_bundle_required": True,
        "precompute_bundle_found": False,
        "bundle_source": "none",
        "bundle_report_date": str(report_date),
        "bundle_status": "bundle_download_failed",
        "artifact_name": artifact_name,
        "artifact_run_id": "",
    }
    destination.mkdir(parents=True, exist_ok=True)

    run_list_cmd = [
        "gh",
        "run",
        "list",
        "--repo",
        repo,
        "--workflow",
        PRECOMPUTE_WORKFLOW_FILE,
        "--json",
        "databaseId,status,conclusion,headBranch,createdAt",
        "--limit",
        "50",
    ]
    try:
        completed = _run_command(run_list_cmd)
        runs = json.loads(completed.stdout or "[]")
    except Exception:
        return payload

    if branch:
        runs = [run for run in runs if str(run.get("headBranch") or "") == branch]

    successful_runs = [
        run
        for run in runs
        if str(run.get("status") or "") == "completed"
        and str(run.get("conclusion") or "") == "success"
    ]

    for run in successful_runs:
        run_id = str(run.get("databaseId") or "").strip()
        if not run_id:
            continue
        try:
            _run_command(
                [
                    "gh",
                    "run",
                    "download",
                    run_id,
                    "--repo",
                    repo,
                    "--name",
                    artifact_name,
                    "--dir",
                    str(destination),
                ]
            )
        except Exception:
            continue
        download_root = _find_download_root(destination, report_date)
        if download_root is None:
            payload["bundle_status"] = "bundle_invalid"
            continue
        _copy_tree(
            download_root / "outputs" / "precompute" / report_date,
            Path("outputs/precompute") / report_date,
        )
        _copy_tree(
            download_root / "outputs" / "workflow_status" / report_date,
            Path("outputs/workflow_status") / report_date,
        )
        payload.update(
            {
                "precompute_bundle_found": True,
                "bundle_source": "artifact",
                "bundle_status": "bundle_downloaded",
                "artifact_run_id": run_id,
            }
        )
        return payload

    payload["bundle_status"] = "MISSING_PRECOMPUTE_BUNDLE"
    return payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download authoritative same-day Alpaca precompute artifact")
    parser.add_argument("--report-date", required=True)
    parser.add_argument("--destination", default="downloaded_precompute")
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", ""))
    parser.add_argument("--branch", default=os.getenv("GITHUB_REF_NAME", ""))
    parser.add_argument("--json-output", default="")
    parser.add_argument("--require-artifact", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    payload = fetch_precompute_artifact(
        report_date=args.report_date,
        destination=Path(args.destination),
        repo=str(args.repo or ""),
        branch=str(args.branch or ""),
    )
    json_output = str(args.json_output or "").strip()
    if not json_output:
        json_output = str(workflow_status_dir(args.report_date) / "live_artifact_fetch.json")
    _write_json(Path(json_output), payload)
    _write_outputs(payload)
    print(
        "[PRECOMPUTE_ARTIFACT] "
        f"report_date={payload['report_date']} "
        f"artifact_name={payload['artifact_name']} "
        f"precompute_bundle_found={str(payload['precompute_bundle_found']).lower()} "
        f"bundle_source={payload['bundle_source']} "
        f"bundle_status={payload['bundle_status']} "
        f"artifact_run_id={payload['artifact_run_id'] or 'none'}"
    )
    if args.require_artifact and not bool(payload["precompute_bundle_found"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
