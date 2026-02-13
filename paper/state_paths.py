from __future__ import annotations

import os
import shutil
from pathlib import Path

DEFAULT_PAPER_STATE_DIR = Path("outputs/paper_state")
LEDGER_FILENAME = "ledger.csv"
TRADES_FILENAME = "trades.csv"

LEDGER_HEADERS = [
    "date",
    "ticker",
    "sleeve",
    "shares",
    "price",
    "market_value",
    "cash",
    "total_equity",
]
TRADES_HEADERS = [
    "date",
    "ticker",
    "side",
    "shares",
    "price",
    "slippage_cost",
    "notional",
    "reason",
]


def get_paper_state_dir() -> Path:
    raw = os.getenv("PAPER_STATE_DIR", "").strip()
    return Path(raw) if raw else DEFAULT_PAPER_STATE_DIR


def _initialize_csv(path: Path, headers: list[str], seed_candidates: list[Path]) -> None:
    if path.exists():
        return

    for seed in seed_candidates:
        if not seed.exists():
            continue
        if seed.stat().st_size > 0:
            shutil.copyfile(seed, path)
            return

    path.write_text(",".join(headers) + "\n", encoding="utf-8")


def ensure_paper_state_files() -> tuple[str, str]:
    state_dir = get_paper_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)

    ledger_path = state_dir / LEDGER_FILENAME
    trades_path = state_dir / TRADES_FILENAME

    _initialize_csv(
        ledger_path,
        LEDGER_HEADERS,
        [
            Path("paper/ledger.csv"),
            Path("paper/ledger.template.csv"),
        ],
    )
    _initialize_csv(
        trades_path,
        TRADES_HEADERS,
        [
            Path("paper/trades.csv"),
            Path("paper/trades.template.csv"),
        ],
    )

    return str(ledger_path), str(trades_path)
