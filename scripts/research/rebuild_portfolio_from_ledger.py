#!/usr/bin/env python3
"""CLI to rebuild holdings/cash from ledger for a given asof date."""
from __future__ import annotations

import argparse

from paper.ledger import load_ledger
from paper.positions import rebuild_positions_from_ledger, write_position_outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asof", required=True)
    args = parser.parse_args()

    ledger = load_ledger()
    rebuilt = rebuild_positions_from_ledger(ledger, args.asof)
    paths = write_position_outputs(rebuilt["positions"], rebuilt["cash"], args.asof)
    print(paths)


if __name__ == "__main__":
    main()
