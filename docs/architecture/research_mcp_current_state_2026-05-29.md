# Research MCP — Current State Assessment

**Snapshot date:** 2026-05-29 · **Author:** Caerus engineering ·
**Type:** point-in-time conformance + roadmap audit

Historical status: this is a point-in-time snapshot, not the current MCP
capability inventory. Verify current tool and capability counts from
`research_registry/`, `research_registry/mcp_server/`, and current tests before
making present-tense claims.

## Document Contract

| Field | Value |
|---|---|
| Purpose | Record point-in-time Research MCP implementation state as of 2026-05-29. |
| Owner | Caerus engineering named in header; repository verification still required for current-state claims. |
| Inputs | Research MCP code, research registry code, tests, and generated MCP artifacts available on 2026-05-29. |
| Outputs | Historical MCP capability and gap assessment. |
| Related Documents | `docs/architecture/caerus_research_mcp_architecture.md`, `docs/architecture/semantics/README.md`, `docs/architecture/DOCUMENT_INVENTORY.md`. |
| Related Tests | Research registry and MCP tests listed in `docs/architecture/DOCUMENT_INVENTORY.md`. |
| Related Implementation | `research_registry/`, `research_registry/mcp_server/`, MCP scripts. |
| Related Artifacts | `outputs/research_mcp/`, `outputs/shadow_candidates/`. |
| Known Gaps | Historical snapshot; refresh from code/tests before using it as current capability inventory. |

This document is a deliberate counter-balance to
[`caerus_research_mcp_architecture.md`](caerus_research_mcp_architecture.md),
which is the **aspirational** design intent (1320 lines, drafted
2026-05-21, pre-implementation). The architecture doc describes what
the MCP *could become*. **This document describes what it actually is
on 2026-05-29**, with citations to the source files that prove each
claim. Where the architecture and reality disagree, reality wins here.

For frozen semantic contracts the MCP must honour, see
[`semantics/`](semantics/). Those are not re-litigated in this doc;
they remain the constitutional layer.

---

## A. What is complete

### A.1 Server + transport layer

| Component | File | Status |
|---|---|---|
| Stdio JSON-RPC server | `research_registry/mcp_server/server.py` | Live |
| Tool dispatch | `research_registry/mcp_server/tools.py` | Live, **20 tools** registered |
| Tool schema definitions | `research_registry/mcp_server/schemas.py` | Live, schemas for all 20 tools |
| CLI entrypoint | `scripts/research_registry_mcp_server.py` | Live, `tools`/`smoke`/`stdio` subcommands |
| Operator gateway | `scripts/research_mcp_ask.py` (+ `.sh` shim) | Live, one-command question interface |

### A.2 Implemented MCP tools (20)

Grouped by purpose. Names are exact and dispatchable via `call_tool`.

**Registry indexing / inspection (9):**
`build_caerus_registry`, `latest_runs`, `run_health`, `integrity_findings`,
`governance_open`, `research_packet_status`, `registry_summary`,
`query_registry`, `lineage`.

**Operator intelligence (6):**
`daily_operator_brief`, `artifact_status`, `operator_daily_summary`,
`artifact_drilldown`, `morning_cio_brief`, `anomaly_report`.

**Research-question tools (4):**
`promotion_readiness`, `execution_timing_by_vix_regime`,
`execution_timing_summary`, `shadow_comparison`.

**Planner (1):** `answer_research_question` — capability-based router.

### A.3 Capability-based router

`research_registry/research/capabilities.py` defines a machine-readable
`CAPABILITY_REGISTRY` with 9 entries. Each capability declares:

- `name` — stable intent identifier
- `description` — one-paragraph human description
- `patterns` — case-insensitive regex strings
- `required_artifact_globs` — repo-root-relative paths/globs
- `tool_name` — existing MCP tool to dispatch to (or `None` if not yet built)
- `tool_kwargs`, `output_fields`, `limitations`, `example_questions`,
  `suggested_next_build` (the last only when `tool_name is None`)

