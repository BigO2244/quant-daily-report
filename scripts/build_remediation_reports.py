#!/usr/bin/env python3
"""Daily read-only certification, lane parity and Shadow evidence package."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from core.execution_certification import certify_window
from core.paper_live_parity import compare, paper_intent_from_artifacts, live_intent_from_artifacts
from core.trading_integrity_certification import _read
from core.shadow_health_report import build_shadow_health_report


def current_session_status(certificate, trade_date):
    rows = [row for row in certificate.get('sessions', []) if row.get('trade_date') == trade_date]
    if (certificate.get('through_date') != trade_date or len(rows) != 1
            or rows[0].get('certified') is not True
            or rows[0].get('unexplained_count') != 0
            or rows[0].get('unexplained_discrepancies') != []):
        return 'FAILED'
    if certificate.get('status') == 'CERTIFIED':
        return 'CERTIFIED'
    if (certificate.get('status') == 'NOT_CERTIFIED'
            and type(certificate.get('consecutive_clean_sessions')) is int
            and 1 <= certificate['consecutive_clean_sessions'] < 5
            and certificate.get('required_sessions') == 5):
        return 'OBSERVING'
    return 'FAILED'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=ROOT)
    parser.add_argument('--trade-date', required=True)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--live-state-root', type=Path, default=Path.home()/'.caerus/lyra_live_state')
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    output = args.output_dir or root / 'outputs/remediation' / args.trade_date
    output.mkdir(parents=True, exist_ok=True)
    certificate = certify_window(repo_root=root, through_date=args.trade_date)
    inputs = root / 'outputs/paper_live_parity' / args.trade_date
    parity = compare(paper=_read(inputs/'paper_intent.json') or paper_intent_from_artifacts(root,args.trade_date),
                     live=_read(inputs/'live_intent.json') or live_intent_from_artifacts(args.live_state_root,args.trade_date),
                     trade_date=args.trade_date, repo_root=root, explanations=_read(inputs/'explanations.json'))
    shadow = build_shadow_health_report(repo_root=root, trade_date=args.trade_date)
    for name, report in [('execution_certification', certificate), ('paper_live_parity_report', parity), ('shadow_health_report', shadow)]:
        (output/(name+'.json')).write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False)+'\n')
    observation = current_session_status(certificate, args.trade_date)
    print(json.dumps({'certification': certificate['status'], 'current_session': observation,
                      'consecutive_clean_sessions': certificate['consecutive_clean_sessions'],
                      'parity': parity['status'], 'shadow': shadow['status']}))
    return 0 if observation in {'CERTIFIED', 'OBSERVING'} and parity['UNEXPLAINED']==0 and shadow['status']=='PASS' else 1


if __name__ == '__main__': raise SystemExit(main())
