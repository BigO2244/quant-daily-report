#!/usr/bin/env python3
"""Create a write-once prospective PAPER opening from acquired local evidence.

No network access. Historical ownership and broker artifacts are never edited.
The caller must supply the reviewed frozen history identity and PAPER account.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.causal_ownership_ledger import (
    CausalOwnershipError, _hash, _opening_contract, _read_json, _read_jsonl,
)


def create_cutover(*, ledger_dir: Path, account_snapshot: Path,
                   positions_snapshot: Path, no_open_orders_capture: Path,
                   expected_account_id_hash: str, expected_history_sha256: str,
                   expected_history_bytes: int, expected_history_fill_count: int,
                   output: Path) -> dict:
    """Bind existing inventory to Orion prospectively, without assigning returns."""
    history_path = ledger_dir / 'causal_fills.jsonl'
    raw = history_path.read_bytes()
    if (len(raw) != expected_history_bytes
            or hashlib.sha256(raw).hexdigest() != expected_history_sha256
            or len([line for line in raw.splitlines() if line.strip()]) != expected_history_fill_count):
        raise CausalOwnershipError('reviewed immutable history prefix mismatch')
    account = _read_json(account_snapshot)
    positions = _read_json(positions_snapshot)
    capture = _read_json(no_open_orders_capture)
    body = dict(capture)
    claimed_hash = body.pop('content_hash', None)
    if (claimed_hash != _hash(body)
            or capture.get('schema_version') != 'caerus.paper_opening_capture.v1'
            or capture.get('account_scope') != 'PAPER'
            or capture.get('account_id_hash') != expected_account_id_hash
            or account.get('account_id_hash') != expected_account_id_hash
            or capture.get('pulled_at_utc') != account.get('pulled_at_utc')
            or positions.get('pulled_at_utc') != account.get('pulled_at_utc')
            or capture.get('account_snapshot_hash') != _hash(account)
            or capture.get('positions_snapshot_hash') != _hash(positions)
            or capture.get('open_orders') != []):
        raise CausalOwnershipError('PAPER account/snapshot/no-open-orders capture mismatch')
    if account not in _read_jsonl(ledger_dir / 'account_snapshots.jsonl'):
        raise CausalOwnershipError('opening account snapshot absent from acquired ledger')
    if positions != _read_json(ledger_dir / 'positions_latest.json'):
        raise CausalOwnershipError('opening positions differ from acquired ledger')
    book = []
    for row in positions.get('positions', []):
        quantity = float(row['qty'])
        if quantity == 0:
            continue
        book.append({'symbol': str(row['symbol']).upper(), 'sleeve_id': 'caerus_orion',
                     'quantity': quantity})
    book.sort(key=lambda row: row['symbol'])
    contract = {
        'schema_version': 'caerus.ownership_cutover.v1', 'account_scope': 'PAPER',
        'account_id_hash': expected_account_id_hash, 'effective_at': account['pulled_at_utc'],
        'history_prefix_bytes': expected_history_bytes, 'history_sha256': expected_history_sha256,
        'history_fill_count': expected_history_fill_count,
        'opening_positions_snapshot': positions, 'positions_snapshot_hash': _hash(positions),
        'opening_account_snapshot': account, 'account_snapshot_hash': _hash(account),
        'opening_book': book, 'no_open_orders_capture': capture,
        'no_open_orders_capture_hash': claimed_hash,
        'history_policy': 'PRESERVE_IMMUTABLE_HISTORY_PROSPECTIVE_ORION_OPENING_ONLY',
    }
    contract['content_hash'] = _hash(contract)
    encoded = (json.dumps(contract, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    # Validate using the same strict consumer before publishing the final path.
    with tempfile.TemporaryDirectory(prefix='paper-cutover-validation-') as temp:
        candidate = Path(temp) / 'ownership_cutover.json'
        candidate.write_bytes(encoded)
        _opening_contract(candidate, ledger_dir)
    if history_path.read_bytes() != raw:
        raise CausalOwnershipError('history changed during cutover creation')
    if output.exists():
        if output.read_bytes() != encoded:
            raise CausalOwnershipError('write-once cutover already differs')
        return contract
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('ledger-dir', 'account-snapshot', 'positions-snapshot', 'no-open-orders-capture', 'output'):
        parser.add_argument('--' + option, type=Path, required=True)
    for option in ('expected-account-id-hash', 'expected-history-sha256'):
        parser.add_argument('--' + option, required=True)
    for option in ('expected-history-bytes', 'expected-history-fill-count'):
        parser.add_argument('--' + option, type=int, required=True)
    contract = create_cutover(**vars(parser.parse_args()))
    print(json.dumps({'status': 'PASS', 'content_hash': contract['content_hash'],
                      'history_fill_count': contract['history_fill_count'], 'broker_calls': 0}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
