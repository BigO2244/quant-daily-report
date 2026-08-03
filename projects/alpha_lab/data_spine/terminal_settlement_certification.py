"""Fail-closed certification of exact historical terminal settlements.

The auditor consumes a caller-supplied, immutable evidence bundle.  It does
not collect evidence, infer proceeds from the last trade, or mutate a price
panel.  A scope is certified only when every terminated common-equity history
has one exact, independently sourced final cash outcome (including an
officially evidenced zero recovery).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from projects.alpha_lab.factory import canonical_hash, canonical_json
from projects.alpha_lab.factory.canonical import parse_datetime

from .storage import sha256_file


SCHEMA_VERSION = "caerus_alpha_lab_terminal_settlement_certification_v1"
EVIDENCE_SCHEMA_VERSION = "caerus_alpha_lab_terminal_settlement_evidence_v1"
POPULATION_RULE = "explicit_eligible_terminal_actions_v1"

_FINAL_OUTCOMES = frozenset({"FINAL_CASH", "FINAL_ZERO_RECOVERY"})
_FINALITY_BASES = frozenset(
    {
        "CLOSING_PAYMENT",
        "FINAL_BANKRUPTCY_DISTRIBUTION",
        "FINAL_LIQUIDATION_DISTRIBUTION",
        "NO_RECOVERY_FINAL_ORDER",
    }
)
_OFFICIAL_AUTHORITIES = frozenset(
    {
        "COURT_ORDER",
        "EXCHANGE_NOTICE",
        "ISSUER_FINAL_DISTRIBUTION",
        "SEC_FILING",
        "TRANSFER_AGENT",
    }
)
_TERMINATION_TYPES = frozenset(
    {
        "ACQUISITION",
        "BANKRUPTCY",
        "LIQUIDATION",
        "MERGER",
        "REGULATORY_DELISTING",
        "VOLUNTARY_DELISTING",
    }
)


def _date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("{} must be an ISO-8601 date".format(field)) from exc


def _bounded_path(root: Path, relative: Any, field: str) -> Path:
    value = Path(str(relative))
    if value.is_absolute() or ".." in value.parts:
        raise ValueError("{} must be relative to the evidence bundle".format(field))
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("{} escapes the evidence bundle".format(field)) from exc
    return path


def _load_json_lines(path: Path) -> list[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("evidence line {} must be an object".format(line_number))
            rows.append(value)
    return rows


def _security_master(security_master_path: Path) -> Dict[str, Dict[str, str]]:
    securities: Dict[str, Dict[str, str]] = {}
    with security_master_path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            security_id = str(row.get("security_id") or "").strip()
            if not security_id:
                continue
            if security_id in securities:
                raise ValueError("duplicate security_id in security master: {}".format(security_id))
            securities[security_id] = dict(row)
    return securities


def _terminated_population(
    *,
    security_master_path: Path,
    termination_rows: Iterable[Mapping[str, Any]],
    documents: Mapping[str, Mapping[str, Any]],
    scope_start: date,
    scope_end: date,
    blockers: list[Dict[str, str]],
) -> Dict[str, Dict[str, str]]:
    securities = _security_master(security_master_path)
    population: Dict[str, Dict[str, str]] = {}
    action_ids = set()
    for row in termination_rows:
        security_id = str(row.get("security_id") or "").strip()
        action_id = str(row.get("termination_action_id") or "").strip()
        if not security_id or not action_id:
            blockers.append({"code": "TERMINATION_IDENTITY_MISSING", "detail": security_id or "missing"})
            continue
        if action_id in action_ids or security_id in population:
            blockers.append({"code": "DUPLICATE_TERMINATION_ACTION", "detail": action_id})
            continue
        action_ids.add(action_id)
        security = securities.get(security_id)
        if security is None:
            blockers.append({"code": "TERMINATION_SECURITY_UNRESOLVED", "detail": security_id})
            continue
        if str(security.get("category") or "") != "Domestic Common Stock":
            blockers.append({"code": "TERMINATION_NOT_DOMESTIC_COMMON_STOCK", "detail": security_id})
            continue
        if row.get("termination_type") not in _TERMINATION_TYPES:
            blockers.append({"code": "TERMINATION_TYPE_INVALID", "detail": security_id})
            continue
        try:
            effective = _date(row.get("termination_effective_date"), "termination_effective_date")
        except ValueError as exc:
            blockers.append({"code": "TERMINATION_DATE_INVALID", "detail": "{}: {}".format(security_id, exc)})
            continue
        if not scope_start <= effective <= scope_end:
            blockers.append({"code": "TERMINATION_OUTSIDE_SCOPE", "detail": security_id})
            continue
        source_ids = row.get("source_document_ids")
        if not isinstance(source_ids, list) or not source_ids or any(
            str(value) not in documents for value in source_ids
        ):
            blockers.append({"code": "TERMINATION_ELIGIBILITY_SOURCE_UNVERIFIED", "detail": security_id})
            continue
        population[security_id] = {
            "security_id": security_id,
            "ticker": str(security.get("ticker") or ""),
            "termination_action_id": action_id,
            "termination_type": str(row["termination_type"]),
            "termination_effective_date": effective.isoformat(),
        }
    return population


def _last_prices(path: Path, scope_end: date) -> tuple[Dict[str, Dict[str, Any]], set[str]]:
    if path.suffix.lower() in {".parquet", ".pq"}:
        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError("duckdb is required to audit a parquet price panel") from exc
        connection = duckdb.connect()
        try:
            rows = connection.execute(
                """
                WITH bounded AS (
                  SELECT security_id, CAST(date AS DATE) AS observed_date,
                         CAST(close AS DOUBLE) AS close
                  FROM read_parquet(?)
                  WHERE CAST(date AS DATE) <= CAST(? AS DATE)
                ), maxima AS (
                  SELECT security_id, MAX(observed_date) AS last_date
                  FROM bounded GROUP BY security_id
                )
                SELECT b.security_id, m.last_date, MAX(b.close), COUNT(*)
                FROM bounded b JOIN maxima m
                  ON b.security_id = m.security_id AND b.observed_date = m.last_date
                GROUP BY b.security_id, m.last_date
                ORDER BY b.security_id
                """,
                [str(path), scope_end.isoformat()],
            ).fetchall()
        finally:
            connection.close()
        result = {
            str(security_id): {"date": str(last_date), "close": float(close)}
            for security_id, last_date, close, count in rows
            if security_id and close is not None and math.isfinite(float(close)) and float(close) > 0 and count == 1
        }
        duplicates = {str(security_id) for security_id, _, _, count in rows if count != 1}
        return result, duplicates

    observations: Dict[str, Dict[str, list[float]]] = {}
    with path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            security_id = str(row.get("security_id") or "").strip()
            observed_date = str(row.get("date") or "")[:10]
            if not security_id or not observed_date:
                continue
            if _date(observed_date, "price.date") > scope_end:
                continue
            close = float(str(row.get("close") or "nan"))
            if not math.isfinite(close) or close <= 0:
                continue
            observations.setdefault(security_id, {}).setdefault(observed_date, []).append(close)
    result: Dict[str, Dict[str, Any]] = {}
    duplicates = set()
    for security_id, by_date in observations.items():
        last_date = max(by_date)
        values = by_date[last_date]
        if len(values) != 1:
            duplicates.add(security_id)
            continue
        result[security_id] = {"date": last_date, "close": values[0]}
    return result, duplicates


def _source_documents(
    *, manifest: Mapping[str, Any], bundle_root: Path, as_of: datetime,
    blockers: list[Dict[str, str]]
) -> Dict[str, Dict[str, Any]]:
    documents: Dict[str, Dict[str, Any]] = {}
    for record in manifest.get("source_documents", []):
        if not isinstance(record, dict):
            blockers.append({"code": "INVALID_SOURCE_DOCUMENT", "detail": "source document is not an object"})
            continue
        document_id = str(record.get("document_id") or "").strip()
        if not document_id or document_id in documents:
            blockers.append({"code": "INVALID_SOURCE_DOCUMENT_ID", "detail": document_id or "missing"})
            continue
        try:
            path = _bounded_path(bundle_root, record.get("path"), "source_documents.path")
        except ValueError as exc:
            blockers.append({"code": "INVALID_SOURCE_DOCUMENT_PATH", "detail": str(exc)})
            continue
        expected_hash = str(record.get("sha256") or "")
        if not path.is_file() or sha256_file(path) != expected_hash:
            blockers.append({"code": "SOURCE_DOCUMENT_HASH_MISMATCH", "detail": document_id})
            continue
        if record.get("authority") not in _OFFICIAL_AUTHORITIES:
            blockers.append({"code": "SOURCE_NOT_OFFICIAL_FINALITY_EVIDENCE", "detail": document_id})
            continue
        if (
            not str(record.get("provider_id") or "").strip()
            or record.get("provider_id") == manifest.get("price_provider_id")
        ):
            blockers.append({"code": "SOURCE_NOT_INDEPENDENT", "detail": document_id})
            continue
        try:
            published_at = parse_datetime(str(record.get("published_at") or ""))
        except ValueError:
            blockers.append({"code": "INVALID_SOURCE_PUBLICATION_TIME", "detail": document_id})
            continue
        if published_at > as_of:
            blockers.append({"code": "SOURCE_NOT_AVAILABLE_AS_OF", "detail": document_id})
            continue
        if not str(record.get("source_uri") or "").strip():
            blockers.append({"code": "SOURCE_URI_MISSING", "detail": document_id})
            continue
        review = record.get("reviewer_attestation")
        try:
            reviewed_at = parse_datetime(str(review.get("reviewed_at") or "")) if isinstance(review, dict) else None
        except ValueError:
            reviewed_at = None
        if (
            not str(record.get("pinpoint_locator") or "").strip()
            or not str(record.get("extracted_term") or "").strip()
            or not isinstance(review, dict)
            or not str(review.get("reviewer") or "").strip()
            or review.get("conclusion") != "VERIFIED_EXACT_TERM"
            or review.get("independent_of_source_and_price_provider") is not True
            or reviewed_at is None
            or reviewed_at > as_of
        ):
            blockers.append({"code": "SOURCE_REVIEW_ATTESTATION_INCOMPLETE", "detail": document_id})
            continue
        documents[document_id] = dict(record)
    return documents


def audit_terminal_settlements(
    *,
    evidence_manifest_path: Path,
    security_master_path: Path,
    price_panel_path: Path,
    scope_start: date,
    scope_end: date,
    as_of: datetime,
) -> Dict[str, Any]:
    """Audit exact payout evidence and return a deterministic certification packet."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if scope_end < scope_start:
        raise ValueError("scope_end precedes scope_start")
    manifest_path = evidence_manifest_path.expanduser().resolve()
    master_path = security_master_path.expanduser().resolve()
    panel_path = price_panel_path.expanduser().resolve()
    for path in (manifest_path, master_path, panel_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("evidence manifest must be an object")
    blockers: list[Dict[str, str]] = []
    if manifest.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        blockers.append({"code": "EVIDENCE_SCHEMA_INVALID", "detail": str(manifest.get("schema_version"))})
    if manifest.get("classification") != "RESEARCH_ONLY_NON_EXECUTIONAL":
        blockers.append({"code": "CLASSIFICATION_INVALID", "detail": str(manifest.get("classification"))})
    if manifest.get("population_rule") != POPULATION_RULE:
        blockers.append({"code": "POPULATION_RULE_INVALID", "detail": str(manifest.get("population_rule"))})
    if manifest.get("security_master_sha256") != sha256_file(master_path):
        blockers.append({"code": "SECURITY_MASTER_HASH_MISMATCH", "detail": str(master_path)})
    if manifest.get("price_panel_sha256") != sha256_file(panel_path):
        blockers.append({"code": "PRICE_PANEL_HASH_MISMATCH", "detail": str(panel_path)})
    expected_price_basis = {
        "field": "close",
        "semantics": "UNADJUSTED_LAST_OBSERVED_TRADE",
        "terminal_proceeds_included": False,
        "terminal_return_application": "AFTER_LAST_OBSERVED_RETURN_ONLY",
    }
    if manifest.get("price_basis") != expected_price_basis:
        blockers.append(
            {
                "code": "PRICE_RETURN_OVERLAP_NOT_DISPROVEN",
                "detail": canonical_json(manifest.get("price_basis")),
            }
        )
    expected_scope = {"start": scope_start.isoformat(), "end": scope_end.isoformat()}
    if manifest.get("scope") != expected_scope:
        blockers.append({"code": "SCOPE_MISMATCH", "detail": canonical_json(manifest.get("scope"))})
    expected_extract = {
        "prechallenge_extract": True,
        "maximum_observation_date": scope_end.isoformat(),
    }
    if manifest.get("price_extract_contract") != expected_extract:
        blockers.append(
            {
                "code": "PRECHALLENGE_PRICE_EXTRACT_NOT_CERTIFIED",
                "detail": canonical_json(manifest.get("price_extract_contract")),
            }
        )

    bundle_root = manifest_path.parent
    if not str(manifest.get("price_provider_id") or "").strip():
        blockers.append({"code": "PRICE_PROVIDER_ID_MISSING", "detail": "manifest.price_provider_id"})
    documents = _source_documents(
        manifest=manifest, bundle_root=bundle_root, as_of=as_of, blockers=blockers
    )
    termination_rows: list[Dict[str, Any]] = []
    termination_record = manifest.get("termination_population_file")
    if not isinstance(termination_record, dict):
        blockers.append({"code": "TERMINATION_POPULATION_FILE_MISSING", "detail": "manifest.termination_population_file"})
    else:
        try:
            termination_path = _bounded_path(
                bundle_root,
                termination_record.get("path"),
                "termination_population_file.path",
            )
            if (
                not termination_path.is_file()
                or sha256_file(termination_path) != termination_record.get("sha256")
            ):
                blockers.append({"code": "TERMINATION_POPULATION_HASH_MISMATCH", "detail": str(termination_path)})
            else:
                termination_rows = _load_json_lines(termination_path)
        except (ValueError, json.JSONDecodeError) as exc:
            blockers.append({"code": "TERMINATION_POPULATION_INVALID", "detail": str(exc)})
    completeness = manifest.get("population_completeness_attestation")
    try:
        completeness_reviewed_at = (
            parse_datetime(str(completeness.get("reviewed_at") or ""))
            if isinstance(completeness, dict)
            else None
        )
    except ValueError:
        completeness_reviewed_at = None
    if (
        not isinstance(completeness, dict)
        or completeness.get("conclusion") != "COMPLETE_FOR_SCOPE"
        or completeness.get("independent_of_evidence_preparer") is not True
        or not str(completeness.get("reviewer") or "").strip()
        or not str(completeness.get("methodology") or "").strip()
        or completeness_reviewed_at is None
        or completeness_reviewed_at > as_of
    ):
        blockers.append({"code": "POPULATION_COMPLETENESS_NOT_ATTESTED", "detail": "termination population"})
    evidence_record = manifest.get("evidence_file")
    rows: list[Dict[str, Any]] = []
    if not isinstance(evidence_record, dict):
        blockers.append({"code": "EVIDENCE_FILE_MISSING", "detail": "manifest.evidence_file"})
    else:
        try:
            evidence_path = _bounded_path(bundle_root, evidence_record.get("path"), "evidence_file.path")
            if not evidence_path.is_file() or sha256_file(evidence_path) != evidence_record.get("sha256"):
                blockers.append({"code": "EVIDENCE_FILE_HASH_MISMATCH", "detail": str(evidence_path)})
            else:
                rows = _load_json_lines(evidence_path)
        except (ValueError, json.JSONDecodeError) as exc:
            blockers.append({"code": "EVIDENCE_FILE_INVALID", "detail": str(exc)})

    population = _terminated_population(
        security_master_path=master_path,
        termination_rows=termination_rows,
        documents=documents,
        scope_start=scope_start,
        scope_end=scope_end,
        blockers=blockers,
    )
    prices, duplicate_last_prices = _last_prices(panel_path, scope_end)
    for security_id in sorted(duplicate_last_prices & set(population)):
        blockers.append({"code": "LAST_OBSERVED_PRICE_NOT_UNIQUE", "detail": security_id})
    by_security: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        security_id = str(row.get("security_id") or "").strip()
        if not security_id:
            blockers.append({"code": "EVIDENCE_SECURITY_ID_MISSING", "detail": "row"})
            continue
        if security_id in by_security:
            blockers.append({"code": "DUPLICATE_SETTLEMENT_EVIDENCE", "detail": security_id})
            continue
        by_security[security_id] = row
        if security_id not in population:
            blockers.append({"code": "OUT_OF_SCOPE_EVIDENCE", "detail": security_id})

    certified_rows = []
    for security_id in sorted(population):
        row = by_security.get(security_id)
        if row is None:
            blockers.append({"code": "SETTLEMENT_EVIDENCE_MISSING", "detail": security_id})
            continue
        outcome = str(row.get("outcome_type") or "")
        if outcome not in _FINAL_OUTCOMES:
            blockers.append(
                {
                    "code": "NONCASH_OR_CONTINGENT_OUTCOME_NOT_EXACTLY_VALUED",
                    "detail": security_id,
                }
            )
            continue
        if row.get("finality_basis") not in _FINALITY_BASES:
            blockers.append({"code": "FINALITY_BASIS_INVALID", "detail": security_id})
            continue
        if str(row.get("currency") or "") != "USD":
            blockers.append({"code": "NON_USD_OR_MISSING_CURRENCY", "detail": security_id})
            continue
        try:
            proceeds = float(row.get("terminal_proceeds_per_pre_action_share"))
            settlement_date = _date(row.get("settlement_effective_date"), "settlement_effective_date")
            available_at = parse_datetime(str(row.get("evidence_available_at") or ""))
        except (TypeError, ValueError) as exc:
            blockers.append({"code": "INVALID_SETTLEMENT_VALUE_OR_TIME", "detail": "{}: {}".format(security_id, exc)})
            continue
        if not math.isfinite(proceeds) or proceeds < 0:
            blockers.append({"code": "INVALID_TERMINAL_PROCEEDS", "detail": security_id})
            continue
        if (outcome == "FINAL_ZERO_RECOVERY") != (proceeds == 0.0):
            blockers.append({"code": "OUTCOME_PROCEEDS_CONFLICT", "detail": security_id})
            continue
        if outcome == "FINAL_ZERO_RECOVERY" and row.get("finality_basis") not in {
            "FINAL_BANKRUPTCY_DISTRIBUTION",
            "FINAL_LIQUIDATION_DISTRIBUTION",
            "NO_RECOVERY_FINAL_ORDER",
        }:
            blockers.append({"code": "ZERO_RECOVERY_FINALITY_INSUFFICIENT", "detail": security_id})
            continue
        if outcome == "FINAL_CASH" and row.get("finality_basis") == "NO_RECOVERY_FINAL_ORDER":
            blockers.append({"code": "CASH_FINALITY_BASIS_CONFLICT", "detail": security_id})
            continue
        if available_at > as_of:
            blockers.append({"code": "EVIDENCE_NOT_AVAILABLE_AS_OF", "detail": security_id})
            continue
        source_ids = row.get("source_document_ids")
        if not isinstance(source_ids, list) or not source_ids or any(
            str(value) not in documents for value in source_ids
        ):
            blockers.append({"code": "FINALITY_SOURCE_UNVERIFIED", "detail": security_id})
            continue
        source_publication_times = [
            parse_datetime(str(documents[str(value)]["published_at"]))
            for value in source_ids
        ]
        if available_at < max(source_publication_times):
            blockers.append({"code": "EVIDENCE_AVAILABLE_BEFORE_SOURCE", "detail": security_id})
            continue
        termination_effective_date = _date(
            population[security_id]["termination_effective_date"],
            "termination_effective_date",
        )
        if settlement_date < termination_effective_date or settlement_date > as_of.date():
            blockers.append({"code": "SETTLEMENT_TIMING_OUT_OF_BOUNDS", "detail": security_id})
            continue
        price = prices.get(security_id)
        if price is None:
            blockers.append({"code": "LAST_OBSERVED_PRICE_MISSING", "detail": security_id})
            continue
        if _date(price["date"], "last_observed_date") > settlement_date:
            blockers.append({"code": "SETTLEMENT_PRECEDES_LAST_PRICE", "detail": security_id})
            continue
        terminal_return = proceeds / float(price["close"]) - 1.0
        certified_rows.append(
            {
                "security_id": security_id,
                "last_observed_date": price["date"],
                "last_observed_close": float(price["close"]),
                "settlement_effective_date": settlement_date.isoformat(),
                "termination_action_id": population[security_id]["termination_action_id"],
                "evidence_available_at": available_at.isoformat().replace("+00:00", "Z"),
                "terminal_proceeds_per_pre_action_share": proceeds,
                "verified_terminal_return": terminal_return,
                "source_document_ids": sorted(str(value) for value in source_ids),
                "use_in_primary_point_estimate": True,
            }
        )

    blockers.sort(key=lambda value: (value["code"], value["detail"]))
    status = "CERTIFIED_READY" if population and not blockers and len(certified_rows) == len(population) else "NOT_CERTIFIED"
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "status": status,
        "as_of": as_of.isoformat().replace("+00:00", "Z"),
        "scope": expected_scope,
        "population_rule": POPULATION_RULE,
        "population_security_count": len(population),
        "evidence_security_count": len(by_security),
        "certified_security_count": len(certified_rows),
        "terminal_settlement_certified": status == "CERTIFIED_READY",
        "historical_point_in_time_terminal_return_verified": status == "CERTIFIED_READY",
        "blockers": blockers,
        "verified_terminal_returns": certified_rows,
        "source_manifest_sha256": sha256_file(manifest_path),
        "security_master_sha256": sha256_file(master_path),
        "price_panel_sha256": sha256_file(panel_path),
        "last_trade_used_as_settlement": False,
        "terminal_return_application": "AFTER_LAST_OBSERVED_RETURN_ONLY",
        "provider_return_double_count_permitted": False,
        "inferred_payouts_permitted": False,
        "orders_submitted": False,
        "trading_behavior_changed": False,
    }
    return {**unsigned, "certification_hash": canonical_hash(unsigned)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit exact terminal-settlement evidence")
    parser.add_argument("--evidence-manifest", type=Path, required=True)
    parser.add_argument("--security-master", type=Path, required=True)
    parser.add_argument("--price-panel", type=Path, required=True)
    parser.add_argument("--scope-start", type=date.fromisoformat, required=True)
    parser.add_argument("--scope-end", type=date.fromisoformat, required=True)
    parser.add_argument("--as-of", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = audit_terminal_settlements(
        evidence_manifest_path=args.evidence_manifest,
        security_master_path=args.security_master,
        price_panel_path=args.price_panel,
        scope_start=args.scope_start,
        scope_end=args.scope_end,
        as_of=parse_datetime(args.as_of),
    )
    print(canonical_json(result))
    return 0 if result["status"] == "CERTIFIED_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
