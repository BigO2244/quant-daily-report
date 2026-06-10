"""FR-051 Cygnus Stage 2 — cygnus_v0_event_reaction scoring (RESEARCH_ONLY).

Implements the addendum A3 Wave-1 composite (no consensus dependency):

    cygnus_v0_score =
      0.40 * percentile(event_reaction_abnormal_return)
    + 0.25 * percentile(revenue_yoy_acceleration)
    + 0.20 * percentile(drift_confirmation)
    + 0.15 * filing_quality_bonus            # on-time filer, 8-K + exhibit present
    - pre_event_runup_penalty
    - failed_reaction_penalty

Percentiles are cross-sectional within the contemporaneous candidate cohort
passed to `compute_v0_scores` (no future information). Component weights are
pre-registered and frozen — they are not tuned.
"""
from __future__ import annotations

from typing import Any

W_REACTION = 0.40
W_REVENUE = 0.25
W_DRIFT = 0.20
W_FILING_QUALITY = 0.15
RUNUP_PENALTY_WEIGHT = 0.10  # applied to the runup percentile above the 0.8 decile
FAILED_REACTION_PENALTY = 0.10  # flat penalty when the reaction was negative


def percentile_rank(values: list[float | None]) -> list[float | None]:
    """Cross-sectional percentile in [0,1] (average-rank), None preserved."""
    present = [(i, v) for i, v in enumerate(values) if v is not None]
    out: list[float | None] = [None] * len(values)
    n = len(present)
    if n == 0:
        return out
    if n == 1:
        out[present[0][0]] = 0.5
        return out
    order = sorted(present, key=lambda kv: kv[1])
    # average-rank to handle ties
    i = 0
    while i < n:
        j = i
        while j + 1 < n and order[j + 1][1] == order[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0
        for k in range(i, j + 1):
            out[order[k][0]] = avg_rank / (n - 1)
        i = j + 1
    return out


def compute_v0_scores(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score a contemporaneous cohort of candidate events.

    Each candidate dict must carry: event_reaction_abnormal_return,
    revenue_yoy_acceleration, drift_confirmation, filing_quality_bonus (0..1),
    pre_event_runup. Returns the candidates with `cygnus_v0_score` and the
    component percentiles attached. Candidates missing the reaction component
    (the 0.40 driver) are scored None and excluded by the caller.
    """
    pct_reaction = percentile_rank([c.get("event_reaction_abnormal_return") for c in candidates])
    pct_revenue = percentile_rank([c.get("revenue_yoy_acceleration") for c in candidates])
    pct_drift = percentile_rank([c.get("drift_confirmation") for c in candidates])
    pct_runup = percentile_rank([c.get("pre_event_runup") for c in candidates])

    scored: list[dict[str, Any]] = []
    for i, c in enumerate(candidates):
        out = dict(c)
        reaction = c.get("event_reaction_abnormal_return")
        if pct_reaction[i] is None:
            out["cygnus_v0_score"] = None
            scored.append(out)
            continue
        # Missing optional components contribute a neutral 0.5 percentile (PARTIAL).
        pr = pct_reaction[i] if pct_reaction[i] is not None else 0.5
        prev = pct_revenue[i] if pct_revenue[i] is not None else 0.5
        pdr = pct_drift[i] if pct_drift[i] is not None else 0.5
        fq = float(c.get("filing_quality_bonus") or 0.0)

        runup_pct = pct_runup[i] if pct_runup[i] is not None else 0.5
        runup_penalty = RUNUP_PENALTY_WEIGHT * max(0.0, runup_pct - 0.8) / 0.2
        failed_penalty = FAILED_REACTION_PENALTY if (reaction is not None and reaction < 0) else 0.0

        score = (
            W_REACTION * pr
            + W_REVENUE * prev
            + W_DRIFT * pdr
            + W_FILING_QUALITY * fq
            - runup_penalty
            - failed_penalty
        )
        out["cygnus_v0_score"] = score
        out["pct_reaction"] = pr
        out["pct_revenue"] = prev
        out["pct_drift"] = pdr
        out["runup_penalty"] = runup_penalty
        out["failed_reaction_penalty"] = failed_penalty
        scored.append(out)
    return scored


def select_basket(scored: list[dict[str, Any]], *, top_n: int = 10, min_basket: int = 5) -> list[dict[str, Any]]:
    """Top-N by score with positive event quality (reaction non-negative)."""
    eligible = [
        c for c in scored
        if c.get("cygnus_v0_score") is not None
        and (c.get("event_reaction_abnormal_return") or 0.0) >= 0.0
    ]
    eligible.sort(key=lambda c: c["cygnus_v0_score"], reverse=True)
    if len(eligible) < min_basket:
        return []
    return eligible[:top_n]
