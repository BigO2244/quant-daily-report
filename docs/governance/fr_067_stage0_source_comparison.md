# FR-067 Vela — Stage 0 PIT Small-Cap Universe Source Comparison

Status: Draft (decision input — owner must pick a source before Stage 1)
Owner: Caerus Research Program
Last Updated: 2026-06-10
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL (no execution, broker, cron, registry, allocation,
or paper/live behavior is changed by this document)

## Purpose and Boundary

FR-067 (Vela small-cap momentum sleeve) is **blocked** until a point-in-time
(PIT) small-cap universe exists. This document is the Stage 0 deliverable: a
written comparison of candidate universe/price sources so the owner can make the
source decision recorded in `CURRENT_RESEARCH_ROADMAP.md`. **No strategy code is
written until that pick is made** (FR-067 Integration Points; roadmap Section 6).

The hard requirement Stage 0 must satisfy (FR-067 §"Hard Dependency"):

1. Historical index membership with entry/exit dates, OR a PIT-safe market-cap
   band that reproduces the small-cap venue honestly.
2. Delisted names included, with delisting returns handled conservatively.
3. Validated price coverage for **dead tickers** (yfinance is known-weak here).
4. A per-rebalance universe snapshot artifact
   (`outputs/research/vela/universe/universe_<date>.csv`) with membership-source
   metadata.

Explicitly forbidden (FR-067): hand-curating today's small-cap names and
backtesting them historically. That is the exact survivorship-bias failure of
`data/universe.csv` (201 names applied retroactively to 2014) that this FR
exists to avoid. Any source that silently drops dead tickers reproduces the bias.

> Pricing figures below are indicative ranges for budgeting only and **must be
> reconfirmed against current vendor quotes** before any purchase. License tiers
> (personal vs commercial/fund) materially change cost and are the single most
> important diligence item, because Caerus is a fund, not a hobby account.

## Candidate Sources

The four candidates are the two commercial vendors named in FR-067 (Norgate,
Sharadar), the reconstructed-S&P-600 approach, and CRSP/WRDS (the academic gold
standard, included for completeness because FR-067 mentions it).

### A. Norgate Data

