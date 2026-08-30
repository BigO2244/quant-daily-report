from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from projects.alpha_lab.evaluators.industry_earnings_diffusion import (
    _adverse_sensitivity,
    deterministic_cluster_inference,
    evaluate_primary_v1,
    student_t_cdf,
    student_t_ppf,
)
from projects.alpha_lab.experiments.run_hyp_2026_015_evaluator import (
    GENESIS_HASH,
    _append_event,
    _event_rows,
    _load_factors,
    _read_events,
    _reserve_trial,
    _trial_outcome,
)
from projects.alpha_lab.factory.errors import (
    ContractValidationError,
    ResearchBoundaryError,
)


def _business_dates(start: date, count: int) -> list[date]:
    values = []
    current = start
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return values


def _synthetic_trial(
    event_count: int,
    *,
    contamination_reversal: bool = False,
) -> tuple[list[dict], list[dict], list[dict]]:
    sessions = _business_dates(date(2018, 10, 1), 1700)
    event_indices = [70 + ordinal * 10 for ordinal in range(event_count)]
    events: list[dict] = []
    prices: list[dict] = []
    seen_rows: set[tuple[str, str]] = set()

    def add_price(
        security_id: str,
        session: date,
        *,
        closeadj: float,
        open_price: float | None = None,
        volume: float = 100.0,
    ) -> None:
        key = (security_id, session.isoformat())
        if key in seen_rows:
            return
        seen_rows.add(key)
        prices.append(
            {
                "security_id": security_id,
                "date": session.isoformat(),
                "open": closeadj if open_price is None else open_price,
                "close": closeadj,
                "closeadj": closeadj,
                "volume": volume,
                "dollar_ADV_20": 100_000_000.0,
            }
        )

    for ordinal, event_index in enumerate(event_indices):
        reaction = sessions[event_index]
        if reaction.year < 2019 or reaction.year > 2024:
            raise AssertionError("synthetic validation date escaped 2019-2024")
        entry = sessions[event_index + 1]
        exit_session = sessions[event_index + 5]
        sic4 = "{:04d}".format(1000 + (ordinal % 20))
        reporter = "R{:03d}".format(ordinal)
        peers = ["P{:03d}-{}".format(ordinal, item) for item in range(3)]
        controls = ["C{:03d}-{}".format(ordinal, item) for item in range(3)]
        events.append(
            {
                "reaction_session": reaction.isoformat(),
                "entry_session": entry.isoformat(),
                "exit_session": exit_session.isoformat(),
                "sic4": sic4,
                "sic2": sic4[:2],
                "reporter_security_id": reporter,
                "reporter_cik": "{:010d}".format(ordinal + 1),
                "accession": "{:010d}-26-{:06d}".format(ordinal + 1, ordinal + 1),
                "peer_security_ids": peers,
                "industry_control_security_ids": controls,
                "peer_report_during_hold_security_ids": (
                    [peers[0]] if contamination_reversal else []
                ),
            }
        )
        needed = [reporter, *peers, *controls]
        for security_id in needed:
            for prior_index in range(event_index - 20, event_index):
                add_price(security_id, sessions[prior_index], closeadj=100.0)
            reaction_close = 106.0 if security_id == reporter else 100.0
            add_price(
                security_id,
                reaction,
                closeadj=reaction_close,
                volume=300.0 if security_id == reporter else 100.0,
            )
            add_price(security_id, entry, closeadj=reaction_close, open_price=100.0)
            for hold_index in range(event_index + 2, event_index + 5):
                add_price(security_id, sessions[hold_index], closeadj=100.0)
            if security_id in peers:
                if contamination_reversal:
                    terminal = 120.0 if security_id == peers[0] else 95.0
                else:
                    terminal = 102.0
            elif security_id in controls:
                terminal = 100.0
            else:
                terminal = 100.0
            add_price(security_id, exit_session, closeadj=terminal)
    factors = [
        {
            "date": session.isoformat(),
            "MKT_RF": ((index % 11) - 5) / 10000.0,
            "SMB": ((index % 13) - 6) / 12000.0,
            "HML": ((index % 17) - 8) / 14000.0,
            "RMW": ((index % 19) - 9) / 16000.0,
            "CMA": ((index % 23) - 11) / 18000.0,
            "UMD": ((index % 29) - 14) / 20000.0,
        }
        for index, session in enumerate(sessions)
        if session < date(2025, 1, 1)
    ]
    return events, prices, factors


