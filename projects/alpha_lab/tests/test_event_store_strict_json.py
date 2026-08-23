"""Adversarial JSON parsing tests for the append-only event store."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from projects.alpha_lab.factory import AppendOnlyJSONLEventStore
from projects.alpha_lab.factory.errors import EventStoreIntegrityError


NOW = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)


def _store_with_one_event(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    path = root / "events.jsonl"
    store = AppendOnlyJSONLEventStore(path, research_root=root)
    store.append(
        event_id="event-1",
        event_type="synthetic",
        occurred_at=NOW,
        recorded_at=NOW,
        payload={"value": 1},
    )
    return store, path


def test_event_store_rejects_duplicate_keys_before_contract_hashing(tmp_path):
    store, path = _store_with_one_event(tmp_path)
    line = path.read_text(encoding="utf-8")
    line = line.replace(
        '"event_id":"event-1"',
        '"event_id":"duplicate","event_id":"event-1"',
        1,
    )
    path.write_text(line, encoding="utf-8")
    with pytest.raises(EventStoreIntegrityError, match="duplicate JSON key"):
        store.read_all()


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_event_store_rejects_non_finite_json_numbers(tmp_path, constant):
    store, path = _store_with_one_event(tmp_path)
    line = path.read_text(encoding="utf-8").replace('"value":1', '"value":{}'.format(constant))
    path.write_text(line, encoding="utf-8")
    with pytest.raises(EventStoreIntegrityError, match="non-finite JSON number"):
        store.read_all()


def test_event_store_rejects_non_object_lines(tmp_path):
    store, path = _store_with_one_event(tmp_path)
    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(EventStoreIntegrityError, match="must contain a JSON object"):
        store.read_all()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda raw: {**raw, "unexpected": True},
        lambda raw: {**raw, "schema_version": "caerus_alpha_lab_event_v3"},
        lambda raw: {key: value for key, value in raw.items() if key != "payload_hash"},
    ],
)
def test_event_store_rejects_schema_surprises_before_hashing(tmp_path, mutation):
    store, path = _store_with_one_event(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(mutation(raw)) + "\n", encoding="utf-8")
    with pytest.raises(EventStoreIntegrityError, match="schema or fields"):
        store.read_all()


def test_event_store_rejects_non_object_payload_before_hashing(tmp_path):
    store, path = _store_with_one_event(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["payload"] = []
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    with pytest.raises(EventStoreIntegrityError, match="payload must be a JSON object"):
        store.read_all()