`answer_research_question` runs **classify → artifact-check → route →
wrap** with four terminal statuses: `OK`, `NEEDS_DATA`,
`NEEDS_CAPABILITY`, `UNSUPPORTED_INTENT`. Ties in classification are
broken by registry order (deterministic). No LLM call, no external
network, no execution-path coupling.

### A.4 Capability matrix (point-in-time snapshot)

| Capability | Status | Routed tool | Required artifacts | Questions answered today |
|---|---|---|---|---|
| `execution_timing_summary` | **implemented** | `execution_timing_summary` | `outputs/research/execution_timing/*/timing_summary.json` | "Is 9:35 better than 9:30?", "What is the best execution time?", "Compare 9:30 and 9:35." |
| `timing_by_vix_regime` | **implemented** | `execution_timing_by_vix_regime` | timing replay + `outputs/vix_regime/regime_history.csv` | "Does timing matter in high VIX regimes?", "How does regime affect execution timing?" |
| `shadow_comparison` | **implemented** | `shadow_comparison` | `outputs/shadow_candidates/<DATE>/shadow_evaluation.json` | "How is Polaris doing versus Orion?", "Which strategy is performing best?", "Compare Orion and Lyra." |
| `promotion_readiness` | **implemented** | `promotion_readiness` | (tool tolerates missing) | "Is Orion ready for promotion?" |
| `anomaly_report` | **implemented** | `anomaly_report` | (tool tolerates missing) | "What are today's anomalies?", "Anything stale?" |
| `morning_brief` | **implemented** | `morning_cio_brief` | (tool tolerates missing) | "What ran today?", "Today's brief." |
| `regime_intelligence` | **implemented** | `morning_cio_brief` | `outputs/vix_regime/regime_current.json` | "What is the current VIX regime?" |
| `attribution_analysis` | **planned (stub)** | — | — | none yet; matches "alpha attribution", "what drove returns" → NEEDS_CAPABILITY |
| `stable_window_evaluation` | **planned (stub)** | — | — | none yet; matches "stable window", "random windows" → NEEDS_CAPABILITY |

### A.5 Ingestion families (registered)

`research_registry/ingestion/families.py` registers 16 family adapters
(execution_run, execution_integrity, research_packet, governance_doc,
attribution, shadow_evaluation, regime_intelligence,
performance_veracity, exposure_intelligence, validation,
execution_timing, vix_regime_history, plus four grandfathered/audit
families). Smoke output as of 2026-05-29: 92 objects, 75 lineage edges,
0 surface conflicts, registry digest stable.

### A.6 Test coverage

| Test file | Tests | Subject |
|---|---:|---|
| `Tests/test_research_registry_mcp_server.py` | 19 | JSON-RPC dispatch + 16 grandfathered tools |
| `Tests/test_research_registry_capabilities.py` | 27 | Registry schema, classifier, artifact checker, end-to-end routing for 6 question families |
| `Tests/test_research_registry_timing_regime.py` | 36 | timing × VIX regime loader + planner integration |
| `Tests/test_research_registry_timing_summary.py` | 16 | aggregate timing summary + recommendation rule |
| `Tests/test_research_registry_shadow_comparison.py` | 17 | shadow comparison loader + leader selection + NEEDS_DATA paths |
| `Tests/test_research_mcp_ask.py` | 16 | gateway: pure rendering, CLI behaviour, exit codes, subprocess smoke |
| **Total** | **131** | All passing as of 2026-05-29 |

### A.7 Operator-facing artifacts

| Artifact | Purpose |
|---|---|
| `scripts/research_mcp_ask.py` | One-command question gateway |
| `scripts/research_mcp_ask.sh` | Shell wrapper that resolves repo root |
| `outputs/research_mcp/questions/<TIMESTAMP>/answer.{json,md}` | Per-invocation deterministic record (gitignored under existing `outputs/` rule) |
| `docs/operator/research_mcp_operator_guide.md` | This release's first operator-facing MCP doc |

---

## B. What is partially complete

### B.1 Architecture-to-implementation abstraction gap

