from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable


MODEL_QUALITY_ROOT = Path("outputs/model_quality")


def normalize_date(value: str) -> str:
    return dt.date.fromisoformat(str(value)).isoformat()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def round_or_none(value: Any, digits: int = 10) -> float | None:
    numeric = safe_float(value)
    return round(numeric, digits) if numeric is not None else None


def symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def model_quality_dir(repo_root: Path | str, trade_date: str, output_root: Path | str | None = None) -> Path:
    repo = Path(repo_root)
    root = Path(output_root) if output_root is not None else repo / MODEL_QUALITY_ROOT
    return root / normalize_date(trade_date)


def dated_source(
    repo_root: Path | str,
    relative_root: Path | str,
    trade_date: str,
    required_file: str,
) -> tuple[Path | None, str | None, list[str]]:
    root = Path(repo_root) / Path(relative_root)
    target = normalize_date(trade_date)
    reasons: list[str] = []
    exact = root / target
    if (exact / required_file).exists():
        return exact, target, ["ok"]
    if not root.exists():
        return None, None, [
            f"{Path(relative_root).as_posix().replace('/', '_')}_missing",
            f"{required_file.removesuffix('.json')}_missing",
        ]
    candidates: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            child_date = normalize_date(child.name)
        except Exception:
            continue
        if child_date <= target and (child / required_file).exists():
            candidates.append(child)
    if not candidates:
        reasons.append(f"{required_file.removesuffix('.json')}_missing")
        return None, None, reasons
    selected = sorted(candidates, key=lambda item: item.name)[-1]
    if selected.name != target:
        reasons.append("SOURCE_DATE_DIFFERS_FROM_TARGET")
    return selected, selected.name, sorted(set(reasons)) or ["ok"]


def source_status(
    *,
    name: str,
    path: Path | None,
    source_date: str | None,
    target_date: str,
    reason_codes: Iterable[str],
) -> dict[str, Any]:
    reasons = sorted({str(code) for code in reason_codes if str(code) and str(code) != "ok"}) or ["ok"]
    status = "PRESENT" if path is not None else "MISSING"
    if path is not None and reasons != ["ok"]:
        status = "STALE" if "SOURCE_DATE_DIFFERS_FROM_TARGET" in reasons else "PARTIAL"
    return {
        "name": name,
        "status": status,
        "path": str(path) if path else None,
        "source_date": source_date,
        "target_date": normalize_date(target_date),
        "reason_codes": reasons,
    }


def collect_reason_codes(*blocks: Iterable[str]) -> list[str]:
    reasons: set[str] = set()
    for block in blocks:
        for code in block:
            text = str(code)
            if text and text != "ok":
                reasons.add(text)
    return sorted(reasons) or ["ok"]


def md_join(values: Iterable[Any]) -> str:
    items = [str(value) for value in values if value is not None and str(value) != ""]
    return ", ".join(items) if items else "none"