class Hyp015EvaluatorTests(unittest.TestCase):
    def test_factor_reader_stops_before_decoding_challenge_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "factors.csv"
            path.write_bytes(
                b"date,MKT_RF,SMB,HML,RMW,CMA,UMD\n"
                b"2024-12-31,0.1,0.2,0.3,0.4,0.5,0.6\n"
                b"2025-01-02,DO_NOT_DECODE,DO_NOT_DECODE,DO_NOT_DECODE,"
                b"DO_NOT_DECODE,DO_NOT_DECODE,DO_NOT_DECODE\n"
            )
            rows = _load_factors(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["date"], "2024-12-31")

    def test_student_t_helpers_match_known_quantile(self) -> None:
        self.assertAlmostEqual(student_t_cdf(0.0, 10), 0.5, places=12)
        self.assertAlmostEqual(student_t_ppf(0.90, 10), 1.372183641, places=7)

    def test_primary_v1_passes_complete_synthetic_validation(self) -> None:
        events, prices, factors = _synthetic_trial(150)
        result = evaluate_primary_v1(events, prices, factors)
        self.assertEqual(result["variant_count"], 1)
        self.assertEqual(result["breadth"]["independent_unit_count"], 150)
        self.assertEqual(result["breadth"]["unique_peer_count"], 450)
        self.assertEqual(result["breadth"]["unique_sic4_count"], 20)
        self.assertAlmostEqual(result["primary_metric_value"], 0.02, places=10)
        self.assertTrue(result["primary_inference"]["holm_reject_at_0_10"])
        self.assertTrue(result["primary_validation_pass"])
        self.assertFalse(result["challenge_period_accessed"])
        decay = result["validation"]["mean_active_return_decay"]
        self.assertAlmostEqual(decay["one_session"], 0.0)
        self.assertAlmostEqual(decay["three_session"], 0.0)
        self.assertAlmostEqual(decay["five_session"], 0.02)
        self.assertFalse(
            result["secondary_diagnostic_completeness"][
                "complete_alpha_card_eligible"
            ]
        )

    def test_short_raw_momentum_pool_leaves_frozen_cash_slots(self) -> None:
        events, prices, factors = _synthetic_trial(1)
        events[0]["industry_control_security_ids"] = events[0][
            "industry_control_security_ids"
        ][:1]
        result = evaluate_primary_v1(events, prices, factors)
        cluster = result["event_cluster_results"][0]
        self.assertAlmostEqual(cluster["raw_momentum_base_net_return"], -0.001)

    def test_peer_report_removal_sign_reversal_blocks_positive_result(self) -> None:
        events, prices, factors = _synthetic_trial(3, contamination_reversal=True)
        result = evaluate_primary_v1(events, prices, factors)
        diagnostic = result["peer_report_contamination"]
        self.assertTrue(diagnostic["validation_active_return_sign_reversal"])
        self.assertFalse(diagnostic["positive_classification_gate_pass"])
        self.assertFalse(result["primary_validation_pass"])

    def test_challenge_event_is_rejected_before_market_access(self) -> None:
        event = {
            "reaction_session": "2025-01-02",
            "entry_session": "2025-01-03",
            "exit_session": "2025-01-09",
            "sic4": "3571",
            "sic2": "35",
            "reporter_security_id": "R",
            "reporter_cik": "0000000001",
            "accession": "0000000001-25-000001",
            "peer_security_ids": ["P1", "P2", "P3"],
            "industry_control_security_ids": ["C1"],
        }
        with self.assertRaisesRegex(
            ContractValidationError, "challenge-period event access"
        ):
            evaluate_primary_v1([event], [], [])

    def test_adverse_sensitivity_recomputes_concentration(self) -> None:
        clusters = [
            {
                "event_cluster_id": "E{}".format(item),
                "reporter_set_id": "R{}".format(item),
                "reporter_security_ids": ["R{}".format(item)],
                "sic4": "{:04d}".format(1000 + item % 20),
                "reaction_quarter": "{}Q{}".format(2019 + item % 6, item % 4 + 1),
                "calendar_year": str(2019 + item % 6),
                "base_active_return": 0.02,
                "stress_active_return": 0.02,
            }
            for item in range(150)
        ]
        exclusions = [
            {
                "adverse_sensitivity_key": "X{}".format(item),
                "year": "2019",
                "sic": "1000",
                "issuer_cik": "0000009999",
                "adverse_sensitivity_eligible": True,
            }
            for item in range(100)
        ]
        result = _adverse_sensitivity(
            clusters,
            exclusions,
            included_breadth_pass=True,
            included_capacity_pass=True,
            included_concentration_pass=True,
            included_reporter_concentration_pass=True,
        )
        self.assertFalse(result["adverse_concentration_pass"])
        self.assertFalse(result["pass"])

    def test_local_event_chain_uses_zero_genesis_and_detects_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            first = _append_event(
                path,
                event_id="one",
                event_type="preoutcome_registration_sealed",
                occurred_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
                payload={"outcome_data_accessed": False},
            )
            second = _append_event(
                path,
                event_id="two",
                event_type="outcome_access_started",
                occurred_at=datetime(2026, 8, 30, 0, 1, tzinfo=timezone.utc),
                payload={"challenge_accessed": False},
            )
            self.assertEqual(first["previous_event_hash"], GENESIS_HASH)
            self.assertEqual(second["previous_event_hash"], first["event_hash"])
            self.assertEqual(len(_read_events(path)), 2)
            rows = path.read_text(encoding="utf-8").splitlines()
            tampered = json.loads(rows[1])
            tampered["payload"]["challenge_accessed"] = True
            path.write_text(rows[0] + "\n" + json.dumps(tampered) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "hash mismatch"):
                _read_events(path)

    def test_trial_reservation_is_global_and_same_run_idempotent(self) -> None:
        timestamp = datetime(2026, 8, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _reserve_trial(
                root,
                run_id="RUN-A",
                registration_hash="a" * 64,
                created_at=timestamp,
            )
            second = _reserve_trial(
                root,
                run_id="RUN-A",
                registration_hash="a" * 64,
                created_at=timestamp,
            )
            self.assertEqual(first, second)
            with self.assertRaisesRegex(ResearchBoundaryError, "already reserved"):
                _reserve_trial(
                    root,
                    run_id="RUN-B",
                    registration_hash="b" * 64,
                    created_at=timestamp,
                )

    def test_gate_adapter_requires_peer_report_diagnostic(self) -> None:
        row = {
            "reaction_session": "2024-01-02",
            "entry_session": "2024-01-03",
            "exit_session": "2024-01-09",
            "four_digit_sic": "3571",
            "reporters": [
                {
                    "security_id": "R",
                    "cik": "0000000001",
                    "accession": "0000000001-24-000001",
                }
            ],
            "included_peer_security_ids": ["P1", "P2", "P3"],
            "industry_control_security_ids": ["C1"],
        }
        with self.assertRaisesRegex(
            ContractValidationError, "peer-report-during-hold"
        ):
            _event_rows([row])
        row["peer_report_during_hold_security_ids"] = []
        row["terminal_outcome_required"] = True
        with self.assertRaisesRegex(
            ContractValidationError, "terminal_outcome_required=false"
        ):
            _event_rows([row])

    def test_failed_structural_gate_is_inconclusive_not_negative(self) -> None:
        result = {
            "primary_validation_pass": False,
            "primary_metric_value": 0.01,
            "primary_inference": {"status": "INFERENCE_ELIGIBLE"},
            "breadth": {"pass": False},
            "capacity": {"primary_pair_pass_for_every_validation_cluster": True},
            "concentration": {"pass": True},
            "adverse_missingness_sensitivity": {"pass": True},
            "factor_industry_momentum_attribution": {"status": "EVALUATED"},
            "raw_momentum_comparison": {
                "candidate_minus_raw_momentum_inference": {"mean": 0.01}
            },
        }
        self.assertEqual(_trial_outcome(result), "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
