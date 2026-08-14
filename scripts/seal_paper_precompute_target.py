from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.paper_target_authority import seal_paper_target_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seal the sole Orion PAPER Decision target into today's precompute bundle."
    )
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--bundle-dir", default=None)
    args = parser.parse_args(argv)
    bundle_dir = Path(args.bundle_dir) if args.bundle_dir else (
        REPO_ROOT / "outputs" / "precompute" / args.trade_date
    )
    package = seal_paper_target_bundle(
        bundle_dir=bundle_dir,
        trade_date=str(args.trade_date),
        repo_root=REPO_ROOT,
    )
    print(
        json.dumps(
            {
                "status": "SEALED",
                "trade_date": args.trade_date,
                "approved_sleeve": package["approved_sleeve"],
                "approved_target_hash": package["approved_target_hash"],
                "target_name_count": len(package["target_rows"]),
                "source_strategy_artifact": package["source_strategy_artifact"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
