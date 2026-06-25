from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_data.hydration import read_json, utc_now_iso, write_json


P1_DATASETS = {"ohlcv_prices", "security_master_pit", "corporate_actions", "dataset_freshness"}


def normalize_p1(
    *,
    repo_root: Path,
    as_of_date: str | None = None,
    dataset_ids: set[str] | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root)
    selected = set(dataset_ids or P1_DATASETS)
    unsupported = selected - P1_DATASETS
    if unsupported:
        raise ValueError(f"Unsupported P1 datasets: {sorted(unsupported)}")
    run_metadata = _latest_hydration_metadata(repo_root)
    effective_as_of = as_of_date or run_metadata.get("summary", {}).get("as_of_date") or datetime.now(UTC).date().isoformat()
    generated_at = utc_now_iso()
    results: list[dict[str, Any]] = []

    normalizers = {
        "ohlcv_prices": _normalize_ohlcv_prices,
        "security_master_pit": _normalize_security_master,
        "corporate_actions": _normalize_corporate_actions,
        "dataset_freshness": _normalize_dataset_freshness,
    }
    for dataset_id in sorted(selected):
        try:
            results.append(normalizers[dataset_id](repo_root, effective_as_of, generated_at, run_metadata))
        except FileNotFoundError as exc:
            results.append(
                _result(
                    dataset_id=dataset_id,
                    status="MISSING_SOURCE",
                    artifact_path=None,
                    row_count=0,
                    validation_errors=[str(exc)],
                    source_artifacts=[],
                    generated_at=generated_at,
                    as_of_date=effective_as_of,
                )
            )

    manifest = {
        "schema_version": "p1_normalization_manifest_v1",
        "generated_at": generated_at,
        "as_of_date": effective_as_of,
        "runtime_impact": "read_only_normalization_no_trading_path_changes",
        "dataset_count": len(results),
        "normalized_dataset_count": sum(1 for item in results if item["status"] in {"OK", "WARN"}),
        "failed_dataset_count": sum(1 for item in results if item["status"] not in {"OK", "WARN"}),
        "datasets": results,
    }
    write_json(repo_root / "data" / "manifests" / "p1_normalization_manifest.json", manifest)
    return manifest


def _normalize_ohlcv_prices(repo_root: Path, as_of_date: str, generated_at: str, run_metadata: dict[str, Any]) -> dict[str, Any]:
    path = _require_first(
        repo_root,
        [
            "data/raw/ohlcv_prices/yahoo_chart_public/ohlcv_prices_sample.json",
            "data/raw/ohlcv_prices/nasdaq_sharadar/sharadar_sep_sample.json",
        ],
    )
    payload = read_json(path)
    rows = _normalize_yahoo_ohlcv(payload, as_of_date, generated_at, path)
    artifact = repo_root / "data" / "normalized" / "prices" / "ohlcv_prices.json"
    normalized = _dataset_payload("ohlcv_prices", as_of_date, generated_at, [path], rows)
    validation = _validate_required(rows, ["security_id", "source_symbol", "trade_date", "close", "price_source", "as_of_date", "ingestion_timestamp"])
    validation.extend(_validate_date_lte(rows, "trade_date", "as_of_date"))
    normalized["validation"] = _validation_payload(validation)
    write_json(artifact, normalized)
    return _result_from_payload("ohlcv_prices", artifact, normalized, generated_at, as_of_date, run_metadata)


def _normalize_security_master(repo_root: Path, as_of_date: str, generated_at: str, run_metadata: dict[str, Any]) -> dict[str, Any]:
    path = _require_first(
        repo_root,
        [
            "data/raw/security_master_pit/nasdaq_sharadar/sharadar_tickers_sample.json",
            "data/raw/security_master_pit/sec_edgar_public/company_tickers_sample.json",
        ],
    )
    payload = read_json(path)
    if path.name == "company_tickers_sample.json":
        rows = _normalize_sec_company_tickers(payload, as_of_date, generated_at, path)
    else:
        rows = _normalize_sharadar_tickers(payload, as_of_date, generated_at, path)
    artifact = repo_root / "data" / "normalized" / "security_master" / "security_master.json"
    normalized = _dataset_payload("security_master_pit", as_of_date, generated_at, [path], rows)
    validation = _validate_required(rows, ["security_id", "ticker", "asset_type", "is_active", "effective_start_date", "as_of_date", "source", "ingestion_timestamp"])
    validation.extend(_validate_date_lte(rows, "effective_start_date", "as_of_date"))
    normalized["validation"] = _validation_payload(validation)
    write_json(artifact, normalized)
    return _result_from_payload("security_master_pit", artifact, normalized, generated_at, as_of_date, run_metadata)


