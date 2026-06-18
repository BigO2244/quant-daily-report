from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from scripts.research.build_pit_liquidity_panel import build_panel


def test_build_pit_liquidity_panel_computes_adv(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    path = cache / "AAPL.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "open", "high", "low", "close", "closeadj", "volume"])
        writer.writeheader()
        writer.writerow({"date": "2024-01-02", "open": 10, "high": 11, "low": 9, "close": 10, "closeadj": 10, "volume": 100})
        writer.writerow({"date": "2024-01-03", "open": 20, "high": 21, "low": 19, "close": 20, "closeadj": 20, "volume": 300})

    manifest = build_panel(repo_root=tmp_path, cache_dir=cache, output_dir=tmp_path / "out")

    assert manifest["coverage"]["ticker_count"] == 1
    assert manifest["coverage"]["row_count"] == 2
    panel = pd.read_csv(manifest["panel_path"])
    last = panel.iloc[-1]
    assert last["dollar_volume"] == 6000
    assert last["ADV_20"] == 200
    assert last["dollar_ADV_20"] == 3500
    assert manifest["panel_sha256"]
