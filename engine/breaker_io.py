# engine/breaker_io.py
import json
import os
from pathlib import Path

DEFAULT_STATE_PATH = "outputs/alpha_report/state.json"

def load_alpha_state(path: str | None = None) -> dict:
    p = Path(path or os.getenv("ALPHA_STATE_PATH", DEFAULT_STATE_PATH))
    if not p.exists():
        return {"ok": False, "path": str(p), "error": "missing"}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {"ok": True, "path": str(p), "data": data}
    except Exception as e:
        return {"ok": False, "path": str(p), "error": repr(e)}


def load_alpha_breaker_state(path: str | None = None) -> dict:
    if path is None:
        env_path = os.getenv("ALPHA_BREAKER_STATE_PATH")
        path = env_path if env_path else None
    return load_alpha_state(path)