def _normalize_corporate_actions(repo_root: Path, as_of_date: str, generated_at: str, run_metadata: dict[str, Any]) -> dict[str, Any]:
    path = _require_first(
        repo_root,
        [
            "data/raw/corporate_actions/nasdaq_sharadar/sharadar_actions_sample.json",
        ],
    )
    payload = read_json(path)
    rows = _normalize_sharadar_actions(payload, as_of_date, generated_at, path)
    artifact = repo_root / "data" / "normalized" / "corporate_actions" / "actions.json"
    normalized = _dataset_payload("corporate_actions", as_of_date, generated_at, [path], rows)
    validation = _validate_required(rows, ["corporate_action_id", "security_id", "action_type", "effective_date", "as_of_date", "source", "ingestion_timestamp"])
    validation.extend(_validate_date_lte(rows, "effective_date", "as_of_date"))
    normalized["validation"] = _validation_payload(validation)
    write_json(artifact, normalized)
    return _result_from_payload("corporate_actions", artifact, normalized, generated_at, as_of_date, run_metadata)


def _normalize_dataset_freshness(repo_root: Path, as_of_date: str, generated_at: str, run_metadata: dict[str, Any]) -> dict[str, Any]:
    path = _require_first(repo_root, ["data/manifests/dataset_freshness.json"])
    payload = read_json(path)
    rows = []
    for row in payload.get("datasets") or []:
        rows.append(
            {
                "dataset_id": row.get("dataset_id"),
                "dataset_name": row.get("dataset_name"),
                "as_of_date": row.get("as_of_date") or payload.get("as_of_date") or as_of_date,
                "freshness_status": row.get("freshness_status"),
                "hydration_status": row.get("hydration_status"),
                "latest_source_observation_date": row.get("latest_source_observation_date"),
                "latest_ingestion_timestamp": row.get("latest_ingestion_timestamp"),
                "artifact_path": row.get("artifact_path"),
                "records_written": row.get("records_written"),
                "validation_status": row.get("validation_status"),
                "PIT_safe_status": row.get("PIT_safe_status"),
                "reason": row.get("reason"),
                "generated_at": generated_at,
                "source": "hydration_swarm",
                "source_artifact_digest": _sha256(path),
            }
        )
    artifact = repo_root / "data" / "normalized" / "freshness" / "dataset_freshness.json"
    normalized = _dataset_payload("dataset_freshness", as_of_date, generated_at, [path], rows)
    validation = _validate_required(rows, ["dataset_id", "as_of_date", "freshness_status", "hydration_status", "latest_ingestion_timestamp", "validation_status", "PIT_safe_status", "reason", "generated_at"])
    normalized["validation"] = _validation_payload(validation)
    write_json(artifact, normalized)
    return _result_from_payload("dataset_freshness", artifact, normalized, generated_at, as_of_date, run_metadata)


