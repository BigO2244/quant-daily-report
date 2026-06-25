from __future__ import annotations

from pathlib import Path
from typing import Any

from research_data.hydration import read_json


REPO_ROOT = Path(__file__).resolve().parents[1]

NORMALIZED_ARTIFACTS = {
    "ohlcv_prices": Path("data/normalized/prices/ohlcv_prices.json"),
    "security_master_pit": Path("data/normalized/security_master/security_master.json"),
    "corporate_actions": Path("data/normalized/corporate_actions/actions.json"),
    "dataset_freshness": Path("data/normalized/freshness/dataset_freshness.json"),
    "fundamentals_pit": Path("data/normalized/fundamentals/statements.json"),
    "macro_rates": Path("data/normalized/macro/macro_rates.json"),
    "yield_curve": Path("data/normalized/macro/yield_curve.json"),
    "credit_spreads": Path("data/normalized/macro/credit_spreads.json"),
    "vix_volatility_regime": Path("data/normalized/volatility/vix.json"),
    "insider_form4": Path("data/normalized/insiders/form4_filings.json"),
    "sec_8k_events": Path("data/normalized/sec_events/eight_k_items.json"),
    "sec_10q_10k_metadata": Path("data/normalized/sec_events/filings.json"),
    "etf_index_constituents": Path("data/normalized/constituents/constituents.json"),
    "institutional_13f": Path("data/normalized/institutional_holdings/form13f_filings.json"),
    "news_metadata": Path("data/normalized/news/news_metadata.json"),
    "fundamental_features": Path("data/features/fundamental_features/features.json"),
    "macro_regime_features": Path("data/features/macro_regime_features/features.json"),
}

FEATURE_DATASETS = ("fundamental_features", "macro_regime_features")
OBSERVABILITY_PATH = Path("data/manifests/research_data_observability.json")
DATA_TRUST_SUMMARY_PATH = Path("outputs/data_trust/data_trust_summary.json")


def load_dataset(
    dataset_id: str,
    *,
    repo_root: Path | None = None,
    required: bool = True,
    as_of_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    security_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    fields: set[str] | list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    rel_path = NORMALIZED_ARTIFACTS.get(dataset_id)
    if rel_path is None:
        if required:
            raise KeyError(f"No normalized artifact is registered for dataset {dataset_id!r}")
        return []
    path = root / rel_path
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Normalized artifact does not exist for {dataset_id}: {path}")
        return []
    payload = read_json(path)
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"Normalized artifact for {dataset_id} is missing rows list: {path}")
    return _filter_rows(
        rows,
        as_of_date=as_of_date,
        start_date=start_date,
        end_date=end_date,
        security_ids=security_ids,
        fields=fields,
    )


def load_dataset_diagnostics(dataset_id: str, *, repo_root: Path | None = None, required: bool = True) -> dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    path = root / OBSERVABILITY_PATH
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Research data observability manifest does not exist: {path}")
        return {
            "dataset_id": dataset_id,
            "diagnostics_status": "MISSING_OBSERVABILITY",
            "reason": f"Research data observability manifest does not exist: {path}",
        }
    payload = read_json(path)
    for row in payload.get("datasets") or []:
        if row.get("dataset_id") == dataset_id:
            return {
                "diagnostics_status": "OK",
                "schema_version": payload.get("schema_version"),
                "manifest_as_of_date": payload.get("as_of_date"),
                "manifest_generated_at": payload.get("generated_at"),
                **row,
            }
    if required:
        raise KeyError(f"No diagnostics row exists for dataset {dataset_id!r}")
    return {
        "dataset_id": dataset_id,
        "diagnostics_status": "MISSING_DATASET_DIAGNOSTICS",
        "reason": f"No diagnostics row exists for dataset {dataset_id!r}",
    }


