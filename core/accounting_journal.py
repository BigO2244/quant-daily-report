"""Strict append-only accounting journal contracts for Caerus lanes.

This module is an accounting foundation, not a broker collector, valuation
engine, or execution path.  It accepts only explicit inputs and never reads
runtime configuration.  Every event is hash-bound, contains a balanced set of
double-entry postings, and identifies whether its economics are broker-factual
or theoretical.  A journal batch may contain only one account, lane,
deployment, and performance surface.

Historical economics that predate causal order lineage may be retained only as
``legacy_unattributed``.  New attributed BUY and SELL events require the full
session -> decision -> allocation -> plan -> broker order -> fill chain.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


ACCOUNTING_JOURNAL_ENTRY_SCHEMA = "caerus.accounting_journal_entry.v1"
LEGACY_UNATTRIBUTED = "legacy_unattributed"

EVENT_TYPES = frozenset(
    {
        "OPENING_CAPITAL",
        "ALLOCATION_TRANSFER",
        "BUY",
        "SELL",
        "FEE",
        "DIVIDEND",
        "INTEREST",
        "CORPORATE_ACTION",
        "EXTERNAL_FLOW",
        "MARK",
    }
)
LANE_SURFACE_AUTHORITY = {
    "SHADOW": ("MODELED_SHADOW_NAV", "THEORETICAL_MODEL"),
    "PAPER": ("FACTUAL_PAPER", "BROKER_RECONCILED"),
    "LIVE": ("FACTUAL_LIVE", "BROKER_RECONCILED"),
}
ATTRIBUTION_STATUSES = frozenset({"ATTRIBUTED", "LEGACY_UNATTRIBUTED"})

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,31}$")
_ACCOUNT = re.compile(r"^[A-Z][A-Z0-9_]*(?::[A-Z0-9_]+)+$")
_ZERO = Decimal("0")
_MONEY_TOLERANCE = Decimal("0.00000001")

_ENTRY_FIELDS = frozenset(
    {
        "schema_version",
        "journal_entry_id",
        "event_type",
        "event_time",
        "trade_date",
        "account_id_hash",
        "lane_id",
        "lane_kind",
        "deployment_version",
        "sleeve_id",
        "counterparty_sleeve_id",
        "attribution_status",
        "performance_surface",
        "economic_authority",
        "symbol",
        "quantity",
        "price",
        "gross_amount",
        "fee_amount",
        "net_amount",
        "session_id",
        "decision_id",
        "allocation_id",
        "plan_id",
        "broker_order_id",
        "fill_id",
        "source_hash",
        "postings",
        "record_hash",
    }
)
_POSTING_FIELDS = frozenset(
    {
        "posting_id",
        "ledger_account",
        "sleeve_id",
        "currency",
        "debit_amount",
        "credit_amount",
    }
)
_LINEAGE_FIELDS = (
    "session_id",
    "decision_id",
    "allocation_id",
    "plan_id",
    "broker_order_id",
    "fill_id",
)
_FILL_EVENTS = frozenset({"BUY", "SELL"})
_SYMBOL_EVENTS = frozenset({"BUY", "SELL", "DIVIDEND", "CORPORATE_ACTION", "MARK"})
_CASH_ONLY_EVENTS = frozenset(
    {"OPENING_CAPITAL", "ALLOCATION_TRANSFER", "FEE", "DIVIDEND", "INTEREST", "EXTERNAL_FLOW"}
)


class AccountingJournalError(ValueError):
    """Raised when journal identity, lineage, balance, or immutability fails."""


def _reject_json_constant(value: str) -> None:
    raise AccountingJournalError(f"non-finite JSON constant is forbidden: {value}")


def _object_without_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AccountingJournalError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def canonical_json(payload: Any) -> str:
    """Return the deterministic JSON representation used by journal hashes."""

    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AccountingJournalError(f"journal data is not canonical JSON: {exc}") from exc


def journal_record_hash(payload: Mapping[str, Any]) -> str:
    """Hash one journal record, excluding its self-referential record hash."""

    body = dict(payload)
    body.pop("record_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _strict_fields(
    payload: Mapping[str, Any], expected: frozenset[str], *, label: str
) -> None:
    missing = sorted(expected - set(payload))
    unknown = sorted(set(payload) - expected)
    if missing or unknown:
        raise AccountingJournalError(
            f"{label} fields mismatch; missing={missing}, unknown={unknown}"
        )


def _strict_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise AccountingJournalError(
            f"{label} must be a non-blank string without surrounding whitespace"
        )
    return value


def _safe_id(value: Any, *, label: str) -> str:
    normalized = _strict_string(value, label=label)
    if not _SAFE_ID.fullmatch(normalized) or ".." in normalized:
        raise AccountingJournalError(f"{label} is not a valid identifier")
    return normalized


def _optional_id(value: Any, *, label: str) -> str | None:
    if value is None:
        return None
    return _safe_id(value, label=label)


def _decimal(value: Any, *, label: str, nonnegative: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise AccountingJournalError(f"{label} must be a finite number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AccountingJournalError(f"{label} must be a finite number") from exc
    if not number.is_finite() or (nonnegative and number < _ZERO):
        qualifier = "finite and nonnegative" if nonnegative else "finite"
        raise AccountingJournalError(f"{label} must be {qualifier}")
    return number


def _timestamp(value: Any, *, label: str) -> dt.datetime:
    raw = _strict_string(value, label=label)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AccountingJournalError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AccountingJournalError(f"{label} must include a timezone")
    return parsed


def _date(value: Any, *, label: str) -> str:
    raw = _strict_string(value, label=label)
    try:
        dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise AccountingJournalError(f"{label} must be an ISO date") from exc
    return raw


def _sha256(value: Any, *, label: str) -> str:
    normalized = _strict_string(value, label=label)
    if not _SHA256.fullmatch(normalized):
        raise AccountingJournalError(f"{label} must be a lowercase SHA-256 digest")
    return normalized


def _validate_postings(
    postings: Any,
    *,
    event_type: str,
    sleeve_id: str,
    counterparty_sleeve_id: str | None,
) -> tuple[Decimal, Decimal]:
    if not isinstance(postings, list) or len(postings) < 2:
        raise AccountingJournalError("postings must contain at least two entries")
    posting_ids: set[str] = set()
    allowed_sleeves = {sleeve_id}
    if counterparty_sleeve_id is not None:
        allowed_sleeves.add(counterparty_sleeve_id)
    debit_total = _ZERO
    credit_total = _ZERO
    for index, posting in enumerate(postings):
        label = f"postings[{index}]"
        if not isinstance(posting, Mapping):
            raise AccountingJournalError(f"{label} must be an object")
        _strict_fields(posting, _POSTING_FIELDS, label=label)
        posting_id = _safe_id(posting["posting_id"], label=f"{label}.posting_id")
        if posting_id in posting_ids:
            raise AccountingJournalError("posting_id values must be unique within an entry")
        posting_ids.add(posting_id)
        ledger_account = _strict_string(
            posting["ledger_account"], label=f"{label}.ledger_account"
        )
        if not _ACCOUNT.fullmatch(ledger_account):
            raise AccountingJournalError(f"{label}.ledger_account is invalid")
        posting_sleeve = _safe_id(posting["sleeve_id"], label=f"{label}.sleeve_id")
        if posting_sleeve not in allowed_sleeves:
            raise AccountingJournalError(
                f"{label}.sleeve_id is not bound by the journal entry"
            )
        if posting["currency"] != "USD":
            raise AccountingJournalError(f"{label}.currency must be USD")
        debit = _decimal(posting["debit_amount"], label=f"{label}.debit_amount", nonnegative=True)
        credit = _decimal(
            posting["credit_amount"], label=f"{label}.credit_amount", nonnegative=True
        )
        if debit > _ZERO and credit > _ZERO:
            raise AccountingJournalError(f"{label} cannot contain both a debit and credit")
        if debit == _ZERO and credit == _ZERO and event_type != "CORPORATE_ACTION":
            raise AccountingJournalError(f"{label} must contain a debit or credit")
        debit_total += debit
        credit_total += credit
    if abs(debit_total - credit_total) > _MONEY_TOLERANCE:
        raise AccountingJournalError(
            f"journal entry postings are unbalanced: debits={debit_total}, credits={credit_total}"
        )
    if debit_total == _ZERO and event_type != "CORPORATE_ACTION":
        raise AccountingJournalError("zero-value postings are allowed only for corporate actions")
    return debit_total, credit_total


def _require_money_equal(actual: Decimal, expected: Decimal, *, label: str) -> None:
    if abs(actual - expected) > _MONEY_TOLERANCE:
        raise AccountingJournalError(
            f"{label} mismatch: actual={actual}, expected={expected}"
        )


def validate_journal_entry(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly validate one immutable journal entry and return a detached copy."""

    if not isinstance(payload, Mapping):
        raise AccountingJournalError("journal entry must be an object")
    _strict_fields(payload, _ENTRY_FIELDS, label="journal entry")
    if payload["schema_version"] != ACCOUNTING_JOURNAL_ENTRY_SCHEMA:
        raise AccountingJournalError("unsupported accounting journal entry schema")

    _safe_id(payload["journal_entry_id"], label="journal_entry_id")
    event_type = _strict_string(payload["event_type"], label="event_type")
    if event_type not in EVENT_TYPES:
        raise AccountingJournalError(f"unsupported event_type: {event_type}")
    _timestamp(payload["event_time"], label="event_time")
    _date(payload["trade_date"], label="trade_date")
    _sha256(payload["account_id_hash"], label="account_id_hash")
    _safe_id(payload["lane_id"], label="lane_id")
    lane_kind = _strict_string(payload["lane_kind"], label="lane_kind")
    if lane_kind not in LANE_SURFACE_AUTHORITY:
        raise AccountingJournalError(f"unsupported lane_kind: {lane_kind}")
    _safe_id(payload["deployment_version"], label="deployment_version")
    sleeve_id = _safe_id(payload["sleeve_id"], label="sleeve_id")
    counterparty = _optional_id(
        payload["counterparty_sleeve_id"], label="counterparty_sleeve_id"
    )
    if event_type == "ALLOCATION_TRANSFER":
        if counterparty is None or counterparty == sleeve_id:
            raise AccountingJournalError(
                "allocation transfer requires a distinct counterparty_sleeve_id"
            )
    elif counterparty is not None:
        raise AccountingJournalError(
            "counterparty_sleeve_id is permitted only for allocation transfers"
        )

    attribution = _strict_string(payload["attribution_status"], label="attribution_status")
    if attribution not in ATTRIBUTION_STATUSES:
        raise AccountingJournalError(f"unsupported attribution_status: {attribution}")
    if sleeve_id == LEGACY_UNATTRIBUTED:
        if attribution != "LEGACY_UNATTRIBUTED":
            raise AccountingJournalError(
                "legacy_unattributed sleeve requires LEGACY_UNATTRIBUTED status"
            )
    elif attribution != "ATTRIBUTED":
        raise AccountingJournalError(
            "LEGACY_UNATTRIBUTED status is permitted only for legacy_unattributed"
        )

    expected_surface, expected_authority = LANE_SURFACE_AUTHORITY[lane_kind]
    if payload["performance_surface"] != expected_surface:
        raise AccountingJournalError(
            f"{lane_kind} journal entries require performance_surface={expected_surface}"
        )
    if payload["economic_authority"] != expected_authority:
        raise AccountingJournalError(
            f"{lane_kind} journal entries require economic_authority={expected_authority}"
        )

    symbol = payload["symbol"]
    if symbol is None:
        if event_type in _SYMBOL_EVENTS:
            raise AccountingJournalError(f"{event_type} requires symbol")
    else:
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            raise AccountingJournalError("symbol must be an uppercase security identifier")

    quantity = _decimal(payload["quantity"], label="quantity")
    price = _decimal(payload["price"], label="price", nonnegative=True)
    gross = _decimal(payload["gross_amount"], label="gross_amount", nonnegative=True)
    fee = _decimal(payload["fee_amount"], label="fee_amount", nonnegative=True)
    net = _decimal(payload["net_amount"], label="net_amount")
    if event_type in {"BUY", "SELL", "MARK"}:
        if quantity <= _ZERO:
            raise AccountingJournalError(f"{event_type} quantity must be positive")
        expected_gross = quantity * price
        if abs(gross - expected_gross) > _MONEY_TOLERANCE:
            raise AccountingJournalError(
                f"{event_type} gross_amount must equal quantity multiplied by price"
            )
    elif event_type == "CORPORATE_ACTION":
        if quantity == _ZERO and gross == _ZERO and net == _ZERO:
            raise AccountingJournalError(
                "corporate action must have a quantity or monetary effect"
            )
    elif event_type in _CASH_ONLY_EVENTS and quantity != _ZERO:
        raise AccountingJournalError(f"{event_type} quantity must be zero")

    if event_type == "BUY" and abs(net + gross + fee) > _MONEY_TOLERANCE:
        raise AccountingJournalError("BUY net_amount must equal -(gross_amount + fee_amount)")
    if event_type == "SELL" and abs(net - (gross - fee)) > _MONEY_TOLERANCE:
        raise AccountingJournalError("SELL net_amount must equal gross_amount - fee_amount")
    if event_type == "FEE":
        if fee <= _ZERO or gross != _ZERO or abs(net + fee) > _MONEY_TOLERANCE:
            raise AccountingJournalError(
                "FEE requires positive fee_amount, zero gross_amount, and net_amount=-fee_amount"
            )
    if event_type == "ALLOCATION_TRANSFER":
        if gross <= _ZERO or fee != _ZERO or net != _ZERO:
            raise AccountingJournalError(
                "ALLOCATION_TRANSFER requires positive gross_amount, zero fee, and zero net"
            )
    if event_type in {"OPENING_CAPITAL", "DIVIDEND", "INTEREST"}:
        if gross <= _ZERO or fee != _ZERO or abs(net - gross) > _MONEY_TOLERANCE:
            raise AccountingJournalError(
                f"{event_type} requires positive gross_amount, zero fee, and net_amount=gross_amount"
            )
    if event_type == "EXTERNAL_FLOW":
        if gross <= _ZERO or fee != _ZERO or abs(abs(net) - gross) > _MONEY_TOLERANCE:
            raise AccountingJournalError(
                "EXTERNAL_FLOW requires positive gross_amount, zero fee, and abs(net_amount)=gross_amount"
            )
    if event_type == "MARK" and fee != _ZERO:
        raise AccountingJournalError("MARK fee_amount must be zero")
    if event_type in _CASH_ONLY_EVENTS and price != _ZERO:
        raise AccountingJournalError(f"{event_type} price must be zero")

    lineage = {
        field: _optional_id(payload[field], label=field) for field in _LINEAGE_FIELDS
    }
    if event_type in _FILL_EVENTS and sleeve_id != LEGACY_UNATTRIBUTED:
        missing_lineage = [field for field, value in lineage.items() if value is None]
        if missing_lineage:
            raise AccountingJournalError(
                "attributed fill event lacks exact lineage: " + ", ".join(missing_lineage)
            )
    if event_type == "ALLOCATION_TRANSFER":
        for field in ("session_id", "allocation_id"):
            if lineage[field] is None:
                raise AccountingJournalError(
                    f"allocation transfer requires {field} lineage"
                )
    if sleeve_id == LEGACY_UNATTRIBUTED and any(lineage.values()):
        raise AccountingJournalError(
            "legacy_unattributed events cannot claim causal execution lineage"
        )

    _sha256(payload["source_hash"], label="source_hash")
    posting_total, _ = _validate_postings(
        payload["postings"],
        event_type=event_type,
        sleeve_id=sleeve_id,
        counterparty_sleeve_id=counterparty,
    )
    expected_posting_total = {
        "OPENING_CAPITAL": gross,
        "ALLOCATION_TRANSFER": gross,
        "BUY": gross + fee,
        "SELL": gross,
        "FEE": fee,
        "DIVIDEND": gross,
        "INTEREST": gross,
        "EXTERNAL_FLOW": gross,
        "MARK": abs(net),
    }.get(event_type)
    if expected_posting_total is not None:
        _require_money_equal(
            posting_total,
            expected_posting_total,
            label=f"{event_type} posting total",
        )
    elif event_type == "CORPORATE_ACTION" and net != _ZERO:
        _require_money_equal(
            posting_total,
            abs(net),
            label="CORPORATE_ACTION posting total",
        )
    declared_hash = _sha256(payload["record_hash"], label="record_hash")
    if declared_hash != journal_record_hash(payload):
        raise AccountingJournalError("journal entry record_hash mismatch")
    # JSON round-trip detaches nested mappings and rejects non-JSON values.
    return json.loads(canonical_json(payload))


