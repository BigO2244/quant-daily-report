#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

ARCHIVE_DIRS = (
    "outputs/ledger",
    "outputs/perf",
    "outputs/daily",
    "outputs/execution_email",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _archive_state(repo_root: Path, archive_path: Path) -> list[Path]:
    copied: list[Path] = []
    archive_path.mkdir(parents=True, exist_ok=True)
    for rel in ARCHIVE_DIRS:
        src = repo_root / rel
        if not src.exists():
            continue
        dst = archive_path / rel
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        copied.append(src)
    return copied


def _reset_targets(repo_root: Path) -> list[Path]:
    targets: set[Path] = set()

    for rel in (
        "outputs/ledger/trades.csv",
        "outputs/perf/nav_timeseries.csv",
    ):
        path = repo_root / rel
        if path.exists():
            targets.add(path)

    patterns = (
        "outputs/orders_*.csv",
        "outputs/ledger/orders_sent*.json",
        "outputs/ledger/orders_sent*.csv",
        "outputs/execution_email/*.json",
    )
    for pattern in patterns:
        targets.update(repo_root.glob(pattern))

    return sorted(targets, key=lambda p: str(p))


def _delete_paths(paths: list[Path]) -> list[Path]:
    deleted: list[Path] = []
    for path in paths:
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        deleted.append(path)
    return deleted


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Archive then clear local execution artifacts for a clean local Day 1 reset. "
            "This script only touches local files."
        )
    )
    ap.add_argument(
        "--no-archive",
        action="store_true",
        help="Skip archive copy before cleanup.",
    )
    ap.add_argument(
        "--archive-dir",
        default="outputs/_archive",
        help="Base archive directory. Timestamped subfolder is created inside this path.",
    )
    args = ap.parse_args()

    repo_root = _repo_root()
    ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    archive_path = (repo_root / args.archive_dir / f"day0_reset_{ts}").resolve()

    archived: list[Path] = []
    if args.no_archive:
        print("[RESET] Archive skipped (--no-archive).")
    else:
        archived = _archive_state(repo_root, archive_path)
        print(f"[RESET] Archive path: {archive_path}")
        if archived:
            for src in archived:
                print(f"[RESET] Archived: {src.relative_to(repo_root)}")
        else:
            print("[RESET] Nothing to archive.")

    targets = _reset_targets(repo_root)
    deleted = _delete_paths(targets)
    if deleted:
        for path in deleted:
            print(f"[RESET] Deleted: {path.relative_to(repo_root)}")
    else:
        print("[RESET] Nothing to delete.")

    print(f"[RESET] Completed. archived={len(archived)} deleted={len(deleted)}")


if __name__ == "__main__":
    main()
