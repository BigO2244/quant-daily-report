# Research MCP — Operator Guide

**Status:** active (Phase 7) · **Audience:** Brett (operator) · **Last updated:** 2026-05-29

The Research MCP is the read-only operator-intelligence layer over the Caerus
artifact tree. This guide covers the **one-command operator gateway** for
asking it questions. For the JSON-RPC stdio server itself, see
[`mcp_server.md`](../mcp_server.md). For the architectural design intent,
see [`architecture/caerus_research_mcp_architecture.md`](../architecture/caerus_research_mcp_architecture.md).
For what the MCP can and cannot answer today, see the
[current-state assessment](../architecture/research_mcp_current_state_2026-05-29.md).

This guide does **not** describe trading, broker, or scheduling
interaction. The MCP cannot place orders, edit cron, or mutate execution
state. It is artifact-backed and read-only by constitutional contract.

---

## 1. Ask a question

The primary entry point is a single command:

```bash
python -m scripts.research_mcp_ask "Does timing matter more in high VIX regimes?"
```

A thin shell wrapper exists for convenience:

```bash
./scripts/research_mcp_ask.sh "Does timing matter more in high VIX regimes?"
```

Both forms route through `research_registry.mcp_server.call_tool` against
the local registry server (the same server `mcp_server.md` documents).
The shell wrapper resolves the repo root automatically, so it works from
any working directory.

### What the command does

1. Classifies the question against the **capability registry** (regex
   matching, deterministic, no LLM).
2. Checks the **required artifacts** are present on disk.
3. Routes to the **existing MCP tool** the capability maps to.
4. Renders a concise human view + writes deterministic artifacts to
   `outputs/research_mcp/questions/<TIMESTAMP>/`:
   - `answer.json` — full structured payload from the MCP.
   - `answer.md` — the same content as a Markdown record.

### Useful flags

| Flag | Purpose |
|---|---|
| `--no-write` | Print to stdout only; skip the on-disk artifact pair. |
| `--raw-json` | Print the raw JSON payload (artifacts still written unless `--no-write`). |
| `--output-root <path>` | Where to write artifacts (default `outputs/research_mcp/questions/`). |
| `--timestamp <stamp>` | Override the artifact directory name (useful in tests / scripts). |

---

## 2. Example commands

Each example shows what to type and what status to expect on a healthy VM.

### Timing × regime (the question that proved the loop)

```bash
python -m scripts.research_mcp_ask "Does timing matter more in high VIX regimes?"
```

Expected: per-regime opportunity table (USD + bps) with `insufficient_sample`
flags on small-N buckets. Requires both
`outputs/research/execution_timing/<RUN_DATE>/per_trade_timing.json` and
`outputs/vix_regime/regime_history.csv` to be present.

### Aggregate timing (no regime stratification)

```bash
python -m scripts.research_mcp_ask "Is 9:35 better than 9:30?"
python -m scripts.research_mcp_ask "What is the best execution time?"
python -m scripts.research_mcp_ask "Compare 9:30 and 9:35 execution timing."
```

Expected: per-offset mean/median opportunity table + a conservative
recommendation (`retain_9_35_baseline`, `earlier_timing_appears_better`,
or `insufficient_evidence`). The recommendation refuses to flip unless
the best offset has **both** positive mean **and** positive median across
**≥ 5 days** AND is earlier than the 9:35 baseline.

### Shadow strategy comparison

```bash
python -m scripts.research_mcp_ask "How is Polaris doing versus Orion?"
python -m scripts.research_mcp_ask "Which strategy is performing best?"
python -m scripts.research_mcp_ask "Compare Orion and Lyra."
```

Expected: per-strategy NAV / cumulative return / excess vs SPY / turnover
panel, leader by both cumulative return and excess vs SPY, and pairwise
overlap (shared tickers, overlap weight %) when two strategies are
named. Strategy names are restricted to the closed set
`polaris | orion | lyra | leda` — anything else fails closed as
`NEEDS_DATA` with the unknown slug listed.

### Promotion readiness (strategy-aware)

```bash
python -m scripts.research_mcp_ask "Is Orion ready for promotion?"
python -m scripts.research_mcp_ask "Compare Polaris and Orion promotion readiness."
python -m scripts.research_mcp_ask "Which strategy is closest to promotion?"
python -m scripts.research_mcp_ask "Why is Lyra not promotion-ready?"
```

