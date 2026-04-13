from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest

from core.options_execution import build_option_symbol, write_options_execution_review


class DummyBroker:
    def __init__(self) -> None:
        self.submitted: list[dict[str, object]] = []

    def submit_option_market_order(self, symbol: str, qty: float, side: str, client_order_id: str, tif: str = "day"):
        payload = {
            "id": "opt-order-1",
            "status": "accepted",
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "client_order_id": client_order_id,
            "tif": tif,
        }
        self.submitted.append(payload)
        return payload


class OptionsExecutionTests(unittest.TestCase):
    def test_build_option_symbol_occ_format(self) -> None:
        self.assertEqual(build_option_symbol(underlying="SPY", expiry="2026-04-17", option_type="put", strike=480.0), "SPY   260417P00480000")

    def test_writes_dry_run_review_for_ready_paper_lane(self) -> None:
        paper_review = {
            "paper_review_status": "READY_FOR_PAPER_REVIEW",
            "paper_ready": True,
            "allocator_review_status": "ready",
            "paper_plan": {
                "strategy": "protective_put",
                "contracts_recommended": 1,
                "expiry": "2026-04-17",
                "target_dte": 35,
                "long_put": {"strike": 480.0, "kind": "PUT"},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            review = write_options_execution_review(
                run_root=Path(tmp) / "run",
                output_dir=Path(tmp) / "out",
                trade_date="2026-04-10",
                asof_date="2026-04-10",
                paper_review=paper_review,
                allow_live_submission=False,
            )
            self.assertEqual(review["execution_status"], "READY_FOR_LIVE_REVIEW")
            self.assertFalse(review["submission"]["attempted"])
            self.assertFalse(review["submission"]["submitted"])
            self.assertTrue((Path(tmp) / "run" / "options_execution_review.json").exists())

    def test_submits_when_enabled(self) -> None:
        paper_review = {
            "paper_review_status": "READY_FOR_PAPER_REVIEW",
            "paper_ready": True,
            "allocator_review_status": "ready",
            "paper_plan": {
                "strategy": "protective_put",
                "contracts_recommended": 1,
                "expiry": "2026-04-17",
                "target_dte": 35,
                "long_put": {"strike": 480.0, "kind": "PUT"},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            broker = DummyBroker()
            policy_path = Path(tmp) / "options_execution_policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "allow_live_submission": True,
                        "max_contracts": 1,
                        "allowed_strategies": ["protective_put"],
                    }
                ),
                encoding="utf-8",
            )
            review = write_options_execution_review(
                run_root=Path(tmp) / "run",
                output_dir=Path(tmp) / "out",
                trade_date="2026-04-10",
                asof_date="2026-04-10",
                paper_review=paper_review,
                allow_live_submission=True,
                broker=broker,
                policy_path=policy_path,
            )
            self.assertEqual(review["execution_status"], "SUBMITTED")
            self.assertTrue(review["submission"]["attempted"])
            self.assertTrue(review["submission"]["submitted"])
            self.assertEqual(len(broker.submitted), 1)


if __name__ == "__main__":
    unittest.main()
