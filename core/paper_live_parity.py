"""Daily comparison of explicit lane intent; absence cannot count as parity."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path

REQUIRED = ('model_version', 'data_cutoff', 'universe', 'security_master', 'prices',
            'target_weights', 'target_shares', 'target_cash', 'plan_hash', 'sizing_logic',
            'rebudgeting_logic', 'execution_version', 'account_nav', 'positions',
            'available_cash', 'fractional_constraints', 'risk_capital_limits')
ACCOUNT_DIFFERENCES = {'account_nav', 'positions', 'available_cash'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()



def valid_field(field, value):
    def finite(v, positive=False):
        try: return not isinstance(v, bool) and math.isfinite(float(v)) and (float(v)>0 if positive else float(v)>=0)
        except (TypeError, ValueError): return False
    if value is None: return False
    if field in {'account_nav', 'available_cash', 'target_cash'}:
        return finite(value, field=='account_nav')
    if field in {'prices', 'target_weights'}:
        return (isinstance(value, dict) and bool(value) and all(isinstance(k,str) and k and finite(v,field=='prices') for k,v in value.items())
                and (field!='target_weights' or 0 < sum(map(float,value.values())) <= 1.00000001))
    if field in {'positions', 'target_shares'}:
        if isinstance(value, dict): return all(isinstance(k,str) and k and finite(v) for k,v in value.items())
        if isinstance(value, list): return all(isinstance(r,dict) and r.get('symbol') and finite(r.get('quantity',r.get('qty'))) for r in value)
        return False
    if field=='fractional_constraints': return isinstance(value,bool) or isinstance(value,dict) and bool(value)
    if field=='risk_capital_limits': return isinstance(value,dict) and bool(value)
    if field in {'universe','security_master','plan_hash'}:
        return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    if field=='data_cutoff':
        import datetime
        try: datetime.date.fromisoformat(value[:10]); return True
        except (TypeError,ValueError): return False
    return isinstance(value,str) and bool(value.strip())

def compare(*, paper, live, trade_date, repo_root: Path, explanations=None):
    differences = []
    for field in REQUIRED:
        left, right = paper.get(field), live.get(field)
        classification, reason = 'UNEXPLAINED', 'required_value_missing'
        if valid_field(field,left) and valid_field(field,right):
            if digest(left) == digest(right):
                classification, reason = 'EXPECTED', 'identical'
            elif field in ACCOUNT_DIFFERENCES:
                classification, reason = 'EXPECTED', 'owner_permitted_account_difference'
            else:
                explanation = (explanations or {}).get(field) or {}
                try:
                    path = (repo_root / explanation['evidence_path']).resolve()
                    path.relative_to(repo_root.resolve())
                    valid = (hashlib.sha256(path.read_bytes()).hexdigest() == explanation['evidence_sha256']
                             and explanation['paper_value_hash'] == digest(left)
                             and explanation['live_value_hash'] == digest(right)
                             and explanation['trade_date'] == trade_date and bool(explanation['reason']))
                except (KeyError, OSError, ValueError, TypeError):
                    valid = False
                if valid: classification, reason = 'EXPLAINED', explanation['reason']
                else: reason = 'different_without_value_bound_evidence'
        differences.append({'field': field, 'paper': left, 'live': right,
                            'classification': classification, 'reason': reason})
    for lane, document in (('paper', paper), ('live', live)):
        if document.get('trade_date') != trade_date:
            differences.append({'field': lane + '.trade_date', 'classification': 'UNEXPLAINED', 'reason': 'wrong_or_missing_session'})
    count = sum(row['classification'] == 'UNEXPLAINED' for row in differences)
    return {'schema_version': 'caerus.paper_live_parity.v1', 'trade_date': trade_date,
            'status': 'NOT_ALIGNED' if count else 'ALIGNED_WITH_EXPECTED_ACCOUNT_DIFFERENCES',
            'UNEXPLAINED': count, 'execution_gate_pass': count == 0,
            'production_authority': False, 'differences': differences,
            'paper_input_hash': digest(paper), 'live_input_hash': digest(live)}


def paper_intent_from_artifacts(root: Path, trade_date: str):
    from core.trading_integrity_certification import _read, _find_submit_run
    workflow = _read(root / 'outputs/workflow' / trade_date / 'execution.json')
    run = _find_submit_run(root, trade_date, workflow)
    payload = (_read(run / 'execution_payload.json') or {}) if run else {}
    plan = payload.get('exact_execution_plan') or {}
    package = payload.get('approved_execution_package') or {}
    contract = _read(root / 'outputs/precompute' / trade_date / 'contract.json') or {}
    lineage = contract.get('decision_lineage') or {}
    rows = package.get('approved_target_rows') or []
    constraints = plan.get('constraints') or {}
    hashes = plan.get('source_artifact_hashes') or {}
    return {'trade_date': trade_date, 'model_version': plan.get('strategy_id'),
            'data_cutoff': lineage.get('market_data_asof'), 'universe': lineage.get('universe_hash'),
            'security_master': hashes.get('security_master'),
            'prices': {row['symbol']: row['price'] for row in rows} or None,
            'target_weights': {row['symbol']: row['target_weight'] for row in rows} or None,
            'target_shares': plan.get('expected_posttrade_positions'), 'target_cash': plan.get('expected_posttrade_cash'),
            'plan_hash': plan.get('content_hash'), 'sizing_logic': 'FULL_BROKER_NAV' if constraints.get('full_current_account_required') else None,
            'rebudgeting_logic': constraints.get('post_sell_rebudgeting'), 'execution_version': plan.get('orchestrator_version'),
            'account_nav': plan.get('portfolio_nav'), 'positions': plan.get('starting_positions'),
            'available_cash': plan.get('starting_cash'), 'fractional_constraints': constraints.get('allow_fractional'),
            'risk_capital_limits': constraints or None}


def live_intent_from_artifacts(state_root: Path, trade_date: str):
    from core.trading_integrity_certification import _read
    plan = _read(state_root / trade_date / 'plan.json') or {}
    if not plan: return {}
    return {'trade_date': plan.get('execution_session'), 'model_version': plan.get('source_variant'),
            'data_cutoff': plan.get('signal_as_of'), 'universe': None, 'security_master': None,
            'prices': plan.get('latest_prices'), 'target_weights': plan.get('target_weights'),
            'target_shares': None, 'target_cash': plan.get('required_cash_reserve_usd'),
            'plan_hash': plan.get('content_hash'), 'sizing_logic': 'MIN_BROKER_NAV_MAX_LIVE_CAPITAL' if plan.get('sizing_basis_usd') else None,
            'rebudgeting_logic': 'EXACT_ORDERS_CONFIRMED_CASH', 'execution_version': plan.get('deployed_sha'),
            'account_nav': plan.get('factual_equity_usd'), 'positions': plan.get('starting_positions'),
            'available_cash': plan.get('factual_cash_usd'), 'fractional_constraints': True,
            'risk_capital_limits': {'max_live_capital_usd':plan.get('max_live_capital_usd'),'maximum_gross_usd':plan.get('maximum_gross_usd')}}


def _paused_live_paper_dependency(root: Path, exact):
    """Read current allowlisted runtime truth; never consume a cached pause claim."""
    from core.operating_truth import load_lane_registry, parse_env_gates
    registry = load_lane_registry(root/'config/operations/operating_lane_registry.json')
    lanes = {row['lane_id']: row for row in registry['lanes']}
    paper, live = lanes['orion_paper'], lanes['lyra_live']
    runtime = live['runtime']
    if (paper['lane_kind'] != 'PAPER' or paper['declared_state'] != 'ACTIVE'
            or paper['broker_environment'] != 'ALPACA_PAPER'
            or paper['authority']['kind'] != 'strategy_registry_paper'
            or paper['authority']['approval_scope'] != 'PAPER_ONLY'
            or live['lane_kind'] != 'LIVE' or live['broker_environment'] != 'ALPACA_LIVE'
            or live['strategy_ids'] != ['caerus_lyra']
            or live['authority']['kind'] != 'owner_decision'
            or runtime['env_path'] != '.caerus/lyra_live.env'
            or runtime['required_gates']['CAERUS_LYRA_LIVE_ENABLED'] != '1'
            or runtime['required_gates']['CAERUS_LYRA_LIVE_SUBMIT_APPROVED'] != '1'
            or runtime['required_gates']['CAERUS_LYRA_LIVE_OWNER_DECISION_HASH']
               != live['authority']['content_hash']):
        return None
    registered = set(paper['strategy_ids'])
    sleeves = {row['sleeve_id'] for row in exact.sleeve_allocations
               if row.get('capital_eligible') is True}
    if (not sleeves or not sleeves <= registered
            or exact.strategy_id not in registered | {'caerus_paper_portfolio'}):
        return None
    gate = parse_env_gates(Path.home()/'.caerus/lyra_live.env', {'CAERUS_LYRA_LIVE_ENABLED'})
    if gate.get('CAERUS_LYRA_LIVE_ENABLED') != '0':
        return None
    import datetime
    return {'reason': 'independently_validated_paper_plan_with_live_runtime_paused',
            'registry_content_hash': registry['content_hash'],
            'runtime_env_path': '.caerus/lyra_live.env',
            'runtime_gate': {'CAERUS_LYRA_LIVE_ENABLED': '0'},
            'observed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'paper_plan_hash': exact.content_hash}


def require_pretrade_parity(*, repo_root: Path, trade_date: str, lane: str, plan_hash: str,
                            actual_paper_plan=None):
    """Recompute from value-bound pretrade inputs; never trust a cached green flag."""
    from core.trading_integrity_certification import _read
    if trade_date < '2026-09-11':
        return {'status': 'NOT_APPLICABLE_HISTORICAL_POLICY'}
    if lane not in {'paper', 'live'}:
        raise ValueError('unknown execution lane')
    root = Path(repo_root).resolve()
    inputs = root / 'outputs/paper_live_parity' / trade_date
    paper = _read(inputs/'paper_intent.json') or {}
    live = _read(inputs/'live_intent.json') or {}
    report = compare(paper=paper, live=live, trade_date=trade_date, repo_root=root,
                     explanations=_read(inputs/'explanations.json'))
    lane_input = paper if lane == 'paper' else live
    if lane_input.get('plan_hash') != plan_hash:
        report['differences'].append({'field':lane+'.plan_hash','classification':'UNEXPLAINED','reason':'actual_submission_plan_hash_differs'})
        report['UNEXPLAINED'] += 1
        report['execution_gate_pass'] = False
        report['status'] = 'NOT_ALIGNED'
    dependency = None
    if lane == 'paper' and actual_paper_plan is not None:
        try:
            from authority.exact_plan import exact_execution_plan_from_dict
            exact = exact_execution_plan_from_dict(actual_paper_plan, expected_account_scope='PAPER')
            if exact.content_hash != plan_hash or exact.trade_date != trade_date:
                raise ValueError('actual PAPER plan identity mismatch')
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError):
            report['differences'].append({'field': 'paper.actual_plan', 'classification': 'UNEXPLAINED',
                                          'reason': 'actual_authorized_paper_plan_invalid'})
            report['UNEXPLAINED'] += 1
            report['execution_gate_pass'] = False
            report['status'] = 'NOT_ALIGNED'
        else:
            try:
                dependency = _paused_live_paper_dependency(root, exact)
            except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
                dependency = None  # Unproved runtime/registry truth preserves strict parity.
    report['submission_dependency_satisfied'] = report['execution_gate_pass']
    if dependency is not None:
        report['status'] = 'NOT_COMPARABLE_LIVE_PAUSED'
        report['execution_gate_pass'] = False
        report['submission_dependency_satisfied'] = True
        report['submission_dependency_evidence'] = dependency
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs/'paper_live_parity_report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n')
    if not report['submission_dependency_satisfied']:
        raise ValueError('paper_live_parity_not_aligned: UNEXPLAINED='+str(report['UNEXPLAINED']))
    return report
