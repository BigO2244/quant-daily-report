from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from datetime import datetime
from pathlib import Path


def generate_run_id(now: datetime | None = None) -> str:
    ts = (now or datetime.now().astimezone()).strftime("%Y-%m-%dT%H%M%S%z")
    suffix = secrets.token_hex(4)[:7]
    return f"{ts}_{suffix}"


def get_run_id() -> str:
    value = str(os.getenv("RUN_ID", "")).strip()
    if value:
        return value
    run_id = generate_run_id()
    os.environ["RUN_ID"] = run_id
    return run_id


def get_run_dir(run_id: str) -> Path:
    return Path("outputs") / "runs" / str(run_id)


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _write_temp_bytes(path: Path, data: bytes) -> Path:
    ensure_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return tmp_path


def safe_write_bytes(path: Path, data: bytes, allow_overwrite: bool = False) -> None:
    tmp_path = _write_temp_bytes(path, data)
    try:
        if allow_overwrite:
            os.replace(str(tmp_path), str(path))
            return
        try:
            os.link(str(tmp_path), str(path))
        except FileExistsError as exc:
            raise FileExistsError(f"Refusing to overwrite existing file: {path}") from exc
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def safe_write_text(
    path: Path,
    text: str,
    allow_overwrite: bool = False,
    encoding: str = "utf-8",
) -> None:
    safe_write_bytes(path, text.encode(encoding), allow_overwrite=allow_overwrite)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_latest_pointer(latest_path: Path, payload: dict) -> None:
    safe_write_text(
        latest_path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        allow_overwrite=True,
    )


def collect_manifest(run_root: Path) -> dict:
    files = []
    for path in sorted(p for p in run_root.rglob("*") if p.is_file()):
        rel = path.relative_to(run_root).as_posix()
        files.append(
            {
                "path": rel,
                "size": int(path.stat().st_size),
                "sha256": file_sha256(path),
            }
        )
    return {
        "root": str(run_root),
        "file_count": int(len(files)),
        "files": files,
    }