def load_dataset_with_diagnostics(
    dataset_id: str,
    *,
    repo_root: Path | None = None,
    required: bool = True,
    as_of_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    security_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    fields: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    return {
        "dataset_id": dataset_id,
        "rows": load_dataset(
            dataset_id,
            repo_root=repo_root,
            required=required,
            as_of_date=as_of_date,
            start_date=start_date,
            end_date=end_date,
            security_ids=security_ids,
            fields=fields,
        ),
        "diagnostics": load_dataset_diagnostics(dataset_id, repo_root=repo_root, required=required),
    }


def load_research_data_observability(*, repo_root: Path | None = None, required: bool = True) -> dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    path = root / OBSERVABILITY_PATH
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Research data observability manifest does not exist: {path}")
        return {}
    return read_json(path)


def load_data_trust_summary(*, repo_root: Path | None = None, required: bool = True) -> dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    path = root / DATA_TRUST_SUMMARY_PATH
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Data trust summary does not exist: {path}")
        return {}
    return read_json(path)


def load_prices(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    return load_dataset("ohlcv_prices", repo_root=repo_root, required=required, **query)


def load_security_master(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    return load_dataset("security_master_pit", repo_root=repo_root, required=required, **query)


def load_corporate_actions(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    return load_dataset("corporate_actions", repo_root=repo_root, required=required, **query)


def load_dataset_freshness(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    return load_dataset("dataset_freshness", repo_root=repo_root, required=required, **query)


def load_fundamentals(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    return load_dataset("fundamentals_pit", repo_root=repo_root, required=required, **query)


def load_macro(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dataset_id in ("macro_rates", "yield_curve", "credit_spreads"):
        rows.extend(load_dataset(dataset_id, repo_root=repo_root, required=False, **query))
    if required and not rows:
        root = Path(repo_root) if repo_root is not None else REPO_ROOT
        raise FileNotFoundError(f"No normalized macro artifacts exist under {root / 'data' / 'normalized' / 'macro'}")
    return rows


def load_yield_curve(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    return load_dataset("yield_curve", repo_root=repo_root, required=required, **query)


def load_credit_spreads(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    return load_dataset("credit_spreads", repo_root=repo_root, required=required, **query)


def load_vix(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    return load_dataset("vix_volatility_regime", repo_root=repo_root, required=required, **query)


def load_insiders(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    return load_dataset("insider_form4", repo_root=repo_root, required=required, **query)


def load_sec_events(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dataset_id in ("sec_8k_events", "sec_10q_10k_metadata"):
        rows.extend(load_dataset(dataset_id, repo_root=repo_root, required=False, **query))
    if required and not rows:
        root = Path(repo_root) if repo_root is not None else REPO_ROOT
        raise FileNotFoundError(f"No normalized SEC event artifacts exist under {root / 'data' / 'normalized' / 'sec_events'}")
    return rows


def load_features(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dataset_id in FEATURE_DATASETS:
        rows.extend(load_dataset(dataset_id, repo_root=repo_root, required=False, **query))
    if required and not rows:
        root = Path(repo_root) if repo_root is not None else REPO_ROOT
        raise FileNotFoundError(f"No canonical feature artifacts exist under {root / 'data' / 'features'}")
    return rows


def load_fundamental_features(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    return load_dataset("fundamental_features", repo_root=repo_root, required=required, **query)


def load_macro_regime_features(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    return load_dataset("macro_regime_features", repo_root=repo_root, required=required, **query)


def load_constituents(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    return load_dataset("etf_index_constituents", repo_root=repo_root, required=required, **query)


def load_institutional_holdings(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    return load_dataset("institutional_13f", repo_root=repo_root, required=required, **query)


def load_news_metadata(*, repo_root: Path | None = None, required: bool = True, **query: Any) -> list[dict[str, Any]]:
    return load_dataset("news_metadata", repo_root=repo_root, required=required, **query)


def _filter_rows(
    rows: list[dict[str, Any]],
    *,
    as_of_date: str | None,
    start_date: str | None,
    end_date: str | None,
    security_ids: set[str] | list[str] | tuple[str, ...] | None,
    fields: set[str] | list[str] | tuple[str, ...] | None,
) -> list[dict[str, Any]]:
    security_filter = set(security_ids or [])
    field_filter = list(fields or [])
    filtered = []
    for row in rows:
        if as_of_date and str(row.get("as_of_date") or "")[:10] > as_of_date:
            continue
        row_date = _row_date(row)
        if start_date and row_date and row_date < start_date:
            continue
        if end_date and row_date and row_date > end_date:
            continue
        if security_filter and row.get("security_id") not in security_filter:
            continue
        filtered.append(_project_fields(row, field_filter) if field_filter else dict(row))
    return filtered


def _row_date(row: dict[str, Any]) -> str | None:
    for field in (
        "feature_date",
        "trade_date",
        "observation_date",
        "filing_date",
        "event_date",
        "publication_date",
        "transaction_date",
        "effective_date",
        "report_period_end",
        "as_of_date",
    ):
        value = row.get(field)
        if value:
            return str(value)[:10]
    timestamp = row.get("publication_timestamp") or row.get("acceptance_timestamp")
    return str(timestamp)[:10] if timestamp else None


def _project_fields(row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: row[field] for field in fields if field in row}