Expected: trade-date header, `closest_to_promotion` summary, Phase C
sidecar present/missing note, then a per-strategy table with
recommendation tier (one of `promote` / `hold` / `research_only` /
`insufficient_evidence`), confidence, `excess_vs_spy`, `max_drawdown`,
`realized_volatility_ann`, and `valid_observation_windows`. Each
strategy gets a `blockers` list (e.g. `metric_unavailable:max_drawdown`,
`insufficient_observation_window:0/20`, `phase_c_state:CONTINUE_SHADOW`)
plus a one-line explanation grounded in artifacts.

Strategy names parsed from the question are restricted to the closed
set `polaris | orion | lyra | leda`. Unknown names fail closed with
`NEEDS_DATA` and `missing_strategies` populated. When the FR-028
Phase C sidecar (`promotion_readiness.json`) is on disk its
`readiness_state` / `confidence` / `reason_codes` are used
authoritatively per strategy; otherwise the recommendation is derived
from `shadow_evaluation.json` metrics + per-strategy
`stability_analysis.json` flags, and confidence is capped at LOW.

The legacy top-level fields (`current_leader`, `recommendation`,
`confidence_level`, `valid_observation_window_count`, `evidence`,
`guardrail`) are preserved on the response for backward compatibility.

### Operational intelligence (what ran today / anomalies)

```bash
python -m scripts.research_mcp_ask "What ran today?"
python -m scripts.research_mcp_ask "What are today's anomalies?"
```

Expected: routes to `morning_cio_brief` / `anomaly_report` respectively
and returns the existing operator-intelligence output (artifact summary,
strategy leadership, regime context, findings list).

---

## 3. Supported capability classes

The MCP exposes nine capability classes today. **Six are implemented**;
**three are recognised but not yet wired** to a tool. Asking a question
that matches an unwired capability returns `NEEDS_CAPABILITY` rather
than `UNSUPPORTED_INTENT`, so you can see exactly what's missing and
what would have to be built next.

| Capability | Status | Routed tool | Example question |
|---|---|---|---|
| `execution_timing_summary` | **implemented** | `execution_timing_summary` | "Is 9:35 better than 9:30?" |
| `timing_by_vix_regime` | **implemented** | `execution_timing_by_vix_regime` | "Does timing matter in high VIX regimes?" |
| `shadow_comparison` | **implemented** | `shadow_comparison` | "How is Polaris doing versus Orion?" |
| `promotion_readiness` | **implemented** | `promotion_readiness` | "Is Orion ready for promotion?" |
| `anomaly_report` | **implemented** | `anomaly_report` | "What are today's anomalies?" |
| `morning_brief` | **implemented** | `morning_cio_brief` | "What ran today?" |
| `regime_intelligence` | **implemented** | `morning_cio_brief` | "What is the current VIX regime?" |
| `attribution_analysis` | **planned** | — | "What drove last quarter's alpha?" |
| `stable_window_evaluation` | **planned** | — | "How does the strategy perform across random windows?" |

Adding a new capability is a one-entry change to
`research_registry/research/capabilities.py`. The registry is the single
source of truth — if it's in the registry and implemented, you can ask
the question.

---

## 4. Meaning of statuses

Every response from `answer_research_question` carries a top-level
`status` field. The gateway maps each to a distinct **exit code** so
shell pipelines can branch on the outcome.

| Status | Exit | What it means | What to do |
|---|---:|---|---|
| `OK` | 0 | A capability matched, the artifacts were present, the routed tool produced a real answer. | Read the answer. |
| `NEEDS_DATA` | 2 | A capability matched and is implemented, but at least one required artifact is missing on disk. Response carries `missing_artifacts`. | Produce the named artifact, then re-ask. The output prints the exact next command. |
| `NEEDS_CAPABILITY` | 3 | A capability matched but no tool is wired yet. Response carries `matched_capability` + `suggested_next_build`. | The question is a known gap; either implement the capability or rephrase. |
| `UNSUPPORTED_INTENT` | 3 | No capability matched. Response carries `closest_capabilities` (top-3 by token overlap) and the full `available_intents` list. | Rephrase using one of the supported example questions, or file a new capability. |

There are also two **inner statuses** that can bubble up unchanged when
the routed tool returns them:

| Inner status | When | What it means |
|---|---|---|
| `NO_TIMING_DATA` / `NO_REGIME_DATA` | timing tools | The artifact directory exists but has no usable rows. |
| `BAD_REGIME_SCHEMA` | timing × regime | The CSV is present but its columns aren't recognised; response carries `missing_columns`. |
| `NO_SHADOW_DATA` | shadow_comparison | No `outputs/shadow_candidates/<DATE>/shadow_evaluation.json` on disk. |
| `NEEDS_OPERATOR` | morning brief / anomaly | Daily artifacts are stale or missing for the current trade date. |

