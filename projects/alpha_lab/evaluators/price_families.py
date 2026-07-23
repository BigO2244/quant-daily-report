"""Bounded discovery evaluators for the three price-history research families.

The adapters deliberately use only certified Alpha Lab packets.  They never
import production strategy code, read the challenge window, or submit orders.
Terminal outcomes are reported under both frozen sensitivity scenarios.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from .regime_diagnostics import summarize_regime_observations


DISCOVERY_START = "2012-01-03"
DISCOVERY_END = "2018-12-31"
VALIDATION_START = "2019-01-01"
VALIDATION_END = "2024-12-31"


def _asset_path(packet: Mapping[str, Any], asset_id: str) -> Path:
    root = Path(str(packet["repo_root"])).expanduser().resolve()
    records = packet["assets"][asset_id]["files"]
    if not records:
        raise ValueError("certified asset has no files: {}".format(asset_id))
    path = (root / records[-1]["path"]).resolve()
    path.relative_to(root)
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _quote(path: Path) -> str:
    return "'{}'".format(str(path).replace("'", "''"))


def _connection(packet: Mapping[str, Any]):
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("duckdb is required for price-family evaluation") from exc
    root = Path(str(packet["repo_root"])).expanduser().resolve()
    temporary = root / "outputs/research/alpha_lab/shared/.evaluator_duckdb_tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    connection.execute("PRAGMA threads=1")
    connection.execute("PRAGMA memory_limit='900MB'")
    connection.execute("PRAGMA preserve_insertion_order=false")
    connection.execute(
        "PRAGMA temp_directory='{}'".format(
            str(temporary).replace("'", "''")
        )
    )
    return connection


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _iso_date(value: Any) -> str:
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value).split(" ", 1)[0]


def _regime(row: Mapping[str, Any]) -> str:
    market_20 = float(row.get("market_20") or 0.0)
    market_63 = float(row.get("market_63") or 0.0)
    vol_20 = float(row.get("market_vol_20") or 0.0)
    if market_20 <= -0.12:
        return "panic"
    if market_20 >= 0.05 and market_63 < 0:
        return "recovery"
    if vol_20 >= 0.25:
        return "high_vol"
    if market_63 >= 0.05:
        return "bull_trend"
    if market_63 <= -0.05:
        return "bear_trend"
    if vol_20 <= 0.12:
        return "low_vol"
    return "neutral"


def _summarize_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    annualization: int,
    one_way_cost_bps: float,
) -> Dict[str, Any]:
    records = [dict(row) for row in rows]
    phases: Dict[str, Any] = {}
    round_trip = 2.0 * one_way_cost_bps / 10000.0
    for phase in ("DISCOVERY", "VALIDATION"):
        selected = [row for row in records if row["sample_phase"] == phase]
        phase_result: Dict[str, Any] = {"observation_count": len(selected)}
        for scenario in ("pessimistic", "zero_incremental"):
            active = [
                float(row["candidate_{}_return".format(scenario)])
                - float(row["benchmark_{}_return".format(scenario)])
                - round_trip
                for row in selected
                if row["candidate_{}_return".format(scenario)] is not None
                and row["benchmark_{}_return".format(scenario)] is not None
            ]
            mean = sum(active) / len(active) if active else None
            if len(active) > 1:
                variance = sum((item - mean) ** 2 for item in active) / (
                    len(active) - 1
                )
                volatility = math.sqrt(variance)
                t_stat = mean / (volatility / math.sqrt(len(active))) if volatility else None
            else:
                volatility = None
                t_stat = None
            phase_result[scenario] = {
                "observation_count": len(active),
                "mean_active_return_after_costs": mean,
                "annualized_excess_return_after_costs": (
                    mean * annualization if mean is not None else None
                ),
                "active_return_volatility": volatility,
                "t_statistic": t_stat,
            }
        phases[phase] = phase_result

    validation_values = [
        phases["VALIDATION"][scenario]["annualized_excess_return_after_costs"]
        for scenario in ("pessimistic", "zero_incremental")
    ]
    finite_validation = [item for item in validation_values if item is not None]
    worst_case = min(finite_validation) if finite_validation else None

    regime_rows = []
    for row in records:
        if row["sample_phase"] != "VALIDATION":
            continue
        candidate = _finite(row.get("candidate_pessimistic_return"))
        benchmark = _finite(row.get("benchmark_pessimistic_return"))
        if candidate is None or benchmark is None:
            continue
        decision_date = _iso_date(row["decision_date"])
        end_date = _iso_date(row["return_end_date"])
        regime_rows.append(
            {
                "observation_id": "{}:{}".format(
                    row.get("variant_id", "variant"), decision_date
                ),
                "decision_at": decision_date + "T21:00:00+00:00",
                "regime_available_at": decision_date + "T21:00:00+00:00",
                "return_start_at": decision_date + "T21:00:01+00:00",
                "return_end_at": end_date + "T21:00:00+00:00",
                "regime": _regime(row),
                "candidate_return": candidate,
                "benchmark_return": benchmark,
            }
        )
    return {
        "phases": phases,
        "worst_case_validation_annualized_excess_return_after_costs": worst_case,
        "terminal_scenarios_reported": [
            "pessimistic_total_loss",
            "zero_incremental",
        ],
        "regime_diagnostics": summarize_regime_observations(regime_rows),
    }


def _ranked_price_variant(
    packet: Mapping[str, Any],
    *,
    variant_id: str,
    lookback: int,
    skip: int,
    hold: int,
    cadence: str,
    direction: float,
    one_way_cost_bps: float,
    volatility_scale: bool = False,
) -> Dict[str, Any]:
    price = _asset_path(packet, "pit_observed_prices_v1")
    characteristics = _asset_path(packet, "pit_characteristics_v1")
    factors = _asset_path(packet, "factor_panel_v1")
    terminal = _asset_path(packet, "terminal_return_sensitivity_v1")
    period = "week" if cadence == "WEEKLY" else "month"
    annualization = 52 if cadence == "WEEKLY" else 12
    connection = _connection(packet)
    try:
        rows = connection.execute(
            """
            WITH factor_base AS (
              SELECT date,
                     CASE WHEN ABS(MKT_RF) > 1 THEN MKT_RF/100.0 ELSE MKT_RF END AS mkt
              FROM read_csv_auto({factors}, header=true)
              WHERE date BETWEEN DATE '{start}' - INTERVAL 400 DAY
                             AND DATE '{end}'
            ), factor_roll AS (
              SELECT date,
                     EXP(SUM(LN(GREATEST(1.0+mkt,-0.999999)))
                       OVER (ORDER BY date ROWS BETWEEN {lookback} PRECEDING
                             AND {skip} PRECEDING))-1.0 AS market_signal,
                     EXP(SUM(LN(GREATEST(1.0+mkt,-0.999999)))
                       OVER (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW))-1.0
                       AS market_20,
                     EXP(SUM(LN(GREATEST(1.0+mkt,-0.999999)))
                       OVER (ORDER BY date ROWS BETWEEN 62 PRECEDING AND CURRENT ROW))-1.0
                       AS market_63,
                     STDDEV_SAMP(mkt) OVER
                       (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
                       * SQRT(252.0) AS market_vol_20
              FROM factor_base
            ), daily AS (
              SELECT p.security_id, p.date, p.close, p.dollar_ADV_20,
                     LAG(p.closeadj,{skip}) OVER w /
                       NULLIF(LAG(p.closeadj,{lookback}) OVER w,0)-1.0
                       AS raw_price_signal,
                     LEAD(p.closeadj,{hold}) OVER w / NULLIF(p.closeadj,0)-1.0
                       AS observed_forward_return,
                     LEAD(p.date,{hold}) OVER w AS observed_return_end,
                     ROW_NUMBER() OVER (
                       PARTITION BY p.security_id, DATE_TRUNC('{period}',p.date)
                       ORDER BY p.date DESC
                     ) AS period_rank,
                     1 AS materialized_marker
              FROM read_parquet({price}) p
              WHERE p.date BETWEEN DATE '{start}' - INTERVAL 400 DAY
                               AND DATE '{end}'
              WINDOW w AS (PARTITION BY p.security_id ORDER BY p.date)
            ), sampled AS (
              SELECT d.*,c.beta_252d,c.realized_volatility_20d,
                     {direction} * (
                       d.raw_price_signal
                       - COALESCE(c.beta_252d,1.0) * COALESCE(f.market_signal,0.0)
                     ) {volatility_scale} AS signal,
                     f.market_20,f.market_63,f.market_vol_20
              FROM daily d
              LEFT JOIN read_parquet({characteristics}) c
                ON d.security_id=c.security_id AND d.date=c.date
              LEFT JOIN factor_roll f ON d.date=f.date
              WHERE d.period_rank=1 AND d.date >= DATE '{start}'
                AND d.close >= 5.0 AND d.dollar_ADV_20 >= 1000000.0
            ), scored AS (
              SELECT d.*, t.last_observed_date,
                     CASE
                       WHEN observed_forward_return IS NOT NULL
                         AND observed_return_end <= DATE '{end}'
                         THEN observed_forward_return
                       WHEN t.last_observed_date > d.date
                         AND t.last_observed_date <= DATE '{end}'
                         AND t.last_observed_date <= d.date + INTERVAL 45 DAY
                         THEN -1.0
                     END AS pessimistic_return,
                     CASE
                       WHEN observed_forward_return IS NOT NULL
                         AND observed_return_end <= DATE '{end}'
                         THEN observed_forward_return
                       WHEN t.last_observed_date > d.date
                         AND t.last_observed_date <= DATE '{end}'
                         AND t.last_observed_date <= d.date + INTERVAL 45 DAY
                         THEN 0.0
                     END AS zero_incremental_return
              FROM sampled d
              LEFT JOIN read_parquet({terminal}) t USING (security_id)
              WHERE d.signal IS NOT NULL
            ), ranked AS (
              SELECT *, NTILE(5) OVER (PARTITION BY date ORDER BY signal) AS bucket
              FROM scored
              WHERE pessimistic_return IS NOT NULL
                AND zero_incremental_return IS NOT NULL
            )
            SELECT '{variant}' AS variant_id, date AS decision_date,
                   MAX(observed_return_end) AS return_end_date,
                   CASE WHEN date <= DATE '2018-12-31'
                        THEN 'DISCOVERY' ELSE 'VALIDATION' END AS sample_phase,
                   AVG(pessimistic_return) FILTER (WHERE bucket=5)
                     AS candidate_pessimistic_return,
                   AVG(pessimistic_return) AS benchmark_pessimistic_return,
                   AVG(zero_incremental_return) FILTER (WHERE bucket=5)
                     AS candidate_zero_incremental_return,
                   AVG(zero_incremental_return) AS benchmark_zero_incremental_return,
                   MAX(market_20) AS market_20, MAX(market_63) AS market_63,
                   MAX(market_vol_20) AS market_vol_20,
                   COUNT(*) FILTER (WHERE bucket=5) AS selected_count,
                   COUNT(*) AS eligible_count
            FROM ranked
            GROUP BY date
            HAVING COUNT(*) >= 50 AND COUNT(*) FILTER (WHERE bucket=5) >= 10
            ORDER BY date
            """.format(
                factors=_quote(factors),
                price=_quote(price),
                characteristics=_quote(characteristics),
                terminal=_quote(terminal),
                start=DISCOVERY_START,
                end=VALIDATION_END,
                lookback=lookback,
                skip=skip,
                hold=hold,
                period=period,
                direction=direction,
                volatility_scale=(
                    "/ NULLIF(c.realized_volatility_20d,0.0)"
                    if volatility_scale
                    else ""
                ),
                variant=variant_id,
            )
        ).fetchdf().to_dict("records")
    finally:
        connection.close()
    summary = _summarize_rows(
        rows, annualization=annualization, one_way_cost_bps=one_way_cost_bps
    )
    summary["variant_id"] = variant_id
    summary["cadence"] = cadence
    summary["lookback_sessions"] = lookback
    summary["skip_sessions"] = skip
    summary["holding_sessions"] = hold
    summary["volatility_scaled"] = volatility_scale
    return summary


def _seasonality_variant(
    packet: Mapping[str, Any], *, variant_id: str, target_month_offset: int
) -> Dict[str, Any]:
    price = _asset_path(packet, "pit_observed_prices_v1")
    factors = _asset_path(packet, "factor_panel_v1")
    terminal = _asset_path(packet, "terminal_return_sensitivity_v1")
    connection = _connection(packet)
    try:
        rows = connection.execute(
            """
            WITH month_ends AS (
              SELECT security_id,DATE_TRUNC('month',date) AS month_id,
                     MAX(date) AS date
              FROM read_parquet({price})
              WHERE date BETWEEN DATE '{start}' - INTERVAL 7 YEAR AND DATE '{end}'
              GROUP BY security_id,DATE_TRUNC('month',date)
            ), monthly_base AS (
              SELECT p.security_id,p.date,p.close,p.closeadj,p.dollar_ADV_20
              FROM read_parquet({price}) p
              JOIN month_ends m ON p.security_id=m.security_id AND p.date=m.date
            ), monthly AS (
              SELECT security_id,date,close,closeadj,dollar_ADV_20,
                     closeadj/LAG(closeadj) OVER
                       (PARTITION BY security_id ORDER BY date)-1.0 AS month_return,
                     LEAD(closeadj) OVER
                       (PARTITION BY security_id ORDER BY date)/NULLIF(closeadj,0)-1.0
                       AS observed_forward_return,
                     LEAD(date) OVER (PARTITION BY security_id ORDER BY date)
                       AS observed_return_end
              FROM monthly_base
            ), history AS (
              SELECT p.security_id,p.date,p.close,p.dollar_ADV_20,
                     p.observed_forward_return,p.observed_return_end,
                     AVG(h.month_return) AS signal,COUNT(h.month_return) AS history_count
              FROM monthly p JOIN monthly h
                ON p.security_id=h.security_id
               AND h.date < p.date
               AND h.date >= p.date - INTERVAL 6 YEAR
               AND EXTRACT(MONTH FROM h.date)=
                   EXTRACT(MONTH FROM p.date + INTERVAL {offset} MONTH)
              WHERE p.date BETWEEN DATE '{start}' AND DATE '{end}'
              GROUP BY p.security_id,p.date,p.close,p.dollar_ADV_20,
                       p.observed_forward_return,p.observed_return_end
              HAVING COUNT(h.month_return) >= 3
            ), factor_roll AS (
              SELECT date,
                     EXP(SUM(LN(GREATEST(1.0+
                       CASE WHEN ABS(MKT_RF)>1 THEN MKT_RF/100.0 ELSE MKT_RF END,
                       -0.999999))) OVER
                       (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW))-1.0
                       AS market_20,
                     EXP(SUM(LN(GREATEST(1.0+
                       CASE WHEN ABS(MKT_RF)>1 THEN MKT_RF/100.0 ELSE MKT_RF END,
                       -0.999999))) OVER
                       (ORDER BY date ROWS BETWEEN 62 PRECEDING AND CURRENT ROW))-1.0
                       AS market_63,
                     STDDEV_SAMP(CASE WHEN ABS(MKT_RF)>1 THEN MKT_RF/100.0 ELSE MKT_RF END)
                       OVER (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
                       *SQRT(252.0) AS market_vol_20
              FROM read_csv_auto({factors},header=true)
              WHERE date BETWEEN DATE '{start}' - INTERVAL 100 DAY AND DATE '{end}'
            ), scored AS (
              SELECT h.*,t.last_observed_date,f.market_20,f.market_63,f.market_vol_20,
                     CASE
                       WHEN observed_forward_return IS NOT NULL
                         AND observed_return_end <= DATE '{end}'
                         THEN observed_forward_return
                       WHEN t.last_observed_date > h.date
                         AND t.last_observed_date <= DATE '{end}'
                         AND t.last_observed_date <= h.date + INTERVAL 45 DAY
                         THEN -1.0
                     END AS pessimistic_return,
                     CASE
                       WHEN observed_forward_return IS NOT NULL
                         AND observed_return_end <= DATE '{end}'
                         THEN observed_forward_return
                       WHEN t.last_observed_date > h.date
                         AND t.last_observed_date <= DATE '{end}'
                         AND t.last_observed_date <= h.date + INTERVAL 45 DAY
                         THEN 0.0
                     END AS zero_incremental_return
              FROM history h
              LEFT JOIN read_parquet({terminal}) t USING(security_id)
              ASOF LEFT JOIN factor_roll f ON h.date>=f.date
              WHERE h.close>=5 AND h.dollar_ADV_20>=1000000
            ), ranked AS (
              SELECT *,NTILE(5) OVER(PARTITION BY date ORDER BY signal) AS bucket
              FROM scored
              WHERE pessimistic_return IS NOT NULL AND zero_incremental_return IS NOT NULL
            )
            SELECT '{variant}' AS variant_id,date AS decision_date,
                   MAX(observed_return_end) AS return_end_date,
                   CASE WHEN date<=DATE '2018-12-31'
                        THEN 'DISCOVERY' ELSE 'VALIDATION' END AS sample_phase,
                   AVG(pessimistic_return) FILTER(WHERE bucket=5)
                     AS candidate_pessimistic_return,
                   AVG(pessimistic_return) AS benchmark_pessimistic_return,
                   AVG(zero_incremental_return) FILTER(WHERE bucket=5)
                     AS candidate_zero_incremental_return,
                   AVG(zero_incremental_return) AS benchmark_zero_incremental_return,
                   MAX(market_20) AS market_20,MAX(market_63) AS market_63,
                   MAX(market_vol_20) AS market_vol_20
            FROM ranked GROUP BY date
            HAVING COUNT(*)>=50 AND COUNT(*) FILTER(WHERE bucket=5)>=10
            ORDER BY date
            """.format(
                price=_quote(price),
                factors=_quote(factors),
                terminal=_quote(terminal),
                start=DISCOVERY_START,
                end=VALIDATION_END,
                offset=target_month_offset,
                variant=variant_id,
            )
        ).fetchdf().to_dict("records")
    finally:
        connection.close()
    summary = _summarize_rows(rows, annualization=12, one_way_cost_bps=15.0)
    summary["variant_id"] = variant_id
    summary["target_month_offset"] = target_month_offset
    return summary


def evaluate_residual_momentum(packet: Dict[str, Any], *, phase: str) -> Dict[str, Any]:
    if phase != "DISCOVERY":
        raise ValueError("challenge access is not implemented by this adapter")
    variants = [
        _ranked_price_variant(
            packet,
            variant_id="residual_momentum_12_minus_1",
            lookback=252,
            skip=21,
            hold=21,
            cadence="MONTHLY",
            direction=1.0,
            one_way_cost_bps=15.0,
        ),
        _ranked_price_variant(
            packet,
            variant_id="residual_momentum_6_minus_1",
            lookback=126,
            skip=21,
            hold=21,
            cadence="MONTHLY",
            direction=1.0,
            one_way_cost_bps=15.0,
        ),
        _ranked_price_variant(
            packet,
            variant_id="residual_momentum_3_minus_1",
            lookback=63,
            skip=21,
            hold=21,
            cadence="MONTHLY",
            direction=1.0,
            one_way_cost_bps=15.0,
        ),
    ]
    return {
        "primary_metric_name": "worst_case_annualized_excess_return_after_costs",
        "primary_metric_value": variants[0][
            "worst_case_validation_annualized_excess_return_after_costs"
        ],
        "variant_count": 3,
        "variants": variants,
        "challenge_period_accessed": False,
        "terminal_settlement_certified": False,
        "alpha_claim_permitted": False,
        "orders_submitted": False,
    }


def evaluate_stock_specific_seasonality(
    packet: Dict[str, Any], *, phase: str
) -> Dict[str, Any]:
    if phase != "DISCOVERY":
        raise ValueError("challenge access is not implemented by this adapter")
    variants = [
        _seasonality_variant(
            packet,
            variant_id="same_calendar_month_five_year",
            target_month_offset=1,
        ),
        _seasonality_variant(
            packet,
            variant_id="adjacent_calendar_month_placebo",
            target_month_offset=2,
        ),
    ]
    return {
        "primary_metric_name": "worst_case_annualized_excess_return_after_costs",
        "primary_metric_value": variants[0][
            "worst_case_validation_annualized_excess_return_after_costs"
        ],
        "variant_count": 2,
        "variants": variants,
        "challenge_period_accessed": False,
        "terminal_settlement_certified": False,
        "alpha_claim_permitted": False,
        "orders_submitted": False,
    }


def evaluate_short_horizon_reversal(
    packet: Dict[str, Any], *, phase: str
) -> Dict[str, Any]:
    if phase != "DISCOVERY":
        raise ValueError("challenge access is not implemented by this adapter")
    variants = [
        _ranked_price_variant(
            packet,
            variant_id="five_day_residual_reversal",
            lookback=5,
            skip=0,
            hold=5,
            cadence="WEEKLY",
            direction=-1.0,
            one_way_cost_bps=25.0,
        ),
        _ranked_price_variant(
            packet,
            variant_id="twenty_day_residual_reversal",
            lookback=20,
            skip=0,
            hold=20,
            cadence="MONTHLY",
            direction=-1.0,
            one_way_cost_bps=25.0,
        ),
        _ranked_price_variant(
            packet,
            variant_id="five_day_volatility_scaled_reversal",
            lookback=5,
            skip=0,
            hold=5,
            cadence="WEEKLY",
            direction=-1.0,
            one_way_cost_bps=25.0,
            volatility_scale=True,
        ),
    ]
    return {
        "primary_metric_name": "worst_case_annualized_excess_return_after_costs",
        "primary_metric_value": variants[0][
            "worst_case_validation_annualized_excess_return_after_costs"
        ],
        "variant_count": 3,
        "variants": variants,
        "challenge_period_accessed": False,
        "terminal_settlement_certified": False,
        "alpha_claim_permitted": False,
        "orders_submitted": False,
    }