The architecture document (Section 15) lists **~21 individual tools**
like `compare_strategies`, `assess_promotion_readiness`,
`trace_lineage`, `get_strategy_overview`. The actual implementation
collapses many of these behind the `answer_research_question` planner
+ `CAPABILITY_REGISTRY` indirection. The two views are not in conflict
— the planner is a *layer* above the tools, and most tools the
architecture names map cleanly to a capability that already exists or
a planned capability. But the abstraction layer itself is **not in the
architecture document**. A future revision of that doc (or its v2
successor) should formalise the planner pattern.

### B.2 Conformance against the frozen semantics layer

`docs/architecture/semantics/` contains 8 SEM specs + 5 freeze/boundary
docs declared frozen on 2026-05-21. They make normative claims about
provenance, confidence floor propagation, governance inheritance,
temporal honesty, and registry invariants. **No conformance audit
document yet certifies that the implemented MCP honours every clause.**
The implementation is *believed* to honour them — confidence stays at
`LOW` for grandfathered artifacts, lineage edges are written, surface
conflicts are reported as zero — but this has not been audited end to
end against each SEM doc. Listed in §D as a near-term investment.

### B.3 Ingestion vs. query symmetry

The two newest ingestion adapters (`execution_timing`,
`vix_regime_history`) are registered in `FAMILY_ADAPTERS` and exported,
but they are not yet **wired into `build_caerus_registry`'s default
family list**. They hydrate on demand via `ingest_artifact_family` but
do not appear in the smoke output's family roll-up. This is a small
cosmetic gap; the registry can index them, just not by default. Listed
in §D.

### B.4 Strategy-aware promotion readiness

`promotion_readiness` runs generically against the shadow_candidates
tree. The capability registry routes "Is Orion ready for promotion?"
to it cleanly, but the strategy name in the question is **informational
only** — the tool does not filter or focus on one specific challenger.
The capability is implemented but the tool is not strategy-aware. The
matrix in §A.4 honestly labels this; the operator guide warns about
it. Future enhancement, not a bug.

### B.5 Gateway renderer coverage

The gateway's human-readable renderer covers: `OK` with regime
aggregates, `OK` with shadow panels, `OK` with timing-summary tables,
`NEEDS_DATA`, `NEEDS_CAPABILITY`, `UNSUPPORTED_INTENT`,
`NO_TIMING_DATA`, `NO_REGIME_DATA`, `BAD_REGIME_SCHEMA`. Inner statuses
from `promotion_readiness` / `anomaly_report` / `morning_cio_brief`
bubble up unchanged and are displayed via the fall-through `Status:`
line + `Warnings:` list — they don't get bespoke rendering yet. Good
enough for operator use; rich rendering is a nice-to-have.

---

## C. What is missing

### C.1 No implemented analysis tools for two recognised capabilities

`attribution_analysis` and `stable_window_evaluation` are in the
registry with `tool_name=None`. Asking those questions returns a clean
`NEEDS_CAPABILITY` with a `suggested_next_build` paragraph naming the
exact artifacts to read. The analyses themselves do not exist.

### C.2 No ingestion family for shadow_candidates as such

The `shadow_comparison` tool reads `shadow_evaluation.json` /
`comparison.json` directly via its own loader. The `shadow_evaluation`
adapter exists as a generic grandfathered family but does not parse the
per-strategy panel into typed objects with lineage. If we wanted
"`lineage(strategy=caerus_polaris)`" to walk back through the shadow
artifacts, we would need a richer ingestion family.

### C.3 No conformance audit doc

See §B.2.

### C.4 No deployment runbook for the MCP server

`docs/mcp_server.md` covers the CLI surface and the smoke command, but
there is no operational runbook for: starting the stdio server as a
long-running process on the VM, restarting on failure, monitoring
health, rotating the disposable SQLite DB, or troubleshooting from
operator logs. Today the operator runs the gateway interactively, so
this gap is tolerable. If we ever wire the MCP into a continuously-
listening surface (e.g., a Claude Desktop or VSCode integration on the
VM), the runbook becomes a hard requirement.

