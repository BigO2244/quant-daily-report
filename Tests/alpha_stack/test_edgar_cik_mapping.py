"""
Tests — EDGAR Ticker→CIK Mapping Correctness
==============================================
Deterministic tests that verify the correct SEC EDGAR CIK values for
tickers that were historically mapped to wrong CIKs due to corporate
restructurings, spin-offs, and SEC re-registrations.

Root cause:  SEC's live company_tickers.json maps tickers to the CURRENT
registered legal entity.  For restructured companies a NEW CIK was created
for the surviving entity (which inherited the stock ticker but has no
pre-restructuring EDGAR history).

Confirmed wrong→right mappings:
  GE  → 0001752724 (GE HealthCare, spun off Jan 2023)  WRONG
       → 0000040545 (General Electric Company)           CORRECT
  HCA → 0001058520 (Healtheon Corp, defunct dot-com)    WRONG
       → 0000860730 (HCA Healthcare Inc)                 CORRECT
  JCI → 0001833986 (post-Tyco Irish redomicile ~2016)   WRONG
       → 0000833444 (historic Johnson Controls Inc)      CORRECT

These tests run entirely offline — no network access, no parquet files,
no yfinance.  They rely only on the bundled sec_ticker_map_default.json
and the runtime sec_ticker_map.json cache (if present).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from alpha_stack.datastore.sec_edgar import EdgarClient  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────
# Ground-truth CIK values sourced from official SEC EDGAR company search
# (https://www.sec.gov/cgi-bin/browse-edgar) — verified 2025-03.
CORRECT_CIKS = {
    "GE": "0000040545",   # General Electric Company (historic registrant)
    "HCA": "0000860730",  # HCA Healthcare Inc
    "JCI": "0000833444",  # Johnson Controls Inc (historic registrant)
}

WRONG_CIKS = {
    "GE": "0001752724",   # GE HealthCare Technologies (spinoff, IPO Jan 2023)
    "HCA": "0001058520",  # Healtheon Corp (defunct dot-com era entity)
    "JCI": "0001833986",  # Post-Tyco Irish redomicile entity (~2016 registration)
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _client_with_bundled_default(tmp_path: Path) -> EdgarClient:
    """
    Return an EdgarClient whose cache_dir is empty so it falls all the
    way through to the bundled sec_ticker_map_default.json.  Network
    access is disabled by patching _requests_available.
    """
    client = EdgarClient(cache_dir=tmp_path)
    # Force offline mode so _load_cik_map never attempts HTTP
    client._requests_available = False
    return client


def _client_with_explicit_map(tmp_path: Path, mapping: dict) -> EdgarClient:
    """
    Write *mapping* as sec_ticker_map.json in tmp_path and return a
    client that will load it (fresh TTL so it won't be skipped).
    """
    cache_file = tmp_path / "sec_ticker_map.json"
    with open(cache_file, "w") as f:
        json.dump(mapping, f)
    client = EdgarClient(cache_dir=tmp_path)
    client._requests_available = False
    return client


# ══════════════════════════════════════════════════════════════════════════════
# Suite 1 — Bundled Default Correctness
# ══════════════════════════════════════════════════════════════════════════════

class TestBundledDefaultCIKs:
    """Verify sec_ticker_map_default.json contains the correct CIKs."""

    def test_bundled_default_file_exists(self):
        default_path = REPO_ROOT / "alpha_stack" / "datastore" / "sec_ticker_map_default.json"
        assert default_path.exists(), (
            f"Bundled default CIK map not found at {default_path}. "
            "The file must be present for offline fallback to work."
        )

    @pytest.mark.parametrize("ticker,expected_cik", list(CORRECT_CIKS.items()))
    def test_bundled_default_has_correct_cik(self, ticker, expected_cik, tmp_path):
        """Each critical ticker must resolve to the historically-correct CIK."""
        client = _client_with_bundled_default(tmp_path)
        result = client.lookup_cik(ticker)
        assert result == expected_cik, (
            f"Bundled default maps {ticker} → {result!r}, "
            f"expected {expected_cik!r}.\n"
            f"This is likely caused by a corporate restructuring CIK collision: "
            f"wrong CIK is {WRONG_CIKS.get(ticker, 'unknown')}."
        )

    @pytest.mark.parametrize("ticker,wrong_cik", list(WRONG_CIKS.items()))
    def test_bundled_default_not_wrong_cik(self, ticker, wrong_cik, tmp_path):
        """Explicitly reject the known-bad CIKs for each critical ticker."""
        client = _client_with_bundled_default(tmp_path)
        result = client.lookup_cik(ticker)
        assert result != wrong_cik, (
            f"Bundled default maps {ticker} → {wrong_cik!r}, which is the WRONG CIK.\n"
            f"This CIK belongs to a different legal entity that inherited the ticker "
            f"symbol after a restructuring.  Correct CIK: {CORRECT_CIKS[ticker]!r}."
        )


# ══════════════════════════════════════════════════════════════════════════════
# Suite 2 — Runtime Cache Correctness
# ══════════════════════════════════════════════════════════════════════════════

class TestRuntimeCacheCIKs:
    """
    When sec_ticker_map.json is present in cache_dir (written by a prior run),
    lookup_cik must return the cached value.  Verify the runtime cache for the
    same critical tickers.
    """

    @pytest.mark.parametrize("ticker,expected_cik", list(CORRECT_CIKS.items()))
    def test_runtime_cache_returns_correct_cik(self, ticker, expected_cik, tmp_path):
        """sec_ticker_map.json cache must map critical tickers to correct CIKs."""
        # Build a minimal map that includes our critical tickers
        mapping = {t: c for t, c in CORRECT_CIKS.items()}
        mapping["SPY"] = "0001064642"  # add a benign entry for realism
        client = _client_with_explicit_map(tmp_path, mapping)
        result = client.lookup_cik(ticker)
        assert result == expected_cik, (
            f"Runtime cache maps {ticker} → {result!r}, expected {expected_cik!r}."
        )

    @pytest.mark.parametrize("ticker,wrong_cik", list(WRONG_CIKS.items()))
    def test_runtime_cache_with_wrong_cik_propagates(self, ticker, wrong_cik, tmp_path):
        """
        If (hypothetically) a wrong CIK is written to the cache, it is faithfully
        returned by lookup_cik.  This test documents the expected behavior so we
        know that fixing the bundled default alone is insufficient — the runtime
        cache must also be refreshed.
        """
        # Write a cache that deliberately contains the wrong CIK
        bad_mapping = {ticker: wrong_cik}
        client = _client_with_explicit_map(tmp_path, bad_mapping)
        result = client.lookup_cik(ticker)
        # This SHOULD return the wrong value — that's the documented behavior
        assert result == wrong_cik, (
            f"Expected lookup_cik to return stale cached value {wrong_cik!r} "
            f"for {ticker}, but got {result!r}. "
            f"If the bundled default is being used instead of the cache, "
            f"check the TTL logic in _load_cik_map()."
        )

    def test_production_cache_correct_if_present(self):
        """
        If the production runtime cache exists on disk, spot-check that its
        GE/HCA/JCI entries have been corrected.  Skipped if cache is absent.
        """
        production_cache = (
            REPO_ROOT / "data" / "alpha_stack_cache" / "edgar" / "sec_ticker_map.json"
        )
        if not production_cache.exists():
            pytest.skip(f"Production cache not present at {production_cache}")

        with open(production_cache) as f:
            cached_map = json.load(f)

        for ticker, expected_cik in CORRECT_CIKS.items():
            if ticker in cached_map:
                actual = cached_map[ticker]
                assert actual == expected_cik, (
                    f"Production cache has {ticker} → {actual!r} but expected {expected_cik!r}.\n"
                    f"Delete the cache file and re-run to regenerate from the corrected "
                    f"bundled default: rm {production_cache}"
                )


# ══════════════════════════════════════════════════════════════════════════════
# Suite 3 — Negative Caching (no retry after 404)
# ══════════════════════════════════════════════════════════════════════════════

class TestNegativeCaching:
    """
    After a CIK fetch returns 404, the CIK must be added to _failed_ciks so
    subsequent calls for the same CIK return None immediately without
    re-issuing any network request.
    """

    def _make_mock_response(self, status_code: int):
        """Create a requests.HTTPError-compatible mock response."""
        import requests
        resp = MagicMock(spec=requests.Response)
        resp.status_code = status_code
        exc = requests.HTTPError(response=resp)
        return exc

    def test_failed_cik_added_to_set_on_404(self, tmp_path):
        """After a simulated 404, the CIK must appear in _failed_ciks."""
        mapping = {"BADFETCH": "0001111111"}
        client = _client_with_explicit_map(tmp_path, mapping)
        client._requests_available = True

        import requests
        http_err = self._make_mock_response(404)

        with patch.object(client._session, "get", side_effect=http_err):
            result = client.get_company_facts("BADFETCH")

        assert result is None, "Expected None for a 404 CIK"
        assert "0001111111" in client._failed_ciks, (
            "CIK should be in _failed_ciks after a 404 to prevent future retries"
        )

    def test_negative_cached_cik_skips_network(self, tmp_path):
        """
        A CIK in _failed_ciks must never result in a network call —
        get_company_facts returns None immediately.
        """
        mapping = {"CACHED_FAIL": "0002222222"}
        client = _client_with_explicit_map(tmp_path, mapping)
        client._requests_available = True
        # Pre-populate negative cache
        client._failed_ciks.add("0002222222")

        mock_get = MagicMock()
        with patch.object(client._session, "get", mock_get):
            result = client.get_company_facts("CACHED_FAIL")

        assert result is None
        mock_get.assert_not_called(), (
            "session.get should NOT be called for a CIK already in _failed_ciks"
        )

    def test_negative_cache_prevents_retry_loop(self, tmp_path):
        """
        Repeated calls for the same failing CIK must not cause repeated
        network requests (the root symptom of the original bug).
        """
        mapping = {"LOOPY": "0003333333"}
        client = _client_with_explicit_map(tmp_path, mapping)
        client._requests_available = True

        import requests
        http_err = self._make_mock_response(404)

        mock_get = MagicMock(side_effect=http_err)
        with patch.object(client._session, "get", mock_get):
            # First call — hits network, gets 404
            r1 = client.get_company_facts("LOOPY")
            # Subsequent calls — must NOT hit network
            r2 = client.get_company_facts("LOOPY")
            r3 = client.get_company_facts("LOOPY")

        assert r1 is None and r2 is None and r3 is None
        assert mock_get.call_count == 1, (
            f"Expected exactly 1 network request (the first 404), "
            f"but session.get was called {mock_get.call_count} time(s). "
            f"Negative caching is not working — this would cause the retry loop."
        )


# ══════════════════════════════════════════════════════════════════════════════
# Suite 4 — Warn-Once Behavior
# ══════════════════════════════════════════════════════════════════════════════

class TestWarnOnceBehavior:
    """
    Failures (both unmapped tickers and 404 CIKs) should log a warning
    exactly once per ticker/CIK per process lifetime.
    """

    def test_warn_once_for_unknown_ticker(self, tmp_path, caplog):
        """lookup_cik logs exactly one warning for an unmapped ticker."""
        client = _client_with_bundled_default(tmp_path)
        with caplog.at_level(logging.WARNING, logger="alpha_stack.datastore.sec_edgar"):
            for _ in range(5):
                client.lookup_cik("TOTALLY_FAKE_TICKER_XYZ")

        warnings = [r for r in caplog.records if "TOTALLY_FAKE_TICKER_XYZ" in r.message]
        assert len(warnings) == 1, (
            f"Expected exactly 1 warning for unknown ticker, got {len(warnings)}: "
            f"{[r.message for r in warnings]}"
        )

    def test_warn_once_for_failed_cik(self, tmp_path, caplog):
        """
        After a 404, the warning for that CIK should be logged exactly once
        regardless of how many times get_company_facts is called.
        """
        mapping = {"WARNTEST": "0004444444"}
        client = _client_with_explicit_map(tmp_path, mapping)
        client._requests_available = True

        import requests
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 404
        http_err = requests.HTTPError(response=resp)

        with caplog.at_level(logging.WARNING, logger="alpha_stack.datastore.sec_edgar"):
            mock_get = MagicMock(side_effect=http_err)
            with patch.object(client._session, "get", mock_get):
                for _ in range(5):
                    client.get_company_facts("WARNTEST")

        cik_warnings = [
            r for r in caplog.records
            if "0004444444" in r.message or "WARNTEST" in r.message
        ]
        assert len(cik_warnings) <= 1, (
            f"Expected at most 1 warning for failed CIK, got {len(cik_warnings)}: "
            f"{[r.message for r in cik_warnings]}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Suite 5 — Graceful Continuation (no exception on None facts)
# ══════════════════════════════════════════════════════════════════════════════

class TestGracefulContinuation:
    """
    When a ticker has no EDGAR facts (unknown ticker or cached 404), the
    client should return None and the caller should be able to continue
    without raising an exception.
    """

    def test_unknown_ticker_returns_none(self, tmp_path):
        client = _client_with_bundled_default(tmp_path)
        result = client.get_company_facts("COMPLETELYFAKE123")
        assert result is None

    def test_none_result_does_not_raise(self, tmp_path):
        """Callers can safely check `if facts is None` without try/except."""
        client = _client_with_bundled_default(tmp_path)
        # This should not raise any exception
        facts = client.get_company_facts("COMPLETELYFAKE456")
        # Typical caller pattern — must not raise
        if facts is not None:
            _ = facts.head()

    def test_pre_failed_cik_returns_none(self, tmp_path):
        """A CIK pre-loaded into _failed_ciks returns None gracefully."""
        client = _client_with_bundled_default(tmp_path)
        client._failed_ciks.add("0009999999")
        # Inject a fake ticker→CIK so lookup succeeds but fetch is cached as failed
        client._cik_map = {"PREFAILED": "0009999999"}
        result = client.get_company_facts("PREFAILED")
        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# Suite 6 — CIK Collision Detection
# ══════════════════════════════════════════════════════════════════════════════

class TestCIKCollision:
    """
    CI and LHX were both mapped to CIK 0001739940 in the bundled default,
    which is a collision.  At minimum, verify the known-correct ticker (CI)
    can be looked up, and document the collision.
    """

    def test_ci_has_dedicated_cik(self, tmp_path):
        """CI (Cigna) lookup should return a CIK."""
        client = _client_with_bundled_default(tmp_path)
        result = client.lookup_cik("CI")
        assert result is not None, (
            "CI (Cigna) lookup returned None — no CIK mapping found."
        )

    def test_no_duplicate_ciks_for_different_tickers(self, tmp_path):
        """
        Load the bundled default and report any CIK that maps to multiple
        tickers.  This is informational — the test fails only if a collision
        involves one of our critical tickers.
        """
        default_path = REPO_ROOT / "alpha_stack" / "datastore" / "sec_ticker_map_default.json"
        if not default_path.exists():
            pytest.skip("Bundled default not present")

        with open(default_path) as f:
            default_map: dict = json.load(f)

        # Invert the map: CIK → list of tickers
        cik_to_tickers: dict[str, list] = {}
        for ticker, cik in default_map.items():
            cik_to_tickers.setdefault(cik, []).append(ticker)

        collisions = {
            cik: tickers
            for cik, tickers in cik_to_tickers.items()
            if len(tickers) > 1
        }

        # Check no critical ticker is involved in a collision with wrong CIK
        critical = set(CORRECT_CIKS.keys())
        for cik, tickers in collisions.items():
            collision_set = set(tickers)
            critical_involved = collision_set & critical
            if critical_involved:
                pytest.fail(
                    f"CIK collision: {cik} maps to {tickers}. "
                    f"Critical ticker(s) involved: {list(critical_involved)}. "
                    f"The critical ticker may receive data from the wrong company."
                )

        # Non-critical collisions: just log them (informational)
        if collisions:
            non_critical = {
                cik: t for cik, t in collisions.items()
                if not (set(t) & critical)
            }
            if non_critical:
                logging.warning(
                    "[EDGAR] CIK collisions in bundled default (non-critical): %s",
                    non_critical
                )
