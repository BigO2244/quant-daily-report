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


def _log(msg: str) -> None:
    print(f"[ARTIFACT_FETCH] {msg}", flush=True)


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


def _log_file_tree(root: Path, label: str) -> None:
    """Emit the full file tree under root, one path per line."""
    entries = sorted(root.rglob("*"))
    if not entries:
        _log(f"  {label}: (empty)")
        return
    _log(f"  {label}:")
    for entry in entries:
        rel = entry.relative_to(root)
        kind = "DIR " if entry.is_dir() else "FILE"
        _log(f"    {kind} {rel}")


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

    _log(
        f"report_date={report_date} "
        f"artifact_name={artifact_name} "
        f"repo={repo or '(not set)'} "
        f"branch={branch or '(not set)'} "
        f"destination={destination} "
        f"workflow={PRECOMPUTE_WORKFLOW_FILE}"
    )

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
    _log(f"querying runs: {' '.join(run_list_cmd)}")
    try:
        completed = _run_command(run_list_cmd)
        runs = json.loads(completed.stdout or "[]")
    except Exception as exc:
        _log(f"run list failed: {exc}")
        return payload

    _log(f"total runs returned: {len(runs)}")
    for i, run in enumerate(runs):
        _log(
            f"  run[{i}] id={run.get('databaseId')} "
            f"status={run.get('status')} "
            f"conclusion={run.get('conclusion')} "
            f"branch={run.get('headBranch')} "
            f"createdAt={run.get('createdAt')}"
        )

    if branch:
        before = len(runs)
        runs = [run for run in runs if str(run.get("headBranch") or "") == branch]
        _log(f"branch filter '{branch}': {before} -> {len(runs)} runs")

    successful_runs = [
        run
        for run in runs
        if str(run.get("status") or "") == "completed"
        and str(run.get("conclusion") or "") == "success"
    ]
    _log(f"eligible runs (completed+success): {len(successful_runs)}")
    for i, run in enumerate(successful_runs):
        _log(
            f"  eligible[{i}] id={run.get('databaseId')} "
            f"createdAt={run.get('createdAt')} "
            f"branch={run.get('headBranch')}"
        )

    if not successful_runs:
        _log("no eligible runs found — cannot proceed")
        payload["bundle_status"] = "MISSING_PRECOMPUTE_BUNDLE"
        return payload

    # Once we find the exact same-day artifact, stop — do not fall through to older runs.
    same_day_attempted = False

    for run in successful_runs:
        run_id = str(run.get("databaseId") or "").strip()
        if not run_id:
            _log(f"  skipping run with no databaseId: {run}")
            continue

        download_cmd = [
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
        _log(
            f"attempting download: run_id={run_id} "
            f"artifact={artifact_name} "
            f"dir={destination} "
            f"cmd={' '.join(download_cmd)}"
        )
        try:
            _run_command(download_cmd)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            _log(
                f"  download rejected: run_id={run_id} "
                f"returncode={exc.returncode} "
                f"reason=artifact_not_found_on_run "
                f"stderr={stderr!r}"
            )
            continue
        except Exception as exc:
            _log(f"  download error: run_id={run_id} error={exc}")
            continue

        # Artifact with the exact same-day name was found on this run — do not search further.
        same_day_attempted = True
        _log(f"  download succeeded: run_id={run_id} destination={destination}")

        _log(f"  extracted file tree under {destination}:")
        _log_file_tree(destination, str(destination))

        contract_direct = destination / artifact_name / "outputs" / "precompute" / report_date / "contract.json"
        _log(
            f"  contract.json probe: path={contract_direct} "
            f"exists={contract_direct.exists()}"
        )

        download_root = _find_download_root(destination, report_date)
        _log(
            f"  _find_download_root result: {'found at ' + str(download_root) if download_root else 'None (no contract.json under destination)'}"
        )

        if download_root is None:
            summary_match = next(
                iter(destination.glob(f"**/workflow_status/{report_date}/precompute_workflow_summary.json")),
                None,
            )
            if summary_match is not None:
                _log(f"  workflow summary found at: {summary_match}")
                try:
                    summary = json.loads(summary_match.read_text(encoding="utf-8"))
                    skip_status = str(summary.get("bundle_status") or "").upper()
                    _log(f"  workflow summary bundle_status: {skip_status}")
                    if skip_status in {"SKIPPED_AS_STALE", "GUARD_FAILED"}:
                        payload["bundle_status"] = f"bundle_{skip_status.lower()}"
                        _log(f"  verdict: {payload['bundle_status']} — precompute was blocked, no bundle built")
                    else:
                        payload["bundle_status"] = "bundle_invalid"
                        _log(f"  verdict: bundle_invalid (summary present but no contract.json)")
                except Exception as exc:
                    payload["bundle_status"] = "bundle_invalid"
                    _log(f"  verdict: bundle_invalid (summary parse error: {exc})")
            else:
                payload["bundle_status"] = "bundle_invalid"
                _log(f"  verdict: bundle_invalid (no contract.json and no workflow summary)")

            # Exact same-day artifact found but unusable — stop here, do not fall through.
            _log(f"  stopping search: same-day artifact found on run_id={run_id} but bundle is not usable")
            break

        _log(f"  contract.json confirmed at: {download_root / 'outputs' / 'precompute' / report_date / 'contract.json'}")
        _copy_tree(
            download_root / "outputs" / "precompute" / report_date,
            Path("outputs/precompute") / report_date,
        )
        _copy_tree(
            download_root / "outputs" / "workflow_status" / report_date,
            Path("outputs/workflow_status") / report_date,
        )
        _log(
            f"  bundle extracted to outputs/precompute/{report_date}/ "
            f"and outputs/workflow_status/{report_date}/"
        )
        payload.update(
            {
                "precompute_bundle_found": True,
                "bundle_source": "artifact",
                "bundle_status": "bundle_downloaded",
                "artifact_run_id": run_id,
            }
        )
        _log(
            f"  verdict: bundle_downloaded — accepted run_id={run_id}"
        )
        return payload

    if not same_day_attempted:
        # No run had an artifact with the expected name — network failures only.
        _log(
            f"no run yielded artifact '{artifact_name}' "
            f"(all download attempts rejected — artifact name not present on any eligible run)"
        )
        payload["bundle_status"] = "MISSING_PRECOMPUTE_BUNDLE"

    _log(
        f"final: precompute_bundle_found={payload['precompute_bundle_found']} "
        f"bundle_status={payload['bundle_status']}"
    )
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