### C.5 No CLI surface for `answer_research_question` outside the gateway

The MCP server's `tools` subcommand prints all 20 schemas, but there is
no `python scripts/research_registry_mcp_server.py ask "..."` shortcut.
The gateway (`research_mcp_ask.py`) is the answer there, and it works
— but a server-side subcommand would make non-interactive scripted use
slightly easier. Optional, not blocking.

---

## D. What should be built next

Prioritised below in §F (Strategic recommendation). At a high level:

1. **Conformance audit doc** mapping each frozen SEM clause to the
   implementation that honours it (or to the gap that doesn't).
2. **`attribution_analysis` tool + capability flip** — read
   `outputs/attribution/attribution_summary.json` and
   `factor_exposure.json`, return per-factor / per-strategy
   decomposition. Highest-value missing capability.
3. **Strategy-aware promotion readiness** — accept a `strategy` arg and
   filter the shadow_candidates lookup; eliminate the "informational
   only" footnote.
4. **`build_caerus_registry` defaults extended** to include the two new
   ingestion families so smoke output is complete.
5. **`stable_window_evaluation` tool + capability flip** — consume the
   alpha-lab `random_windows_*.csv` outputs already on disk.

---

## E. Estimated maturity level

We define five levels for this project:

| Level | Definition | Evidence required |
|---|---|---|
| 0 | No MCP | — |
| 1 | Registry exists | Tools that index and query artifacts by structured filters. |
| 2 | Tool-based retrieval | Multiple operator tools (daily brief, promotion readiness, anomaly report). |
| 3 | Capability routing | Single NL entrypoint, registry of capabilities, deterministic routing with structured statuses (OK / NEEDS_DATA / NEEDS_CAPABILITY / UNSUPPORTED_INTENT). |
| 4 | Research question answering | Multiple research-grade analytical tools answering substantive research questions (timing × regime, shadow comparison, attribution, regime fragility). |
| 5 | Autonomous research orchestration | The MCP plans multi-step research workflows, schedules its own runs, and produces decisional artifacts without operator prompting. |

**Current level: 3, with two analytical tools beyond the routing layer
(timing × regime, shadow comparison) that begin to nudge into Level 4.**

Justification:

- **Level 1 cleared.** Registry, SQLite storage, lineage graph,
  ingestion families, query facade — all present and tested.
- **Level 2 cleared.** Operator tools (daily brief, promotion
  readiness, anomaly report, morning CIO brief, artifact status) are
  live and consumed.
- **Level 3 cleared.** The capability registry, classifier,
  artifact-availability checker, planner, and four structured terminal
  statuses are all live and tested. The gateway hides this layer from
  the operator.
- **Level 4 partial.** Two research-question tools (timing × regime,
  shadow comparison) and one aggregate (execution_timing_summary) are
  live and answer substantive research questions with real data.
  Three more research capabilities (`attribution_analysis`,
  `stable_window_evaluation`, strategy-aware comparison drill-downs)
  are recognised but not implemented. To call Level 4 cleared we want
  at least 4 implemented research-grade tools across distinct artifact
  surfaces (timing, shadow, attribution, regime fragility).
- **Level 5 not in scope** for this project window. The MCP
  constitutionally does not schedule, orchestrate, or trigger work.

---

## F. Strategic recommendation — top 5 next investments

Ranked by **(research value × operator value) / implementation effort**.
None of these expand the MCP's blast radius outside read-only artifact
consumption. None require cron, broker, or execution-path changes.

### Priority 1 — Conformance audit doc against the frozen semantics layer

**Effort:** S (1–2 days, documentation only) · **Impact:** High ·
**Dependencies:** None.

Cross-reference each SEM-001..008 clause against the implementing
module. Mark each as `IMPLEMENTED`, `PARTIAL` (with caveats), or
`PENDING`. Produces a defensible "what we promised vs. what we ship"
ledger. Highest leverage because (a) the frozen layer is the
constitutional contract and (b) without this audit the freeze is
unverifiable.

### Priority 2 — `attribution_analysis` capability promoted to implemented

**Effort:** M (2–3 days) · **Impact:** High ·
**Dependencies:** Existing `outputs/attribution/attribution_summary.json`
and `factor_exposure.json` artifacts on disk; existing
`AttributionArtifactAdapter` ingestion family.

Add a `research_registry/research/attribution.py` loader that reads
those two files, computes per-factor / per-strategy decomposition over
a date range, and returns a structured panel similar to
`shadow_comparison`. Add a `attribution_analysis` MCP tool. Flip the
capability's `tool_name`. This is the single most-asked research
question family that we currently send to `NEEDS_CAPABILITY`.

### Priority 3 — Strategy-aware promotion readiness drill-down

**Effort:** S–M (1–2 days) · **Impact:** Medium-high ·
**Dependencies:** `promotion_readiness` tool signature extension; no
new artifact sources.

Add a `strategy` kwarg to `promotion_readiness` and a corresponding
parser in the capability so "Is Orion ready?" actually focuses on
Orion's per-strategy panel rather than returning the generic challenger
verdict. Removes the operator-facing footnote.

### Priority 4 — Extend `build_caerus_registry` defaults to ingest the two new families

**Effort:** S (0.5 day) · **Impact:** Low-medium ·
**Dependencies:** None; both adapters already exist.

Wire `execution_timing` and `vix_regime_history` into the smoke /
`build_caerus_registry` default family list. Surfaces both families in
`registry_summary` automatically, gives the timing × regime work
lineage edges, and removes a small inconsistency.

### Priority 5 — `stable_window_evaluation` capability promoted to implemented

**Effort:** M (2–3 days) · **Impact:** Medium ·
**Dependencies:** Existing `outputs/research/stable_window_evaluation/`
and `outputs/research/random_windows_*.csv` artifacts.

Add a loader that summarises the distribution of headline metrics
(Sharpe, max drawdown, Sortino) across the window panel with
p10/p50/p90. Add a `stable_window_evaluation` MCP tool. Flip the
capability. Closes the second known capability gap.

---

## G. Things deliberately *not* recommended

Listed so future revisions know we considered them:

- **LLM-backed classifier** — would replace the regex classifier with
  natural-language inference. Deferred until we have empirical evidence
  the regex layer fails to route a meaningful fraction of operator
  questions. Today we have zero such cases.
- **Multi-step planner / chained tool calls** — would let one question
  invoke several tools (e.g., "compare Polaris and Orion in elevated
  VIX regimes" → shadow_comparison + regime filter). Defer until the
  single-step path has 6+ implemented research tools to chain.
- **MCP as a continuously-listening service** — adds an operational
  surface to monitor. The current interactive-gateway model is
  sufficient; the runbook gap (§C.4) becomes a hard requirement before
  any "always-on MCP" investment.
- **New ingestion families for attribution / portfolio_history as typed
  objects** — bundled into Priority 2 as a side effect; not worth
  pursuing independently.
- **Refactoring the architecture doc** — it's aspirational design;
  this current-state doc is the operating reference. Mark the
  architecture as "v1 design intent" and point readers here from its
  header (one-line edit, low priority).

---

## H. References

- Operator guide: [`docs/operator/research_mcp_operator_guide.md`](../operator/research_mcp_operator_guide.md)
- Server documentation: [`docs/mcp_server.md`](../mcp_server.md)
- Aspirational architecture: [`caerus_research_mcp_architecture.md`](caerus_research_mcp_architecture.md)
- Frozen semantic contracts: [`semantics/`](semantics/)
- Capability registry source of truth: `research_registry/research/capabilities.py`
- Planner source of truth: `research_registry/mcp_server/tools.py::answer_research_question`
- Strategic backlog: [`../governance/caerus_strategic_backlog.md`](../governance/caerus_strategic_backlog.md)
- Active FR backlog: [`../governance/fr_active_backlog.md`](../governance/fr_active_backlog.md)