In all cases the response is a structured JSON payload — never an
exception, never a stack trace. If you ever see a stack trace, that is
itself a bug in the MCP, not an expected answer.

---

## 5. Troubleshooting

### "Status: NEEDS_DATA, missing outputs/research/execution_timing/..."

The execution-timing replay has not been run yet (or has been run but
not synced to this machine). Produce the artifact:

```bash
python -m scripts.research.execution_timing_replay --run-date $(date -u +%F)
```

Then re-ask the original question.

### "Status: NEEDS_DATA, missing outputs/vix_regime/regime_history.csv"

The VIX classifier has not written its history file. Verify the daily
VIX update job has run; check `outputs/vix_regime/regime_current.json`
exists. If only `regime_history.csv` is missing, the classifier may
have failed mid-write — inspect the most recent run logs.

### "Status: BAD_REGIME_SCHEMA, missing_columns: ['date|as_of|execution_date']"

Upstream changed the regime CSV columns and the loader doesn't yet
recognise the new schema. The response names the missing column
alternatives. Either revert the CSV to the prior schema or extend
`research_registry.research.timing_regime._REGIME_DATE_COLUMN_CANDIDATES`.
This is a code change, not an operator change.

### "Status: UNSUPPORTED_INTENT"

Your question didn't match any capability's regex patterns. Read the
`closest_capabilities` block in the response (or `answer.json`) for the
top three nearest matches and rephrase using one of their
`example_questions`. If your question is a genuinely new research need,
file a backlog item with the question text and what artifacts could
answer it.

### "Status: NEEDS_CAPABILITY, intent: shadow_comparison"

The classifier recognised the question shape but no tool is wired. As
of 2026-05-29 the only two unwired capabilities are
`attribution_analysis` and `stable_window_evaluation`. The response
carries a `suggested_next_build` paragraph naming the artifacts the new
tool should read.

### MCP server itself doesn't start / `python -m scripts.research_mcp_ask` errors

The gateway imports the MCP server in-process — there is no separate
service to start. If the import fails, the underlying problem is in
`research_registry/` (a syntax error or a missing dependency). Run

```bash
python -m pytest Tests/test_research_registry_mcp_server.py -q
```

to verify the server itself is sane. If those tests pass and the
gateway still errors, the bug is in the gateway; re-run

```bash
python -m pytest Tests/test_research_mcp_ask.py -q
```

### A question routes to the wrong capability

The classifier is regex-based and ties are broken by registry order
(deterministic). If a question matches multiple capabilities, the one
with more pattern hits wins; if equal, the one earlier in
`CAPABILITY_REGISTRY` wins. To force a specific tool, call it directly
instead of going through the gateway:

```bash
# Skip the planner; call a tool by name via call_tool semantics.
python - <<'PY'
import json
from research_registry.mcp_server import call_tool
print(json.dumps(call_tool("shadow_comparison", {"question": "compare polaris and orion"}), indent=2))
PY
```

---

## 6. Where the artifacts go

Every gateway invocation that doesn't pass `--no-write` writes:

```
outputs/research_mcp/questions/<TIMESTAMP>/answer.json
outputs/research_mcp/questions/<TIMESTAMP>/answer.md
```

`<TIMESTAMP>` is `YYYY-MM-DDTHH-MM-SSZ` (UTC, filesystem-safe — no
colons). The directory tree is gitignored under the existing `outputs/`
rule. These artifacts are useful as paste-into-PR records of what
question was asked and what answer the MCP gave at that moment.

---

## 7. What this gateway is NOT

- It is **not** a chat agent. It does not maintain conversation state.
- It does **not** call an LLM. The classifier is a regex over the
  capability registry. The same question always produces the same routing.
- It does **not** access the broker, place orders, edit cron, or mutate
  any execution-path artifact. Constitutional contract per
  [`MCP_IMPLEMENTATION_BOUNDARIES_v1.md`](../architecture/semantics/MCP_IMPLEMENTATION_BOUNDARIES_v1.md).
- It does **not** invent metrics. Null fields in the underlying
  artifacts are reported as null and surfaced in per-strategy
  `unavailable_metrics` blocks — never interpolated.

If you need a capability the gateway doesn't have, file a backlog item
or extend the registry. Do **not** ask the gateway to do trading or
scheduling work — those are different blast-radius categories with
different governance.
