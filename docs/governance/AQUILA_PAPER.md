# Caerus Aquila — experimental PAPER contract

Owner: Brett Olson. Decision date: 2026-09-08. Strategy:`caerus_aquila`.
Brett authorized the existing Alpaca PAPER account and50% Aquila. TOP10_EQUAL
is the stated working model. This is prospective evidence collection; the
proxy backtest did not establish reliable incremental alpha. No Live approval.

At the initial eligible formation and the first trading session of each month,
rank current S&P500 companies by market capitalization from the last completed
session. Combine share classes by issuer identity and select one execution
symbol per issuer. Apply the explicit owner exclusions below, then own the ten
largest eligible issuers, each at5% of account NAV.
Orion receives 45%and target cash is 5%. The registry represents invested-risk
budgets as 10/19 Aquila and 9/19 Orion. No growth/inflation overlay is active.

Between formations, Aquila holds its actual reconciled shares. Daily sources
mark those shares with the canonical previous-session close; authorization
uses fresh broker prices to preserve quantities. Orion uses the residual
account allocation after held Aquila exposure and5% target cash. Weights drift.
No automatic catch-up or replacement formation follows partial execution.
Missing, stale, inconsistent or incomplete evidence blocks the account plan.

Yahoo membership/rank captures are immutable prospective inputs. The collector
uses at most 500 issuer lookups, 1600 actual HTTP requests, 4 workers, 4 requests
per second, 900 seconds and 64 MiB. Failed collections publish no formation.
Source-only probes cannot authorize holdings. Normal capture must occur after
the previous close and before the execution session opens. Historical proxy
results are not a substitute for a fresh source. Execution/cache symbol aliases
are explicit and collision-checked.

The existing account emergency threshold and recovery observations remain
unchanged. Adding a sleeve must not reset Orion's persistent risk namespace.
Risk-adjusted Aquila targets require explicit reconciled intervention. Opposing
sleeve demands in a shared stock require a receipt-bound ownership transfer;
until supported, that plan fails closed. These are operating limitations, not
permission to bypass the risk or accounting controls.

A hash-bound prospective opening contract assigns existing holdings to Orion
from a fresh reconciled PAPER snapshot with no open orders. The immutable
historical fill prefix remains unchanged; historical unattributed P&L is never
claimed as Orion or Aquila performance. New fills carry exact order and sleeve
decision lineage. Independent ownership audit and posttrade attainment remain
required. Cash floor, price collars, asset checks and reconciliation still apply.

Review prospective results at monthly formation: returns, SPY and sector/factor
comparisons, drawdown, turnover, costs, tracking error and operational failures.
No reactive tuning or automatic scale-up. Any allocation change, retirement or
Live decision requires Brett's separate approval. Deployment attestation,
registry, exact execution receipts and broker truth govern actual runtime state.

## Source collection repair — September 9

The collector requests raw Yahoo v7 quotes directly. A missing marketCap may
use Yahoo's provider-reported quoteSummary price-module marketCap only when
symbol, USD currency, regular-market epoch and price exactly match the v7
quote. The v7 quote and cap fragment retain separate immutable source hashes.
No shares-times-price calculation, silently skipped eligible issuer or stale
substitution is allowed. Transient 502/503/504 responses get at most three attempts per required
endpoint within the original global limits. Rate limits and malformed or
incomplete evidence fail closed. Source-only probes never publish formations.

## Owner eligibility decision — September 9

Brett excludes AutoZone (AZO) prospectively because he does not want to hold
it. The existing strategy registry records this explicit exclusion. Retain
full raw membership, but exclude AZO before quote collection and rank only
the remaining eligible issuers. The ranking records the policy hash, excluded
issuer identity and coverage counts. All remaining eligible issuers still
require complete valid source fields. Aquila formation and quantity contracts
reject AZO. This is an owner preference, not an inferred historical rank result.
Existing failures, captures and backtests remain unchanged. No liquidation is
triggered by this source change; a held excluded position fails closed for
owner review. Deployment receipts establish runtime activation.
