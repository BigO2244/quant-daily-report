from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest

from core.options_smoke_session import build_options_smoke_session


class DummyBroker:
    def __init__(self) -> None:
        self.orders: list[dict[str, object]] = []

    def submit_option_market_order(self, symbol: str, qty: float, side: str, client_order_id: str, tif: str = "day"):
        payload = {
            "id": f"{side}-{symbol}",
            "status": "accepted",
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "client_order_id": client_order_id,
            "tif": tif,
        }
        self.orders.append(payload)
        return payload


class OptionsSmokeSessionTests(unittest.TestCase):
    def test_open_pair_when_no_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "benchmark": "SPY",
                        "default_expiry": "2026-04-17",
                        "default_strike": 680.0,
                    }
                ),
                encoding="utf-8",
            )
            review = build_options_smoke_session(
                trade_date="2026-04-10",
                asof_date="2026-04-10",
                broker=DummyBroker(),
                account={"equity": "10000", "options_buying_power": "5000", "options_trading_level": 3},
                positions=[],
                policy_path=policy_path,
                state_root=Path(tmp) / "state",
                allow_submission=True,
            )
            self.assertEqual(review["action"], "open_pair")
            self.assertEqual(review["submitted_count"], 2)

    def test_hold_when_same_day_open_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "policy.json"
            policy_path.write_text(json.dumps({"benchmark": "SPY"}), encoding="utf-8")
            state_root = Path(tmp) / "state"
            state_root.mkdir(parents=True, exist_ok=True)
            (state_root / "options_smoke_session_state.json").write_text(
                json.dumps({"last_open_date": "2026-04-10"}),
                encoding="utf-8",
            )
            review = build_options_smoke_session(
                trade_date="2026-04-10",
                asof_date="2026-04-10",
                broker=DummyBroker(),
                account={"equity": "10000"},
                positions=[{"symbol": "SPY260417P00680000", "qty": "1", "asset_class": "us_option"}],
                policy_path=policy_path,
                state_root=state_root,
            )
            self.assertEqual(review["action"], "hold")
            self.assertEqual(review["submitted_count"], 0)


if __name__ == "__main__":
    unittest.main()
