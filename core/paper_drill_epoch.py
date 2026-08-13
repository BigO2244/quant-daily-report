"""Governed one-day PAPER intraday drill epoch helpers.

An epoch isolates immutable exact-plan claims and submission WAL records without
weakening the account/date execution mutex.  The policy is deliberately stored
in a reviewed repository artifact; arbitrary operator-provided epochs fail
closed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping


POLICY_SCHEMA_VERSION = "caerus.paper_intraday_drill_policy.v1"
_EPOCH = re.compile(r"^(\d{4}-\d{2}-\d{2})T([01]\d|2[0-3])([0-5]\d)ET$")


def validate_drill_epoch(
    epoch: str | None,
    *,
    trade_date: str,
    policy_path: Path | str | None,
    broker_paper: bool,
    base_url: str,
) -> str | None:
    """Return a governed epoch or fail closed.

    A missing epoch preserves the production one-plan-per-account/date behavior.
    """

    normalized = str(epoch or "").strip()
    if not normalized:
        return None
    match = _EPOCH.fullmatch(normalized)
    if match is None or match.group(1) != str(trade_date):
        raise RuntimeError("paper drill epoch must match trade date and YYYY-MM-DDTHHMMET")
    if not broker_paper or base_url.rstrip("/") != "https://paper-api.alpaca.markets":
        raise RuntimeError("paper drill epoch requires the Alpaca PAPER endpoint")
    if policy_path is None:
        raise RuntimeError("paper drill epoch requires a governed policy artifact")
    path = Path(policy_path)
    if not path.is_file():
        raise RuntimeError("paper drill epoch policy artifact is missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise RuntimeError("paper drill epoch policy must be an object")
    if payload.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise RuntimeError("paper drill epoch policy schema is unsupported")
    if payload.get("paper_only") is not True or payload.get("live_eligible") is not False:
        raise RuntimeError("paper drill epoch policy must prohibit live eligibility")
    if str(payload.get("trade_date") or "") != str(trade_date):
        raise RuntimeError("paper drill epoch policy trade date mismatch")
    allowed = payload.get("allowed_epochs")
    if not isinstance(allowed, list) or normalized not in allowed:
        raise RuntimeError("paper drill epoch is not approved by policy")
    if len(allowed) != len(set(str(value) for value in allowed)):
        raise RuntimeError("paper drill epoch policy contains duplicate epochs")
    for value in allowed:
        item = str(value)
        item_match = _EPOCH.fullmatch(item)
        if item_match is None or item_match.group(1) != str(trade_date):
            raise RuntimeError("paper drill epoch policy contains an invalid epoch")
    safety = payload.get("safety_contract")
    required_safety = (
        "account_date_mutex_remains_global",
        "epoch_reuse_is_idempotent_only",
        "unresolved_prior_epoch_blocks_submission",
        "wal_and_claim_records_are_append_only",
        "normal_hours_market_orders_after_close_prohibited",
    )
    if not isinstance(safety, Mapping) or any(
        safety.get(key) is not True for key in required_safety
    ):
        raise RuntimeError("paper drill epoch policy safety contract is incomplete")
    return normalized


def plan_drill_epoch(plan: Any) -> str | None:
    constraints = getattr(plan, "constraints", None)
    if not isinstance(constraints, Mapping):
        return None
    value = str(constraints.get("paper_drill_epoch") or "").strip()
    if not value:
        return None
    match = _EPOCH.fullmatch(value)
    trade_date = str(getattr(plan, "trade_date", ""))
    if match is None or match.group(1) != trade_date:
        raise RuntimeError("sealed paper drill epoch is invalid")
    if constraints.get("paper_drill_live_eligible") is not False:
        raise RuntimeError("sealed paper drill epoch does not prohibit live eligibility")
    return value


def scoped_wal_root(wal_root: Path | str, epoch: str | None) -> Path:
    root = Path(wal_root)
    return root if epoch is None else root / "epochs" / epoch


def claim_namespace(epoch: str | None) -> Path:
    return Path() if epoch is None else Path("epochs") / str(epoch)
