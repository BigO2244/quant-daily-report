"""Compatibility entrypoint for the trading audit tool."""

from scripts.research.trading_audit import *  # noqa: F401,F403
from scripts.research.trading_audit import main


if __name__ == "__main__":
    raise SystemExit(main())
