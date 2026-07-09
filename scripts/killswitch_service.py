#!/usr/bin/env python3
"""Caerus live-pilot kill-switch service.

A tiny, dependency-free HTTP service that is the *server-side authority* for the
live-pilot kill switch. The dashboard button calls this; the button itself has no
power. Engaging writes ``CAERUS_LIVE_PILOT_KILL_SWITCH=1`` into the live-pilot env
file that ``scripts/cron_live_pilot_execute.sh`` reads at the top of every run:

    if [[ "${CAERUS_LIVE_PILOT_KILL_SWITCH:-0}" == "1" ]]; then
        write_gate_state_blocked "live_pilot_kill_switch_enabled"; exit

So engaging the switch guarantees the next scheduled live execution aborts
*before submitting any order*. It is a HALT (no new orders); it does NOT liquidate
existing positions — flattening is a deliberate, separate action (see README).

Design:
  * Binds to 127.0.0.1 only. Put it behind nginx (basic auth + TLS) — see
    deploy/caerus-killswitch.nginx. Never expose this port publicly.
  * POST endpoints require a bearer token (CAERUS_KILLSWITCH_TOKEN) as a second
    factor on top of nginx auth. Reads/writes the env file atomically.
  * Also mirrors state to <dashboard_dir>/kill_switch_state.json so the static
    dashboard can show the current state even without hitting the API.

Endpoints:
  GET  /api/killswitch              -> {"engaged": bool, "source": "...", "updated_at": iso}
  POST /api/killswitch/engage       -> sets flag=1   (body: {"confirm":"ENGAGE"})
  POST /api/killswitch/disengage    -> sets flag=0   (body: {"confirm":"DISENGAGE"})

Run:
  CAERUS_KILLSWITCH_TOKEN=... python3 scripts/killswitch_service.py \
      --env-file ~/.caerus/live_pilot.env \
      --state-file /var/www/caerus-dashboard/kill_switch_state.json \
      --host 127.0.0.1 --port 8787
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FLAG = "CAERUS_LIVE_PILOT_KILL_SWITCH"
# Explicit "off" tokens. Read is fail-CLOSED to match core.live_pilot_guardrails:
# anything that is not an explicit off value (and not missing) is treated as
# ENGAGED, so the dashboard can never under-report a halt.
OFF_VALUES = {"0", "false", "no", "off", ""}
_WRITE_LOCK = threading.Lock()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _strip_export(key: str) -> str:
    key = key.strip()
    return key[len("export "):].strip() if key.startswith("export ") else key


def read_env_flag(env_file: Path) -> bool:
    """Return True if the kill switch is currently engaged in the env file.

    Fail-closed: a present-but-unrecognized value is treated as ENGAGED.
    A missing flag (or explicit off value) is treated as not engaged, matching
    the cron default ``${CAERUS_LIVE_PILOT_KILL_SWITCH:-0}``.
    """
    if not env_file.exists():
        return False
    # tolerant decode so a garbled env file cannot crash the safety control
    text = env_file.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        if _strip_export(key) == FLAG:
            return val.strip().strip('"').strip("'").lower() not in OFF_VALUES
    return False


def write_env_flag(env_file: Path, engaged: bool) -> None:
    """Set/replace the kill-switch line in the env file, atomically, preserving others."""
    with _WRITE_LOCK:
        env_file.parent.mkdir(parents=True, exist_ok=True)
        lines = (env_file.read_text(encoding="utf-8", errors="replace").splitlines()
                 if env_file.exists() else [])
        out, found = [], False
        for line in lines:
            stripped = line.strip()
            key = _strip_export(stripped.split("=", 1)[0]) if "=" in stripped else ""
            if key == FLAG:
                out.append(f"{FLAG}={'1' if engaged else '0'}")  # cron checks == "1"
                found = True
            else:
                out.append(line)
        if not found:
            out.append(f"{FLAG}={'1' if engaged else '0'}")
        fd, tmp = tempfile.mkstemp(dir=str(env_file.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        os.replace(tmp, env_file)
        os.chmod(env_file, 0o600)


def mirror_state(state_file: Path | None, engaged: bool) -> None:
    if not state_file:
        return
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(
            {"engaged": engaged, "flag": FLAG, "updated_at": _now(), "source": "killswitch_service"},
            indent=2), encoding="utf-8")
    except Exception:
        pass  # mirror is best-effort; env file is the source of truth


class Handler(BaseHTTPRequestHandler):
    env_file: Path = Path.home() / ".caerus" / "live_pilot.env"
    state_file: Path | None = None
    token: str | None = None

    def log_message(self, fmt, *args):  # quieter logs
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        # Mandatory bearer token for state changes. This is what defeats the
        # localhost-CSRF vector: a cross-origin browser fetch cannot set the
        # Authorization header, and a simple/no-cors POST therefore fails here.
        if not self.token:
            return False
        got = self.headers.get("Authorization", "")
        return got == f"Bearer {self.token}"

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""  # always drain the body
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def do_GET(self):
        if self.path.split("?")[0].rstrip("/") == "/api/killswitch":
            try:
                engaged = read_env_flag(self.env_file)
            except Exception as exc:
                return self._json(200, {"engaged": None, "source": "env_file",
                                        "flag": FLAG, "error": str(exc), "updated_at": _now()})
            mirror_state(self.state_file, engaged)
            return self._json(200, {"engaged": engaged, "source": "env_file",
                                    "flag": FLAG, "updated_at": _now()})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        if path == "/api/killswitch/engage":
            target, word = True, "ENGAGE"
        elif path == "/api/killswitch/disengage":
            target, word = False, "DISENGAGE"
        else:
            return self._json(404, {"error": "not found"})
        body = self._read_json_body()  # drain socket regardless of auth outcome
        if not self._authed():
            # includes the token-unset case: refuse state changes rather than
            # fail open and let anything re-arm live trading.
            return self._json(401, {"error": "unauthorized (bearer token required)"})
        if str(body.get("confirm", "")).strip().upper() != word:
            return self._json(400, {"error": f"confirmation required: body.confirm must equal '{word}'"})
        try:
            write_env_flag(self.env_file, target)
            mirror_state(self.state_file, target)
        except Exception as exc:
            return self._json(500, {"error": f"write failed: {exc}"})
        return self._json(200, {"engaged": target, "flag": FLAG, "updated_at": _now(),
                                "note": "HALT only — existing positions not liquidated"})


def main() -> int:
    ap = argparse.ArgumentParser(description="Caerus live-pilot kill-switch service")
    ap.add_argument("--env-file", default=str(Path.home() / ".caerus" / "live_pilot.env"))
    ap.add_argument("--state-file", default=None,
                    help="Optional JSON state mirror for the static dashboard "
                         "(e.g. /var/www/caerus-dashboard/kill_switch_state.json)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()

    Handler.env_file = Path(args.env_file).expanduser()
    Handler.state_file = Path(args.state_file).expanduser() if args.state_file else None
    Handler.token = os.environ.get("CAERUS_KILLSWITCH_TOKEN") or None
    if not Handler.token:
        print("WARNING: CAERUS_KILLSWITCH_TOKEN unset — engage/disengage POSTs are "
              "DISABLED (401). The service is read-only until a token is set and "
              "injected by nginx (see deploy/caerus-killswitch.nginx).")

    # Seed the mirror on startup so the dashboard has state immediately.
    mirror_state(Handler.state_file, read_env_flag(Handler.env_file))

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"kill-switch service on http://{args.host}:{args.port} (env={Handler.env_file})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
