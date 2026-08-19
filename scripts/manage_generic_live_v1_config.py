#!/usr/bin/env python3
"""Atomic generic Live v1 config install and rollback utility."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.generic_live_v1_ops import install_config_with_backup, restore_config_backup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("install", "rollback"), required=True)
    parser.add_argument("--active-path", type=Path, required=True)
    parser.add_argument("--backup-path", type=Path, required=True)
    parser.add_argument("--candidate-path", type=Path)
    parser.add_argument("--allowed-root", action="append", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "install":
        if args.candidate_path is None:
            parser.error("--candidate-path is required for install")
        result = install_config_with_backup(
            candidate_path=args.candidate_path,
            active_path=args.active_path,
            backup_path=args.backup_path,
            allowed_roots=args.allowed_root,
        )
    else:
        result = restore_config_backup(
            active_path=args.active_path,
            backup_path=args.backup_path,
            allowed_roots=args.allowed_root,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
