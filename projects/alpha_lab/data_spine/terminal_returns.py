"""Build a conservative terminal-return sensitivity envelope.

This module does not infer or certify delisting settlement proceeds.  It
separates the provider's last observed trading-day return from two explicitly
hypothetical post-observation scenarios:

* ``pessimistic_total_loss`` assigns a further -100% return; and
* ``zero_incremental`` assigns no return after the last observed close.

Research evaluators may use both scenarios as a robustness envelope.  Neither
scenario is a substitute for an independently verified terminal settlement
return, and the output deliberately cannot satisfy the frozen terminal-return
provider gate.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from projects.alpha_lab.factory import canonical_json

from .storage import output_root, sha256_file, write_bundle_from_paths


SCHEMA_VERSION = "caerus_alpha_lab_terminal_return_sensitivity_v1"


def _quote(path: Path) -> str:
    return "'{}'".format(str(path.resolve()).replace("'", "''"))


def _panel_columns(connection: Any, panel_path: Path) -> set[str]:
    rows = connection.execute(
        "DESCRIBE SELECT * FROM read_parquet(?)", [str(panel_path)]
    ).fetchall()
    return {str(row[0]) for row in rows}


def build_terminal_return_sensitivity(
    *,
    repo_root: Path,
    generated_at: datetime | None = None,
    panel_path: Path | None = None,
) -> Dict[str, Any]:
    """Materialize a small, immutable sensitivity table for terminated names.

    The command is intended to run only from the authoritative GCP Alpha Lab
    checkout.  Tests may use a temporary repository root.  It never rewrites
    the source price panel or its provider certification.
    """

    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "duckdb is required for terminal-return sensitivity materialization"
        ) from exc

    timestamp = generated_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")

    root = repo_root.expanduser().resolve()
    price_path = (
        panel_path.expanduser().resolve()
        if panel_path is not None
        else root / "outputs/research/pit_liquidity/pit_liquidity_panel.parquet"
    )
    master_path = root / "data/pit_universe/security_master.csv"
    if not price_path.is_file():
        raise FileNotFoundError(price_path)
    if not master_path.is_file():
        raise FileNotFoundError(master_path)

    staging = output_root(root) / ".staging" / "terminal_return_sensitivity"
    staging.mkdir(parents=True, exist_ok=True)
    envelope_path = staging / "terminal_return_sensitivity.parquet"
    quality_path = staging / "quality.json"
    envelope_path.unlink(missing_ok=True)
    quality_path.unlink(missing_ok=True)

    connection = duckdb.connect()
    try:
        columns = _panel_columns(connection, price_path)
        if not {"security_id", "date", "close"} <= columns:
            raise ValueError(
                "price panel requires security_id, date, and close columns"
            )
        if "last_observed_total_return" in columns:
            observed_expression = "p.last_observed_total_return"
            observed_lineage = "explicit_last_observed_total_return"
        elif "terminal_return" in columns:
            # Legacy v2 panels populated terminal_return with the final observed
            # provider daily return.  Preserve it only under an honest name.
            observed_expression = "p.terminal_return"
            observed_lineage = "legacy_v2_terminal_field_reclassified_as_last_observed_return"
        else:
            observed_expression = "NULL::DOUBLE"
            observed_lineage = "not_available"

        connection.execute(
            """
            CREATE TEMP TABLE security_master AS
            SELECT security_id,
                   TRY_CAST(NULLIF(effective_end, '') AS DATE) AS effective_end,
                   TRY_CAST(NULLIF(lastpricedate, '') AS DATE) AS last_price_date,
                   ticker
            FROM read_csv_auto({}, header=true, all_varchar=true)
            """.format(_quote(master_path))
        )
        connection.execute(
            """
            COPY (
              WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (
                  PARTITION BY security_id ORDER BY date DESC
                ) AS terminal_rank
                FROM read_parquet({})
              ),
              panel_meta AS (
                SELECT MAX(TRY_CAST(date AS DATE)) AS panel_max_date
                FROM read_parquet({})
              )
              SELECT
                p.security_id,
                m.ticker,
                TRY_CAST(p.date AS DATE) AS last_observed_date,
                m.effective_end AS membership_end_date,
                m.last_price_date,
                TRY_CAST(p.close AS DOUBLE) AS last_observed_close,
                TRY_CAST({} AS DOUBLE) AS provider_final_day_total_return,
                NULL::DOUBLE AS verified_terminal_return,
                -1.0::DOUBLE AS pessimistic_total_loss_return,
                0.0::DOUBLE AS zero_incremental_return,
                'UNVERIFIED_TERMINAL_SETTLEMENT' AS terminal_return_status,
                false AS use_in_primary_point_estimate,
                meta.panel_max_date
              FROM ranked p
              JOIN security_master m USING (security_id)
              CROSS JOIN panel_meta meta
              WHERE p.terminal_rank=1
                AND m.effective_end IS NOT NULL
                AND m.effective_end <= meta.panel_max_date
              ORDER BY p.security_id
            ) TO {} (FORMAT PARQUET, COMPRESSION ZSTD)
            """.format(
                _quote(price_path),
                _quote(price_path),
                observed_expression,
                _quote(envelope_path),
            )
        )
        stats = connection.execute(
            """
            SELECT COUNT(*),
                   COUNT(provider_final_day_total_return),
                   COUNT(verified_terminal_return),
                   COUNT(DISTINCT security_id),
                   MIN(last_observed_date),
                   MAX(last_observed_date),
                   MAX(panel_max_date)
            FROM read_parquet(?)
            """,
            [str(envelope_path)],
        ).fetchone()
    finally:
        connection.close()

    quality = {
        "schema_version": SCHEMA_VERSION,
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "generated_at": timestamp.astimezone(timezone.utc).isoformat(),
        "source_price_panel": str(price_path.relative_to(root)),
        "source_price_panel_sha256": sha256_file(price_path),
        "observed_return_lineage": observed_lineage,
        "candidate_rows": int(stats[0]),
        "candidate_security_count": int(stats[3]),
        "provider_final_day_return_count": int(stats[1]),
        "verified_terminal_return_count": int(stats[2]),
        "last_observed_date_range": [
            str(stats[4]) if stats[4] is not None else None,
            str(stats[5]) if stats[5] is not None else None,
        ],
        "panel_max_date": str(stats[6]) if stats[6] is not None else None,
        "status": "SENSITIVITY_ONLY",
        "terminal_settlement_certified": False,
        "historical_point_in_time_terminal_return_verified": False,
        "scenario_contract": {
            "pessimistic_total_loss": (
                "assign a further -100% return after the final observed close"
            ),
            "zero_incremental": (
                "assign no return after the final observed close"
            ),
            "required_use": (
                "report both scenarios; neither is a verified point estimate"
            ),
        },
        "alpha_claim_permitted_from_this_artifact_alone": False,
        "trading_behavior_changed": False,
    }
    quality_path.write_text(canonical_json(quality) + "\n", encoding="utf-8")
    bundle = write_bundle_from_paths(
        repo_root=root,
        source_id="terminal_return_sensitivity",
        files={
            "terminal_return_sensitivity.parquet": envelope_path,
            "quality.json": quality_path,
        },
        metadata={
            "schema_version": SCHEMA_VERSION,
            "source_price_panel_sha256": quality["source_price_panel_sha256"],
            "candidate_security_count": quality["candidate_security_count"],
            "terminal_settlement_certified": False,
            "sensitivity_only": True,
        },
        retrieved_at=timestamp,
    )
    envelope_path.unlink(missing_ok=True)
    quality_path.unlink(missing_ok=True)
    try:
        staging.rmdir()
    except OSError:
        pass
    return {
        **bundle,
        "quality": quality,
        "terminal_candidate_count": quality["candidate_security_count"],
        "terminal_return_status": quality["status"],
    }


def load_quality(path: Path) -> Dict[str, Any]:
    """Read a finalized quality artifact for operator tooling."""

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("terminal-return quality artifact must be a JSON object")
    return value
