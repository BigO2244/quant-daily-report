#!/usr/bin/env python3
"""Run read-only target production verification and write one diagnostic receipt."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from core.target_replay import verify_target_replay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, required=True)
    parser.add_argument('--payload-path', type=Path, required=True)
    parser.add_argument('--trade-date', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    payload = args.payload_path.resolve()
    payload.relative_to(root)
    output = args.output.resolve()
    if output.is_relative_to(root):
        parser.error('diagnostic output must be outside immutable input root')
    result = verify_target_replay(repo_root=root, payload=json.loads(payload.read_text()), trade_date=args.trade_date)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as handle:
        handle.write(json.dumps(result, sort_keys=True, indent=2, allow_nan=False)+'\n')
    print(json.dumps({'pass': result['pass'], 'reasons': result['reasons'], 'output': str(output)}))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
