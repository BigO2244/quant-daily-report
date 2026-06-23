# FR-068 PIT Universe Certification

Date: 2026-06-22
Program: Decision-Grade PIT Research Infrastructure Completion
Phase: 1 - FR-068 Universe Certification
Governance label: RESEARCH_ONLY / NON_EXECUTIONAL
Runtime impact: none
Gate result: PASS

## Objective

Certify that canonical `Universe(as_of_date, "caerus_large_cap")` resolves the existing FR-068 large-cap membership artifact instead of returning zero members.

## Root Cause

`Universe()` only read `data/pit_universe/membership_universe.csv`. The `caerus_large_cap` family is materialized separately at `data/pit_universe/membership_universe_large_cap.csv`, so filtering the canonical membership file for `caerus_large_cap` returned an empty row set.

The failure mode was silent and deterministic: the resolver returned zero members even though the family artifact contained valid date-effective rows.

## Correction

`research/pit_universe.py` now registers explicit family membership files:

- `caerus_large_cap` -> `membership_universe_large_cap.csv`

Resolution order:

1. Read `membership_universe.csv`.
2. If the requested family is found there, use it.
3. If the family is registered separately, read the family-specific artifact.
4. If the family is unknown or missing, raise `PITUniverseUnavailable`.
5. Never fall back to `data/universe.csv`.

## Certification Results

| Date | Expected members | Canonical members | Result |
| --- | ---: | ---: | --- |
| 2014-01-02 | 1,197 | 1,197 | PASS |
| 2020-01-02 | 1,243 | 1,243 | PASS |
| 2026-01-02 | 1,260 | 1,260 | PASS |

Command:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 - <<'PY'
from research.pit_universe import Universe
for d in ['2014-01-02','2020-01-02','2026-01-02']:
    rows = Universe(d, 'caerus_large_cap')
    print(d, len(rows), rows[0]['security_id'], rows[-1]['security_id'])
PY
```

Output:

```text
2014-01-02 1197 SHARADAR:103779 SHARADAR:573113
2020-01-02 1243 SHARADAR:110294 SHARADAR:376609
2026-01-02 1260 SHARADAR:110006 SHARADAR:645956
```

## Validation

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -m py_compile research/pit_universe.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -m pytest -p no:cacheprovider Tests/test_pit_universe.py Tests/test_pit_phase_2_5.py
```

Result:

```text
26 passed
```

## Caveats

- This certifies the canonical resolver and family artifact wiring.
- This does not certify that the current `caerus_large_cap` family is PIT-exact by market-cap rank through history.
- The existing family artifact uses current `scalemarketcap`; DAILY numeric market cap remains the PIT-exact source for historical large-cap membership.
- Downstream replay certification must label this family as `PIT_APPROXIMATE_SCALE` until a DAILY-marketcap family replaces it.

## Files Changed

- `research/pit_universe.py`
- `Tests/test_pit_universe.py`
- `reports/pit_universe_certification.md`

## Gate

PASS for Phase 1.
