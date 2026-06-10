# data/pit_universe/ — Point-in-Time Universe artifacts (FR-068 Phase 1)

Generated, **gitignored** vendor-derived artifacts (regenerable from Sharadar,
like `data/fundamental/`). Only this README is tracked.

## Generate

```bash
# canonical ingest (paid Sharadar key; key never logged)
python3 scripts/research/build_pit_universe_from_sharadar.py --api-key "$NASDAQ_DATA_LINK_API_KEY"

# no-network smoke seed (embedded fixture; no key)
python3 scripts/research/build_pit_universe_from_sharadar.py --demo-fixture
```

## Artifacts

| File | Purpose |
|---|---|
| `security_master.csv` | Identity (`security_id` = `SHARADAR:<permaticker>`), ticker, name, exchange, category, isdelisted, first/last price date, relatedtickers, currency, location, source, lastupdated, confidence |
| `symbol_history.csv` | `security_id` → related/prior tickers (ticker-change linkage) |
| `security_events.csv` | DELISTING events for inactive names (effective = lastpricedate) |
| `membership_universe.csv` | `sharadar_security_existence` family rows (start = firstpricedate, end = lastpricedate or open) |
| `manifest.json` | source, retrieved_at, row counts, sha256 per file, filters, schema_version |

## Read

Use `research.pit_universe.Universe(as_of_date)` — the canonical PIT interface.
It never falls back to `data/universe.csv`; missing artifacts raise
`PITUniverseUnavailable`. Phase 1 implements **security-existence** PIT only;
historical *index* membership families (sp500_proxy, small_cap_band) come later.

Governance: RESEARCH_ONLY / NON_EXECUTIONAL. No execution, broker, cron, or
strategy-registry behavior depends on these artifacts.
