#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PLACEHOLDER_TOKENS = ("<<", ">>", "PASTE", "YOUR_", "INSERT", "REPLACE")
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _placeholder_flag(value: str) -> bool:
    upper = value.upper()
    return any(tok in upper for tok in PLACEHOLDER_TOKENS)


def _redacted_meta(value: str | None) -> dict:
    if value is None:
        return {
            "set": False,
            "length": 0,
            "tail4": "",
            "placeholder_tokens": False,
            "leading_space": False,
            "trailing_space": False,
        }
    return {
        "set": True,
        "length": len(value),
        "tail4": value[-4:] if len(value) >= 4 else value,
        "placeholder_tokens": _placeholder_flag(value),
        "leading_space": value != value.lstrip(),
        "trailing_space": value != value.rstrip(),
    }


def _print_env_diag() -> tuple[str | None, str | None, str | None, str | None]:
    env_order = [
        "ALPACA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
        "ALPACA_PAPER",
        "ALPACA_BASE_URL",
        "ALPACA_KEY_ID",
        "ALPACA_SECRET_KEY",
    ]
    print("[DIAG] Environment variable diagnostics (redacted):")
    for name in env_order:
        raw = os.getenv(name)
        m = _redacted_meta(raw)
        print(
            f"  - {name}: set={'YES' if m['set'] else 'NO'} "
            f"len={m['length']} tail4={m['tail4']!r} "
            f"placeholder_tokens={'YES' if m['placeholder_tokens'] else 'NO'} "
            f"leading_space={m['leading_space']} trailing_space={m['trailing_space']}"
        )

    key = os.getenv("ALPACA_API_KEY_ID") or os.getenv("ALPACA_KEY_ID")
    secret = os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
    paper = os.getenv("ALPACA_PAPER")
    base = os.getenv("ALPACA_BASE_URL")

    if base and base.rstrip().endswith("/v2"):
        print("[DIAG][WARN] ALPACA_BASE_URL ends with '/v2'. Recommended host-only base URL.")
    else:
        print("[DIAG] ALPACA_BASE_URL suffix check: OK (host-only or unset)")

    return key, secret, paper, base


def _curl_probe(key: str | None, secret: str | None) -> int:
    print("[DIAG] Running curl probe to paper /v2/account ...")
    out_path = Path("/tmp/alpaca_account.json")
    url = "https://paper-api.alpaca.markets/v2/account"
    if out_path.exists():
        out_path.unlink()

    cmd = [
        "curl",
        "-sS",
        "-o",
        str(out_path),
        "-w",
        "%{http_code}",
        "-H",
        f"APCA-API-KEY-ID: {key or ''}",
        "-H",
        f"APCA-API-SECRET-KEY: {secret or ''}",
        url,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception as exc:
        print(f"[DIAG][CURL][ERROR] curl invocation failed: {type(exc).__name__}: {exc}")
        return 2

    code = proc.stdout.strip() if proc.stdout else ""
    code_int = int(code) if code.isdigit() else 0
    print(f"[DIAG][CURL] HTTP code: {code or 'UNKNOWN'}")

    body = ""
    if out_path.exists():
        body = out_path.read_text(encoding="utf-8", errors="replace")

    if code_int == 200:
        try:
            payload = json.loads(body)
        except Exception as exc:
            print(f"[DIAG][CURL][WARN] 200 response but JSON parse failed: {exc}")
            return 0
        print(
            "[DIAG][CURL] account="
            f"id={payload.get('id')} status={payload.get('status')} "
            f"cash={payload.get('cash')} equity={payload.get('equity')}"
        )
    else:
        preview = body[:200].replace("\n", " ")
        print(f"[DIAG][CURL] body_preview={preview!r}")
        print(
            "[DIAG][CURL][GUIDE] 401 means keys are invalid for paper or malformed "
            "(placeholders/whitespace) or revoked."
        )
        print("[DIAG][CURL][GUIDE] keys likely wrong or wrong environment")

    return 0


def _adapter_probe() -> int:
    print("[DIAG] Running Python adapter probe (AlpacaBroker.from_env().get_account()) ...")
    try:
        from brokers.alpaca_broker import AlpacaBroker

        broker = AlpacaBroker.from_env()
        acct = broker.get_account()
        print(
            "[DIAG][PY] account="
            f"id={acct.get('id')} status={acct.get('status')} "
            f"cash={acct.get('cash')} equity={acct.get('equity')}"
        )
        print("[DIAG][PY] OK")
        return 0
    except Exception as exc:
        print(f"[DIAG][PY][ERROR] {type(exc).__name__}: {exc}")
        return 1


def main() -> int:
    key, secret, _paper, _base = _print_env_diag()
    _ = _curl_probe(key, secret)
    _ = _adapter_probe()
    return 0


if __name__ == "__main__":
    sys.exit(main())
