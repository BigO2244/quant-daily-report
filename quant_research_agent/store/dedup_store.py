"""
Deduplication store.

Tracks item IDs that have already been ingested so re-runs never
surface the same item twice. Backed by a local JSON file.
Thread-safe for single-process use. No production imports.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).parent / "seen_ids.json"
_DATE_FMT = "%Y-%m-%dT%H:%M:%SZ"


class DedupStore:
    """
    Simple set-backed dedup store persisted as JSON.

    Schema:
    {
      "<item_id>": "<first_seen_utc_iso>",
      ...
    }
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH
        self._store: dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_seen(self, item_id: str) -> bool:
        """Return True if this item_id has been seen before."""
        return item_id in self._store

    def mark_seen(self, item_id: str) -> None:
        """Mark item_id as seen. Persists immediately."""
        if item_id not in self._store:
            self._store[item_id] = datetime.now(tz=timezone.utc).strftime(_DATE_FMT)
            self._save()

    def mark_seen_bulk(self, item_ids: list[str]) -> None:
        """Mark multiple IDs as seen in a single write."""
        now = datetime.now(tz=timezone.utc).strftime(_DATE_FMT)
        changed = False
        for item_id in item_ids:
            if item_id not in self._store:
                self._store[item_id] = now
                changed = True
        if changed:
            self._save()

    def filter_new(self, item_ids: list[str]) -> list[str]:
        """Return only the IDs from item_ids that have not been seen."""
        return [i for i in item_ids if i not in self._store]

    def __len__(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        return f"DedupStore(path={self._path}, entries={len(self._store)})"

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            logger.debug("DedupStore: no existing store at %s, starting fresh.", self._path)
            return
        try:
            with open(self._path, "r") as f:
                self._store = json.load(f)
            logger.debug("DedupStore: loaded %d entries from %s", len(self._store), self._path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("DedupStore: failed to load %s: %s — starting fresh.", self._path, exc)
            self._store = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(self._store, f, indent=2, sort_keys=True)
            tmp.replace(self._path)
        except OSError as exc:
            logger.error("DedupStore: failed to save %s: %s", self._path, exc)
            if tmp.exists():
                tmp.unlink(missing_ok=True)
