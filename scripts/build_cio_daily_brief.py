#!/usr/bin/env python3
"""Build a deterministic, persisted, non-trading CIO daily brief bundle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.cio_daily_brief import (
    build_cio_daily_brief,
    persist_brief_bundle,
    source_artifact,
)


def _read(path: Path) -> tuple[dict[str, Any] | None, bytes | None]:
    try:
        raw = path.read_bytes()
    except OSError:
        return None, None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None, raw
    return (value, raw) if isinstance(value, dict) else (None, raw)


def _display(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _latest_previous(output_root: Path, report_date: str) -> Path | None:
    candidates = (
        sorted(
            path / "brief.json"
            for path in output_root.iterdir()
            if path.is_dir()
            and path.name < report_date
            and (path / "brief.json").is_file()
        )
        if output_root.is_dir()
        else []
    )
    return candidates[-1] if candidates else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--report-date", required=True)
    parser.add_argument("--certification", type=Path)
    parser.add_argument("--operating-truth", type=Path)
    parser.add_argument("--research-projection", type=Path)
    parser.add_argument("--previous-brief", type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)

    root = args.repo_root.resolve()
    output_root = (
        args.output_root or root / "outputs/governance/cio_daily_brief"
    ).resolve()
    certification_path = (
        args.certification
        or root / f"outputs/governance/trading_integrity/{args.report_date}.json"
    ).resolve()
    operating_path = (
        args.operating_truth
        or root / "outputs/operating_state/current/operating_truth.json"
    ).resolve()
    research_path = (
        args.research_projection
        or root / "outputs/research/alpha_lab/ledger/research_projection.v1.json"
    ).resolve()
    previous_path = (
        args.previous_brief.resolve()
        if args.previous_brief
        else _latest_previous(output_root, args.report_date)
    )

    certification, certification_raw = _read(certification_path)
    operating, operating_raw = _read(operating_path)
    research, research_raw = _read(research_path)
    previous, _ = _read(previous_path) if previous_path else (None, None)
    sources = [
        {
            "kind": "trading_integrity",
            **source_artifact(
                path=_display(certification_path, root),
                payload=certification,
                raw_bytes=certification_raw,
            ),
        },
        {
            "kind": "operating_truth",
            **source_artifact(
                path=_display(operating_path, root),
                payload=operating,
                raw_bytes=operating_raw,
            ),
        },
        {
            "kind": "research_projection",
            **source_artifact(
                path=_display(research_path, root),
                payload=research,
                raw_bytes=research_raw,
            ),
        },
    ]
    if previous_path:
        previous_payload, previous_raw = _read(previous_path)
        sources.append(
            {
                "kind": "previous_brief",
                **source_artifact(
                    path=_display(previous_path, root),
                    payload=previous_payload,
                    raw_bytes=previous_raw,
                ),
            }
        )
        previous = previous_payload
    payload = build_cio_daily_brief(
        report_date=args.report_date,
        certification=certification,
        operating_truth=operating,
        previous_brief=previous,
        research_projection=research,
        sources=sources,
    )
    manifest = persist_brief_bundle(output_root=output_root, payload=payload)
    print(
        json.dumps(
            {
                "status": payload["operations"]["status"],
                "output": str(output_root / args.report_date),
                "manifest_content_hash": manifest["content_hash"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
