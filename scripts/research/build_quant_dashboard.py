from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from core.trading_mode import normalize_trading_mode


@dataclass
class SourceRecord:
    path: str
    status: str


def _canonical_mode_label(value: object, default: str = "paper") -> str:
    return str(normalize_trading_mode(value, default=default) or default).upper()


class DashboardBuilder:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.missing_files: list[str] = []
        self.warnings: list[str] = []
        self.sources: list[SourceRecord] = []
        self.degraded_metrics: list[str] = []
        self.broker_source_mode: str = "missing"
        self.broker_source_used: str = "none"
        self.freshness_classification: str = "missing"

    def _abs(self, rel: str) -> Path:
        return self.repo_root / rel

    def _record_source(self, rel_path: str, exists: bool, used: bool = False) -> Path:
        path = self._abs(rel_path)
        if exists:
            self.sources.append(SourceRecord(rel_path, "used" if used else "present"))
        else:
            self.sources.append(SourceRecord(rel_path, "missing"))
            self.missing_files.append(rel_path)
        return path

    def _read_json(self, rel_path: str, required: bool = False, used: bool = True) -> dict[str, Any] | list[Any] | None:
        path = self._abs(rel_path)
        exists = path.exists()
        self._record_source(rel_path, exists=exists, used=used and exists)
        if not exists:
            if required:
                self.warnings.append(f"Missing required JSON artifact: {rel_path}")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.warnings.append(f"Failed to parse JSON {rel_path}: {exc}")
            return None

    def _read_csv(self, rel_path: str, required: bool = False, used: bool = True) -> pd.DataFrame | None:
        path = self._abs(rel_path)
        exists = path.exists()
        self._record_source(rel_path, exists=exists, used=used and exists)
        if not exists:
            if required:
                self.warnings.append(f"Missing required CSV artifact: {rel_path}")
            return None
        try:
            return pd.read_csv(path)
        except Exception as exc:
            self.warnings.append(f"Failed to parse CSV {rel_path}: {exc}")
            return None

    def _read_json_if_exists(self, rel_path: str, *, used: bool = True) -> dict[str, Any] | list[Any] | None:
        path = self._abs(rel_path)
        if not path.exists():
            return None
        self._record_source(rel_path, exists=True, used=used)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.warnings.append(f"Failed to parse JSON {rel_path}: {exc}")
            return None

    @staticmethod
    def _parse_iso_timestamp(raw: Any) -> datetime | None:
        text = str(raw or "").strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _as_iso_z(raw: datetime | str | None) -> str | None:
        if raw is None:
            return None
        if isinstance(raw, str):
            parsed = DashboardBuilder._parse_iso_timestamp(raw)
            if parsed is None:
                return str(raw)
            return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return raw.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _is_truthy(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _normalize_broker_snapshot(
        self,
        *,
        payload: dict[str, Any],
        source: str,
        as_of_hint: str | None = None,
        trust_level: str = "derived",
        source_detail: str | None = None,
    ) -> dict[str, Any] | None:
        account = payload.get("account") if isinstance(payload.get("account"), dict) else payload
        if not isinstance(account, dict):
            return None

        portfolio_value = self._coerce_num(account.get("portfolio_value") or account.get("equity"))
        equity = self._coerce_num(account.get("equity") or account.get("portfolio_value"))
        cash = self._coerce_num(account.get("cash"))
        buying_power = self._coerce_num(account.get("buying_power"))
        
        # Track whether buying_power was not provided vs unavailable
        buying_power_note = None
        if buying_power is None and "buying_power" in account:
            buying_power_note = "Not provided by broker source"
        elif buying_power is None:
            buying_power_note = "Field not present in broker payload"
        market_value = self._coerce_num(account.get("market_value"))
        if market_value is None and equity is not None and cash is not None:
            market_value = float(equity - cash)

        if all(v is None for v in [portfolio_value, equity, cash, buying_power, market_value]):
            return None

        as_of = (
            str(payload.get("as_of") or "").strip()
            or str(payload.get("timestamp_utc") or "").strip()
            or str(payload.get("timestamp") or "").strip()
            or as_of_hint
            or self._safe_iso_now()
        )

        return {
            "portfolio_value": portfolio_value,
            "cash": cash,
            "buying_power": buying_power,
            "buying_power_note": buying_power_note,
            "equity": equity,
            "market_value": market_value,
            "as_of": self._as_iso_z(as_of),
            "source": source,
            "source_detail": source_detail or source,
            "trust_level": trust_level,
            "status": "fresh",
            "suspicious": False,
            "confidence_note": "",
        }

    def _persist_broker_snapshot(self, snapshot: dict[str, Any], *, reason: str) -> None:
        out_path = self._abs("outputs/broker/broker_snapshot_latest.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            **snapshot,
            "persisted_at": self._safe_iso_now(),
            "persist_reason": reason,
        }
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.sources.append(SourceRecord("outputs/broker/broker_snapshot_latest.json", "used"))

    def _extract_snapshot_from_recon(self, payload: dict[str, Any], *, source_path: str) -> dict[str, Any] | None:
        broker_payload = payload.get("broker_snapshot")
        if not isinstance(broker_payload, dict):
            return None
        as_of_hint = str(payload.get("timestamp_utc") or "").strip() or None
        return self._normalize_broker_snapshot(
            payload=broker_payload,
            source=f"artifact:{source_path}",
            as_of_hint=as_of_hint,
            trust_level="reconciled",
            source_detail=f"reconciliation artifact {source_path}",
        )

    def _find_latest_recon_file(self, pattern: str) -> tuple[str | None, dict[str, Any] | None]:
        broker_dir = self._abs("outputs/broker")
        if not broker_dir.exists():
            return None, None
        files = sorted(broker_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files:
            rel = str(path.relative_to(self.repo_root))
            payload = self._read_json_if_exists(rel, used=True)
            if isinstance(payload, dict):
                return rel, payload
        return None, None

    def _load_trading_day_summary(self, run_id: str | None) -> dict[str, Any] | None:
        candidate_paths: list[str] = []
        if run_id:
            candidate_paths.append(f"outputs/runs/{run_id}/trading_day_summary.json")
        candidate_paths.append("outputs/trading_day_summary.json")

        for rel in candidate_paths:
            payload = self._read_json(rel, required=False, used=True)
            if isinstance(payload, dict):
                return payload
        return None

    @staticmethod
    def _build_execution_integrity(summary_obj: dict[str, Any] | None) -> dict[str, Any]:
        broker_context = (summary_obj or {}).get("broker_context") if isinstance(summary_obj, dict) else {}
        if not isinstance(broker_context, dict):
            broker_context = {}
        affected_symbols = [str(sym).strip().upper() for sym in list(broker_context.get("affected_symbols") or []) if str(sym).strip()]
        repair_suggestions = [str(item).strip() for item in list(broker_context.get("repair_suggestions") or []) if str(item).strip()]
        duplicate_guard_status = str(broker_context.get("duplicate_guard_status") or "").strip() or None
        recon_status = str(broker_context.get("post_execution_recon_status") or "").strip() or None
        duplicate_fill_suspicions_count = int(broker_context.get("duplicate_fill_suspicions_count") or 0)
        return {
            "duplicate_guard_status": duplicate_guard_status,
            "post_execution_recon_status": recon_status,
            "affected_symbols": affected_symbols,
            "repair_suggestions": repair_suggestions,
            "duplicate_fill_suspicions_count": duplicate_fill_suspicions_count,
            "visible": bool(
                duplicate_guard_status
                or recon_status
                or affected_symbols
                or repair_suggestions
                or duplicate_fill_suspicions_count
            ),
        }

    def _artifact_broker_snapshot(self, run_id: str | None, report_date: str | None) -> tuple[dict[str, Any] | None, str]:
        candidate_paths: list[str] = [
            "outputs/broker/broker_snapshot_latest.json",
        ]
        if run_id:
            candidate_paths.extend(
                [
                    f"outputs/runs/{run_id}/broker/posttrade_account_snapshot.json",
                    f"outputs/runs/{run_id}/broker/pretrade_account_snapshot.json",
                    f"outputs/runs/{run_id}/broker/broker_snapshot_latest.json",
                    f"outputs/runs/{run_id}/broker/broker_snapshot.json",
                ]
            )

        for rel in candidate_paths:
            payload = self._read_json_if_exists(rel, used=True)
            if isinstance(payload, dict):
                normalized = self._normalize_broker_snapshot(
                    payload=payload,
                    source=f"artifact:{rel}",
                    trust_level="authoritative",
                    source_detail=f"artifact snapshot {rel}",
                )
                if normalized:
                    return normalized, "authoritative_artifact"

        if report_date:
            for rel in [
                f"outputs/broker/recon_posttrade_{report_date}.json",
                f"outputs/broker/recon_pretrade_{report_date}.json",
            ]:
                payload = self._read_json_if_exists(rel, used=True)
                if isinstance(payload, dict):
                    normalized = self._extract_snapshot_from_recon(payload, source_path=rel)
                    if normalized:
                        return normalized, "reconciled_artifact"

        for pattern in ["recon_posttrade_*.json", "recon_pretrade_*.json"]:
            rel, payload = self._find_latest_recon_file(pattern)
            if rel and isinstance(payload, dict):
                normalized = self._extract_snapshot_from_recon(payload, source_path=rel)
                if normalized:
                    return normalized, "reconciled_artifact"

        return None, "missing"

    def _derived_broker_snapshot(
        self,
        *,
        execution_payload: dict[str, Any],
        nav_df: pd.DataFrame | None,
        canonical_positions: dict[str, Any],
        report_date: str | None,
    ) -> tuple[dict[str, Any] | None, str]:
        if isinstance(execution_payload, dict) and execution_payload:
            normalized = self._normalize_broker_snapshot(
                payload=execution_payload,
                source="derived:execution_payload",
                as_of_hint=report_date,
                trust_level="derived",
                source_detail="derived from execution payload",
            )
            if normalized:
                return normalized, "derived_execution_payload"

        if nav_df is not None and not nav_df.empty and "equity" in nav_df.columns:
            latest_row = nav_df.dropna(subset=["equity"]).tail(1)
            if not latest_row.empty:
                row = latest_row.iloc[0]
                payload = {
                    "equity": row.get("equity"),
                    "portfolio_value": row.get("equity"),
                    "cash": row.get("cash") if "cash" in latest_row.columns else None,
                    "as_of": row.get("date") if "date" in latest_row.columns else report_date,
                }
                normalized = self._normalize_broker_snapshot(
                    payload=payload,
                    source="derived:nav_timeseries",
                    as_of_hint=report_date,
                    trust_level="derived",
                    source_detail="derived from governed nav_timeseries",
                )
                if normalized:
                    return normalized, "derived_nav_timeseries"

        if isinstance(canonical_positions, dict) and canonical_positions:
            normalized = self._normalize_broker_snapshot(
                payload=canonical_positions,
                source="derived:canonical_positions",
                as_of_hint=report_date,
                trust_level="derived",
                source_detail="derived from canonical positions snapshot",
            )
            if normalized:
                return normalized, "derived_canonical_positions"

        return None, "missing"

    def _maybe_live_broker_snapshot(self, report_date: str | None) -> tuple[dict[str, Any] | None, str]:
        if not self._is_truthy(os.getenv("DASHBOARD_FETCH_BROKER_SNAPSHOT"), default=False):
            return None, "disabled"
        key_id = str(os.getenv("ALPACA_API_KEY_ID") or os.getenv("ALPACA_KEY_ID") or "").strip()
        secret = str(os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY") or "").strip()
        if not key_id or not secret:
            self.warnings.append("Live broker fetch requested but Alpaca credentials are not set.")
            return None, "missing_credentials"
        try:
            from brokers.alpaca_broker import AlpacaBroker

            broker = AlpacaBroker.from_env()
            account = broker.get_account() or {}
            snapshot = self._normalize_broker_snapshot(
                payload=account,
                source="live:alpaca_account",
                as_of_hint=self._safe_iso_now(),
                trust_level="authoritative",
                source_detail="live Alpaca account fetch",
            )
            if snapshot:
                return snapshot, "live_fetch"
        except Exception as exc:
            self.warnings.append(f"Live broker fetch failed: {exc}")
            return None, "live_fetch_failed"
        return None, "live_fetch_failed"

    def _classify_freshness(
        self,
        *,
        report_date: str | None,
        run_last_updated: str | None,
        broker_as_of: str | None,
    ) -> tuple[dict[str, Any], str]:
        run_dt = self._parse_iso_timestamp(run_last_updated)
        broker_dt = self._parse_iso_timestamp(broker_as_of)
        run_date_obj = None
        if report_date:
            try:
                run_date_obj = datetime.fromisoformat(str(report_date)).date()
            except Exception:
                run_date_obj = None

        alignment = "missing"
        detail = "Broker snapshot unavailable."
        stale_threshold_hours = 36

        if broker_dt is None:
            alignment = "missing"
            detail = "Broker snapshot timestamp unavailable."
        else:
            age_hours = (datetime.now(timezone.utc) - broker_dt.astimezone(timezone.utc)).total_seconds() / 3600.0
            if age_hours > stale_threshold_hours:
                alignment = "stale"
                detail = f"Broker snapshot older than {stale_threshold_hours} hours."
            elif run_date_obj is None:
                alignment = "mismatch"
                detail = "Run report date unavailable; alignment cannot be confirmed."
            else:
                broker_date = broker_dt.date()
                if broker_date == run_date_obj:
                    alignment = "aligned"
                    detail = "Broker snapshot date aligns with run report date."
                else:
                    alignment = "mismatch"
                    if broker_date > run_date_obj:
                        detail = "Broker snapshot is newer than governed run date."
                    else:
                        detail = "Governed run date is newer than broker snapshot."

        freshness = {
            "run_report_date": report_date,
            "run_last_updated": self._as_iso_z(run_last_updated),
            "broker_as_of": self._as_iso_z(broker_as_of),
            "broker_vs_run_alignment": alignment,
            "alignment_detail": detail,
            "stale_threshold_hours": stale_threshold_hours,
        }
        return freshness, alignment

    def _apply_broker_sanity_guardrails(
        self,
        *,
        broker_snapshot: dict[str, Any],
        governed_snapshot: dict[str, Any],
        data_freshness: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        trust = str(broker_snapshot.get("trust_level") or "missing")
        broker_value = self._coerce_num(
            broker_snapshot.get("equity")
            if broker_snapshot.get("equity") is not None
            else broker_snapshot.get("portfolio_value")
        )
        governed_value = self._coerce_num(governed_snapshot.get("portfolio_value"))

        suspicious = False
        suspicious_reasons: list[str] = []

        # Stricter plausibility checks for derived/stale snapshots
        if broker_value is not None and governed_value and governed_value > 0:
            ratio = broker_value / governed_value
            # Flag as suspicious if off by >2x or <0.5x (stricter for derived)
            if trust == "derived" and (ratio > 2.0 or ratio < 0.5):
                suspicious = True
                suspicious_reasons.append(
                    f"Derived broker equity ${broker_value:,.0f} is {ratio:.2f}x vs governed ${governed_value:,.0f}"
                )
            # Flag as very suspicious if off by >5x or <0.2x (even for authoritative)
            elif (ratio > 5.0 or ratio < 0.2):
                suspicious = True
                suspicious_reasons.append(
                    f"Broker equity ${broker_value:,.0f} is {ratio:.2f}x vs governed ${governed_value:,.0f} (implausible magnitude)"
                )

        if suspicious:
            broker_snapshot["status"] = "suspicious"
            broker_snapshot["suspicious"] = True
            broker_snapshot["confidence_note"] = "; ".join(suspicious_reasons)
            broker_snapshot["display_equity"] = None
            data_freshness["suspicious_broker_value"] = True
            data_freshness["suspicious_reason"] = broker_snapshot["confidence_note"]
            self.warnings.append(f"Suspicious broker snapshot: {broker_snapshot['confidence_note']}")
        else:
            broker_snapshot.setdefault("suspicious", False)
            broker_snapshot.setdefault("confidence_note", "")
            broker_snapshot["display_equity"] = broker_value
            data_freshness.setdefault("suspicious_broker_value", False)

        return broker_snapshot, suspicious

    def _find_latest_execution_payload(self, report_date: str | None, run_id: str | None = None) -> tuple[str | None, dict[str, Any] | None]:
        # Canonical-first resolution order:
        # 1) outputs/latest_run.json -> run_root
        # 2) run_root/operator_summary.json
        # 3) run_root/execution_results.json
        # 4) run_root/execution_payload.json
        # 5) legacy outputs/execution_email/<date>.json fallback
        run_root_candidates: list[Path] = []
        latest_run = self._read_json("outputs/latest_run.json", required=False, used=True)
        if isinstance(latest_run, dict):
            run_root_raw = str(latest_run.get("run_root") or "").strip()
            if run_root_raw:
                run_root_path = Path(run_root_raw)
                if not run_root_path.is_absolute():
                    run_root_path = self.repo_root / run_root_path
                run_root_candidates.append(run_root_path)
        if run_id:
            run_root_candidates.append(self._abs(f"outputs/runs/{run_id}"))

        for run_root_path in run_root_candidates:
            if not run_root_path.exists() or not run_root_path.is_dir():
                continue
            try:
                run_root_rel = str(run_root_path.relative_to(self.repo_root))
            except Exception:
                run_root_rel = run_root_path.as_posix()

            op_rel = f"{run_root_rel}/operator_summary.json"
            rs_rel = f"{run_root_rel}/execution_results.json"
            ep_rel = f"{run_root_rel}/execution_payload.json"

            operator_summary = self._read_json(op_rel, required=False, used=True)
            execution_results = self._read_json(rs_rel, required=False, used=True)
            execution_payload = self._read_json(ep_rel, required=False, used=True)

            merged: dict[str, Any] = {}
            source = None

            if isinstance(operator_summary, dict):
                source = op_rel
                merged.update(operator_summary)
                pretrade_status = str(operator_summary.get("pretrade_status") or "").upper()
                if pretrade_status:
                    merged["execution_status"] = pretrade_status
                if operator_summary.get("pretrade_halt_reason") and not merged.get("halt_reason"):
                    merged["halt_reason"] = operator_summary.get("pretrade_halt_reason")

            if isinstance(execution_results, dict):
                source = source or rs_rel
                merged.update(execution_results)
                status = str(execution_results.get("status") or "").upper()
                if status:
                    merged["execution_status"] = status

            if isinstance(execution_payload, dict):
                source = source or ep_rel
                for key, value in execution_payload.items():
                    merged.setdefault(key, value)
                payload_status = str(execution_payload.get("status") or execution_payload.get("execution_status") or "").upper()
                if payload_status and not merged.get("execution_status"):
                    merged["execution_status"] = payload_status

            if merged:
                return source, merged

        # Legacy fallback for backward compatibility when canonical artifacts are absent.
        exec_dir = self._abs("outputs/execution_email")
        if not exec_dir.exists():
            self._record_source("outputs/execution_email", exists=False)
            self.warnings.append("Execution payload directory not found.")
            return None, None

        if report_date:
            candidate = f"outputs/execution_email/{report_date}.json"
            payload = self._read_json(candidate, required=False, used=True)
            if isinstance(payload, dict):
                return candidate, payload

        files = sorted(exec_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for file in files:
            name = file.name
            if ".empty." in name:
                continue
            rel = str(file.relative_to(self.repo_root))
            payload = self._read_json(rel, required=False, used=True)
            if isinstance(payload, dict):
                return rel, payload

        self.warnings.append("No usable execution payload JSON found.")
        return None, None

    def _find_latest_health_integrity(self, run_id: str | None, report_date: str | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None, str | None]:
        if not run_id or not report_date:
            return None, None, None, None

        run_root = self._abs(f"outputs/runs/{run_id}/snapshots")
        health_rel = f"outputs/runs/{run_id}/snapshots/health_{report_date}.json"
        integrity_rel = f"outputs/runs/{run_id}/snapshots/integrity_{report_date}.json"

        health = self._read_json(health_rel, required=False, used=True)
        integrity = self._read_json(integrity_rel, required=False, used=True)

        if isinstance(health, dict) or isinstance(integrity, dict):
            return (
                health if isinstance(health, dict) else None,
                integrity if isinstance(integrity, dict) else None,
                health_rel if isinstance(health, dict) else None,
                integrity_rel if isinstance(integrity, dict) else None,
            )

        if not run_root.exists():
            self.warnings.append(f"Run snapshot folder missing for run_id={run_id}")
            return None, None, None, None

        health_files = sorted(run_root.glob("health_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        integrity_files = sorted(run_root.glob("integrity_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

        health_obj = None
        integrity_obj = None
        health_used = None
        integrity_used = None

        if health_files:
            rel = str(health_files[0].relative_to(self.repo_root))
            parsed = self._read_json(rel, required=False, used=True)
            if isinstance(parsed, dict):
                health_obj = parsed
                health_used = rel

        if integrity_files:
            rel = str(integrity_files[0].relative_to(self.repo_root))
            parsed = self._read_json(rel, required=False, used=True)
            if isinstance(parsed, dict):
                integrity_obj = parsed
                integrity_used = rel

        return health_obj, integrity_obj, health_used, integrity_used

    def _read_first_existing_json(self, rel_paths: list[str], *, required: bool = False) -> tuple[str | None, dict[str, Any] | None]:
        for rel in rel_paths:
            parsed = self._read_json(rel, required=False, used=True)
            if isinstance(parsed, dict):
                return rel, parsed
        if required and rel_paths:
            self.warnings.append(f"Missing required JSON artifact: {rel_paths[0]}")
        return None, None

    def _find_preflight_failure(self, run_id: str | None) -> tuple[dict[str, Any] | None, str | None]:
        if not run_id:
            return None, None
        rel = f"outputs/runs/{run_id}/logs/preflight_failure.json"
        payload = self._read_json(rel, required=False, used=True)
        if isinstance(payload, dict):
            return payload, rel
        return None, None

    def _infer_early_halt_without_artifacts(self, run_id: str | None) -> tuple[bool, str | None]:
        if not run_id:
            return False, None
        run_root = self._abs(f"outputs/runs/{run_id}")
        if not run_root.exists() or not run_root.is_dir():
            return False, None
        artifact_files: list[str] = []
        for file_path in sorted(p for p in run_root.rglob("*") if p.is_file()):
            rel = file_path.relative_to(run_root).as_posix()
            if rel in {"meta.json", "manifest.json", "checksums.sha256"}:
                continue
            artifact_files.append(rel)
        if artifact_files:
            return False, None
        return True, "Run folder has no stage artifacts beyond meta/manifest/checksums."

    @staticmethod
    def _coerce_num(v: Any) -> float | None:
        try:
            if v is None or (isinstance(v, str) and not v.strip()):
                return None
            n = float(v)
            return n if pd.notna(n) else None
        except Exception:
            return None

    @staticmethod
    def _safe_iso_now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _to_series(df: pd.DataFrame | None, date_col: str, value_col: str) -> list[dict[str, Any]]:
        if df is None or date_col not in df.columns or value_col not in df.columns:
            return []
        out: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            date = str(row.get(date_col, "")).strip()
            value = DashboardBuilder._coerce_num(row.get(value_col))
            if date and value is not None:
                out.append({"date": date, "value": value})
        return out

    @staticmethod
    def _calc_drawdown(nav_series: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not nav_series:
            return []
        peak = None
        out: list[dict[str, Any]] = []
        for p in nav_series:
            value = p.get("value")
            if value is None:
                continue
            if peak is None or value > peak:
                peak = value
            dd = 0.0 if not peak else (value / peak) - 1.0
            out.append({"date": p.get("date"), "value": dd})
        return out

    @staticmethod
    def _cumulative_return(returns: pd.Series) -> float | None:
        if returns.empty:
            return None
        returns = returns.dropna()
        if returns.empty:
            return None
        prod_val = DashboardBuilder._coerce_num((1.0 + returns).prod())
        if prod_val is None:
            return None
        return float(prod_val - 1.0)

    @staticmethod
    def _returns_from_series(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(points) < 2:
            return []
        out: list[dict[str, Any]] = []
        prev = None
        for p in points:
            value = DashboardBuilder._coerce_num(p.get("value"))
            date = str(p.get("date") or "").strip()
            if value is None or not date:
                continue
            if prev is not None and prev != 0:
                out.append({"date": date, "value": (value / prev) - 1.0})
            prev = value
        return out

    def _mark_degraded(self, metric_name: str, reason: str) -> None:
        self.degraded_metrics.append(f"{metric_name}: {reason}")

    def _filter_executive_warnings(self) -> list[str]:
        """Filter warnings to only executive-level concerns.
        
        Suppresses low-level artifact/parsing warnings and focuses on:
        - Preflight/execution failures
        - Suspicious broker values
        - Missing critical position/ledger artifacts
        """
        executive_warnings = []
        for w in self.warnings:
            # Include critical warnings
            if any(keyword in w.lower() for keyword in [
                "preflight", "suspicious", "broker snapshot",
                "canonical positions", "ledger"
            ]):
                executive_warnings.append(w)
            # Suppress low-level artifact warnings
            elif any(keyword in w.lower() for keyword in [
                "failed to parse", "derived strategy daily returns",
                "estimated daily p/l", "payload directory not found",
                "no usable execution payload"
            ]):
                continue
            # Include other warnings selectively
            elif "missing required" in w.lower():
                # Only if it's about core artifacts
                if any(core in w.lower() for core in ["canonical_performance", "nav_timeseries", "latest.json"]):
                    executive_warnings.append(w)
        return executive_warnings

    def _discover_runs(self) -> list[dict[str, Any]]:
        """Discover all runs in outputs/runs/ and classify them by completeness.
        
        Classification includes:
        - is_complete: Has health/integrity or quant_report
        - is_failed: Has preflight_failure or is sparse and incomplete
        - is_sparse: Only has meta.json/manifest
        - has_real_activity: Has trades/execution records
        - mode: canonical paper/live with legacy aliases normalized
        - is_viable_executive: Meets criteria for executive dashboard display
        """
        runs_root = self._abs("outputs/runs")
        if not runs_root.exists() or not runs_root.is_dir():
            return []
        
        discovered_runs: list[dict[str, Any]] = []
        for run_dir in sorted(runs_root.iterdir(), reverse=True):
            if not run_dir.is_dir() or run_dir.name.startswith("."):
                continue
            
            run_id = run_dir.name
            meta_path = run_dir / "meta.json"
            meta_obj = {}
            if meta_path.exists():
                try:
                    meta_obj = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            
            # Validate report_date - skip test/placeholder runs
            report_date = meta_obj.get("report_date")
            if report_date and report_date in {"2000-01-01", "1970-01-01"}:
                # Skip obvious test/placeholder dates
                continue
            if report_date:
                try:
                    # Validate date format and range
                    from datetime import datetime
                    parsed_date = datetime.strptime(report_date, "%Y-%m-%d")
                    if parsed_date.year < 2020 or parsed_date.year > 2030:
                        # Skip dates outside reasonable production range
                        continue
                except (ValueError, TypeError):
                    # Skip runs with malformed dates
                    continue
            
            # Check for completion indicators
            has_health = (run_dir / "snapshots" / f"health_{meta_obj.get('report_date', '')}.json").exists() if meta_obj.get('report_date') else False
            has_integrity = (run_dir / "snapshots" / f"integrity_{meta_obj.get('report_date', '')}.json").exists() if meta_obj.get('report_date') else False
            has_quant_report = any((run_dir / "reports").glob("quant_report_*.html")) if (run_dir / "reports").exists() else False
            has_preflight_failure = (run_dir / "logs" / "preflight_failure.json").exists()
            
            # Check for real activity artifacts
            has_trades = (run_dir / "ledger" / "trades.csv").exists()
            has_execution_email = any((run_dir / "snapshots").glob("execution_email_*.json")) if (run_dir / "snapshots").exists() else False
            
            # Sparse check
            artifact_count = 0
            for file_path in run_dir.rglob("*"):
                if file_path.is_file():
                    rel = file_path.relative_to(run_dir).as_posix()
                    if rel not in {"meta.json", "manifest.json", "checksums.sha256"}:
                        artifact_count += 1
            
            is_sparse = artifact_count == 0
            is_complete = (has_health and has_integrity) or has_quant_report
            is_failed = has_preflight_failure or (is_sparse and not is_complete)
            has_real_activity = has_trades or has_execution_email
            
            # For executive display, requires:
            # 1. Complete run (health/integrity or report) - proves actual execution
            # 2. Not failed/halted
            # 3. Not sparse/meta-only (meta-only runs never executed)
            # Activity evidence is preferred but not strictly required if otherwise complete
            is_viable_executive = (
                is_complete and 
                not is_failed and 
                not is_sparse
            )
            
            discovered_runs.append({
                "run_id": run_id,
                "report_date": meta_obj.get("report_date"),
                "created_at": meta_obj.get("created_at"),
                "mode": normalize_trading_mode(meta_obj.get("mode"), default="paper"),
                "raw_mode": str(meta_obj.get("mode") or "").strip().lower() or "paper",
                "is_complete": is_complete,
                "is_failed": is_failed,
                "is_sparse": is_sparse,
                "has_real_activity": has_real_activity,
                "has_preflight_failure": has_preflight_failure,
                "artifact_count": artifact_count,
                "is_viable_executive": is_viable_executive,
            })
        
        return discovered_runs

    def _select_executive_run(self, latest_attempted_run_id: str | None) -> tuple[str | None, str]:
        """Select the best run for executive metrics display.
        
        Strict selection criteria:
        1. Must be complete and not failed
        2. Must have real activity evidence (trades, execution records)
        3. Must not be sparse/meta-only
        4. Prefer live over paper
        5. Prefer latest viable run matching above criteria
        
        Returns tuple of (selected_run_id, selection_reason).
        """
        discovered = self._discover_runs()
        if not discovered:
            return latest_attempted_run_id, "no_runs_discovered"
        
        # Find viable executive runs (complete, not sparse, has activity)
        viable_runs = [r for r in discovered if r["is_viable_executive"] and not r["is_failed"]]
        
        if viable_runs:
            # Among viable runs, prefer by canonical mode: live > paper.
            live_runs = [r for r in viable_runs if str(r.get("mode") or "").lower() == "live"]
            paper_runs = [r for r in viable_runs if str(r.get("mode") or "").lower() == "paper"]
            
            # Use highest-priority available mode
            if live_runs:
                selected = live_runs[0]
                return selected["run_id"], "latest_viable_live_run"
            if paper_runs:
                selected = paper_runs[0]
                return selected["run_id"], "latest_viable_paper_run"
        
        # Fallback: No viable executive runs found. Use latest attempted if available.
        if latest_attempted_run_id:
            # Even though not ideal, provide context that this is a fallback
            return latest_attempted_run_id, "latest_attempted_fallback_no_viable_executive_run"
        
        # Last resort: Most recent discovered run
        if discovered:
            return discovered[0]["run_id"], "most_recent_run_fallback_no_viable"
        
        return None, "no_runs_discovered"


    def build(self) -> dict[str, Any]:
        latest = self._read_json("outputs/latest.json", required=False, used=True)
        latest_obj = latest if isinstance(latest, dict) else {}

        # Latest attempted run from outputs/latest.json
        latest_attempted_run_id = str(latest_obj.get("run_id") or "").strip() or None
        latest_attempted_report_date = str(latest_obj.get("report_date") or "").strip() or None
        latest_attempted_mode = _canonical_mode_label(latest_obj.get("mode") or "paper")
        latest_attempted_raw_mode = str(latest_obj.get("mode") or "").strip().lower() or "paper"
        latest_attempted_created_at = str(
            latest_obj.get("created_at")
            or latest_obj.get("last_updated")
            or latest_obj.get("updated_at")
            or ""
        ).strip() or None

        # Discover and select executive run
        selected_run_id, selection_reason = self._select_executive_run(latest_attempted_run_id)
        discovered_runs = self._discover_runs()

        # ── Latest-attempted run observability ───────────────────────────────
        # Read latest_run.json for authoritative terminal_status (written by
        # _finalize_run_context_once, which now uses _RUN_TERMINAL_STATUS).
        latest_run_ptr = self._read_json("outputs/latest_run.json", required=False, used=True)
        latest_run_ptr_obj = latest_run_ptr if isinstance(latest_run_ptr, dict) else {}
        latest_attempted_terminal_status: str | None = str(
            latest_run_ptr_obj.get("status") or ""
        ).strip() or None

        # Classify whether the latest attempted run is considered complete.
        latest_attempted_is_complete: bool = latest_attempted_terminal_status in {
            "success", "no_action"
        }

        # List authoritative per-run artifacts that are missing from the latest attempted run.
        latest_attempted_missing_artifacts: list[str] = []
        if latest_attempted_run_id:
            _lat_root = self._abs(f"outputs/runs/{latest_attempted_run_id}")
            for _artifact_name in (
                "operator_summary.json",
                "execution_payload.json",
                "execution_results.json",
            ):
                if not (_lat_root / _artifact_name).exists():
                    latest_attempted_missing_artifacts.append(_artifact_name)

        # Identify the most recent genuinely complete run for fallback display.
        latest_successful_run_id: str | None = None
        latest_successful_trade_date: str | None = None
        for _r in discovered_runs:
            if _r.get("is_complete") and not _r.get("is_failed") and not _r.get("is_sparse"):
                latest_successful_run_id = _r["run_id"]
                latest_successful_trade_date = _r.get("report_date")
                break

        fallback_in_use: bool = (
            selected_run_id != latest_attempted_run_id
            and latest_attempted_run_id is not None
        )
        fallback_reason: str | None = selection_reason if fallback_in_use else None

        # Use selected run for executive metrics
        report_date = latest_attempted_report_date if selected_run_id == latest_attempted_run_id else None
        run_id = selected_run_id
        mode = latest_attempted_mode
        raw_mode = latest_attempted_raw_mode
        run_last_updated = latest_attempted_created_at if selected_run_id == latest_attempted_run_id else None

        # If selected run differs from latest attempted, read its metadata
        if selected_run_id and selected_run_id != latest_attempted_run_id:
            selected_meta = self._read_json(f"outputs/runs/{selected_run_id}/meta.json", required=False, used=False)
            if isinstance(selected_meta,dict):
                report_date = str(selected_meta.get("report_date") or "").strip() or None
                mode = _canonical_mode_label(selected_meta.get("mode") or "paper")
                raw_mode = str(selected_meta.get("mode") or "").strip().lower() or "paper"
                run_last_updated = str(
                    selected_meta.get("created_at")
                    or selected_meta.get("last_updated")
                    or selected_meta.get("updated_at")
                    or ""
                ).strip() or None

        canonical_df = self._read_csv("outputs/alpha_assessment/canonical_performance.csv", required=False, used=True)
        nav_df = self._read_csv("outputs/perf/nav_timeseries.csv", required=False, used=True)
        benchmark_df = self._read_csv("outputs/perf/benchmark_close_history.csv", required=False, used=True)
        vix_df = self._read_csv("outputs/perf/vix_close_history.csv", required=False, used=True)
        trades_df = self._read_csv("outputs/ledger/trades.csv", required=False, used=True)

        _, execution_payload = self._find_latest_execution_payload(report_date, run_id=run_id)
        exec_payload_obj: dict[str, Any] = execution_payload if isinstance(execution_payload, dict) else {}
        health_obj, integrity_obj, _, _ = self._find_latest_health_integrity(run_id, report_date)
        trading_day_summary_obj = self._load_trading_day_summary(run_id)
        execution_integrity = self._build_execution_integrity(trading_day_summary_obj)

        canonical_positions_path, positions_obj = self._read_first_existing_json(
            [
                "outputs/paper_state/canonical_positions.json",
                "canonical-model-snapshot/canonical_positions.json",
            ],
            required=False,
        )
        if canonical_positions_path is None:
            canonical_positions_path = "outputs/paper_state/canonical_positions.json"
        if positions_obj is None:
            positions_obj = {}

        preflight_failure_obj, _preflight_failure_path = self._find_preflight_failure(run_id)
        preflight_halted = isinstance(preflight_failure_obj, dict)
        inferred_early_halt, inferred_note = self._infer_early_halt_without_artifacts(run_id)

        # Normalize canonical numeric columns if present.
        if canonical_df is not None and not canonical_df.empty:
            for col in [
                "strategy_nav",
                "strategy_return",
                "spy_close",
                "spy_return",
                "excess_return",
                "vix_close",
                "cash_weight",
                "gross_exposure",
                "turnover",
                "holdings_count",
            ]:
                if col in canonical_df.columns:
                    canonical_df[col] = pd.to_numeric(canonical_df[col], errors="coerce")
            if "date" in canonical_df.columns:
                canonical_df["date"] = pd.to_datetime(canonical_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")

        if nav_df is not None and not nav_df.empty:
            for col in ["equity", "cash", "gross_exposure", "net_exposure", "return_1d", "turnover"]:
                if col in nav_df.columns:
                    nav_df[col] = pd.to_numeric(nav_df[col], errors="coerce")
            if "date" in nav_df.columns:
                nav_df["date"] = pd.to_datetime(nav_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")

        if benchmark_df is not None and not benchmark_df.empty:
            for col in ["spy_close", "spy_return"]:
                if col in benchmark_df.columns:
                    benchmark_df[col] = pd.to_numeric(benchmark_df[col], errors="coerce")
            if "date" in benchmark_df.columns:
                benchmark_df["date"] = pd.to_datetime(benchmark_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")

        if vix_df is not None and not vix_df.empty and "vix_close" in vix_df.columns:
            vix_df["vix_close"] = pd.to_numeric(vix_df["vix_close"], errors="coerce")
            if "date" in vix_df.columns:
                vix_df["date"] = pd.to_datetime(vix_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")

        nav_series = []
        benchmark_series = []
        daily_returns_series = []
        excess_returns_series = []

        if canonical_df is not None and not canonical_df.empty:
            nav_series = self._to_series(canonical_df, "date", "strategy_nav")
            benchmark_series = self._to_series(canonical_df, "date", "spy_close")
            daily_returns_series = self._to_series(canonical_df, "date", "strategy_return")
            excess_returns_series = self._to_series(canonical_df, "date", "excess_return")

        if not nav_series and nav_df is not None and not nav_df.empty:
            nav_series = self._to_series(nav_df, "date", "equity")

        if not benchmark_series and benchmark_df is not None and not benchmark_df.empty:
            benchmark_series = self._to_series(benchmark_df, "date", "spy_close")

        if not daily_returns_series and nav_df is not None and not nav_df.empty and "return_1d" in nav_df.columns:
            daily_returns_series = self._to_series(nav_df, "date", "return_1d")

        drawdown_series = self._calc_drawdown(nav_series)

        if not daily_returns_series:
            daily_returns_series = self._returns_from_series(nav_series)
            if daily_returns_series:
                self.warnings.append("Derived strategy daily returns from NAV series due to missing canonical strategy_return values.")

        benchmark_returns_series = self._to_series(canonical_df, "date", "spy_return") if canonical_df is not None else []
        if not benchmark_returns_series and benchmark_df is not None and not benchmark_df.empty:
            benchmark_returns_series = self._to_series(benchmark_df, "date", "spy_return")
        if not benchmark_returns_series:
            benchmark_returns_series = self._returns_from_series(benchmark_series)

        # Latest row extraction for KPI-level metrics.
        latest_nav = nav_series[-1]["value"] if nav_series else None
        prev_nav = nav_series[-2]["value"] if len(nav_series) > 1 else None
        daily_pl = (latest_nav - prev_nav) if (latest_nav is not None and prev_nav is not None) else None

        latest_daily_return = daily_returns_series[-1]["value"] if daily_returns_series else None
        if latest_daily_return is None and nav_series and len(nav_series) >= 2:
            base = nav_series[-2]["value"]
            if base:
                latest_daily_return = (nav_series[-1]["value"] / base) - 1.0

        if daily_pl is None and latest_nav is not None and latest_daily_return is not None and (1.0 + latest_daily_return) != 0:
            prev_est = latest_nav / (1.0 + latest_daily_return)
            daily_pl = latest_nav - prev_est
            self.warnings.append("Estimated daily P/L from latest NAV and daily return due to missing prior NAV point.")

        governed_snapshot = {
            "portfolio_value": latest_nav,
            "equity": latest_nav,
            "cash": None,
            "market_value": None,
            "as_of": nav_series[-1]["date"] if nav_series else report_date,
            "source": "governed:canonical_performance" if canonical_df is not None and not canonical_df.empty else "governed:nav_timeseries",
            "status": "fresh" if latest_nav is not None else "missing",
        }

        if nav_df is not None and not nav_df.empty:
            nav_tail = nav_df.tail(1)
            if not nav_tail.empty:
                nav_row = nav_tail.iloc[0]
                governed_snapshot["cash"] = self._coerce_num(nav_row.get("cash"))
                if governed_snapshot.get("as_of") is None and "date" in nav_tail.columns:
                    governed_snapshot["as_of"] = str(nav_row.get("date") or "").strip() or report_date

        if governed_snapshot["equity"] is not None and governed_snapshot["cash"] is not None:
            governed_snapshot["market_value"] = float(governed_snapshot["equity"] - governed_snapshot["cash"])

        broker_snapshot, broker_mode = self._artifact_broker_snapshot(run_id=run_id, report_date=report_date)
        if broker_snapshot is None:
            broker_snapshot, broker_mode = self._maybe_live_broker_snapshot(report_date=report_date)
            if broker_snapshot is not None:
                try:
                    self._persist_broker_snapshot(broker_snapshot, reason="self_heal_live_fetch")
                except Exception as exc:
                    self.warnings.append(f"Failed to persist live broker snapshot artifact: {exc}")
        if broker_snapshot is None:
            broker_snapshot, broker_mode = self._derived_broker_snapshot(
                execution_payload=exec_payload_obj,
                nav_df=nav_df,
                canonical_positions=positions_obj,
                report_date=report_date,
            )
            if broker_snapshot is not None:
                try:
                    self._persist_broker_snapshot(broker_snapshot, reason=f"self_heal_{broker_mode}")
                except Exception as exc:
                    self.warnings.append(f"Failed to persist derived broker snapshot artifact: {exc}")

        if broker_snapshot is None:
            broker_snapshot = {
                "portfolio_value": None,
                "cash": None,
                "buying_power": None,
                "equity": None,
                "market_value": None,
                "as_of": None,
                "source": "missing",
                "source_detail": "broker snapshot unavailable",
                "trust_level": "missing",
                "status": "missing",
                "suspicious": False,
                "confidence_note": "",
                "display_equity": None,
            }

        data_freshness, alignment = self._classify_freshness(
            report_date=report_date,
            run_last_updated=run_last_updated,
            broker_as_of=broker_snapshot.get("as_of"),
        )
        data_freshness["broker_trust_level"] = broker_snapshot.get("trust_level", "missing")
        data_freshness["broker_source_detail"] = broker_snapshot.get("source_detail", broker_snapshot.get("source", "missing"))
        if alignment == "missing":
            broker_snapshot["status"] = "missing"
        elif alignment == "stale":
            broker_snapshot["status"] = "stale"
        else:
            broker_snapshot["status"] = "fresh"

        broker_snapshot, suspicious_broker_value = self._apply_broker_sanity_guardrails(
            broker_snapshot=broker_snapshot,
            governed_snapshot=governed_snapshot,
            data_freshness=data_freshness,
        )

        self.broker_source_mode = broker_mode
        self.broker_source_used = str(broker_snapshot.get("source") or "missing")
        self.freshness_classification = alignment

        benchmark_return = None
        if benchmark_returns_series:
            benchmark_return = benchmark_returns_series[-1]["value"]

        if canonical_df is not None and "excess_return" in canonical_df.columns and not canonical_df["excess_return"].dropna().empty:
            excess_return = float(canonical_df["excess_return"].dropna().iloc[-1])
        elif latest_daily_return is not None and benchmark_return is not None:
            excess_return = float(latest_daily_return - benchmark_return)
        else:
            excess_return = None

        holdings = None
        if canonical_df is not None and "holdings_count" in canonical_df.columns and not canonical_df["holdings_count"].dropna().empty:
            holdings = int(float(canonical_df["holdings_count"].dropna().iloc[-1]))
        elif isinstance(positions_obj.get("position_count"), (int, float)):
            position_count = positions_obj.get("position_count")
            if position_count is not None:
                holdings = int(position_count)

        turnover = None
        if canonical_df is not None and "turnover" in canonical_df.columns and not canonical_df["turnover"].dropna().empty:
            turnover = float(canonical_df["turnover"].dropna().iloc[-1])
        elif nav_df is not None and "turnover" in nav_df.columns and not nav_df["turnover"].dropna().empty:
            turnover = float(nav_df["turnover"].dropna().iloc[-1])

        execution_status = "UNKNOWN"
        if exec_payload_obj:
            execution_status = str(
                exec_payload_obj.get("status")
                or exec_payload_obj.get("execution_status")
                or exec_payload_obj.get("status_label")
                or "UNKNOWN"
            ).upper()
        elif isinstance(health_obj, dict):
            execution_status = str(health_obj.get("status") or "UNKNOWN").upper()
        if preflight_halted and execution_status in {"UNKNOWN", "", "N/A"}:
            execution_status = "HALTED_PREFLIGHT"
        if inferred_early_halt and execution_status in {"UNKNOWN", "", "N/A", "HALTED"}:
            execution_status = "HALTED_INFERRED_PREFLIGHT"
        
        # Legacy shadow aliases map into canonical paper mode, but should still
        # be reported honestly as paper simulation rather than broker execution.
        run_mode_lower = normalize_trading_mode(raw_mode, default="paper")
        legacy_shadow_alias = str(raw_mode or "").strip().lower() == "shadow"
        if legacy_shadow_alias:
            # Check if run has actual activity (trades recorded)
            has_run_activity = False
            if run_id:
                try:
                    trades_path = self._abs(f"outputs/runs/{run_id}/ledger/trades.csv")
                    if trades_path.exists():
                        trades_df = pd.read_csv(trades_path)
                        has_run_activity = len(trades_df) > 0
                except Exception:
                    pass
            
            # Adjust status based on activity and original status
            if execution_status in {"PASS", "COMPLETED", "SUCCESS", "READY"}:
                execution_status = "SIMULATED"
            elif execution_status == "PLANNED" and has_run_activity:
                # Has activity despite PLANNED status - report as executed shadow sim
                execution_status = "SIMULATED"
            elif execution_status == "PLANNED" and not has_run_activity:
                execution_status = "PAPER_PLANNED"

        # Performance summary metrics.
        mtd = qtd = si = si_alpha = best_day = worst_day = None
        current_drawdown = drawdown_series[-1]["value"] if drawdown_series else None

        if current_drawdown is None:
            self._mark_degraded("current_drawdown", "insufficient NAV history")

        if canonical_df is not None and not canonical_df.empty and "date" in canonical_df.columns:
            work = canonical_df.copy()
            work["date_dt"] = pd.to_datetime(work["date"], errors="coerce")
            work = work.dropna(subset=["date_dt"]).sort_values("date_dt")

            if "strategy_return" in work.columns:
                sr = pd.to_numeric(work["strategy_return"], errors="coerce")
                if not sr.dropna().empty:
                    last_dt = work["date_dt"].max()
                    month_mask = (work["date_dt"].dt.year == last_dt.year) & (work["date_dt"].dt.month == last_dt.month)
                    quarter = ((last_dt.month - 1) // 3) + 1
                    q_mask = (work["date_dt"].dt.year == last_dt.year) & ((((work["date_dt"].dt.month - 1) // 3) + 1) == quarter)

                    mtd = self._cumulative_return(sr[month_mask])
                    qtd = self._cumulative_return(sr[q_mask])
                    si = self._cumulative_return(sr)
                    best_day = float(sr.dropna().max()) if not sr.dropna().empty else None
                    worst_day = float(sr.dropna().min()) if not sr.dropna().empty else None

            if "spy_return" in work.columns and "strategy_return" in work.columns:
                sr = pd.to_numeric(work["strategy_return"], errors="coerce")
                br = pd.to_numeric(work["spy_return"], errors="coerce")
                aligned = pd.DataFrame({"sr": sr, "br": br}).dropna()
                if not aligned.empty:
                    strat_prod = self._coerce_num((1.0 + aligned["sr"]).prod())
                    bench_prod = self._coerce_num((1.0 + aligned["br"]).prod())
                    if strat_prod is not None and bench_prod is not None:
                        si_alpha = float((strat_prod - 1.0) - (bench_prod - 1.0))

        # Fallback for return metrics when canonical strategy_return is sparse.
        if si is None and daily_returns_series:
            daily_ser = pd.Series([x["value"] for x in daily_returns_series], dtype=float)
            si = self._cumulative_return(daily_ser)
            if si is not None:
                self.warnings.append("Derived since-inception return from fallback daily return series.")

        if mtd is None and daily_returns_series:
            dr_df = pd.DataFrame(daily_returns_series)
            dr_df["date_dt"] = pd.to_datetime(dr_df["date"], errors="coerce")
            dr_df = dr_df.dropna(subset=["date_dt"]).sort_values("date_dt")
            if not dr_df.empty:
                last_dt = dr_df["date_dt"].iloc[-1]
                mask = (dr_df["date_dt"].dt.year == last_dt.year) & (dr_df["date_dt"].dt.month == last_dt.month)
                mtd = self._cumulative_return(pd.Series(dr_df.loc[mask, "value"], dtype=float))

        if si_alpha is None and daily_returns_series and benchmark_returns_series:
            dr = pd.DataFrame(daily_returns_series).rename(columns={"value": "sr"})
            br = pd.DataFrame(benchmark_returns_series).rename(columns={"value": "br"})
            merged = dr.merge(br, on="date", how="inner")
            if not merged.empty:
                strat_prod = self._coerce_num((1.0 + pd.Series(merged["sr"], dtype=float)).prod())
                bench_prod = self._coerce_num((1.0 + pd.Series(merged["br"], dtype=float)).prod())
                if strat_prod is not None and bench_prod is not None:
                    si_alpha = float((strat_prod - 1.0) - (bench_prod - 1.0))

        if daily_pl is None:
            self._mark_degraded("daily_pl", "missing prior NAV and no return-based estimate")
        if mtd is None:
            self._mark_degraded("mtd_return", "insufficient in-month return history")
        if si is None:
            self._mark_degraded("since_inception_return", "insufficient return history")
        if excess_return is None:
            self._mark_degraded("excess_return", "missing strategy or benchmark daily return")

        # Risk section.
        latest_cash_weight = None
        latest_gross = None
        latest_largest = None
        latest_vix_regime = None

        if canonical_df is not None and not canonical_df.empty:
            if "cash_weight" in canonical_df.columns and not canonical_df["cash_weight"].dropna().empty:
                latest_cash_weight = float(canonical_df["cash_weight"].dropna().iloc[-1])
            if "gross_exposure" in canonical_df.columns and not canonical_df["gross_exposure"].dropna().empty:
                latest_gross = float(canonical_df["gross_exposure"].dropna().iloc[-1])
            if "vix_regime" in canonical_df.columns and not canonical_df["vix_regime"].dropna().empty:
                latest_vix_regime = str(canonical_df["vix_regime"].dropna().iloc[-1])

        if latest_cash_weight is None and nav_df is not None and not nav_df.empty and "cash" in nav_df.columns and "equity" in nav_df.columns:
            nav_last = nav_df.dropna(subset=["cash", "equity"]).copy()
            if not nav_last.empty:
                cash_val = float(nav_last.iloc[-1]["cash"])
                equity_val = float(nav_last.iloc[-1]["equity"])
                if equity_val != 0:
                    latest_cash_weight = cash_val / equity_val

        if latest_gross is None and nav_df is not None and not nav_df.empty and "gross_exposure" in nav_df.columns and not nav_df["gross_exposure"].dropna().empty:
            latest_gross = float(nav_df["gross_exposure"].dropna().iloc[-1])

        if exec_payload_obj:
            risk_summary = exec_payload_obj.get("risk_summary")
            if isinstance(risk_summary, dict):
                max_weight_txt = str(risk_summary.get("Max position weight (%)") or "").replace("%", "").strip()
                latest_largest = self._coerce_num(max_weight_txt)
                if latest_largest is not None:
                    latest_largest = latest_largest / 100.0

        if latest_largest is None and isinstance(positions_obj.get("positions"), dict) and latest_nav:
            # Approximation only if position quantities exist without mark prices.
            latest_largest = None

        turnover_limit_pct = 0.35

        breaker_status = None
        if exec_payload_obj:
            breaker = exec_payload_obj.get("breaker")
            if isinstance(breaker, dict):
                breaker_status = str(breaker.get("mode") or "").upper() or None
            if breaker_status is None:
                ao = exec_payload_obj.get("active_overlay")
                if ao is not None:
                    breaker_status = str(ao).upper()
        if breaker_status is None and latest_vix_regime:
            breaker_status = latest_vix_regime.upper()

        # Activity section.
        buys = sells = new_positions = full_exits = orders_filled = orders_rejected = 0
        top_changes: list[dict[str, Any]] = []
        activity_source = "unknown"

        trades_list: list[dict[str, Any]] = []
        if isinstance(exec_payload_obj.get("trades"), list):
            trades_list = [t for t in exec_payload_obj["trades"] if isinstance(t, dict)]
            activity_source = "execution_payload"

        # Fallback: Try to read trades from selected run's ledger if execution payload is missing
        if not trades_list and run_id:
            try:
                trades_path = self._abs(f"outputs/runs/{run_id}/ledger/trades.csv")
                if trades_path.exists():
                    run_trades_df = pd.read_csv(trades_path)
                    if not run_trades_df.empty:
                        for _, row in run_trades_df.iterrows():
                            trade_dict = row.to_dict() if hasattr(row, 'to_dict') else dict(row)
                            trades_list.append(trade_dict)
                        activity_source = "run_trades_csv"
            except Exception:
                pass

        if trades_list:
            for t in trades_list:
                side = str(t.get("side") or "").upper()
                reason = str(t.get("reason") or "").lower()
                if side == "BUY":
                    buys += 1
                elif side == "SELL":
                    sells += 1
                if side == "BUY" and "removed" not in reason:
                    new_positions += 1
                if side == "SELL" and "removed" in reason:
                    full_exits += 1

            equity_ref = self._coerce_num(exec_payload_obj.get("equity")) or latest_nav
            for t in sorted(trades_list, key=lambda x: abs(self._coerce_num(x.get("notional")) or 0.0), reverse=True)[:5]:
                notional = self._coerce_num(t.get("notional"))
                side = str(t.get("side") or "").upper()
                if notional is not None and equity_ref:
                    signed = (notional / equity_ref) * (1 if side == "BUY" else -1)
                else:
                    signed = None
                top_changes.append(
                    {
                        "ticker": t.get("ticker"),
                        "action": side or "N/A",
                        "change_weight": signed,
                        "reason": t.get("reason") or "Not provided",
                    }
                )

        if isinstance(health_obj, dict):
            orders_filled = int(self._coerce_num(health_obj.get("executed_trade_count")) or buys + sells)
            planned = int(self._coerce_num(health_obj.get("planned_trade_count")) or orders_filled)
            orders_rejected = max(planned - orders_filled, 0)
        else:
            orders_filled = buys + sells
            orders_rejected = 0

        # Exceptions and operating checks.
        recon_ok = None
        if exec_payload_obj and exec_payload_obj.get("recon_failure") is not None:
            recon_ok = not bool(exec_payload_obj.get("recon_failure"))
        elif isinstance(health_obj, dict) and health_obj.get("recon_delta") is not None:
            delta = abs(self._coerce_num(health_obj.get("recon_delta")) or 0.0)
            tol = abs(self._coerce_num(health_obj.get("recon_equity_tolerance")) or 0.0)
            recon_ok = delta <= tol if tol > 0 else delta == 0.0
        if preflight_halted and recon_ok is None:
            recon_ok = None
        if inferred_early_halt and recon_ok is None:
            recon_ok = None

        run_success = execution_status in {
            "PASS",
            "READY",
            "SUCCESS",
            "COMPLETED",
            "EXECUTED",
            "NO_ACTION",
            "SKIPPED_DUPLICATE",
        }
        if preflight_halted:
            run_success = False
        if inferred_early_halt:
            run_success = False
        if execution_status in {"HALTED", "HALTED_PREFLIGHT", "HALTED_INFERRED_PREFLIGHT", "FAIL", "FAILED", "ERROR", "RECON_FAIL_AUTO_BOOTSTRAP"}:
            run_success = False

        required_artifacts = [
            "outputs/latest.json",
            "outputs/alpha_assessment/canonical_performance.csv",
            "outputs/perf/nav_timeseries.csv",
            "outputs/ledger/trades.csv",
        ]
        missing_required = [p for p in required_artifacts if not (self.repo_root / p).exists()]

        exceptions: list[dict[str, str]] = []
        if preflight_halted:
            halt_stage = str(preflight_failure_obj.get("halt_stage") or "preflight")
            halt_reason = str(preflight_failure_obj.get("halt_reason") or "preflight gate failed")
            exceptions.append(
                {
                    "category": "Preflight",
                    "status": "fail",
                    "message": f"Run halted at {halt_stage}: {halt_reason}.",
                }
            )
        elif inferred_early_halt:
            exceptions.append(
                {
                    "category": "Preflight",
                    "status": "warning",
                    "message": "Run likely halted before stage artifacts were generated (inferred).",
                }
            )

        if recon_ok is True:
            exceptions.append({"category": "Reconciliation", "status": "pass", "message": "No issues detected."})
        elif preflight_halted:
            exceptions.append({"category": "Reconciliation", "status": "warning", "message": "Reconciliation gate blocked execution before run stages."})
        elif recon_ok is False:
            msg = "Model/broker reconciliation failed."
            if inferred_early_halt:
                msg = "Reconciliation likely blocked run before stage artifacts were generated (inferred)."
                exceptions.append({"category": "Reconciliation", "status": "warning", "message": msg})
            else:
                exceptions.append({"category": "Reconciliation", "status": "fail", "message": msg})
        elif inferred_early_halt:
            exceptions.append({"category": "Reconciliation", "status": "warning", "message": "Reconciliation status inferred from sparse run artifacts."})
        elif legacy_shadow_alias:
            exceptions.append({"category": "Reconciliation", "status": "pass", "message": "Reconciliation not applicable in paper simulation mode."})
        else:
            exceptions.append({"category": "Reconciliation", "status": "warning", "message": "Reconciliation status unavailable."})

        if preflight_halted:
            halt_stage = str(preflight_failure_obj.get("halt_stage") or "preflight")
            exceptions.append(
                {
                    "category": "Execution",
                    "status": "warning",
                    "message": f"Execution did not start due to preflight halt at {halt_stage}.",
                }
            )
        elif inferred_early_halt:
            exceptions.append(
                {
                    "category": "Execution",
                    "status": "warning",
                    "message": "Execution likely did not start; halted before stage artifacts were generated (inferred).",
                }
            )
        elif run_success:
            if legacy_shadow_alias:
                msg = f"Paper simulation status: {execution_status}. (This is simulated activity, not broker execution.)"
            else:
                msg = f"Execution status: {execution_status}."
            exceptions.append({"category": "Execution", "status": "pass", "message": msg})
        else:
            exceptions.append({"category": "Execution", "status": "fail", "message": f"Execution status: {execution_status}."})

        risk_status = "pass"
        risk_message = "No risk exceptions detected."
        if breaker_status in {"LOCK", "HALTED"}:
            risk_status = "fail"
            risk_message = f"Breaker status is {breaker_status}."
        elif breaker_status in {"PARTIAL", "ELEVATED", "HIGH", "CRISIS"}:
            risk_status = "warning"
            risk_message = f"Breaker or regime indicates caution: {breaker_status}."
        exceptions.append({"category": "Risk", "status": risk_status, "message": risk_message})

        broker_alignment_msg = str(data_freshness.get("alignment_detail") or "Broker alignment not available.")
        broker_trust = str(broker_snapshot.get("trust_level") or "missing")
        if suspicious_broker_value:
            broker_alignment_msg = str(data_freshness.get("suspicious_reason") or broker_alignment_msg)
            exceptions.append({
                "category": "Broker snapshot",
                "status": "warning",
                "message": f"Suspicious derived broker estimate: {broker_alignment_msg}",
            })
        elif broker_trust == "derived":
            exceptions.append({
                "category": "Broker snapshot",
                "status": "warning",
                "message": f"Derived broker estimate in use. {broker_alignment_msg}",
            })
        elif alignment == "aligned":
            exceptions.append({"category": "Broker snapshot", "status": "pass", "message": broker_alignment_msg})
        elif alignment in {"mismatch", "stale"}:
            exceptions.append({"category": "Broker snapshot", "status": "warning", "message": broker_alignment_msg})
        else:
            exceptions.append({"category": "Broker snapshot", "status": "warning", "message": "Broker snapshot unavailable; dashboard is showing governed run values."})

        missing_optional = sorted(set(self.missing_files) - set(missing_required))

        if missing_required:
            exceptions.append(
                {
                    "category": "Data / artifacts",
                    "status": "warning",
                    "message": f"Missing critical artifacts: {', '.join(missing_required)}",
                }
            )
        elif missing_optional:
            exceptions.append(
                {
                    "category": "Data / artifacts",
                    "status": "warning",
                    "message": f"Missing optional artifacts: {', '.join(missing_optional[:3])}",
                }
            )
        elif self.degraded_metrics:
            exceptions.append(
                {
                    "category": "Data / artifacts",
                    "status": "warning",
                    "message": f"Metrics degraded: {'; '.join(self.degraded_metrics[:3])}",
                }
            )
        else:
            exceptions.append({"category": "Data / artifacts", "status": "pass", "message": "All critical artifacts present."})

        report_generated = False
        if run_id:
            report_dir = self._abs(f"outputs/runs/{run_id}/reports")
            if report_dir.exists() and any(report_dir.glob("quant_report_*.html")):
                report_generated = True
                self.sources.append(SourceRecord(f"outputs/runs/{run_id}/reports/quant_report_*.html", "used"))
            else:
                self.sources.append(SourceRecord(f"outputs/runs/{run_id}/reports/quant_report_*.html", "missing"))

        canonical_positions_exists = (self.repo_root / canonical_positions_path).exists()

        operating_checks = [
            {
                "label": "Run completed",
                "status": "pass" if run_success else ("warning" if (preflight_halted or inferred_early_halt) else "fail"),
                "detail": (
                    f"Execution status: {execution_status}."
                    if not (preflight_halted or inferred_early_halt)
                    else "Run halted before execution stages completed."
                ),
            },
            {
                "label": "Trades executed",
                "status": "pass" if orders_filled > 0 else "warning",
                "detail": f"Orders filled: {orders_filled}.",
            },
            {
                "label": "Reconciliation passed",
                "status": (
                    "pass"
                    if recon_ok is True
                    else ("warning" if (preflight_halted or inferred_early_halt) else ("fail" if recon_ok is False else "warning"))
                ),
                "detail": (
                    "Preflight reconciliation gate failed."
                    if preflight_halted
                    else (
                        "Reconciliation status inferred; halted before stage artifacts were generated."
                        if inferred_early_halt
                        else ("Reconciliation check available." if recon_ok is not None else "Reconciliation artifact not found.")
                    )
                ),
            },
            {
                "label": "Canonical positions present",
                "status": "pass" if canonical_positions_exists else "fail",
                "detail": canonical_positions_path if canonical_positions_exists else "Canonical snapshot missing.",
            },
            {
                "label": "Ledger updated",
                "status": "pass" if trades_df is not None and not trades_df.empty else "warning",
                "detail": "Ledger has at least one trade row." if trades_df is not None and not trades_df.empty else "No trade rows found in ledger.",
            },
            {
                "label": "Daily report generated",
                "status": "pass" if report_generated else "warning",
                "detail": "Run report HTML found." if report_generated else "Run report HTML not found in latest run archive.",
            },
            {
                "label": "Metric completeness",
                "status": "pass" if not self.degraded_metrics else "warning",
                "detail": "All executive metrics computed." if not self.degraded_metrics else "; ".join(self.degraded_metrics[:3]),
            },
            {
                "label": "Broker snapshot available",
                "status": (
                    "warning"
                    if (suspicious_broker_value or broker_trust == "derived")
                    else ("pass" if broker_snapshot.get("status") == "fresh" else ("warning" if broker_snapshot.get("status") == "stale" else "fail"))
                ),
                "detail": (
                    f"Suspicious derived estimate. Source: {broker_snapshot.get('source_detail') or broker_snapshot.get('source') or 'missing'}"
                    if suspicious_broker_value
                    else (
                        f"Derived estimate. Source: {broker_snapshot.get('source_detail') or broker_snapshot.get('source') or 'missing'}"
                        if broker_trust == "derived"
                        else f"Source: {broker_snapshot.get('source_detail') or broker_snapshot.get('source') or 'missing'}; as_of={broker_snapshot.get('as_of') or 'n/a'}"
                    )
                ),
            },
            {
                "label": "Run vs broker alignment",
                "status": "pass" if alignment == "aligned" else "warning",
                "detail": broker_alignment_msg,
            },
        ]

        status_banner = "Run status unavailable."
        
        # Executive-friendly banner based on run selection context
        if selected_run_id != latest_attempted_run_id and latest_attempted_run_id:
            # Latest attempted run failed/halted, but successful run available
            if report_date and run_success:
                status_banner = (
                    f"Latest run ({latest_attempted_run_id}) halted. "
                    f"Executive metrics from most recent successful run ({report_date}). "
                    f"{('Metrics current as of selected run.' if not suspicious_broker_value else 'Broker estimate is suspicious.')}"
                )
            elif report_date:
                status_banner = f"Showing most recent successful run ({report_date}). Latest attempted run ({latest_attempted_run_id}) failed before completion."
        elif report_date and run_success:
            trades_txt = f"{orders_filled} trades executed" if orders_filled > 0 else "no trades executed"
            excess_txt = "benchmark comparison unavailable"
            if excess_return is not None:
                excess_bps = round(excess_return * 10000)
                excess_txt = f"portfolio {'outperformed' if excess_bps >= 0 else 'underperformed'} benchmark by {abs(excess_bps)} bps"
            status_banner = (
                f"Run completed successfully on {report_date}. "
                f"{trades_txt.capitalize()}. {excess_txt.capitalize()}. "
                f"{('No material exceptions.' if len([e for e in exceptions if e['status'] == 'fail']) == 0 else 'Exceptions require review.')}"
            )
        elif report_date:
            status_banner = f"Run reported {execution_status} on {report_date}. Review exceptions and operating checks."
        if preflight_halted and report_date:
            halt_stage = str(preflight_failure_obj.get("halt_stage") or "preflight")
            status_banner = (
                f"Run halted during {halt_stage} on {report_date}. "
                "Execution did not start; review preflight failure details."
            )
        elif inferred_early_halt and report_date:
            status_banner = (
                f"Run likely halted before stage artifacts were generated on {report_date}. "
                "This classification is inferred from sparse run artifacts."
            )

        if suspicious_broker_value:
            status_banner = f"{status_banner} Derived broker estimate is suspicious and has been de-emphasized."
        elif broker_trust == "derived":
            status_banner = f"{status_banner} Broker view is a derived estimate (not authoritative)."
        elif alignment == "mismatch":
            status_banner = f"{status_banner} Broker snapshot and governed run date are out of sync."
        elif alignment == "stale":
            status_banner = f"{status_banner} Broker snapshot is stale."
        elif alignment == "missing":
            status_banner = f"{status_banner} Broker snapshot unavailable; governed run snapshot shown."

        if inferred_note:
            self.warnings.append(inferred_note)

        # Filter to executive-level warnings only
        executive_warnings = self._filter_executive_warnings()

        overall_status = "PASS" if run_success else ("WARNING" if (preflight_halted or inferred_early_halt or execution_status == "UNKNOWN") else "FAIL")

        model = {
            "run_meta": {
                "report_date": report_date or "Not generated",
                "run_id": run_id or "Not generated",
                "mode": mode,
                "overall_status": overall_status,
                "benchmark": "SPY",
                "last_updated": self._safe_iso_now(),
                "status_banner": status_banner,
"latest_attempted_run": {
                    "run_id": latest_attempted_run_id,
                    "report_date": latest_attempted_report_date,
                    "created_at": latest_attempted_created_at,
                },
                "selected_governed_run": {
                    "run_id": selected_run_id,
                    "report_date": report_date,
                    "selection_reason": selection_reason,
                },
                # Run-observability fields — distinguish incomplete/failed runs from
                # intentional no-action days and stale fallback dashboard states.
                "latest_attempted_terminal_status": latest_attempted_terminal_status,
                "latest_attempted_is_complete": latest_attempted_is_complete,
                "latest_attempted_missing_artifacts": latest_attempted_missing_artifacts,
                "latest_successful_run_id": latest_successful_run_id,
                "latest_successful_trade_date": latest_successful_trade_date,
                "fallback_in_use": fallback_in_use,
                "fallback_reason": fallback_reason,
            },
            "kpis": {
                "portfolio_value": latest_nav,
                "daily_pl": daily_pl,
                "daily_return": latest_daily_return,
                "benchmark_return": benchmark_return,
                "excess_return": excess_return,
                "holdings": holdings,
                "turnover": turnover,
                "run_status": execution_status,
            },
            "perf_summary": {
                "mtd_return": mtd,
                "qtd_return": qtd,
                "since_inception_return": si,
                "since_inception_alpha": si_alpha,
                "current_drawdown": current_drawdown,
                "best_day": best_day,
                "worst_day": worst_day,
            },
            "series": {
                "nav": nav_series,
                "benchmark": benchmark_series,
                "daily_returns": daily_returns_series,
                "excess_returns": excess_returns_series,
                "drawdown": drawdown_series,
                "chart_metadata": {
                    "nav_chart": {
                        "title": "Portfolio NAV Indexed to 100",
                        "x_axis_label": "Date (YYYY-MM-DD)",
                        "y_axis_label": "Indexed Value",
                        "font_size": "12pt",
                        "show_grid": True,
                        "note": "Portfolio NAV (blue) vs SPY benchmark (orange), indexed to 100 at inception" if nav_series else "NAV data unavailable",
                    },
                    "daily_returns_chart": {
                        "title": "Daily Strategy Returns",
                        "x_axis_label": "Date (YYYY-MM-DD)",
                        "y_axis_label": "Daily Return (%)",
                        "baseline": 0.0,
                        "baseline_visible": True,
                        "font_size": "12pt",
                        "show_grid": True,
                        "note": "Daily percentage return; positive values are gains" if daily_returns_series else "Daily returns unavailable",
                    },
                    "excess_returns_chart": {
                        "title": "Daily Excess Return vs SPY",
                        "x_axis_label": "Date (YYYY-MM-DD)",
                        "y_axis_label": "Excess Return (%)",
                        "baseline": 0.0,
                        "baseline_visible": True,
                        "font_size": "12pt",
                        "show_grid": True,
                        "note": "Daily outperformance/underperformance vs SPY" if excess_returns_series else "Excess returns unavailable",
                    },
                },
            },
            "risk": {
                "drawdown": current_drawdown,
                "cash_position": latest_cash_weight,
                "gross_exposure": latest_gross,
                "largest_position_weight": latest_largest,
                "turnover_pct": turnover,
                "turnover_limit_pct": turnover_limit_pct,
                "breaker_status": breaker_status or "UNKNOWN",
            },
            "activity": {
                "buys": buys,
                "sells": sells,
                "new_positions": new_positions,
                "full_exits": full_exits,
                "orders_filled": orders_filled,
                "orders_rejected": orders_rejected,
                "source_run_id": selected_run_id,
                "source_report_date": report_date,
                "activity_source": activity_source,
                "is_simulated": legacy_shadow_alias,
                "note": f"Activity source: {activity_source} from {'paper simulation' if legacy_shadow_alias else 'execution'} run {selected_run_id or 'unavailable'}",
            },
            "governed_snapshot": governed_snapshot,
            "broker_snapshot": broker_snapshot,
            "execution_integrity": execution_integrity,
            "data_freshness": data_freshness,
            "top_changes": top_changes,
            "exceptions": exceptions,
            "operating_checks": operating_checks,
            "sources": [s.__dict__ for s in self.sources],
            "builder_notes": {
                "build_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "missing_files": sorted(set(self.missing_files)),
                "warnings": executive_warnings,
                "all_warnings": self.warnings,
                "degraded_metrics": self.degraded_metrics,
                "broker_snapshot": {
                    "source_mode": self.broker_source_mode,
                    "source_used": self.broker_source_used,
                    "freshness": self.freshness_classification,
                    "trust_level": broker_snapshot.get("trust_level", "missing"),
                    "source_detail": broker_snapshot.get("source_detail", "missing"),
                    "suspicious": bool(broker_snapshot.get("suspicious")),
                    "broker_authoritative_state": bool(broker_snapshot.get("trust_level") == "authoritative"),
                },
            },
        }
        return model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Quant Daily Executive Dashboard data JSON from repo artifacts.")
    parser.add_argument("--repo-root", default=".", help="Repository root path")
    parser.add_argument(
        "--output",
        default="web/dashboard/dashboard_data.json",
        help="Dashboard JSON output path (relative to repo root unless absolute)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = repo_root / output_path

    builder = DashboardBuilder(repo_root=repo_root)
    model = builder.build()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(model, indent=2), encoding="utf-8")

    print(f"[DASHBOARD] wrote {output_path}")
    notes = model.get("builder_notes", {})
    missing = notes.get("missing_files", [])
    warnings = notes.get("warnings", [])
    degraded = notes.get("degraded_metrics", [])
    broker_diag = notes.get("broker_snapshot", {}) if isinstance(notes, dict) else {}
    sources = model.get("sources", [])
    used_count = len([s for s in sources if s.get("status") == "used"])
    missing_count = len([s for s in sources if s.get("status") == "missing"])

    print(f"[DASHBOARD] sources_used={used_count} sources_missing={missing_count}")
    print(f"[DASHBOARD] degraded_metrics={len(degraded)} warnings={len(warnings)}")
    print(
        "[DASHBOARD] broker_source_mode={mode} broker_source_used={used} freshness={fresh} trust={trust} suspicious={susp}".format(
            mode=broker_diag.get("source_mode", "missing"),
            used=broker_diag.get("source_used", "missing"),
            fresh=broker_diag.get("freshness", "missing"),
            trust=broker_diag.get("trust_level", "missing"),
            susp=broker_diag.get("suspicious", False),
        )
    )
    for msg in warnings:
        print(f"[DASHBOARD][WARN] {msg}")
    for metric_msg in degraded:
        print(f"[DASHBOARD][DEGRADED] {metric_msg}")
    if missing:
        print("[DASHBOARD][MISSING] " + ", ".join(missing))

    print()
    print("[DASHBOARD] ── Local development workflow ──────────────────────────────")
    print("[DASHBOARD]   1. serve:  python -m http.server 8765  (from repo root)")
    print("[DASHBOARD]   2. open:   http://localhost:8765/web/dashboard/quant_daily_executive.html")
    print("[DASHBOARD] ─────────────────────────────────────────────────────────────")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
