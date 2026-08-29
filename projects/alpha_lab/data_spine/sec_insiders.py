"""SEC quarterly insider-transaction bulk archives and normalized event tape."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import re
import tarfile
import zipfile
from datetime import date, datetime, timezone
from itertools import zip_longest
from pathlib import Path
from typing import Any, Callable, Dict, Iterator
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from projects.alpha_lab.experiments.catalog import FORM4_EVENTS
from projects.alpha_lab.factory import canonical_json

from . import http
from .materialize import _next_session_open, certify_asset
from .registry import SourceRegistry
from .storage import output_root, write_bundle_from_paths


_LINK = re.compile(r'href="([^"]*/(\d{4})q([1-4])_form345\.zip)"', re.IGNORECASE)
Downloader = Callable[[str, Path, str], None]


def _xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _xml_text(node: ET.Element | None, path: tuple[str, ...]) -> str | None:
    current = node
    for part in path:
        if current is None:
            return None
        current = next((child for child in list(current) if _xml_name(child.tag) == part), None)
    if current is None or not current.text:
        return None
    value = current.text.strip()
    return value or None


def _xml_flag(node: ET.Element | None, path: tuple[str, ...]) -> bool:
    return (_xml_text(node, path) or "").strip().lower() in {"1", "true", "yes"}


def _xml_footnote_ids(node: ET.Element) -> list[str]:
    values = []
    for item in node.iter():
        if _xml_name(item.tag) != "footnoteId":
            continue
        value = str(item.attrib.get("id") or item.text or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _form4_xml_from_submission(payload: bytes) -> str:
    blocks = re.findall(rb"<XML>(.*?)</XML>", payload, re.DOTALL | re.IGNORECASE)
    for block in blocks:
        if re.search(rb"<\s*ownershipDocument(?:\s|>)", block, re.IGNORECASE):
            return block.decode("utf-8", errors="replace").lstrip()
    text = payload.decode("utf-8", errors="replace")
    match = re.search(
        r"(<\s*ownershipDocument(?:\s|>).*?</\s*ownershipDocument\s*>)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        return match.group(1)
    raise ValueError("submission contains no ownershipDocument XML")


def _parse_form4_original(xml_text: str) -> Dict[str, Any]:
    """Parse original Form 4 XML as the canonical transaction source.

    The SEC quarterly tables are intentionally not consulted here. They are a
    discovery surface only and cannot override original ownership XML.
    """

    root = ET.fromstring(xml_text)
    footnotes = {
        str(node.attrib.get("id") or "").strip(): " ".join(
            "".join(node.itertext()).split()
        )
        for node in root.iter()
        if _xml_name(node.tag) == "footnote" and str(node.attrib.get("id") or "").strip()
    }
    owners = []
    for owner in (node for node in root.iter() if _xml_name(node.tag) == "reportingOwner"):
        owners.append(
            {
                "cik": _xml_text(owner, ("reportingOwnerId", "rptOwnerCik")),
                "name": _xml_text(owner, ("reportingOwnerId", "rptOwnerName")),
                "is_director": _xml_flag(
                    owner, ("reportingOwnerRelationship", "isDirector")
                ),
                "is_officer": _xml_flag(
                    owner, ("reportingOwnerRelationship", "isOfficer")
                ),
                "is_ten_percent_owner": _xml_flag(
                    owner, ("reportingOwnerRelationship", "isTenPercentOwner")
                ),
                "is_other": _xml_flag(
                    owner, ("reportingOwnerRelationship", "isOther")
                ),
                "officer_title": _xml_text(
                    owner, ("reportingOwnerRelationship", "officerTitle")
                ),
                "other_text": _xml_text(
                    owner, ("reportingOwnerRelationship", "otherText")
                ),
            }
        )
    transactions = []
    for node_name, is_derivative in (
        ("nonDerivativeTransaction", False),
        ("derivativeTransaction", True),
    ):
        for transaction in (node for node in root.iter() if _xml_name(node.tag) == node_name):
            footnote_ids = _xml_footnote_ids(transaction)
            transactions.append(
                {
                    "transaction_code": _xml_text(
                        transaction, ("transactionCoding", "transactionCode")
                    ),
                    "transaction_date": _xml_text(transaction, ("transactionDate", "value")),
                    "shares": _xml_text(
                        transaction,
                        ("transactionAmounts", "transactionShares", "value"),
                    ),
                    "price": _xml_text(
                        transaction,
                        ("transactionAmounts", "transactionPricePerShare", "value"),
                    ),
                    "acquired_disposed_code": _xml_text(
                        transaction,
                        ("transactionAmounts", "transactionAcquiredDisposedCode", "value"),
                    ),
                    "security_title": _xml_text(transaction, ("securityTitle", "value")),
                    "ownership_nature": _xml_text(
                        transaction,
                        ("ownershipNature", "directOrIndirectOwnership", "value"),
                    ),
                    "post_transaction_shares": _xml_text(
                        transaction,
                        ("postTransactionAmounts", "sharesOwnedFollowingTransaction", "value"),
                    ),
                    "is_derivative": is_derivative,
                    "footnote_ids": footnote_ids,
                    "footnote_text": " | ".join(
                        footnotes[value] for value in footnote_ids if footnotes.get(value)
                    ),
                }
            )
    purchase_value = 0.0
    for transaction in transactions:
        if transaction["transaction_code"] != "P":
            continue
        try:
            purchase_value += float(transaction["shares"] or 0) * float(transaction["price"] or 0)
        except ValueError:
            pass
    return {
        "issuer_cik": (_xml_text(root, ("issuer", "issuerCik")) or "").zfill(10),
        "issuer_name": _xml_text(root, ("issuer", "issuerName")),
        "issuer_ticker": _xml_text(root, ("issuer", "issuerTradingSymbol")),
        "document_type": _xml_text(root, ("documentType",)),
        "period_of_report": _xml_text(root, ("periodOfReport",)),
        "is_10b5_1": _xml_flag(root, ("aff10b5One",)),
        "remarks": _xml_text(root, ("remarks",)),
        "owners": owners,
        "transactions": transactions,
        "purchase_value": purchase_value,
        "footnotes": footnotes,
    }


def _download(url: str, path: Path, user_agent: str) -> None:
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/zip"})
    with urlopen(request, timeout=600) as response, path.open("wb") as stream:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            stream.write(chunk)
        stream.flush()
        os.fsync(stream.fileno())


def collect_sec_insider_archives(
    *,
    repo_root: Path,
    registry: SourceRegistry,
    start_year: int = 2012,
    end_year: int = 2026,
    user_agent: str | None = None,
    fetcher=http.get,
    downloader: Downloader = _download,
    retrieved_at: datetime | None = None,
) -> Dict[str, Any]:
    config = registry.sources["sec"]
    agent = user_agent or os.environ.get(str(config["user_agent_env"]))
    if not agent or "@" not in agent:
        raise RuntimeError("SEC_USER_AGENT must identify the research client and a contact email")
    landing = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
    page = fetcher(landing, headers={"User-Agent": agent}, timeout=120).body.decode(
        "utf-8", errors="replace"
    )
    links = []
    for href, year_text, quarter_text in _LINK.findall(page):
        year = int(year_text)
        quarter = int(quarter_text)
        if start_year <= year <= end_year:
            links.append((year, quarter, urljoin(landing, href)))
    links = sorted(set(links))
    if not links:
        raise ValueError("SEC insider archive page contained no requested quarters")
    staging = output_root(repo_root) / ".staging/sec_insiders"
    staging.mkdir(parents=True, exist_ok=True)
    files = {}
    try:
        for year, quarter, url in links:
            name = "{}q{}_form345.zip".format(year, quarter)
            path = staging / name
            if not path.is_file() or path.stat().st_size == 0:
                downloader(url, path, agent)
            files[name] = path
        timestamp = retrieved_at or datetime.now(timezone.utc)
        return write_bundle_from_paths(
            repo_root=repo_root,
            source_id="sec_insider_quarterly",
            files=files,
            metadata={
                "landing_page": landing,
                "start_year": start_year,
                "end_year": end_year,
                "quarter_count": len(files),
                "quarters": ["{}Q{}".format(year, quarter) for year, quarter, _ in links],
                "as_filed_flattened_source": True,
                "historical_point_in_time_source": True,
                "quarterly_update_frequency": True,
                "user_agent_persisted": False,
            },
            retrieved_at=timestamp,
        )
    finally:
        for path in files.values():
            path.unlink(missing_ok=True)
        try:
            staging.rmdir()
        except OSError:
            pass


def _read_tsv(archive: zipfile.ZipFile, suffix: str) -> list[dict[str, str]]:
    names = [name for name in archive.namelist() if name.upper().endswith(suffix.upper())]
    if not names:
        return []
    with archive.open(names[0]) as raw:
        text = (line.decode("utf-8", errors="replace") for line in raw)
        return list(csv.DictReader(text, delimiter="\t"))


def _sec_date(value: str) -> date:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return datetime.strptime(text, "%d-%b-%Y").date()


def materialize_insider_events(repo_root: Path) -> Dict[str, Any]:
    manifests = sorted(
        (repo_root / "outputs/research/alpha_lab/data_spine/sec_insider_quarterly").glob(
            "*/manifest.json"
        )
    )
    if not manifests:
        raise FileNotFoundError("SEC insider quarterly bundle is absent")
    bundle_root = manifests[-1].parent / "data"
    master_path = repo_root / "data/pit_universe/security_master.csv"
    cik_to_security: dict[str, list[dict[str, str]]] = {}
    issuer_ciks: set[str] = set()
    with master_path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            cik = str(row.get("cik") or "").zfill(10)
            if cik.strip("0"):
                issuer_ciks.add(cik)
                cik_to_security.setdefault(cik, []).append(
                    {
                        "security_id": str(row["security_id"]),
                        "ticker": str(row.get("ticker") or "").upper(),
                        "category": str(row.get("category") or ""),
                        "start": str(row.get("effective_start") or "")[:10],
                        "end": str(row.get("effective_end") or "")[:10],
                    }
                )
    output = repo_root / "outputs/research/cassiopeia/alpha_lab_form4_event_tape.jsonl.gz"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(".{}.tmp".format(output.name))
    row_count = 0
    unmatched = 0
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as sink:
        for archive_path in sorted(bundle_root.glob("*_form345.zip")):
            with zipfile.ZipFile(archive_path) as archive:
                submissions = {
                    str(row.get("ACCESSION_NUMBER") or ""): row
                    for row in _read_tsv(archive, "SUBMISSION.tsv")
                }
                owners = {}
                for row in _read_tsv(archive, "REPORTINGOWNER.tsv"):
                    owners.setdefault(str(row.get("ACCESSION_NUMBER") or ""), []).append(row)
                transactions = _read_tsv(archive, "NONDERIV_TRANS.tsv")
                for transaction in transactions:
                    accession = str(transaction.get("ACCESSION_NUMBER") or "")
                    submission = submissions.get(accession, {})
                    issuer_cik = str(submission.get("ISSUERCIK") or "").zfill(10)
                    owner = (owners.get(accession) or [{}])[0]
                    filed_raw = str(submission.get("FILING_DATE") or "")
                    try:
                        filed_date = _sec_date(filed_raw)
                        available_at = _next_session_open(filed_date)
                    except ValueError:
                        continue
                    transaction_date_raw = str(transaction.get("TRANS_DATE") or "")
                    try:
                        transaction_date = _sec_date(transaction_date_raw).isoformat()
                    except ValueError:
                        transaction_date = ""
                    issuer_ticker = str(submission.get("ISSUERTRADINGSYMBOL") or "").upper()
                    candidates = [
                        item
                        for item in cik_to_security.get(issuer_cik, [])
                        if (not transaction_date or item["start"] <= transaction_date)
                        and (not transaction_date or not item["end"] or transaction_date <= item["end"])
                    ]
                    exact = [item for item in candidates if item["ticker"] == issuer_ticker]
                    candidates = exact or candidates
                    candidates.sort(
                        key=lambda item: (
                            item["category"] != "Domestic Common Stock",
                            item["category"].endswith("Secondary Class"),
                            item["security_id"],
                        )
                    )
                    if not candidates:
                        unmatched += 1
                        continue
                    security_id = candidates[0]["security_id"]
                    shares = transaction.get("TRANS_SHARES") or ""
                    price = transaction.get("TRANS_PRICEPERSHARE") or ""
                    try:
                        transaction_value = float(shares) * float(price)
                    except (TypeError, ValueError):
                        transaction_value = None
                    source_hash = hashlib.sha256(
                        canonical_json({"submission": submission, "transaction": transaction}).encode("utf-8")
                    ).hexdigest()
                    relationship = str(owner.get("RPTOWNER_RELATIONSHIP") or "")
                    relationship_lower = relationship.lower()
                    owner_cik = str(owner.get("RPTOWNERCIK") or "").zfill(10)
                    owner_name = str(owner.get("RPTOWNERNAME") or "")
                    corporate_suffix = re.search(
                        r"\b(INC|LLC|LTD|LP|LLP|CORP|TRUST|FUND|HOLDINGS?)\b",
                        owner_name.upper(),
                    )
                    payload = {
                            "security_id": security_id,
                            "issuer_cik": issuer_cik,
                            "owner_cik": owner_cik,
                            "accession_number": accession,
                            "acceptance_datetime_utc": available_at,
                            "available_at": available_at,
                            "transaction_date": transaction_date,
                            "transaction_code": transaction.get("TRANS_CODE") or "",
                            "acquired_disposed_code": transaction.get("TRANS_ACQUIRED_DISP_CD") or "",
                            "transaction_shares": shares,
                            "transaction_price": price,
                            "transaction_value": transaction_value,
                            "is_derivative": False,
                            "is_director": "director" in relationship_lower,
                            "is_officer": "officer" in relationship_lower,
                            "is_ten_percent_owner": "10%" in relationship_lower,
                            "officer_title": owner.get("RPTOWNER_TITLE") or "",
                            "is_natural_person": owner_cik not in issuer_ciks and corporate_suffix is None,
                            "ownership_nature": transaction.get("DIRECT_INDIRECT_OWNERSHIP") or "",
                            "control_group_id": accession,
                            "is_10b5_1": str(submission.get("AFF10B5ONE") or "0") == "1",
                            "footnote_text": "",
                            "source_document": archive_path.name,
                            "parse_status": "PASS_SEC_FLAT_FILE",
                            "amendment_lineage": submission.get("DOCUMENT_TYPE") or "",
                            "source_sha256": source_hash,
                    }
                    sink.write(canonical_json(payload) + "\n")
                    row_count += 1
    temporary.replace(output)
    certify_asset(
        repo_root=repo_root,
        asset=FORM4_EVENTS,
        data_files=(output,),
        pit_verified=False,
        methodology="SEC quarterly as-filed flattened Form 3/4/5 non-derivative transactions; CIK+ticker+effective-date identity; conservative next-session availability",
        blockers=(
            "original_Form4_filing_lineage_not_yet_hydrated",
            "exact_EDGAR_acceptance_timestamp_not_in_quarterly_flat_file",
        ),
    )
    return {
        "form4_event_rows": row_count,
        "unmatched_transaction_rows": unmatched,
        "form4_event_path": str(output),
    }


def prepare_insider_hydration_index(repo_root: Path) -> Dict[str, Any]:
    """Select original filings that can contain frozen eligible purchases.

    The SEC quarterly flat file is used only as a discovery index here. The
    resulting candidates still require original-submission hydration, exact
    acceptance-time parsing, XML validation, and amendment reconciliation.
    """

    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("duckdb is required to prepare the Form 4 hydration index") from exc
    event_tape = repo_root / "outputs/research/cassiopeia/alpha_lab_form4_event_tape.jsonl.gz"
    index_manifests = sorted(
        (repo_root / "outputs/research/alpha_lab/data_spine/sec_event_index").glob(
            "*/manifest.json"
        )
    )
    if not event_tape.is_file() or not index_manifests:
        raise FileNotFoundError("normalized insider tape or SEC master index is absent")
    event_index = index_manifests[-1].parent / "data/event_index.csv"
    output = repo_root / "outputs/research/alpha_lab/shared/form4_purchase_hydration_index.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(".{}.tmp".format(output.name))
    connection = duckdb.connect()
    try:
        connection.execute(
            """
            CREATE TEMP TABLE eligible_accessions AS
            SELECT DISTINCT accession_number
            FROM read_json_auto(?, format='newline_delimited')
            WHERE transaction_code='P'
              AND acquired_disposed_code='A'
              AND TRY_CAST(transaction_shares AS DOUBLE) > 0
              AND TRY_CAST(transaction_price AS DOUBLE) > 0
              AND is_natural_person
              AND (is_director OR is_officer OR is_ten_percent_owner)
            """,
            [str(event_tape)],
        )
        destination = "'{}'".format(str(temporary.resolve()).replace("'", "''"))
        query = (
            """
            COPY (
              SELECT i.cik, i.company_name, i.form_type, i.filed_date, i.filename,
                     i.index_year, i.index_quarter
              FROM read_csv_auto(?, header=true, all_varchar=true) i
              JOIN eligible_accessions e
                ON REGEXP_EXTRACT(i.filename,
                   '([0-9]{10}-[0-9]{2}-[0-9]{6})', 1)=e.accession_number
              WHERE i.form_type IN ('4', '4/A')
              ORDER BY i.index_year, i.filed_date, i.cik, i.filename
            ) TO __DESTINATION__ (HEADER, DELIMITER ',')
            """
        ).replace("__DESTINATION__", destination)
        connection.execute(query, [str(event_index)])
        row_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM read_csv_auto(?, header=true)", [str(temporary)]
            ).fetchone()[0]
        )
    finally:
        connection.close()
    temporary.replace(output)
    return {
        "hydration_candidate_rows": row_count,
        "hydration_index_path": str(output),
        "selection_method": "quarterly_flat_file_discovery_only_open_market_natural_person_purchase_candidates",
    }


def audit_insider_hydration(repo_root: Path) -> Dict[str, Any]:
    """Compare the original-filing sample with quarterly discovery fields."""

    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("duckdb is required for the Form 4 hydration audit") from exc
    bundles = sorted(
        (repo_root / "outputs/research/alpha_lab/data_spine/sec_original_filings").glob(
            "*/manifest.json"
        )
    )
    if not bundles:
        raise FileNotFoundError("SEC original-filing sample is absent")
    manifest_path = bundles[-1]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data_root = manifest_path.parent / "data"
    inventory_path = data_root / "filing_inventory.csv"
    event_tape = repo_root / "outputs/research/cassiopeia/alpha_lab_form4_event_tape.jsonl.gz"
    inventory = {}
    with inventory_path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            inventory[str(row["accession_number"])] = row
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """
            SELECT e.accession_number, e.issuer_cik, e.owner_cik, e.transaction_date,
                   e.transaction_code, e.transaction_shares, e.transaction_price,
                   e.transaction_value, e.is_director, e.is_officer,
                   e.is_ten_percent_owner
            FROM read_json_auto(?, format='newline_delimited') e
            JOIN read_csv_auto(?, header=true, all_varchar=true) i
              USING (accession_number)
            """,
            [str(event_tape), str(inventory_path)],
        ).fetchall()
    finally:
        connection.close()
    flat: dict[str, list[tuple[Any, ...]]] = {}
    for row in rows:
        flat.setdefault(str(row[0]), []).append(row)

    def _number(value: Any) -> float | None:
        try:
            return round(float(value), 6)
        except (TypeError, ValueError):
            return None

    def _flat_precision(value: Any) -> float | None:
        number = _number(value)
        return round(number, 2) if number is not None else None

    counts = {
        "original_xml_parse": 0,
        "issuer_agreement": 0,
        "owner_set_agreement": 0,
        "purchase_transaction_agreement": 0,
        "purchase_value_agreement": 0,
        "role_agreement": 0,
    }
    parse_errors = []
    for accession, inventory_row in sorted(inventory.items()):
        source = data_root / "filings/{}.txt".format(accession)
        try:
            blocks = re.findall(rb"<XML>(.*?)</XML>", source.read_bytes(), re.DOTALL | re.IGNORECASE)
            ownership = next(block for block in blocks if b"ownershipDocument" in block)
            parsed = _parse_form4_original(
                ownership.decode("utf-8", errors="replace").lstrip()
            )
            counts["original_xml_parse"] += 1
        except Exception as exc:
            parse_errors.append({"accession_number": accession, "error_type": type(exc).__name__})
            continue
        discovery = flat.get(accession, [])
        issuer_ciks = {str(row[1]).zfill(10) for row in discovery if row[1]}
        if str(parsed.get("issuer_cik") or "").zfill(10) in issuer_ciks:
            counts["issuer_agreement"] += 1
        parsed_owners = {
            str(owner.get("cik") or "").zfill(10)
            for owner in parsed.get("owners") or []
            if owner.get("cik")
        }
        flat_owners = {str(row[2]).zfill(10) for row in discovery if row[2]}
        if parsed_owners == flat_owners:
            counts["owner_set_agreement"] += 1
        parsed_purchases = {
            (
                str(row.get("transaction_date") or ""),
                str(row.get("transaction_code") or ""),
                _flat_precision(row.get("shares")),
                _flat_precision(row.get("price")),
            )
            for row in parsed.get("transactions") or []
            if row.get("transaction_code") == "P"
        }
        flat_purchases = {
            (
                str(row[3] or ""),
                str(row[4] or ""),
                _flat_precision(row[5]),
                _flat_precision(row[6]),
            )
            for row in discovery
            if row[4] == "P"
        }
        if parsed_purchases == flat_purchases:
            counts["purchase_transaction_agreement"] += 1
        parsed_value = _number(parsed.get("purchase_value"))
        flat_value = _number(sum(float(row[7] or 0) for row in discovery if row[4] == "P"))
        if (
            parsed_value is not None
            and flat_value is not None
            and abs(parsed_value - flat_value) <= max(0.02, abs(parsed_value) * 0.001)
        ):
            counts["purchase_value_agreement"] += 1
        parsed_roles = {
            (
                bool(owner.get("is_director")),
                bool(owner.get("is_officer")),
                bool(owner.get("is_ten_percent_owner")),
            )
            for owner in parsed.get("owners") or []
        }
        flat_roles = {(bool(row[8]), bool(row[9]), bool(row[10])) for row in discovery}
        if parsed_roles == flat_roles:
            counts["role_agreement"] += 1

    candidate_count = int((manifest.get("metadata") or {}).get("candidate_count") or len(inventory))
    acceptance_pass = int(
        (manifest.get("metadata") or {}).get("acceptance_timestamp_pass_count") or 0
    )
    rates = {key: value / max(candidate_count, 1) for key, value in counts.items()}
    rates["acceptance_timestamp"] = acceptance_pass / max(candidate_count, 1)
    payload = {
        "schema_version": "caerus_alpha_lab_form4_original_sample_audit_v1",
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "source_manifest": str(manifest_path.relative_to(repo_root)),
        "candidate_count": candidate_count,
        "hydrated_count": len(inventory),
        "counts": counts,
        "rates": rates,
        "parse_errors": parse_errors,
        "status": "PASS_99PCT" if all(value >= 0.99 for value in rates.values()) else "FAIL_RECONCILIATION",
        "certifies_full_history": False,
        "trading_behavior_changed": False,
    }
    output = repo_root / "outputs/research/alpha_lab/shared/form4_original_sample_audit.json"
    output.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    return {
        "form4_audit_path": str(output),
        "form4_audit_candidate_count": candidate_count,
        "form4_audit_status": payload["status"],
    }


def _iter_finalized_original_filings(
    repo_root: Path,
) -> tuple[str, Path, bool, Iterator[tuple[Dict[str, Any], bytes]]]:
    """Return the best finalized original-filing source and a streaming reader."""

    def latest_form4_manifest(source_id: str) -> Path | None:
        manifests = sorted(
            (output_root(repo_root) / source_id).glob("*/manifest.json"), reverse=True
        )
        for path in manifests:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            forms = {
                str(value).upper()
                for value in (manifest.get("metadata") or {}).get("forms", [])
            }
            if forms.intersection({"4", "4/A"}):
                return path
        return None

    stream_manifest = latest_form4_manifest("sec_original_filings_stream")
    sample_manifest = latest_form4_manifest("sec_original_filings")
    manifest_path = stream_manifest or sample_manifest
    if manifest_path is None:
        raise FileNotFoundError("no finalized original Form 4 filing bundle is available")
    source_id = "sec_original_filings_stream" if stream_manifest else "sec_original_filings"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_index = str((manifest.get("metadata") or {}).get("index_path") or "")
    full_history = bool(
        stream_manifest
        and source_index.endswith(
            "outputs/research/alpha_lab/shared/form4_purchase_hydration_index.csv"
        )
    )
    data_root = manifest_path.parent / "data"

    def sample_rows() -> Iterator[tuple[Dict[str, Any], bytes]]:
        inventory_path = data_root / "filing_inventory.csv"
        if not inventory_path.is_file():
            raise FileNotFoundError(inventory_path)
        with inventory_path.open("r", encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                accession = str(row.get("accession_number") or "").strip()
                source = data_root / "filings" / "{}.txt".format(accession)
                if not source.is_file():
                    yield dict(row, source_missing=True), b""
                    continue
                yield dict(row), source.read_bytes()

    def partition_rows() -> Iterator[tuple[Dict[str, Any], bytes]]:
        inventories = sorted((data_root / "inventory").glob("part_*_inventory.jsonl.gz"))
        if not inventories:
            raise FileNotFoundError("finalized stream bundle has no partition inventories")
        seen_source_hashes: set[str] = set()
        accession_hashes: dict[str, str] = {}
        for inventory_path in inventories:
            archive_name = inventory_path.name.replace("_inventory.jsonl.gz", ".tar.gz")
            archive_path = data_root / "partitions" / archive_name
            if not archive_path.is_file():
                raise FileNotFoundError(archive_path)
            inventory_rows: list[Dict[str, Any]] = []
            with gzip.open(inventory_path, "rt", encoding="utf-8") as inventory_stream:
                for line in inventory_stream:
                    inventory_rows.append(json.loads(line))
            # Iterate a compressed tar exactly once. Repeated extractfile(name)
            # performs member lookup for every filing and becomes quadratic for
            # 1,000-member partitions. Pair by write order instead of member
            # name because the discovery index can repeat an accession and tar
            # archives legally preserve duplicate member names.
            with tarfile.open(archive_path, "r:gz") as archive:
                members = (item for item in archive if item.isfile())
                for row, member_info in zip_longest(inventory_rows, members):
                    if row is None or member_info is None:
                        raise ValueError(
                            "inventory/archive row-count mismatch for {}".format(archive_name)
                        )
                    accession = str(row.get("accession_number") or "").strip()
                    expected_name = "filings/{}.txt".format(accession.replace("/", "-"))
                    if member_info.name != expected_name:
                        raise ValueError(
                            "inventory/archive order mismatch for {}".format(archive_name)
                        )
                    member = archive.extractfile(member_info)
                    if member is None:
                        yield dict(row, source_missing=True), b""
                        continue
                    payload = member.read()
                    source_hash = hashlib.sha256(payload).hexdigest()
                    expected_hash = str(row.get("source_sha256") or "")
                    if expected_hash and source_hash != expected_hash:
                        raise ValueError(
                            "source hash mismatch for {}".format(expected_name)
                        )
                    if source_hash in seen_source_hashes:
                        continue
                    seen_source_hashes.add(source_hash)
                    prior_hash = accession_hashes.setdefault(accession, source_hash)
                    yield dict(
                        row,
                        accession_payload_collision=prior_hash != source_hash,
                    ), payload

    return (
        source_id,
        manifest_path,
        full_history,
        partition_rows() if stream_manifest is not None else sample_rows(),
    )


def _security_identity_index(repo_root: Path) -> tuple[dict[str, list[Dict[str, str]]], set[str]]:
    master_path = repo_root / "data/pit_universe/security_master.csv"
    if not master_path.is_file():
        raise FileNotFoundError(master_path)
    by_cik: dict[str, list[Dict[str, str]]] = {}
    issuer_ciks: set[str] = set()
    with master_path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            cik = str(row.get("cik") or "").zfill(10)
            if not cik.strip("0"):
                continue
            issuer_ciks.add(cik)
            by_cik.setdefault(cik, []).append(
                {
                    "security_id": str(row.get("security_id") or ""),
                    "ticker": str(row.get("ticker") or "").upper(),
                    "category": str(row.get("category") or ""),
                    "start": str(row.get("effective_start") or row.get("firstpricedate") or "")[:10],
                    "end": str(row.get("effective_end") or row.get("lastpricedate") or "")[:10],
                }
            )
    return by_cik, issuer_ciks


def _resolve_original_security(
    by_cik: dict[str, list[Dict[str, str]]],
    *,
    issuer_cik: str,
    issuer_ticker: str,
    as_of_date: str,
) -> str | None:
    candidates = [
        item
        for item in by_cik.get(issuer_cik, [])
        if (not item["start"] or item["start"] <= as_of_date)
        and (not item["end"] or as_of_date <= item["end"])
    ]
    ticker_matches = [item for item in candidates if item["ticker"] == issuer_ticker.upper()]
    candidates = ticker_matches or candidates
    candidates.sort(
        key=lambda item: (
            item["category"] != "Domestic Common Stock",
            item["category"].endswith("Secondary Class"),
            item["security_id"],
        )
    )
    return candidates[0]["security_id"] if candidates else None


def _natural_person(owner: Dict[str, Any], issuer_ciks: set[str]) -> bool:
    owner_cik = str(owner.get("cik") or "").zfill(10)
    name = str(owner.get("name") or "").upper()
    corporate_suffix = re.search(
        r"\b(INC|LLC|LTD|LP|LLP|CORP|TRUST|FUND|HOLDINGS?|PARTNERS?)\b",
        name,
    )
    return bool(owner_cik.strip("0")) and owner_cik not in issuer_ciks and corporate_suffix is None


def _exclude_amendment_ambiguous_issuers(
    event_path: Path,
    *,
    amendment_issuer_ciks: set[str],
) -> Dict[str, int]:
    """Remove every event for an issuer with any captured Form 4/A.

    Form 4/A does not expose a universally reliable machine-readable pointer to
    the exact original transaction being amended.  Guessing a supersession
    relationship would contaminate cluster counts.  The fail-closed policy is
    therefore deliberately conservative: consume amendments for lineage
    detection, but exclude the amendment and every original event for the same
    issuer from the eligible tape.

    This can reduce coverage; it cannot duplicate an amended purchase or count
    an unresolved correction as independent insider conviction.
    """

    if not amendment_issuer_ciks:
        return {
            "retained_event_row_count": 0,
            "retained_eligible_purchase_row_count": 0,
            "excluded_event_row_count": 0,
            "excluded_eligible_purchase_row_count": 0,
        }
    filtered = event_path.with_name(".{}.amendment-filtered.tmp".format(event_path.name))
    retained = 0
    retained_eligible = 0
    excluded = 0
    excluded_eligible = 0
    with gzip.open(event_path, "rt", encoding="utf-8") as source, gzip.open(
        filtered, "wt", encoding="utf-8", compresslevel=6
    ) as sink:
        for line in source:
            row = json.loads(line)
            eligible = bool(row.get("eligible_open_market_purchase"))
            if str(row.get("issuer_cik") or "").zfill(10) in amendment_issuer_ciks:
                excluded += 1
                excluded_eligible += int(eligible)
                continue
            if row.get("amendment_lineage") == "ORIGINAL":
                row["amendment_lineage"] = (
                    "ORIGINAL_ISSUER_AMENDMENT_FREE_IN_CAPTURE"
                )
            sink.write(canonical_json(row) + "\n")
            retained += 1
            retained_eligible += int(eligible)
    filtered.replace(event_path)
    return {
        "retained_event_row_count": retained,
        "retained_eligible_purchase_row_count": retained_eligible,
        "excluded_event_row_count": excluded,
        "excluded_eligible_purchase_row_count": excluded_eligible,
    }


def materialize_original_insider_events(
    repo_root: Path,
    *,
    generated_at: datetime | None = None,
) -> Dict[str, Any]:
    """Build an original-first Form 4 tape from a finalized immutable bundle.

    A finalized 500-filing sample produces a pilot-usable tape but deliberately
    leaves the provider gate blocked on full-history coverage. Only a finalized
    stream can be considered for full-history certification.
    """

    timestamp = generated_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    source_id, source_manifest, full_history, filings = _iter_finalized_original_filings(
        repo_root
    )
    source_metadata = json.loads(source_manifest.read_text(encoding="utf-8")).get(
        "metadata", {}
    )
    by_cik, issuer_ciks = _security_identity_index(repo_root)
    staging = output_root(repo_root) / ".staging" / "form4_original_materialization"
    staging.mkdir(parents=True, exist_ok=True)
    event_path = staging / "form4_original_event_tape.jsonl.gz"
    quality_path = staging / "form4_original_event_tape_quality.json"
    event_tmp = event_path.with_name(".{}.tmp".format(event_path.name))

    counts = {
        "source_candidate_row_count": int(source_metadata.get("candidate_count") or 0),
        "source_filing_count": 0,
        "unique_accession_count": 0,
        "accession_payload_collision_count": 0,
        "source_missing_count": 0,
        "ownership_xml_parse_count": 0,
        "acceptance_timestamp_count": 0,
        "security_identity_match_count": 0,
        "owner_count": 0,
        "transaction_count": 0,
        "event_row_count": 0,
        "eligible_purchase_row_count": 0,
        "amendment_filing_count": 0,
        "amendment_issuer_count": 0,
        "amendment_excluded_event_row_count": 0,
        "amendment_excluded_eligible_purchase_row_count": 0,
    }
    failures = []
    unique_accessions: set[str] = set()
    amendment_issuer_ciks: set[str] = set()
    with gzip.open(event_tmp, "wt", encoding="utf-8", compresslevel=6) as sink:
        for inventory, payload in filings:
            counts["source_filing_count"] += 1
            accession = str(inventory.get("accession_number") or "").strip()
            unique_accessions.add(accession)
            counts["accession_payload_collision_count"] += int(
                bool(inventory.get("accession_payload_collision"))
            )
            if not payload:
                counts["source_missing_count"] += 1
                failures.append({"accession_number": accession, "reason": "SOURCE_MISSING"})
                continue
            try:
                parsed = _parse_form4_original(_form4_xml_from_submission(payload))
            except Exception as exc:
                failures.append(
                    {"accession_number": accession, "reason": "XML_PARSE_FAILED", "error_type": type(exc).__name__}
                )
                continue
            counts["ownership_xml_parse_count"] += 1
            acceptance_utc = str(inventory.get("acceptance_datetime_utc") or "").strip()
            acceptance_et = str(inventory.get("acceptance_datetime_et") or "").strip()
            if not acceptance_utc:
                failures.append({"accession_number": accession, "reason": "ACCEPTANCE_TIMESTAMP_MISSING"})
                continue
            counts["acceptance_timestamp_count"] += 1
            try:
                accepted_local = datetime.fromisoformat(acceptance_et or acceptance_utc)
                if accepted_local.tzinfo is None or accepted_local.utcoffset() is None:
                    raise ValueError("acceptance timestamp is timezone-naive")
                accepted_local = accepted_local.astimezone(ZoneInfo("America/New_York"))
                accepted_date = accepted_local.date().isoformat()
            except (TypeError, ValueError):
                failures.append({"accession_number": accession, "reason": "ACCEPTANCE_TIMESTAMP_INVALID"})
                continue
            security_id = _resolve_original_security(
                by_cik,
                issuer_cik=str(parsed.get("issuer_cik") or "").zfill(10),
                issuer_ticker=str(parsed.get("issuer_ticker") or ""),
                as_of_date=accepted_date,
            )
            if security_id:
                counts["security_identity_match_count"] += 1
            else:
                failures.append({"accession_number": accession, "reason": "SECURITY_IDENTITY_UNMATCHED"})
                continue
            document_type = str(parsed.get("document_type") or inventory.get("form_type") or "")
            is_amendment = document_type.upper().endswith("/A")
            counts["amendment_filing_count"] += int(is_amendment)
            if is_amendment:
                amendment_issuer_ciks.add(
                    str(parsed.get("issuer_cik") or "").zfill(10)
                )
            owners = list(parsed.get("owners") or [])
            transactions = list(parsed.get("transactions") or [])
            counts["owner_count"] += len(owners)
            counts["transaction_count"] += len(transactions)
            available_at = _next_session_open(date.fromisoformat(accepted_date))
            source_hash = hashlib.sha256(payload).hexdigest()
            for owner_index, owner in enumerate(owners):
                owner_cik = str(owner.get("cik") or "").zfill(10)
                for transaction_index, transaction in enumerate(transactions):
                    shares = transaction.get("shares")
                    price = transaction.get("price")
                    try:
                        transaction_value = float(shares) * float(price)
                    except (TypeError, ValueError):
                        transaction_value = None
                    eligible_purchase = (
                        transaction.get("transaction_code") == "P"
                        and transaction.get("acquired_disposed_code") == "A"
                        and not transaction.get("is_derivative")
                        and _natural_person(owner, issuer_ciks)
                        and any(
                            bool(owner.get(field))
                            for field in ("is_director", "is_officer", "is_ten_percent_owner")
                        )
                        and transaction_value is not None
                        and transaction_value > 0
                    )
                    row = {
                        "event_id": "{}:{}:{}:{}".format(
                            accession, source_hash, owner_index, transaction_index
                        ),
                        "security_id": security_id,
                        "issuer_cik": str(parsed.get("issuer_cik") or "").zfill(10),
                        "issuer_ticker": str(parsed.get("issuer_ticker") or "").upper(),
                        "owner_cik": owner_cik,
                        "owner_name": owner.get("name"),
                        "accession_number": accession,
                        "acceptance_datetime_utc": acceptance_utc,
                        "available_at": available_at,
                        "transaction_date": transaction.get("transaction_date"),
                        "transaction_code": transaction.get("transaction_code"),
                        "acquired_disposed_code": transaction.get("acquired_disposed_code"),
                        "transaction_shares": shares,
                        "transaction_price": price,
                        "transaction_value": transaction_value,
                        "is_derivative": bool(transaction.get("is_derivative")),
                        "is_director": bool(owner.get("is_director")),
                        "is_officer": bool(owner.get("is_officer")),
                        "is_ten_percent_owner": bool(owner.get("is_ten_percent_owner")),
                        "officer_title": owner.get("officer_title"),
                        "is_natural_person": _natural_person(owner, issuer_ciks),
                        "ownership_nature": transaction.get("ownership_nature"),
                        "control_group_id": "{}:{}".format(accession, source_hash),
                        "is_10b5_1": bool(parsed.get("is_10b5_1")),
                        "footnote_text": transaction.get("footnote_text") or "",
                        "source_document": str(inventory.get("source_filename") or ""),
                        "parse_status": "PASS_ORIGINAL_XML",
                        "amendment_lineage": "AMENDMENT_UNRESOLVED" if is_amendment else "ORIGINAL",
                        "source_sha256": source_hash,
                        "eligible_open_market_purchase": eligible_purchase,
                    }
                    sink.write(canonical_json(row) + "\n")
                    counts["event_row_count"] += 1
                    counts["eligible_purchase_row_count"] += int(eligible_purchase)
    counts["pre_amendment_filter_event_row_count"] = counts["event_row_count"]
    counts["pre_amendment_filter_eligible_purchase_row_count"] = counts[
        "eligible_purchase_row_count"
    ]
    amendment_filter = _exclude_amendment_ambiguous_issuers(
        event_tmp,
        amendment_issuer_ciks=amendment_issuer_ciks,
    )
    counts["amendment_issuer_count"] = len(amendment_issuer_ciks)
    counts["amendment_excluded_event_row_count"] = amendment_filter[
        "excluded_event_row_count"
    ]
    counts["amendment_excluded_eligible_purchase_row_count"] = amendment_filter[
        "excluded_eligible_purchase_row_count"
    ]
    if amendment_issuer_ciks:
        counts["event_row_count"] = amendment_filter["retained_event_row_count"]
        counts["eligible_purchase_row_count"] = amendment_filter[
            "retained_eligible_purchase_row_count"
        ]
    counts["unique_accession_count"] = len(unique_accessions)
    counts["duplicate_source_row_count"] = max(
        counts["source_candidate_row_count"] - counts["source_filing_count"], 0
    )
    event_tmp.replace(event_path)

    denominator = max(counts["source_filing_count"], 1)
    rates = {
        "source_present": 1.0 - counts["source_missing_count"] / denominator,
        "ownership_xml_parse": counts["ownership_xml_parse_count"] / denominator,
        "acceptance_timestamp": counts["acceptance_timestamp_count"] / denominator,
        "security_identity_match": counts["security_identity_match_count"] / denominator,
    }
    quality_blockers = []
    if not full_history:
        quality_blockers.append("sample_only_not_full_history")
    for key, value in rates.items():
        if value < 0.99:
            quality_blockers.append("{}_below_99pct".format(key))
    if counts["eligible_purchase_row_count"] == 0:
        quality_blockers.append("no_eligible_open_market_purchase_rows")
    quality = {
        "schema_version": "caerus_alpha_lab_form4_original_event_tape_quality_v2",
        "classification": "RESEARCH_ONLY_NONEXECUTIONAL",
        "source_id": source_id,
        "source_manifest": str(source_manifest.relative_to(repo_root)),
        "coverage_scope": "FULL_HISTORY" if full_history else "PILOT_SAMPLE",
        "counts": counts,
        "rates": rates,
        "amendment_exclusion_rates": {
            "event_rows": counts["amendment_excluded_event_row_count"]
            / max(counts["pre_amendment_filter_event_row_count"], 1),
            "eligible_purchase_rows": counts[
                "amendment_excluded_eligible_purchase_row_count"
            ]
            / max(
                counts["pre_amendment_filter_eligible_purchase_row_count"],
                1,
            ),
        },
        "failures": failures[:1000],
        "failure_count": len(failures),
        "blockers": quality_blockers,
        "status": (
            "READY_FULL_HISTORY"
            if full_history and not quality_blockers
            else (
            "PILOT_USABLE_ORIGINAL_FIRST"
                if not [
                    item
                    for item in quality_blockers
                    if item
                    not in {"sample_only_not_full_history", "amendment_lineage_not_reconciled"}
                ]
                else "BLOCKED_QUALITY"
            )
        ),
        "quarterly_flat_file_role": "DISCOVERY_ONLY_NOT_CANONICAL",
        "amendment_policy": (
            "ISSUER_LEVEL_FAIL_CLOSED_EXCLUSION: if any captured Form 4/A exists "
            "for an issuer, every original and amended event for that issuer is "
            "excluded from the eligible tape"
        ),
        "amendment_lineage_reconciled_by_exclusion": True,
        "alpha_claim_permitted": False,
        "trading_behavior_changed": False,
    }
    quality_path.write_text(canonical_json(quality) + "\n", encoding="utf-8")
    bundle = write_bundle_from_paths(
        repo_root=repo_root,
        source_id="form4_original_event_tape",
        files={"events.jsonl.gz": event_path, "quality.json": quality_path},
        metadata={
            "source_manifest": quality["source_manifest"],
            "coverage_scope": quality["coverage_scope"],
            "quality_status": quality["status"],
            "event_row_count": counts["event_row_count"],
            "eligible_purchase_row_count": counts["eligible_purchase_row_count"],
            "amendment_issuer_count": counts["amendment_issuer_count"],
            "amendment_excluded_event_row_count": counts[
                "amendment_excluded_event_row_count"
            ],
            "amendment_policy": quality["amendment_policy"],
            "original_xml_is_canonical": True,
            "quarterly_flat_file_role": "DISCOVERY_ONLY_NOT_CANONICAL",
        },
        retrieved_at=timestamp,
    )
    finalized_events = bundle["paths"]["events.jsonl.gz"]
    certify_asset(
        repo_root=repo_root,
        asset=FORM4_EVENTS,
        data_files=(finalized_events,),
        pit_verified=quality["status"] == "READY_FULL_HISTORY",
        methodology=(
            "Original SEC ownership XML with exact EDGAR acceptance timestamp, "
            "effective-dated CIK/security identity, and conservative next-session availability; "
            "quarterly flat file used for discovery only; issuers with any captured "
            "Form 4/A are excluded fail-closed rather than heuristically superseded"
        ),
        blockers=tuple(quality_blockers),
    )
    event_path.unlink(missing_ok=True)
    quality_path.unlink(missing_ok=True)
    try:
        staging.rmdir()
    except OSError:
        pass
    return {
        **bundle,
        "form4_original_event_rows": counts["event_row_count"],
        "form4_original_eligible_purchase_rows": counts["eligible_purchase_row_count"],
        "form4_original_quality_status": quality["status"],
        "form4_original_quality_blockers": quality_blockers,
    }
