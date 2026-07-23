"""Compile PIT price, liquidity, and characteristics panels with DuckDB."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from projects.alpha_lab.experiments.catalog import (
    PIT_CHARACTERISTICS,
    PIT_OBSERVED_PRICES,
    PIT_PRICES,
)
from projects.alpha_lab.factory import canonical_json

from .materialize import certify_asset
from .storage import sha256_file


def _quote(path: Path) -> str:
    return "'{}'".format(str(path.resolve()).replace("'", "''"))


def materialize_market_panels(
    *,
    repo_root: Path,
    sep_manifest_path: Path,
    resume_staged_database: bool = False,
) -> Dict[str, Any]:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("duckdb is required for market-panel materialization") from exc
    manifest_path = sep_manifest_path.expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata = manifest.get("metadata") or {}
    if metadata.get("table") != "SHARADAR/SEP":
        raise ValueError("SEP manifest does not describe SHARADAR/SEP")
    if metadata.get("pagination_complete") is not True:
        raise ValueError("SEP stream is not pagination-complete")
    sep_path = manifest_path.parent / "data/sep.csv.gz"
    if not sep_path.is_file():
        raise FileNotFoundError(sep_path)
    master_path = repo_root / "data/pit_universe/security_master.csv"
    actions_manifest = sorted(
        (repo_root / "outputs/research/alpha_lab/data_spine/sharadar_actions").glob(
            "*/manifest.json"
        )
    )[-1]
    actions_path = actions_manifest.parent / "data/actions.jsonl"
    factor_path = repo_root / "outputs/research/alpha_lab/shared/factor_panel.csv"
    facts_path = repo_root / "outputs/research/alpha_lab/shared/sec_companyfacts_compact.csv.gz"
    for path in (master_path, actions_path, factor_path, facts_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    output_root = repo_root / "outputs/research/pit_liquidity"
    output_root.mkdir(parents=True, exist_ok=True)
    price_path = output_root / "pit_liquidity_panel.parquet"
    price_staging_path = output_root / ".pit_liquidity_panel.parquet.tmp"
    characteristics_path = repo_root / "outputs/research/alpha_lab/shared/pit_characteristics.parquet"
    characteristics_path.parent.mkdir(parents=True, exist_ok=True)
    characteristics_staging_path = characteristics_path.with_name(
        ".pit_characteristics.parquet.tmp"
    )
    database = repo_root / "outputs/research/alpha_lab/shared/.market_materialization.duckdb"
    temporary_directory = (
        repo_root / "outputs/research/alpha_lab/shared/.duckdb_tmp"
    )
    temporary_directory.mkdir(parents=True, exist_ok=True)
    if resume_staged_database:
        if not database.is_file():
            raise FileNotFoundError(
                "resumable market staging database is absent: {}".format(database)
            )
    else:
        database.unlink(missing_ok=True)
    price_staging_path.unlink(missing_ok=True)
    characteristics_staging_path.unlink(missing_ok=True)
    connection = duckdb.connect(str(database))
    # The authoritative Alpha Lab VM is intentionally small.  Force DuckDB to
    # spill bounded intermediate state to the research disk instead of letting
    # a four-thread, multi-gigabyte build trigger the kernel OOM killer.
    connection.execute("PRAGMA threads=1")
    connection.execute("PRAGMA memory_limit='900MB'")
    connection.execute("PRAGMA preserve_insertion_order=false")
    connection.execute("PRAGMA temp_directory='{}'".format(
        str(temporary_directory.resolve()).replace("'", "''")
    ))
    build_succeeded = False
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS security_master AS
            SELECT *, TRY_CAST(effective_start AS DATE) AS start_date,
                   TRY_CAST(NULLIF(effective_end, '') AS DATE) AS end_date
            FROM read_csv_auto({}, header=true, all_varchar=true)
            """.format(_quote(master_path))
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS actions AS
            SELECT ticker, TRY_CAST(date AS DATE) AS date,
                   COALESCE(MAX(TRY_CAST(value AS DOUBLE)) FILTER
                     (WHERE action IN ('split','adrratiosplit')), 1.0) AS split_factor,
                   COALESCE(SUM(TRY_CAST(value AS DOUBLE)) FILTER
                     (WHERE action IN ('dividend','spinoffdividend')), 0.0) AS cash_dividend,
                   STRING_AGG(DISTINCT action, '|' ORDER BY action) AS action_types
            FROM read_json_auto({}, format='newline_delimited')
            GROUP BY ticker, TRY_CAST(date AS DATE)
            """.format(_quote(actions_path))
        )
        # Break the large join and the security-history window into persisted
        # phases.  Combining them in one CTE kept both operators' working sets
        # live and exhausted the bounded VM memory even while DuckDB spilled.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_prices AS
            SELECT s.security_id,
                   TRY_CAST(p.date AS DATE) AS date,
                   TRY_CAST(p.open AS DOUBLE) * TRY_CAST(p.closeunadj AS DOUBLE) /
                     NULLIF(TRY_CAST(p.close AS DOUBLE), 0) AS open,
                   TRY_CAST(p.closeunadj AS DOUBLE) AS close,
                   TRY_CAST(p.closeadj AS DOUBLE) AS provider_closeadj,
                   TRY_CAST(p.volume AS DOUBLE) AS volume,
                   COALESCE(a.split_factor, 1.0) AS split_factor,
                   COALESCE(a.cash_dividend, 0.0) AS cash_dividend,
                   a.action_types
            FROM read_csv_auto({}, header=true) p
            JOIN security_master s ON p.ticker=s.ticker
              AND TRY_CAST(p.date AS DATE) >= s.start_date
              AND (s.end_date IS NULL OR TRY_CAST(p.date AS DATE) <= s.end_date)
            LEFT JOIN actions a ON p.ticker=a.ticker AND TRY_CAST(p.date AS DATE)=a.date
            """.format(_quote(sep_path))
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS security_last_dates AS
            SELECT security_id, MAX(date) AS last_date
            FROM raw_prices
            GROUP BY security_id
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS staged_prices AS
            WITH lagged AS (
              SELECT r.*, d.last_date,
                     LAG(provider_closeadj) OVER
                       (PARTITION BY r.security_id ORDER BY r.date) AS prior_closeadj
              FROM raw_prices r
              JOIN security_last_dates d USING (security_id)
            )
            SELECT *, CASE WHEN prior_closeadj > 0 AND provider_closeadj >= 0 THEN
                   provider_closeadj / prior_closeadj - 1.0 END AS daily_total_return
            FROM lagged
            """
        )
        connection.execute(
            """
            COPY (
              WITH first_values AS (
                SELECT security_id,
                       ARG_MIN(close, date) AS first_close,
                       ARG_MIN(provider_closeadj, date) AS first_provider_closeadj
                FROM staged_prices
                GROUP BY security_id
              ), indexed AS (
                SELECT p.*,
                  f.first_close * p.provider_closeadj /
                    NULLIF(f.first_provider_closeadj, 0.0)
                    AS causal_total_return_index,
                  AVG(p.close * p.volume) OVER (
                    PARTITION BY p.security_id ORDER BY p.date
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                  ) AS dollar_ADV_20
                FROM staged_prices p
                JOIN first_values f USING (security_id)
              )
              SELECT security_id, date, open, close,
                     causal_total_return_index AS closeadj, volume, dollar_ADV_20,
                     split_factor, cash_dividend,
                     CASE WHEN action_types IS NULL THEN ''
                          ELSE MD5(security_id || '|' || CAST(date AS VARCHAR) || '|' || action_types) END
                       AS corporate_action_id,
                     CASE WHEN date=last_date THEN daily_total_return END
                       AS last_observed_total_return,
                     NULL::DOUBLE AS delisting_return,
                     NULL::DOUBLE AS terminal_return,
                     CAST(date AS TIMESTAMP) + INTERVAL 1 DAY AS adjustment_available_at,
                     CAST(date AS TIMESTAMP) + INTERVAL 1 DAY AS available_at
              FROM indexed
            ) TO {} (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 250000)
            """.format(_quote(price_staging_path))
        )

        connection.execute(
            """
            CREATE TABLE sec_facts AS
            SELECT cik, logical_fact, TRY_CAST(value AS DOUBLE) AS value,
                   TRY_CAST(available_at AS TIMESTAMP) AS available_at,
                   TRY_CAST("end" AS DATE) AS fact_end, accession_number
            FROM read_csv_auto({}, header=true)
            WHERE logical_fact IN ('shares_outstanding','stockholders_equity','stockholders_equity_including_nci')
              AND TRY_CAST(value AS DOUBLE) IS NOT NULL
            """.format(_quote(facts_path))
        )
        connection.execute(
            """
            CREATE TABLE shares AS
            SELECT * EXCLUDE(rn) FROM (
              SELECT *, ROW_NUMBER() OVER (PARTITION BY cik, CAST(available_at AS DATE)
                ORDER BY fact_end DESC NULLS LAST, accession_number DESC) rn
              FROM sec_facts WHERE logical_fact='shares_outstanding'
            ) WHERE rn=1
            """
        )
        connection.execute(
            """
            CREATE TABLE equity AS
            SELECT * EXCLUDE(rn) FROM (
              SELECT *, ROW_NUMBER() OVER (PARTITION BY cik, CAST(available_at AS DATE)
                ORDER BY CASE WHEN logical_fact='stockholders_equity' THEN 0 ELSE 1 END,
                         fact_end DESC NULLS LAST, accession_number DESC) rn
              FROM sec_facts WHERE logical_fact IN
                ('stockholders_equity','stockholders_equity_including_nci')
            ) WHERE rn=1
            """
        )
        connection.execute(
            """
            COPY (
              WITH prices AS (
                SELECT p.*, s.cik, s.sector_id,
                       p.closeadj / LAG(p.closeadj) OVER
                         (PARTITION BY p.security_id ORDER BY p.date) - 1.0 AS daily_return,
                       p.closeadj / LAG(p.closeadj, 5) OVER
                         (PARTITION BY p.security_id ORDER BY p.date) - 1.0 AS prior_return_5d,
                       p.closeadj / LAG(p.closeadj, 20) OVER
                         (PARTITION BY p.security_id ORDER BY p.date) - 1.0 AS prior_return_20d,
                       p.closeadj / LAG(p.closeadj, 60) OVER
                         (PARTITION BY p.security_id ORDER BY p.date) - 1.0 AS prior_return_60d
                FROM read_parquet({}) p
                JOIN (SELECT DISTINCT security_id, cik, sector AS sector_id FROM security_master) s
                  USING (security_id)
              ), with_factors AS (
                SELECT p.*, TRY_CAST(f.MKT_RF AS DOUBLE) AS market_return
                FROM prices p LEFT JOIN read_csv_auto({}, header=true) f
                  ON p.date=TRY_CAST(f.date AS DATE)
              ), rolling AS (
                SELECT *,
                  STDDEV_SAMP(daily_return) OVER (PARTITION BY security_id ORDER BY date
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) * SQRT(252.0) AS realized_volatility_20d,
                  COVAR_SAMP(daily_return, market_return) OVER (PARTITION BY security_id ORDER BY date
                    ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) /
                  NULLIF(VAR_SAMP(market_return) OVER (PARTITION BY security_id ORDER BY date
                    ROWS BETWEEN 251 PRECEDING AND CURRENT ROW), 0) AS beta_252d
                FROM with_factors
              ), with_shares AS (
                SELECT r.*, sh.value AS shares_outstanding
                FROM rolling r ASOF LEFT JOIN shares sh
                  ON r.cik=sh.cik AND CAST(r.available_at AS TIMESTAMP) >= sh.available_at
              ), with_equity AS (
                SELECT r.*, eq.value AS stockholders_equity
                FROM with_shares r ASOF LEFT JOIN equity eq
                  ON r.cik=eq.cik AND CAST(r.available_at AS TIMESTAMP) >= eq.available_at
              )
              SELECT security_id, date, available_at, sector_id,
                     close * shares_outstanding AS market_cap,
                     stockholders_equity / NULLIF(close * shares_outstanding, 0) AS book_to_market,
                     beta_252d, realized_volatility_20d,
                     prior_return_5d, prior_return_20d, prior_return_60d
              FROM with_equity
            ) TO {} (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 250000)
            """.format(
                _quote(price_staging_path),
                _quote(factor_path),
                _quote(characteristics_staging_path),
            )
        )
        price_stats = connection.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT security_id), MIN(date), MAX(date),
                   COUNT(last_observed_total_return), COUNT(terminal_return)
            FROM read_parquet(?)
            """,
            [str(price_staging_path)],
        ).fetchone()
        characteristic_stats = connection.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT security_id), MIN(date), MAX(date),
                   COUNT(market_cap), COUNT(book_to_market), COUNT(beta_252d),
                   COUNT(realized_volatility_20d)
            FROM read_parquet(?)
            """,
            [str(characteristics_staging_path)],
        ).fetchone()
        build_succeeded = True
    finally:
        connection.close()
        database.unlink(missing_ok=True)
        if not build_succeeded:
            price_staging_path.unlink(missing_ok=True)
            characteristics_staging_path.unlink(missing_ok=True)

    # Publish only after both panels and their statistics complete.  A failed
    # rebuild therefore leaves the last authoritative pair untouched.
    price_staging_path.replace(price_path)
    characteristics_staging_path.replace(characteristics_path)

    price_manifest = {
        "schema_version": "caerus_pit_liquidity_panel_v3",
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "source_sep_manifest": str(manifest_path.relative_to(repo_root)),
        "source_sep_sha256": sha256_file(sep_path),
        "panel": str(price_path.relative_to(repo_root)),
        "panel_sha256": sha256_file(price_path),
        "row_count": int(price_stats[0]),
        "security_count": int(price_stats[1]),
        "date_range": [str(price_stats[2]), str(price_stats[3])],
        "last_observed_return_count": int(price_stats[4]),
        "verified_terminal_return_count": int(price_stats[5]),
        "return_method": "causal_index_from_provider_total_return_ratios_with_unadjusted_close_and_reconstructed_unadjusted_open",
        "delisting_method": (
            "unpopulated until settlement is independently verified; "
            "last observed provider return is retained separately"
        ),
        "terminal_settlement_certified": False,
        "trading_behavior_changed": False,
    }
    (output_root / "manifest.json").write_text(
        canonical_json(price_manifest) + "\n", encoding="utf-8"
    )
    characteristic_manifest = {
        "schema_version": "caerus_pit_characteristics_v1",
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "panel": str(characteristics_path.relative_to(repo_root)),
        "panel_sha256": sha256_file(characteristics_path),
        "row_count": int(characteristic_stats[0]),
        "security_count": int(characteristic_stats[1]),
        "date_range": [str(characteristic_stats[2]), str(characteristic_stats[3])],
        "market_cap_coverage": int(characteristic_stats[4]) / max(int(characteristic_stats[0]), 1),
        "book_to_market_coverage": int(characteristic_stats[5]) / max(int(characteristic_stats[0]), 1),
        "beta_coverage": int(characteristic_stats[6]) / max(int(characteristic_stats[0]), 1),
        "volatility_coverage": int(characteristic_stats[7]) / max(int(characteristic_stats[0]), 1),
        "market_cap_source": "SEC filing-time shares outstanding multiplied by raw Sharadar close",
        "trading_behavior_changed": False,
    }
    characteristics_path.with_name("pit_characteristics_manifest.json").write_text(
        canonical_json(characteristic_manifest) + "\n", encoding="utf-8"
    )
    certify_asset(
        repo_root=repo_root,
        asset=PIT_PRICES,
        data_files=(price_path,),
        pit_verified=False,
        methodology=price_manifest["return_method"],
        blockers=("delisting_settlement_payout_not_independently_verified",),
    )
    observed_price_blockers = []
    if int(price_stats[5]) != 0:
        observed_price_blockers.append(
            "unverified_terminal_return_values_present_in_observed_panel"
        )
    certify_asset(
        repo_root=repo_root,
        asset=PIT_OBSERVED_PRICES,
        data_files=(price_path,),
        pit_verified=not observed_price_blockers,
        methodology=(
            "Causal provider total-return ratios through the last observed "
            "trading day; no delisting settlement value is asserted, and "
            "terminal outcomes require the separately certified two-scenario "
            "sensitivity envelope"
        ),
        blockers=tuple(observed_price_blockers),
    )
    characteristic_blockers = []
    if characteristic_manifest["market_cap_coverage"] < 0.8:
        characteristic_blockers.append("market_cap_coverage_below_80pct")
    if characteristic_manifest["book_to_market_coverage"] < 0.6:
        characteristic_blockers.append("book_to_market_coverage_below_60pct")
    if characteristic_manifest["security_count"] < 1000:
        characteristic_blockers.append("security_coverage_below_1000")
    if characteristic_manifest["date_range"][0] > "2012-01-10":
        characteristic_blockers.append("history_starts_after_discovery_window")
    if characteristic_manifest["date_range"][1] < "2026-06-20":
        characteristic_blockers.append("history_ends_before_challenge_window")
    certify_asset(
        repo_root=repo_root,
        asset=PIT_CHARACTERISTICS,
        data_files=(characteristics_path,),
        pit_verified=not characteristic_blockers,
        methodology="SEC filing-time facts as-of joined to causal Sharadar price/liquidity features",
        blockers=tuple(characteristic_blockers),
    )
    return {
        "price_rows": int(price_stats[0]),
        "price_security_count": int(price_stats[1]),
        "characteristic_rows": int(characteristic_stats[0]),
        "market_cap_coverage": characteristic_manifest["market_cap_coverage"],
        "book_to_market_coverage": characteristic_manifest["book_to_market_coverage"],
        "price_panel_path": str(price_path),
        "characteristics_path": str(characteristics_path),
    }
