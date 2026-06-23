# FR-068 Requirement Replacement Remediation

Date: 2026-06-23
Scope: Implementation / governance remediation / FR-068 certification
Runtime impact: none

## 1. Executive Summary

The approved governance recommendation was implemented in the research certification framework:

`REPLACE REQUIREMENT`

The certifier no longer requires Sharadar DAILY `marketcap` as the sole decision-grade implementation. It now certifies method-neutral membership status:

- PIT-valid;
- survivorship-free;
- security-id keyed;
- date-effective;
- reproducible lineage.

The existing `caerus_large_cap` artifact still fails the revised standard because it is entirely based on current `scale_source=scalemarketcap`.

Final status: FAIL.

Downstream conviction-allocation and sleeve-promotion rebaselines were not run.

## 2. Certification Changes

Changed `research/canonical_replay_panel.py`:

- Added method inference for membership artifacts.
- Added explicit manifest fields:
  - `membership_certification_status`
  - `membership_certification_methods`
  - `membership_certification_warnings`
- Replaced the old DAILY-marketcap-specific decision-grade blocker with:
  - `PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED`
  - `CURRENT_SCALE_MEMBERSHIP_NOT_DECISION_GRADE`

Changed `research/replay_certification.py`:

- Added `require_decision_grade_membership`.
- Decision-grade status now depends on `membership_certification_status == PASS`.
- `require_decision_grade_scale` remains as a backward-compatible alias.

Changed `scripts/research/certify_canonical_replay.py`:

- Added canonical strict flag:
  - `--require-decision-grade-membership`
- Retained:
  - `--require-decision-grade-scale`
  as a backward-compatible alias.

Changed tests:

- `Tests/test_replay_certification.py`
- `Tests/test_canonical_replay_panel.py`

The tests now prove:

- current-scale `scalemarketcap` fails;
- PIT DAILY marketcap can pass;
- PIT index membership can pass without DAILY marketcap when the manifest certifies the required controls.

## 3. Membership Methodology Evaluation

Existing FR-068 membership artifacts reviewed:

| Artifact | Rows | Method | Assessment |
|---|---:|---|---|
| `data/pit_universe/membership_universe.csv` | 20,618 | `sharadar_security_existence` | PIT-valid security existence, but not a large-cap membership methodology |
| `data/pit_universe/membership_universe_large_cap.csv` | 1,600 | `scale_source=scalemarketcap` | Large-cap-shaped, but current-scale and not date-effective |
| `outputs/research/canonical_pit_replay/2026-06-22/manifest.json` | n/a | inherited current-scale family | Not certifiable |
| `outputs/research/canonical_pit_replay/2026-06-22/decision_tape_manifest.json` | n/a | inherited current-scale family | Not certifiable |

Strongest existing candidate:

`data/pit_universe/membership_universe_large_cap.csv`

Reason: it is the only existing large-cap family artifact, is security-id keyed, includes delisted securities, and has reproducible resolver wiring.

Why rejected for PASS:

- all 1,600 rows use `scale_source=scalemarketcap`;
- current `scalemarketcap` is not date-effective;
- it can project current/future size status backward;
- it violates the approved `No current scalemarketcap dependency` requirement.

No existing artifact satisfies all required controls for large-cap membership.

## 4. Rebuild Results

Rebuilt canonical replay panel with the revised certification metadata:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 scripts/research/build_canonical_pit_replay_panel.py \
  --start-date 2012-06-01 \
  --end-date 2024-12-31 \
  --replay-id canonical_pit_replay_2012_2024_membership_standard \
  --output-dir outputs/research/canonical_pit_replay/2026-06-23
```

Manifest result:

```json
{
  "membership_certification_methods": ["CURRENT_SCALE_APPROXIMATION"],
  "membership_certification_status": "FAIL",
  "membership_scale_source_values": ["scalemarketcap"],
  "decision_grade_blockers": [
    "CURRENT_SCALE_MEMBERSHIP_NOT_DECISION_GRADE",
    "PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED"
  ],
  "row_count": 3924559,
  "security_count": 1580,
  "duplicate_date_security_id_count": 0,
  "missing_price_file_count": 0,
  "malformed_price_file_count": 0
}
```

The canonical large-cap membership artifact was not overwritten because no replacement methodology passed the approved standard.

## 5. Certification Result

Strict command:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 scripts/research/certify_canonical_replay.py \
  --panel outputs/research/canonical_pit_replay/2026-06-23/price_panel.parquet \
  --manifest outputs/research/canonical_pit_replay/2026-06-23/manifest.json \
  --output outputs/research/canonical_pit_replay/2026-06-23/decision_grade_certification_result.json \
  --require-decision-grade-membership
```

Result:

```json
{
  "status": "FAIL",
  "decision_grade_status": "FAIL",
  "findings": [
    "CURRENT_SCALE_MEMBERSHIP_NOT_DECISION_GRADE",
    "PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED"
  ],
  "warnings": [
    "MEMBERSHIP_NOT_DECISION_GRADE:FAIL"
  ]
}
```

Non-strict infrastructure certification remains usable but only PARTIAL:

```json
{
  "status": "PASS",
  "decision_grade_status": "PARTIAL",
  "findings": [],
  "warnings": [
    "MEMBERSHIP_NOT_DECISION_GRADE:FAIL"
  ]
}
```

## 6. Validation Results

Commands run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -m py_compile \
  research/canonical_replay_panel.py \
  research/replay_certification.py \
  scripts/research/certify_canonical_replay.py
```

Result: PASS.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -m pytest -p no:cacheprovider \
  Tests/test_canonical_replay_panel.py \
  Tests/test_replay_certification.py
```

Result: 11 passed.

## 7. Remaining Blockers

Exact remaining blockers:

1. `PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED`
2. `CURRENT_SCALE_MEMBERSHIP_NOT_DECISION_GRADE`
3. `PRICING_CERTIFICATION_MUST_BE_RECHECKED_AFTER_MEMBERSHIP_REPLACEMENT`

## 8. Stop Decision

Certification result is FAIL.

Per phase rules, downstream work stopped:

- conviction allocation PIT rebaseline was not run;
- sleeve promotion PIT rebaseline was not run;
- no allocator, production, broker, execution, scheduler, live, or paper behavior changed.

Final status: FAIL.
