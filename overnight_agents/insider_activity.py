"""
Overnight Agent — Insider Trading Cluster Signal
==================================================
Thesis: Clustered insider buying (multiple officers/directors buying the same
stock within 30 days) predicts positive stock performance with a 3-6 week lag.
CEO and CFO purchases are particularly strong signals.

Data: SEC EDGAR Form 4 filings (free, public, real-time)
API:  https://data.sec.gov/submissions/CIK{cik}.json  — structured filings list
      https://data.sec.gov/Archives/edgar/data/...    — Form 4 XML

Method:
    1. Fetch recent Form 4 filings via submissions API for watchlist tickers
    2. Filter to open-market purchases (transaction code P) via XML parse
    3. Cluster: ≥2 insiders buying same stock within lookback = cluster signal
    4. Weight by officer rank (CEO/CFO > VP > Director)
    5. Score per ticker: cluster_score ∈ [0, 1]
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List
from xml.etree import ElementTree as ET

import requests

from overnight_agents.base import BaseAgent

logger = logging.getLogger(__name__)

_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{accession}-index.json"
_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{doc}"
_USER_AGENT = "Caerus Research brett.olson@nextleague.com"
_HEADERS = {"User-Agent": _USER_AGENT, "Accept": "application/json"}

_WATCHLIST_CIKS = {
    "AVGO": "0001730168", "NVDA": "0001045810", "MSFT": "0000789019",
    "META": "0001326801", "GOOGL": "0001652044", "AMZN": "0001018724",
    "MU":   "0000723125", "LMT":  "0000936468", "NOC":  "0001133421",
    "GD":   "0000040533", "AAPL": "0000320193", "JPM":  "0000019617",
    "QCOM": "0000804328", "FTNT": "0001262039",
}

_TITLE_WEIGHTS = {
    "chief executive": 2.0, "ceo": 2.0,
    "chief financial": 1.8, "cfo": 1.8,
    "president": 1.6,
    "chief operating": 1.5, "coo": 1.5,
    "chief technology": 1.4, "cto": 1.4,
    "director": 1.0,
    "vp": 0.8, "vice president": 0.8,
    "officer": 0.7,
}

_LOOKBACK_DAYS = 21
_CLUSTER_MIN = 2
_REQUEST_DELAY = 0.20
_MAX_FORM4_PER_TICKER = 8


class InsiderActivityAgent(BaseAgent):

    @property
    def name(self) -> str:
        return "insider_activity"

    def _fetch(self, as_of_date: date) -> Dict[str, Any]:
        cutoff = as_of_date - timedelta(days=_LOOKBACK_DAYS)
        ticker_purchases: Dict[str, List[dict]] = defaultdict(list)
        tickers_fetched = 0

        for ticker, cik in _WATCHLIST_CIKS.items():
            purchases = _fetch_form4_purchases(cik, ticker, cutoff, as_of_date)
            ticker_purchases[ticker].extend(purchases)
            tickers_fetched += 1
            time.sleep(_REQUEST_DELAY)

        if tickers_fetched == 0:
            raise RuntimeError("EDGAR submissions API unreachable")

        ticker_scores: Dict[str, float] = {}
        cluster_buys: List[str] = []
        cluster_sells: List[str] = []

        for ticker, purchases in ticker_purchases.items():
            if not purchases:
                continue
            buys = [p for p in purchases if p["type"] == "buy"]
            sells = [p for p in purchases if p["type"] == "sell"]

            if len(buys) >= _CLUSTER_MIN:
                weight_sum = sum(p.get("weight", 1.0) for p in buys)
                score = min(weight_sum / 4.0, 1.0)
                ticker_scores[ticker] = round(score, 4)
                cluster_buys.append(ticker)
            elif len(sells) >= _CLUSTER_MIN:
                ticker_scores[ticker] = -0.40
                cluster_sells.append(ticker)

        avg_score = (
            sum(ticker_scores.values()) / len(ticker_scores)
            if ticker_scores else 0.0
        )
        regime = (
            "cluster_buy" if cluster_buys
            else ("cluster_sell" if cluster_sells else "neutral_insider")
        )

        return {
            "regime": regime,
            "score": round(self._clamp(avg_score), 4),
            "ticker_scores": ticker_scores,
            "cluster_buys": cluster_buys,
            "cluster_sells": cluster_sells,
            "interpretation": (
                f"Cluster buys: {cluster_buys[:5]}" if cluster_buys
                else "No insider cluster activity in lookback window"
            ),
            "as_of_date": as_of_date.isoformat(),
        }


def _fetch_form4_purchases(
    cik: str,
    ticker: str,
    start: date,
    end: date,
) -> List[dict]:
    """Fetch Form 4 P-transactions via data.sec.gov/submissions."""
    try:
        url = _SUBMISSIONS_URL.format(cik=cik)
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])

        purchases: List[dict] = []
        parsed = 0
        for form, filing_date, accession in zip(forms, filing_dates, accessions):
            if form != "4":
                continue
            try:
                fd = date.fromisoformat(filing_date)
            except ValueError:
                continue
            if not (start <= fd <= end):
                continue
            if parsed >= _MAX_FORM4_PER_TICKER:
                break

            time.sleep(_REQUEST_DELAY)
            txns = _parse_form4_xml(cik, accession, ticker)
            purchases.extend(txns)
            parsed += 1

        return purchases

    except Exception as exc:
        logger.debug("[INSIDER] %s submissions fetch failed: %s", ticker, exc)
        return []


def _parse_form4_xml(cik: str, accession: str, ticker: str) -> List[dict]:
    """Download the Form 4 XML and extract P-type (purchase) transactions."""
    try:
        cik_int = int(cik)
        acc_nodash = accession.replace("-", "")

        # Get the filing index to find the primary XML doc
        idx_url = _ARCHIVES_URL.format(
            cik=cik_int, acc_nodash=acc_nodash, accession=accession
        )
        idx_resp = requests.get(idx_url, headers=_HEADERS, timeout=10)
        idx_resp.raise_for_status()
        index = idx_resp.json()

        primary_xml = None
        for item in index.get("directory", {}).get("item", []):
            name = item.get("name", "")
            if name.lower().endswith(".xml") and "xsl" not in name.lower():
                primary_xml = name
                break

        if not primary_xml:
            return []

        xml_url = _DOC_URL.format(cik=cik_int, acc_nodash=acc_nodash, doc=primary_xml)
        xml_resp = requests.get(xml_url, headers=_HEADERS, timeout=10)
        xml_resp.raise_for_status()

        root = ET.fromstring(xml_resp.text)
        owner_title = (
            root.findtext(
                ".//reportingOwner/reportingOwnerRelationship/officerTitle", ""
            ) or ""
        ).lower()
        weight = _title_weight(owner_title)

        results = []
        for txn in root.findall(".//nonDerivativeTransaction"):
            code = txn.findtext("transactionCoding/transactionCode", "")
            if code == "P":
                results.append({"ticker": ticker, "type": "buy", "weight": weight})
            elif code == "S":
                results.append({"ticker": ticker, "type": "sell", "weight": weight})

        return results

    except Exception as exc:
        logger.debug("[INSIDER] %s XML parse failed (%s): %s", ticker, accession, exc)
        return []


def _title_weight(title_lower: str) -> float:
    for keyword, weight in _TITLE_WEIGHTS.items():
        if keyword in title_lower:
            return weight
    return 0.7
