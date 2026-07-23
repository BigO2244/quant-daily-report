"""Immutable storage helpers for the forward options proxy lane."""

from __future__ import annotations

import hashlib
import json
import os
import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from projects.alpha_lab.factory import ResearchBoundaryError, canonical_json


RELATIVE_OUTPUT_ROOT = Path("outputs/research/alpha_lab/options_proxy_forward")
_FORBIDDEN_PARTS = frozenset(
    {"broker", "brokers", "cron", "deploy", "execution", "paper", "live", "runtime"}
)


def output_root(repo_root: Path) -> Path:
    root = Path(repo_root).expanduser().resolve()
    target = (root / RELATIVE_OUTPUT_ROOT).resolve()
    research_root = (root / "outputs/research").resolve()
    try:
        target.relative_to(research_root)
    except ValueError as exc:
        raise ResearchBoundaryError("options proxy output must remain under outputs/research") from exc
    return target


def require_research_path(path: Path, *, repo_root: Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    root = output_root(repo_root)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ResearchBoundaryError("path is outside the options proxy research root") from exc
    if not relative.parts:
        raise ResearchBoundaryError("path must identify an artifact inside the research root")
    lowered = {part.lower() for part in relative.parts}
    if lowered.intersection(_FORBIDDEN_PARTS):
        raise ResearchBoundaryError("path contains a forbidden runtime boundary")
    return resolved


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def immutable_json_bytes(payload: Any) -> bytes:
    return (canonical_json(payload) + "\n").encode("utf-8")


def immutable_jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join((canonical_json(row) + "\n").encode("utf-8") for row in rows)


def write_immutable_bytes(path: Path, payload: bytes, *, repo_root: Path) -> Path:
    target = require_research_path(path, repo_root=repo_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != payload:
            raise FileExistsError("immutable research artifact already exists with different bytes")
        return target
    temporary = target.with_name(".{}.tmp.{}".format(target.name, os.getpid()))
    if temporary.exists():
        temporary.unlink()
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(target))
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def write_immutable_json(path: Path, payload: Any, *, repo_root: Path) -> Path:
    return write_immutable_bytes(
        path,
        immutable_json_bytes(payload),
        repo_root=repo_root,
    )


def write_immutable_jsonl(
    path: Path,
    rows: Iterable[Mapping[str, Any]],
    *,
    repo_root: Path,
) -> Path:
    return write_immutable_bytes(
        path,
        immutable_jsonl_bytes(rows),
        repo_root=repo_root,
    )


def read_json(path: Path, *, repo_root: Path) -> Dict[str, Any]:
    target = require_research_path(path, repo_root=repo_root)
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("research JSON artifact must contain an object")
    return raw


def read_jsonl(path: Path, *, repo_root: Path) -> List[Dict[str, Any]]:
    target = require_research_path(path, repo_root=repo_root)
    result = []
    for line_number, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError("JSONL line {} must contain an object".format(line_number))
        result.append(raw)
    return result


def previous_feature_artifact(
    *,
    repo_root: Path,
    as_of_date: str,
) -> Optional[Path]:
    root = output_root(repo_root) / "features"
    if not root.exists():
        return None
    candidates = []
    for path in root.glob("*/*/features.json"):
        try:
            artifact_date = path.relative_to(root).parts[0]
        except (ValueError, IndexError):
            continue
        if artifact_date < as_of_date:
            candidates.append((artifact_date, path))
    return max(candidates, default=(None, None))[1]


def evaluation_artifacts(repo_root: Path) -> List[Path]:
    root = output_root(repo_root) / "evaluations"
    if not root.exists():
        return []
    return sorted(root.glob("*/*/*/evaluation.json"))


def signal_artifacts(repo_root: Path) -> List[Path]:
    root = output_root(repo_root) / "signals"
    return sorted(root.glob("*/*/signal.json")) if root.exists() else []


def signal_artifact_for_date(
    repo_root: Path, as_of_date: str, minimum_source_coverage: float
) -> Optional[Path]:
    root = output_root(repo_root) / "signals" / as_of_date
    candidates = sorted(root.glob("*/signal.json")) if root.exists() else []
    for path in reversed(candidates):
        payload = read_json(path, repo_root=repo_root)
        if (
            payload.get("collection_window_status") == "DECISION_TIME_ELIGIBLE"
            and float(payload.get("source_coverage", 0.0)) >= minimum_source_coverage
        ):
            return path
    return None


def complete_evaluation_for_signal(repo_root: Path, signal_hash: str) -> Optional[Path]:
    for path in reversed(evaluation_artifacts(repo_root)):
        payload = read_json(path, repo_root=repo_root)
        if payload.get("signal_hash") == signal_hash and payload.get("status") == "MATURE_COMPLETE":
            return path
    return None


@contextmanager
def research_run_lock(repo_root: Path):
    """Prevent overlapping standalone research runs without touching any scheduler."""

    path = require_research_path(output_root(repo_root) / "locks" / "daily.lock", repo_root=repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another options proxy research run already holds the lock") from exc
        try:
            yield path
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
