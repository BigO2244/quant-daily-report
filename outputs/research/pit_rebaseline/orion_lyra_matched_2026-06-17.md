# Orion/Lyra PIT Matched Rebaseline

Date: `2026-06-17`

RESEARCH_ONLY / NO_RUNTIME_CHANGE

Classification: `REDUNDANT_CONTINUE_OBSERVING`
Statistical conclusion: `NO_STATISTICALLY_MEANINGFUL_LEAD`

| Metric | Orion | Lyra |
|---|---:|---:|
| Cumulative return | 42.136203 | 41.62628 |
| Volatility | 0.5018745643 | 0.5153377121 |
| Max drawdown | -0.5795 | -0.6419 |
| Avg turnover | 0.044525 | 0.079147 |

## Paired Test

- Observations: `2767`
- Lyra minus Orion total return: `-0.5099234687`
- Mean daily diff: `1.97287e-05`
- t-stat: `0.0808910984`
- return correlation: `0.9201664768`

## Limitations

- sector overlap unavailable because no PIT sector map was found in repo-local inputs
- factor overlap unavailable because no PIT factor exposure model was found for the generated PIT holdings
- large-cap family uses current scalemarketcap approximation as documented in FR-068 Phase 2.5