def seal_journal_entry(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached entry sealed with its record hash, then validate it."""

    body = json.loads(canonical_json(dict(payload)))
    body["record_hash"] = journal_record_hash(body)
    return validate_journal_entry(body)


def validate_journal_batch(
    entries: Iterable[Mapping[str, Any]],
    *,
    existing_entries: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Validate a homogeneous, balanced batch and return idempotent additions.

    Repeating the same ``journal_entry_id`` with the same record hash is a
    no-op.  Reusing an identity for different economics fails closed.  The
    returned list excludes records already present in ``existing_entries``.
    """

    existing_by_id: dict[str, dict[str, Any]] = {}
    journal_scope: tuple[str, str, str, str] | None = None
    batch_scope: tuple[str, str, str, str, str] | None = None
    for raw in existing_entries:
        row = validate_journal_entry(raw)
        file_scope = (
            row["account_id_hash"],
            row["lane_id"],
            row["performance_surface"],
            row["economic_authority"],
        )
        if journal_scope is None:
            journal_scope = file_scope
        elif file_scope != journal_scope:
            raise AccountingJournalError(
                "accounting journal cannot mix account, lane, performance surface, or authority"
            )
        identity = row["journal_entry_id"]
        prior = existing_by_id.get(identity)
        if prior is not None and prior["record_hash"] != row["record_hash"]:
            raise AccountingJournalError(
                f"journal_entry_id is bound to conflicting existing records: {identity}"
            )
        existing_by_id[identity] = row

    additions: list[dict[str, Any]] = []
    seen = dict(existing_by_id)
    debit_total = _ZERO
    credit_total = _ZERO
    for raw in entries:
        row = validate_journal_entry(raw)
        scope = (
            row["account_id_hash"],
            row["lane_id"],
            row["deployment_version"],
            row["performance_surface"],
            row["economic_authority"],
        )
        if batch_scope is None:
            batch_scope = scope
        elif scope != batch_scope:
            raise AccountingJournalError(
                "journal batch cannot mix account, lane, deployment, performance surface, or authority"
            )
        file_scope = (scope[0], scope[1], scope[3], scope[4])
        if journal_scope is not None and file_scope != journal_scope:
            raise AccountingJournalError(
                "accounting journal cannot mix account, lane, performance surface, or authority"
            )
        identity = row["journal_entry_id"]
        prior = seen.get(identity)
        if prior is not None:
            if prior["record_hash"] != row["record_hash"]:
                raise AccountingJournalError(
                    f"journal_entry_id is already bound to different economics: {identity}"
                )
            continue
        for posting in row["postings"]:
            debit_total += _decimal(posting["debit_amount"], label="debit_amount")
            credit_total += _decimal(posting["credit_amount"], label="credit_amount")
        seen[identity] = row
        additions.append(row)
    if abs(debit_total - credit_total) > _MONEY_TOLERANCE:
        raise AccountingJournalError(
            f"journal batch is unbalanced: debits={debit_total}, credits={credit_total}"
        )
    return additions


def validate_accounting_journal(
    entries: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Validate a lane journal history across one or more deployments.

    Deployment versions may advance within one append-only lane journal.  The
    account, lane, performance surface, and authority may not change.  Exact
    duplicate identities are idempotent; conflicting identities fail closed.
    """

    rows: list[dict[str, Any]] = []
    identities: dict[str, dict[str, Any]] = {}
    journal_scope: tuple[str, str, str, str] | None = None
    for raw in entries:
        row = validate_journal_entry(raw)
        scope = (
            row["account_id_hash"],
            row["lane_id"],
            row["performance_surface"],
            row["economic_authority"],
        )
        if journal_scope is None:
            journal_scope = scope
        elif scope != journal_scope:
            raise AccountingJournalError(
                "accounting journal cannot mix account, lane, performance surface, or authority"
            )
        identity = row["journal_entry_id"]
        prior = identities.get(identity)
        if prior is not None:
            if prior["record_hash"] != row["record_hash"]:
                raise AccountingJournalError(
                    f"journal_entry_id is already bound to different economics: {identity}"
                )
            continue
        identities[identity] = row
        rows.append(row)
    return rows


def serialize_journal_entries(entries: Iterable[Mapping[str, Any]]) -> str:
    """Validate and serialize a new homogeneous journal batch as JSON Lines."""

    rows = validate_journal_batch(entries)
    return "".join(canonical_json(row) + "\n" for row in rows)


def _decode_json_line(line: str, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            line,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except (json.JSONDecodeError, AccountingJournalError) as exc:
        raise AccountingJournalError(f"cannot decode {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AccountingJournalError(f"{label} must contain a JSON object")
    return payload


def read_accounting_journal(path: Path | str) -> list[dict[str, Any]]:
    """Read and validate an append-only journal, rejecting identity conflicts."""

    journal_path = Path(path)
    if not journal_path.exists():
        return []
    try:
        lines = journal_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AccountingJournalError(f"cannot read accounting journal {journal_path}: {exc}") from exc
    rows = [
        _decode_json_line(line, label=f"{journal_path}:{line_number}")
        for line_number, line in enumerate(lines, 1)
        if line.strip()
    ]
    # Exact duplicate lines are tolerated as idempotent replay but returned
    # once.  Deployment changes remain explicit within the lane history.
    return validate_accounting_journal(rows)


def write_accounting_journal(
    path: Path | str, entries: Iterable[Mapping[str, Any]]
) -> Path:
    """Create one journal file exactly once; existing evidence is never overwritten."""

    journal_path = Path(path)
    serialized = serialize_journal_entries(entries)
    if not serialized:
        raise AccountingJournalError("cannot create an empty accounting journal")
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with journal_path.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise AccountingJournalError(
            f"accounting journal already exists: {journal_path}"
        ) from exc
    _fsync_directory(journal_path.parent)
    return journal_path


def append_accounting_journal(
    path: Path | str, entries: Iterable[Mapping[str, Any]]
) -> int:
    """Append only new immutable identities and return the number written.

    The file is locked across read/validate/append so a concurrent writer
    cannot silently bind the same identity to different economics.
    """

    raw_candidates = list(entries)
    if not raw_candidates:
        return 0
    # Fail before touching the filesystem when the proposed batch is invalid.
    candidates = validate_journal_batch(raw_candidates)
    journal_path = Path(path)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            existing = [
                _decode_json_line(line, label=f"{journal_path}:{line_number}")
                for line_number, line in enumerate(handle.read().splitlines(), 1)
                if line.strip()
            ]
            additions = validate_journal_batch(candidates, existing_entries=existing)
            if additions:
                handle.seek(0, os.SEEK_END)
                for row in additions:
                    handle.write(canonical_json(row) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return len(additions)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            _fsync_directory(journal_path.parent)


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


__all__ = [
    "ACCOUNTING_JOURNAL_ENTRY_SCHEMA",
    "ATTRIBUTION_STATUSES",
    "AccountingJournalError",
    "EVENT_TYPES",
    "LANE_SURFACE_AUTHORITY",
    "LEGACY_UNATTRIBUTED",
    "append_accounting_journal",
    "canonical_json",
    "journal_record_hash",
    "read_accounting_journal",
    "seal_journal_entry",
    "serialize_journal_entries",
    "validate_accounting_journal",
    "validate_journal_batch",
    "validate_journal_entry",
    "write_accounting_journal",
]
