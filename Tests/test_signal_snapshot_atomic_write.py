from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from paper.signals_io import write_signals_snapshot


class SignalSnapshotAtomicWriteTest(unittest.TestCase):
    def test_partial_write_does_not_corrupt_existing_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir) / "signals"
            out_dir.mkdir(parents=True)
            snapshot_path = out_dir / "2026-04-10.json"
            original_payload = {
                "snapshot_date": "2026-04-10",
                "meta": {
                    "trade_date": "2026-04-10",
                    "asof_date": "2026-04-09",
                    "generated_at": "2026-04-10T16:00:00Z",
                },
                "signals": [{"ticker": "AAA", "target_weight": 1.0, "sleeve": "core", "raw_score": 1.0}],
            }
            snapshot_path.write_text(json.dumps(original_payload) + "\n", encoding="utf-8")

            df = pd.DataFrame(
                [
                    {"ticker": "AAA", "target_weight": 0.7, "sleeve": "core", "signal_strength": 0.7},
                    {"ticker": "BBB", "target_weight": 0.3, "sleeve": "core", "signal_strength": 0.3},
                ]
            )

            def _partial_dump(payload, handle, indent=None):
                handle.write('{"partial": true')
                handle.flush()
                raise RuntimeError("simulated serialization failure")

            with mock.patch("paper.signals_io.json.dump", side_effect=_partial_dump):
                with self.assertRaises(RuntimeError):
                    write_signals_snapshot(
                        df_targets=df,
                        run_date="2026-04-10",
                        asof_date="2026-04-09",
                        out_dir=str(out_dir),
                        sleeve_col="sleeve",
                    )

            self.assertEqual(json.loads(snapshot_path.read_text(encoding="utf-8")), original_payload)

            written_path = write_signals_snapshot(
                df_targets=df,
                run_date="2026-04-10",
                asof_date="2026-04-09",
                out_dir=str(out_dir),
                sleeve_col="sleeve",
            )
            written_payload = json.loads(Path(written_path).read_text(encoding="utf-8"))
            self.assertEqual(written_payload["signals"][0]["raw_score"], 0.7)
            self.assertEqual(written_payload["signals"][1]["raw_score"], 0.3)


if __name__ == "__main__":
    unittest.main()