def _normalize_yahoo_ohlcv(payload: dict[str, Any], as_of_date: str, generated_at: str, path: Path) -> list[dict[str, Any]]:
    source_payload = payload.get("source_payload") or {}
    result = ((source_payload.get("chart") or {}).get("result") or [{}])[0]
    timestamps = result.get("timestamp") or [row.get("timestamp") for row in payload.get("rows", [])]
    quote = (((result.get("indicators") or {}).get("quote") or [{}])[0])
    adjclose = (((result.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose") or []
    rows = []
    for idx, timestamp in enumerate(timestamps):
        if timestamp in (None, ""):
            continue
        trade_date = datetime.fromtimestamp(int(timestamp), UTC).date().isoformat()
        close = _list_get(quote.get("close") or [], idx)
        rows.append(
            {
                "security_id": f"YAHOO:{payload.get('symbol', 'UNKNOWN').replace('%5E', '^')}",
                "source_symbol": payload.get("symbol"),
                "trade_date": trade_date,
                "open": _list_get(quote.get("open") or [], idx),
                "high": _list_get(quote.get("high") or [], idx),
                "low": _list_get(quote.get("low") or [], idx),
                "close": close,
                "close_adjusted": _list_get(adjclose, idx),
                "volume": _list_get(quote.get("volume") or [], idx),
                "price_source": "yahoo_chart_public",
                "adjustment_policy": "source_adjusted_close_when_available",
                "as_of_date": as_of_date,
                "ingestion_timestamp": generated_at,
                "source_retrieved_at": generated_at,
                "source_artifact_digest": _sha256(path),
            }
        )
    return rows


def _normalize_sharadar_tickers(payload: dict[str, Any], as_of_date: str, generated_at: str, path: Path) -> list[dict[str, Any]]:
    rows = []
    for item in payload.get("rows") or []:
        permaticker = item.get("permaticker")
        is_delisted = str(item.get("isdelisted") or "").upper() in {"Y", "YES", "TRUE", "1"}
        first_price_date = _date_or_default(item.get("firstpricedate"), as_of_date)
        rows.append(
            {
                "security_id": f"SHARADAR:{permaticker}" if permaticker not in (None, "") else f"SHARADAR_TICKER:{item.get('ticker')}",
                "source_security_id": str(permaticker) if permaticker not in (None, "") else None,
                "ticker": item.get("ticker"),
                "name": item.get("name"),
                "exchange": item.get("exchange"),
                "asset_type": "equity_or_unit",
                "is_active": not is_delisted,
                "listing_date": item.get("firstpricedate"),
                "delisting_date": item.get("lastpricedate") if is_delisted else None,
                "effective_start_date": first_price_date,
                "effective_end_date": item.get("lastpricedate") if is_delisted else None,
                "as_of_date": as_of_date,
                "source": "nasdaq_sharadar",
                "ingestion_timestamp": generated_at,
                "source_artifact_digest": _sha256(path),
            }
        )
    return rows


def _normalize_sec_company_tickers(payload: dict[str, Any], as_of_date: str, generated_at: str, path: Path) -> list[dict[str, Any]]:
    rows = []
    for item in payload.values():
        cik = item.get("cik_str")
        rows.append(
            {
                "security_id": f"SEC_CIK:{cik}",
                "source_security_id": str(cik) if cik is not None else None,
                "ticker": item.get("ticker"),
                "name": item.get("title"),
                "exchange": None,
                "asset_type": "unknown_sec_company_ticker",
                "is_active": True,
                "listing_date": None,
                "delisting_date": None,
                "effective_start_date": as_of_date,
                "effective_end_date": None,
                "as_of_date": as_of_date,
                "source": "sec_edgar_public",
                "ingestion_timestamp": generated_at,
                "source_artifact_digest": _sha256(path),
            }
        )
    return rows


def _normalize_sharadar_actions(payload: dict[str, Any], as_of_date: str, generated_at: str, path: Path) -> list[dict[str, Any]]:
    rows = []
    for item in payload.get("rows") or []:
        action_type = str(item.get("action") or "").strip().lower()
        ticker = item.get("ticker")
        effective_date = _date_or_default(item.get("date"), as_of_date)
        value = item.get("value")
        raw_key = "|".join(str(part) for part in ("nasdaq_sharadar", ticker, effective_date, action_type, value, item.get("contraticker")))
        rows.append(
            {
                "corporate_action_id": hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24],
                "security_id": f"SHARADAR_TICKER:{ticker}",
                "source_symbol": ticker,
                "action_type": action_type,
                "announcement_date": None,
                "ex_date": effective_date if action_type == "dividend" else None,
                "record_date": None,
                "payable_date": None,
                "effective_date": effective_date,
                "cash_amount": value if action_type == "dividend" else None,
                "split_ratio": value if action_type == "split" else None,
                "adjustment_factor": None,
                "old_ticker": ticker if action_type in {"ticker_change", "tickerchange"} else None,
                "new_ticker": item.get("contraticker") if action_type in {"ticker_change", "tickerchange"} else None,
                "as_of_date": as_of_date,
                "source": "nasdaq_sharadar",
                "ingestion_timestamp": generated_at,
                "source_artifact_digest": _sha256(path),
                "security_id_resolution_status": "UNRESOLVED_SOURCE_SYMBOL_ONLY",
            }
        )
    return rows


def _dataset_payload(dataset_id: str, as_of_date: str, generated_at: str, source_artifacts: list[Path], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": f"{dataset_id}_normalized_v1",
        "dataset_id": dataset_id,
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "row_count": len(rows),
        "source_artifacts": [_source_artifact_payload(path) for path in source_artifacts],
        "rows": rows,
    }


def _source_artifact_payload(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": _sha256(path)}


def _result_from_payload(dataset_id: str, artifact: Path, payload: dict[str, Any], generated_at: str, as_of_date: str, run_metadata: dict[str, Any]) -> dict[str, Any]:
    validation = payload.get("validation") or {}
    status = "OK" if validation.get("status") == "PASS" else "WARN"
    return _result(
        dataset_id=dataset_id,
        status=status,
        artifact_path=artifact,
        row_count=int(payload.get("row_count") or 0),
        validation_errors=validation.get("errors") or [],
        source_artifacts=payload.get("source_artifacts") or [],
        generated_at=generated_at,
        as_of_date=as_of_date,
        latest_hydration_status=_latest_dataset_status(run_metadata, dataset_id),
    )


def _result(
    *,
    dataset_id: str,
    status: str,
    artifact_path: Path | None,
    row_count: int,
    validation_errors: list[str],
    source_artifacts: list[dict[str, Any]],
    generated_at: str,
    as_of_date: str,
    latest_hydration_status: str | None = None,
) -> dict[str, Any]:
    return {
        "dataset_id": dataset_id,
        "status": status,
        "artifact_path": str(artifact_path) if artifact_path else None,
        "row_count": row_count,
        "validation_status": "PASS" if not validation_errors else "WARN",
        "validation_errors": validation_errors,
        "source_artifacts": source_artifacts,
        "latest_hydration_status": latest_hydration_status,
        "as_of_date": as_of_date,
        "generated_at": generated_at,
    }


def _validation_payload(errors: list[str]) -> dict[str, Any]:
    return {"status": "PASS" if not errors else "WARN", "errors": errors}


def _validate_required(rows: list[dict[str, Any]], fields: list[str]) -> list[str]:
    errors = []
    for idx, row in enumerate(rows):
        missing = [field for field in fields if row.get(field) in (None, "")]
        if missing:
            errors.append(f"row {idx} missing required fields: {', '.join(missing)}")
    return errors


def _validate_date_lte(rows: list[dict[str, Any]], left: str, right: str) -> list[str]:
    errors = []
    for idx, row in enumerate(rows):
        left_value = row.get(left)
        right_value = row.get(right)
        if left_value and right_value and str(left_value) > str(right_value):
            errors.append(f"row {idx} violates {left} <= {right}: {left_value} > {right_value}")
    return errors


def _require_first(repo_root: Path, candidates: list[str]) -> Path:
    for rel_path in candidates:
        path = repo_root / rel_path
        if path.exists():
            return path
    raise FileNotFoundError(f"none of the candidate artifacts exist: {candidates}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _latest_hydration_metadata(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "data" / "hydration_logs" / "latest_hydration_swarm.json"
    if not path.exists():
        return {}
    return read_json(path)


def _latest_dataset_status(run_metadata: dict[str, Any], dataset_id: str) -> str | None:
    for row in run_metadata.get("datasets") or []:
        if row.get("dataset_id") == dataset_id:
            return row.get("final_status")
    return None


def _list_get(values: list[Any], idx: int) -> Any:
    return values[idx] if idx < len(values) else None


def _date_or_default(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else default
