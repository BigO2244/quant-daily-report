# FR-069 Child - Security-ID Replay Infrastructure

Date: 2026-06-22
Program: Decision-Grade PIT Research Infrastructure Completion
Phase: 2 - Security-ID Replay Infrastructure
Governance label: RESEARCH_ONLY / NON_EXECUTIONAL
Runtime impact: none
Gate result: PASS for security_id replay panel; PARTIAL for decision-grade replay

## Objective

Build a canonical replay price panel that removes ticker-key dependency from replay infrastructure.

## Artifacts

| Artifact | Path |
| --- | --- |
| Canonical panel builder | `research/canonical_replay_panel.py` |
| Replay certifier | `research/replay_certification.py` |
| Build CLI | `scripts/research/build_canonical_pit_replay_panel.py` |
| Certification CLI | `scripts/research/certify_canonical_replay.py` |
| Price panel | `outputs/research/canonical_pit_replay/2026-06-22/price_panel.parquet` |
| Manifest | `outputs/research/canonical_pit_replay/2026-06-22/manifest.json` |
| Certification result | `outputs/research/canonical_pit_replay/2026-06-22/certification_result.json` |

## Real Panel Summary

| Metric | Value |
| --- | ---: |
| Rows | 3,924,559 |
| Securities | 1,580 |
| Date start | 2012-06-01 |
| Date end | 2024-12-31 |
| Duplicate `date, security_id` keys | 0 |
| Missing SEP files | 0 |
| Malformed SEP files | 0 |
| Delisted-security rows | 605,042 |
| Price source | `sharadar_sep_closeadj` |
| Identity key | `security_id` |
| Ticker role | `display_only` |

## Certification Result

```json
{
  "status": "PASS",
  "decision_grade_status": "PARTIAL",
  "findings": [],
  "warnings": [
    "MEMBERSHIP_SCALE_NOT_PIT_EXACT:PIT_APPROXIMATE_SCALE"
  ]
}
```

## Gate Interpretation

The Phase 2 security-id infrastructure gate passes because:

- the panel is keyed by `date, security_id`;
- ticker is display/source metadata only;
- the panel has no duplicate `date, security_id` rows;
- price lineage carries source file hashes;
- the input paths do not use `data/universe.csv` or the local shadow price panel;
- the panel includes delisted-security rows before their membership end dates.

The broader decision-grade replay gate remains blocked because the current
`caerus_large_cap` family is based on current `scalemarketcap`, not date-effective
DAILY market cap. This blocker must carry into Phase 6 certification.

## Validation Commands

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -m py_compile \
  research/pit_universe.py \
  research/canonical_replay_panel.py \
  research/replay_certification.py \
  scripts/research/build_canonical_pit_replay_panel.py \
  scripts/research/certify_canonical_replay.py

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -m pytest -p no:cacheprovider \
  Tests/test_pit_universe.py \
  Tests/test_pit_phase_2_5.py \
  Tests/test_canonical_replay_panel.py \
  Tests/test_replay_certification.py

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 scripts/research/build_canonical_pit_replay_panel.py \
  --start-date 2012-06-01 \
  --end-date 2024-12-31 \
  --replay-id canonical_pit_replay_2012_2024_warmup \
  --output-dir outputs/research/canonical_pit_replay/2026-06-22

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 scripts/research/certify_canonical_replay.py \
  --panel outputs/research/canonical_pit_replay/2026-06-22/price_panel.parquet \
  --manifest outputs/research/canonical_pit_replay/2026-06-22/manifest.json \
  --output outputs/research/canonical_pit_replay/2026-06-22/certification_result.json
```

Result:

```text
35 passed
certification status PASS
decision_grade_status PARTIAL
certification digest 2fb1ac20c91bae280ec1e4bd6996a1d1a2984569696f19a8a46f4102126924da
```

## Remaining Blocker

`PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED`
