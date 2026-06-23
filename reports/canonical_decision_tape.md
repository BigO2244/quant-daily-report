# FR-069 Child - Canonical Decision Tape

Date: 2026-06-22
Program: Decision-Grade PIT Research Infrastructure Completion
Phase: 3 - Canonical Decision Tape
Governance label: RESEARCH_ONLY / NON_EXECUTIONAL
Runtime impact: none
Gate result: PASS for reproducible historical reconstruction; PARTIAL for decision-grade replay

## Objective

Build daily PIT decision tapes for Polaris, Orion, and Lyra using the canonical security_id replay panel.

## Artifacts

| Artifact | Path |
| --- | --- |
| Decision tape builder | `research/canonical_decision_tape.py` |
| Build CLI | `scripts/research/build_canonical_decision_tapes.py` |
| Polaris tape | `outputs/research/canonical_pit_replay/2026-06-22/decision_tape_caerus_polaris.parquet` |
| Orion tape | `outputs/research/canonical_pit_replay/2026-06-22/decision_tape_caerus_orion.parquet` |
| Lyra tape | `outputs/research/canonical_pit_replay/2026-06-22/decision_tape_caerus_lyra.parquet` |
| Decision tape manifest | `outputs/research/canonical_pit_replay/2026-06-22/decision_tape_manifest.json` |

## Method

The canonical price panel is projected into the existing alpha-lab signal engine with `ticker == security_id`. The original display ticker is attached only as display metadata in the output tape.

Fields emitted:

- `trade_date`
- `security_id`
- `ticker`
- `sleeve`
- `candidate`
- `rank`
- `score`
- `target_weight`

## Results

| Sleeve | Spec | Candidate rows | Candidate securities | Selected rows | Average selected count |
| --- | --- | ---: | ---: | ---: | ---: |
| `caerus_polaris` | `baseline_top10_daily` | 3,360,860 | 1,557 | 27,670 | 10.000000 |
| `caerus_orion` | `h2_rank_decay_exit_h6_top5` | 3,360,860 | 1,557 | 13,835 | 5.000000 |
| `caerus_lyra` | `h1_weekly_h6_top5` | 3,360,860 | 1,557 | 13,803 | 4.992043 |

## Reproducibility

The tapes were rebuilt to a temporary directory and the tape hashes matched exactly.

| Sleeve | SHA256 |
| --- | --- |
| `caerus_polaris` | `d05edec0a827056ceb6d329e3ec5c15deae6a138a2187d52ebd26348e6cffd04` |
| `caerus_orion` | `5383fb2477d6824f0b437bbfd51d0de5a4d87b0be60bdfb4c57b5b14302f4cf3` |
| `caerus_lyra` | `3dcf0ab230237296a7124e94cae58a042cc4dd823532fb0d088f05390d78b322` |

## Validation Commands

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -m py_compile \
  research/canonical_decision_tape.py \
  scripts/research/build_canonical_decision_tapes.py

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -m pytest -p no:cacheprovider \
  Tests/test_canonical_decision_tape.py \
  Tests/test_canonical_replay_panel.py \
  Tests/test_replay_certification.py

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 scripts/research/build_canonical_decision_tapes.py \
  --panel outputs/research/canonical_pit_replay/2026-06-22/price_panel.parquet \
  --manifest outputs/research/canonical_pit_replay/2026-06-22/manifest.json \
  --output-dir outputs/research/canonical_pit_replay/2026-06-22 \
  --start-date 2014-01-02 \
  --end-date 2024-12-31 \
  --sleeves caerus_polaris,caerus_orion,caerus_lyra
```

Result:

```text
10 passed
reproducibility match True
```

## Caveat

The tapes inherit the price-panel membership caveat:
`PIT_DATE_EFFECTIVE_LARGE_CAP_MEMBERSHIP_REQUIRED`.

## Gate

PASS for reproducible security_id-keyed decision tapes.

PARTIAL for decision-grade downstream allocator or promotion replay until
PIT-valid, survivorship-free, security-id keyed, date-effective large-cap
membership is supplied.
