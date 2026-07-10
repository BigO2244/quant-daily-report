"""Regression: client_order_id must stay UNIQUE per order (2026-07-10 live incident).

Naive f"caerus-live-pilot-{run_id}-{seq}-{symbol}"[:48] collided because the prefix
+ run_id already fills 48 chars, so all orders in a run shared one id and the broker
rejected all but the first with "client_order_id must be unique". Both generators now
route through a hash-collapsing helper.
"""
from __future__ import annotations

from brokers.alpaca_broker import alpaca_client_order_id
from core.live_pilot_guardrails import _bounded_client_order_id

# A realistic (long) live run id, like the one in the incident.
RUN_ID = "2026-07-10T20260710T100930-0400"
ORDERS = [(1, "ABBV"), (2, "ALL"), (3, "C")]


def _raw(run_id, seq, sym):
    return f"caerus-live-pilot-{run_id}-{seq}-{sym}".lower()


def test_naive_truncation_would_collide():
    # Documents the bug: plain [:48] on these inputs produces duplicates.
    naive = {_raw(RUN_ID, s, sym)[:48] for s, sym in ORDERS}
    assert len(naive) == 1, "the old truncation collapsed all 3 ids into one"


def test_fixed_ids_are_unique_and_bounded():
    for gen in (alpaca_client_order_id, _bounded_client_order_id):
        ids = [gen(_raw(RUN_ID, s, sym)) for s, sym in ORDERS]
        assert len(set(ids)) == 3, f"{gen.__name__} must yield 3 distinct ids"
        assert all(len(x) <= 48 for x in ids), f"{gen.__name__} must stay <=48 chars"
        assert all(all(32 <= ord(c) <= 126 for c in x) for x in ids)


def test_core_helper_matches_broker_helper():
    for s, sym in ORDERS:
        raw = _raw(RUN_ID, s, sym)
        assert _bounded_client_order_id(raw) == alpaca_client_order_id(raw)


def test_short_ids_pass_through_unchanged():
    for gen in (alpaca_client_order_id, _bounded_client_order_id):
        assert gen("clp-abc-1-abbv") == "clp-abc-1-abbv"