- **PIT index membership:** Strong. Norgate ships historical constituent
  membership with entry/exit dates for S&P 500/400/**600**, Russell 1000/2000/
  3000, Nasdaq 100, and others, via `norgatedata.index_constituent_timeseries()`.
  This directly satisfies the "S&P SmallCap 600 constituent history" preference.
- **Delisted-ticker price coverage:** Strong. Delisted securities are retained
  with full adjusted OHLCV history and are the core of Norgate's
  survivorship-bias-free design. Delisting is explicitly handled.
- **Cost (indicative, verify):** Personal subscription roughly USD ~$50–80/month
  for the US-stock package (annual billing cheaper). Low for a single seat.
- **License:** Personal/individual subscriber license; **no redistribution**.
  Fund/commercial use is a separate (more expensive, possibly bespoke)
  arrangement that must be confirmed with Norgate before relying on it.
- **Integration effort:** **High friction for this stack.** Data is served from
  the Norgate Data Updater (NDU), a **Windows desktop application**; the
  `norgatedata` Python package reads the local NDU database. The Caerus scheduler
  is a Linux GCP VM and local dev is macOS (arm64). Using Norgate means standing
  up and maintaining a Windows host/VM and an export bridge to the Linux stack —
  a standing operational dependency, not a one-time integration.

### B. Sharadar (via Nasdaq Data Link)

- **PIT index membership:** Partial / different shape. Sharadar provides
  `SHARADAR/SP500` (S&P 500 membership change events) but **not** an S&P SmallCap
  600 constituent history. However, the `SHARADAR/DAILY` table carries **daily
  market cap per ticker**, which lets us build a PIT, survivorship-free small-cap
  universe by **market-cap band** (e.g. the $300M–$2B band, or a fixed rank band
  by market cap) recomputed each rebalance date. This is arguably a cleaner match
  to Vela's *economic* thesis ("capacity-constrained $300M–$2B names") than
  index membership, and it sidesteps index-licensing entirely.
- **Delisted-ticker price coverage:** Strong. `SHARADAR/SEP` covers ~16k+ US
  equities **including delisted tickers**, with a corporate-actions table and
  conservative delisting handling. Survivorship-bias-free is the product's
  headline feature. This is the dimension yfinance fails, and Sharadar passes it.
- **Cost (indicative, verify):** The Sharadar Core US Equities bundle (SEP + SF1
  + DAILY + TICKERS + ACTIONS) is roughly low-hundreds USD/month on an individual
  license; commercial/fund licensing is higher. Reconfirm tiers.
- **License:** Nasdaq Data Link license with distinct personal vs
  commercial/institutional tiers. Fund use requires the commercial tier — confirm
  before production use.
- **Integration effort:** **Low, and native to this stack.** REST API + Python
  (`nasdaqdatalink`) with **bulk table export** (whole-table CSV/zip), runs on
  Linux/macOS, no desktop dependency. Bulk exports fit the repo's existing
  parquet/CSV artifact pattern (cf. `edgar_ingestion.py`) and cron model cleanly.

### C. Reconstructed S&P SmallCap 600 (from index announcements)

- **PIT index membership:** Buildable but fragile. Membership is reconstructed
  from S&P Dow Jones Indices add/delete press releases layered on a starting
  snapshot. Announcement date vs effective date differ; historical announcements
  are incomplete and hard to source cleanly going back years.
- **Delisted-ticker price coverage:** **None inherent.** Reconstruction yields
  *membership only* — it provides **no prices**. A separate delisted-price source
  (Sharadar, Norgate, or CRSP) is still required, so this approach does not, on
  its own, solve the dependency that actually blocks the FR.
- **Cost:** "Free" inputs (public announcements) but very high labor; the
  official S&P constituent-history product is a paid, licensed dataset.
- **License:** S&P index membership is proprietary/licensed; reconstructing it
  from press releases is legally grey and contractually risky for a fund, and the
  result is not authoritative.
- **Integration effort:** **Highest and most error-prone.** Bespoke scraping +
  reconciliation, hard to validate, and most likely to silently reintroduce
  survivorship/look-ahead bias — the precise failure mode FR-067 exists to kill.

### D. CRSP via WRDS (academic gold standard) — reference only

- **PIT membership + delisting:** Best in class. CRSP delisting returns are the
  academic standard; survivorship-bias-free by construction.
- **Cost / License:** Institutional WRDS subscription, typically only available
  through an academic affiliation; expensive and license-restricted for a private
  fund. Listed for completeness; not a realistic primary source for Caerus today
  unless an academic affiliation exists.
- **Integration:** WRDS Python/SQL access is clean, but access eligibility — not
  integration — is the blocker.

## Comparison Matrix

| Dimension | Norgate | Sharadar (Nasdaq Data Link) | Reconstructed S&P 600 | CRSP/WRDS |
|---|---|---|---|---|
| PIT index membership (S&P 600) | **Yes, native** | No (use PIT market-cap band) | Partial, fragile | Yes |
| PIT small-cap universe feasible | Yes (index) | Yes (market-cap band) | Membership only | Yes |
| Delisted-ticker **price** coverage | **Strong** | **Strong** | **None (prices not included)** | **Strong** |
| Conservative delisting handling | Yes | Yes (actions table) | N/A | Yes (delisting returns) |
| Indicative cost (verify) | ~$50–80/mo personal | ~low-hundreds/mo bundle | "free" inputs, high labor | Institutional $$$$ |
| License fit for a **fund** | Personal; commercial TBD | Personal vs commercial tiers | Grey / proprietary | Academic-restricted |
| Integration effort on Linux/macOS | **High (Windows NDU)** | **Low (REST/Python/bulk)** | Highest (bespoke scrape) | Low (if access) |
| Reproduces FR-066/PIT remediation pilot | Yes | **Yes** | No | Yes |

## Recommendation (advisory — owner decides)

**Primary recommendation: Sharadar via Nasdaq Data Link**, using a PIT
market-cap-banded small-cap universe rather than official S&P 600 membership.
Rationale:

1. It passes the dimension that actually blocks the FR — **delisted-ticker price
   coverage** — which yfinance fails and which reconstruction does not provide.
2. **Integration is native to the existing Linux VM / macOS stack** (REST + Python
   + bulk export), with no standing Windows dependency. Norgate's data quality is
   comparable, but its NDU-on-Windows requirement is a permanent operational tax
   on a Linux-scheduled fund.
3. The daily-market-cap band is a *cleaner* expression of Vela's economic thesis
   (capacity-constrained $300M–$2B names) than index membership, and it avoids
   index-membership licensing entirely.
4. Stage 0 deliverables double as the **program-wide PIT remediation pilot**
   (FR-067); Sharadar's universe-wide, survivorship-free history is directly
   reusable for Polaris/Orion/Lyra large-cap re-validation.

**Choose Norgate instead if** official S&P SmallCap 600 *membership* (not a
market-cap proxy) is a hard requirement and a maintained Windows host for NDU is
acceptable.

**Do not adopt reconstructed S&P 600 as a primary source** — it yields no prices,
carries the highest survivorship/look-ahead risk, and is licensing-grey. It is
only useful as a secondary cross-check of a priced source's membership.

**CRSP/WRDS** is the gold standard but is gated on academic access; revisit only
if such access exists.

## Open Decisions Routed to Owner (no code until resolved)

1. **Source pick** (Sharadar recommended) — record in
   `CURRENT_RESEARCH_ROADMAP.md`. This unblocks FR-067 Stage 1.
2. If Sharadar: **index membership vs market-cap band** for universe definition
   (recommend band; freeze the band rule — e.g. $300M–$2B, US common stock — before
   any tuning, per FR-067's pre-registration discipline).
3. **License tier** (personal vs commercial/fund) must be confirmed for the
   chosen vendor before any production or shadow use.
4. FR-067 open questions 2–4 (IWM vs S&P 600 benchmark; regime gate inputs;
   REIT/biotech exclusion) remain owner decisions to freeze before Stage-1 tuning;
   they do not block the source pick.

## What Stage 1 Becomes Once a Source Is Picked

With a source approved, Stage 1 builds the PIT universe machinery and the
per-rebalance snapshot artifact
(`outputs/research/vela/universe/universe_<date>.csv` with membership-source
metadata), validates dead-ticker price coverage, and — if coverage is incomplete
in the early window — moves the backtest start date forward to where coverage is
provably complete and records that limitation in every artifact (FR-067 §Hard
Dependency). No `research/vela/` strategy code is written before that.
