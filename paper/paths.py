from __future__ import annotations

import logging
from pathlib import Path

LEDGER_TRADES_PATH = Path("outputs/ledger/trades.csv")
LEDGER_TRADES_LEGACY_PATH = Path("outputs/ledger/trades_legacy.csv")


def ensure_no_legacy_ledger(*, logger: logging.Logger | None = None, when: str = "") -> None:
    if not LEDGER_TRADES_LEGACY_PATH.exists():
        return
    msg = (
        "FORBIDDEN: outputs/ledger/trades_legacy.csv exists. "
        "All ledger writes must go to outputs/ledger/trades.csv. "
        "Delete legacy file and re-run."
    )
    if logger is not None:
        suffix = f" when={when}" if when else ""
        logger.error(
            "[LEDGER][FATAL] legacy ledger path detected path=%s%s",
            str(LEDGER_TRADES_LEGACY_PATH),
            suffix,
        )
    raise RuntimeError(msg)
