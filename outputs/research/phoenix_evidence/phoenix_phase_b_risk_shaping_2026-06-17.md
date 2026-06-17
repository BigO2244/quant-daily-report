# Phoenix Phase B Risk-Shaping Evidence

Date: `2026-06-17`

RESEARCH_ONLY / NO_RUNTIME_CHANGE

Classification: `PHOENIX_RISK_SHAPING_CANDIDATE_PENDING_LIQUIDITY`
Shadow eligible: `False`
Shadow readiness work justified: `True`
Best candidate: `stop_loss_10pct`
Best candidate max drawdown: `-0.3134265027`
Best candidate avg 20D event return: `0.0810008343`

## Variant Summary

| Variant | Classification | Avg 20D event return | Max DD | Upside retention | DD improvement |
|---|---|---:|---:|---:|---:|
| `baseline_close_only` | `RESEARCH_ONLY_NOT_SHADOW_READY` | 0.0609630767 | -0.4511304348 | None | None |
| `stricter_crisis_entry` | `RESEARCH_ONLY_NOT_SHADOW_READY` | 0.0556338054 | -0.4511304348 | 0.9125819824 | 0.0 |
| `volatility_cap_70` | `RESEARCH_ONLY_NOT_SHADOW_READY` | -0.0208379513 | -0.5361461139 | -0.3418126574 | -0.0850156791 |
| `staged_entry_0_5_10` | `RESEARCH_ONLY_NOT_SHADOW_READY` | 0.0524440034 | -0.3503414596 | 0.8602584757 | 0.1007889752 |
| `recovery_confirmation_5d` | `RESEARCH_ONLY_NOT_SHADOW_READY` | None | 0.0 | None | 0.4511304348 |
| `stop_loss_10pct` | `SHADOW_SPEC_CANDIDATE_RESEARCH_ONLY` | 0.0810008343 | -0.3134265027 | 1.328686783 | 0.1377039321 |
| `concentrated_top5_50gross` | `SHADOW_SPEC_CANDIDATE_RESEARCH_ONLY` | 0.0620840656 | -0.3198088189 | 1.0183879979 | 0.1313216159 |
| `broader_top15_75gross` | `RESEARCH_ONLY_NOT_SHADOW_READY` | 0.0471581452 | -0.5015268184 | 0.7735525797 | -0.0503963836 |
| `liquidity_capacity_filter` | `RESEARCH_ONLY_NOT_SHADOW_READY` | None | 0.0 | None | 0.4511304348 |
