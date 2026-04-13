# Weekly Quantitative Research Brief

**Week of April 13, 2026**

---

## Top 5 Findings

### 1. Kronos: A Finance-Native Foundation Model for K-Line Forecasting

**Source:** [arXiv 2508.02739](https://arxiv.org/abs/2508.02739) (Accepted AAAI 2026; pre-trained weights on [GitHub](https://github.com/shiyu-coder/Kronos) and [HuggingFace](https://huggingface.co/NeoQuasar/Kronos-base))

**Summary:** Kronos is a decoder-only transformer foundation model pre-trained autoregressively on 12 billion+ K-line records spanning 45 global exchanges. It introduces a specialized tokenizer that discretizes continuous OHLCV data into token sequences, preserving both price dynamics and trade activity patterns. Unlike general-purpose time series foundation models (Chronos, TimesFM), Kronos is domain-native to financial markets.

**Key Metrics:** 93% improvement in price series forecasting RankIC over the leading general-purpose TSFM; 87% over the best non-pre-trained baseline; 9% lower MAE in volatility forecasting; 22% improvement in generative fidelity for synthetic K-line sequences.

**Asset Class Applicability:** All liquid asset classes with K-line data — equities, futures, FX, crypto. Directly applicable to any exchange-traded instrument.

**Data Requirements:** Historical OHLCV data from exchanges (widely available via standard market data feeds). Pre-trained weights are publicly available, so fine-tuning requires only the target instrument's history.

**Implementation Complexity:** Low-Medium. Pre-trained weights are public. Fine-tuning on proprietary data requires a standard PyTorch stack with moderate GPU resources.

**Potential Alpha Contribution:** High. The magnitude of improvement over existing TSFMs is substantial. The domain-specific tokenization likely captures microstructure patterns that generic models miss. Immediate use cases include return forecasting, volatility prediction, and synthetic data generation for backtesting.

---

### 2. FTS-Diffusion: Synthetic Financial Time Series for Data Augmentation

**Source:** [OpenReview / ICLR 2024](https://openreview.net/forum?id=CdjnzWsQax); follow-up work in [Quantitative Finance (2025)](https://www.tandfonline.com/doi/full/10.1080/14697688.2025.2528697)

**Summary:** FTS-Diffusion is a three-module generative framework that synthesizes financial time series by first extracting scale-invariant recurring patterns (varying in duration and magnitude), then using a diffusion-based generative network to synthesize pattern segments, and finally modeling temporal transitions between patterns. This addresses the core challenge that financial time series exhibit irregular, non-stationary dynamics that standard diffusion models fail to capture.

**Key Metrics:** Augmenting real-world training data with FTS-Diffusion synthetics reduces stock market prediction error by up to 17.9% compared to training on real data alone. Outperforms GAN-based and standard diffusion alternatives on fidelity metrics.

**Asset Class Applicability:** Equities (demonstrated), extensible to any asset class with sufficient historical data. Particularly valuable for instruments with limited history (new listings, exotic derivatives, emerging market assets).

**Data Requirements:** Historical price/return series for the target universe. No external data dependencies.

**Implementation Complexity:** Medium. Requires implementing the three-module pipeline (pattern extraction, diffusion generator, transition model). Standard PyTorch diffusion model training; moderate compute requirements.

**Potential Alpha Contribution:** Medium-High. Primary value is indirect — improving downstream model training through data augmentation. Especially valuable for regime-specific training (generate more samples of rare market regimes like flash crashes or liquidity crises) and for backtesting strategies on synthetic but realistic data.

---

### 3. Correcting the Factor Mirage: Causal Factor Investing Protocol

**Source:** [SSRN 5931616](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5931616) — Lopez de Prado & Zoonekynd; see also [Causality and Factor Investing: A Primer (SSRN 5277078)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5277078)

**Summary:** Lopez de Prado's recent body of work introduces the "factor mirage" concept — factor models that pass conventional statistical tests (t-stats, R-squared, information ratios) but are causally misspecified due to collider bias and confounder bias embedded in standard regression frameworks. The research protocol paper provides a concrete methodology for distinguishing causal factors from statistical artifacts, using structural causal models and do-calculus to test whether factor exposures represent genuine return drivers or spurious associations.

**Key Metrics:** The papers demonstrate that a significant proportion of published factors may be "mirages" — showing in-sample statistical significance but failing out-of-sample due to misspecified causal structure. No single Sharpe ratio to cite, but the methodological implication is that portfolios built on causal factors should exhibit superior out-of-sample stability.

**Asset Class Applicability:** Cross-asset factor strategies, equity long-short, risk premia harvesting. Any strategy that relies on factor-based portfolio construction.

**Data Requirements:** Standard factor return data plus the causal graph specification. No exotic data needed, but requires a shift in modeling methodology from associational to causal inference.

**Implementation Complexity:** Medium-High. Requires adopting causal inference tooling (DoWhy, CausalNex, or custom implementations) and rethinking the factor research pipeline. The conceptual shift is larger than the engineering lift.

**Potential Alpha Contribution:** High. If even a fraction of existing factor exposures are mirages, correcting for this could meaningfully improve out-of-sample Sharpe ratios and reduce drawdowns from factor crowding. This is a foundational methodology change, not a single signal.

---

### 4. Man Group's Agentic AI for Signal Generation (AlphaGPT)

**Source:** [Bloomberg (July 2025)](https://www.bloomberg.com/news/articles/2025-07-10/man-group-says-agentic-ai-is-now-devising-quant-trading-signals); [Man Group AI Diary](https://www.man.com/insights/the-big-picture-ai); [AI Street coverage](https://www.ai-street.co/p/man-group-s-alphagpt)

**Summary:** Man Group ($150B AUM) has deployed an agentic AI system called AlphaGPT within its Man AHL quant unit. The system autonomously generates, codes, and backtests trading hypotheses — marking the arrival of agentic AI in production at a major systematic fund. The system reportedly operates on Man Group's belief that markets exhibit persistent anomalies (trends, mean reversion, carry) rooted in behavioral biases (risk aversion, anchoring, herding), and uses AI to systematically discover and test new expressions of these patterns.

**Key Metrics:** Specific performance metrics not publicly disclosed. The significance is the production deployment at scale by the world's largest listed hedge fund.

**Asset Class Applicability:** Multi-asset (Man AHL trades across equities, fixed income, FX, commodities).

**Data Requirements:** Standard market data plus behavioral indicators. The agentic system likely ingests a wide universe of features for hypothesis generation.

**Implementation Complexity:** High. Building a robust agentic research pipeline that generates, codes, backtests, and validates signals autonomously requires significant infrastructure. However, the concept of using LLM agents for factor discovery is replicable at smaller scale using open-source LLMs + backtesting frameworks.

**Potential Alpha Contribution:** Medium-High. The primary advantage is research velocity — an agentic system can explore the hypothesis space orders of magnitude faster than human researchers. The alpha comes from finding signals that would otherwise take months of manual research.

---

### 5. Chronos-2: Universal Time Series Forecasting with Group Attention

**Source:** [arXiv 2510.15821](https://arxiv.org/abs/2510.15821); [Amazon Science Blog](https://www.amazon.science/blog/introducing-chronos-2-from-univariate-to-universal-forecasting); [HuggingFace](https://huggingface.co/amazon/chronos-2)

**Summary:** Chronos-2 is Amazon's 120M-parameter encoder-only time series foundation model that unifies univariate, multivariate, and covariate-informed forecasting in a single architecture. It introduces a "group attention" mechanism enabling in-context learning across related time series within a group (e.g., correlated assets, a target series with macro covariates). The model achieves SOTA across three comprehensive benchmarks and addresses a key limitation of Chronos-1 — the inability to leverage cross-series dependencies.

**Key Metrics:** SOTA on fev-bench, GIFT-Eval, and Chronos Benchmark II. The 28M-parameter small variant achieves within 1% of the full model's performance at ~2x inference speed.

**Asset Class Applicability:** Multi-asset. The group attention mechanism is particularly relevant for cross-asset forecasting (e.g., forecasting equity returns using bond yields, VIX, and macro indicators as covariates).

**Data Requirements:** Multi-variate time series data. The model works zero-shot but benefits from fine-tuning on financial data. Pre-trained weights are open-source.

**Implementation Complexity:** Low. Pre-trained models on HuggingFace, well-documented API. Fine-tuning requires standard compute. The small variant is deployable for real-time inference.

**Potential Alpha Contribution:** Medium. General-purpose TSFMs still tend to underperform domain-specific models on financial data (see Kronos above), but Chronos-2's covariate support makes it a strong baseline for multi-factor forecasting. Best used as an ensemble component or for rapid prototyping.

---

## New Alternative Data Sources

**Key trends identified for 2026:**

- **Real-time alternative data feeds** are becoming table stakes. Quant desks increasingly require sub-minute latency integration of alt data directly into trading models, not just research workflows. Providers like Thinknum and Exabel are building APIs optimized for this use case.

- **LLM-processed earnings call signals** are emerging as a distinct data category. Rather than raw transcripts, providers are offering pre-computed sentiment scores, topic decompositions, and management tone metrics extracted via fine-tuned LLMs. Signal decay functions (modeling how quickly an earnings sentiment signal loses predictive power) are being published as part of the data product.

- **The alternative data market** is valued at $11-14 billion (2025) and projected to exceed $19 billion by 2030. 86% of investment managers expect to increase alt data usage. This maturation means commodity alt data sources are less likely to provide edge — differentiation comes from novel processing (e.g., causal analysis of alt data rather than simple correlations).

- **Providers to watch:** Exabel (integrated alt data + analytics platform with 75+ datasets), Thinknum (web/app-derived company KPIs), Neudata (alt data discovery and evaluation platform), BattleFin (alt data marketplace connecting providers with funds).

---

## Cross-Industry Watch

### Causal Reinforcement Learning (from Robotics/General AI)

Bareinboim's group at Columbia has formalized causal RL frameworks showing that agents with access to structural causal models learn optimal policies faster and generalize better under unobserved confounders. **Finance transfer:** Execution algorithms and portfolio rebalancing agents that leverage causal knowledge of market microstructure could generalize across regimes better than standard RL policies trained on historical replay.

### Diffusion Models for Conditional Generation (from Computer Vision/Audio)

The maturation of conditional diffusion models — particularly latent-space variants like LFTD (Latent Financial Time-Series Diffusion) that use dual encoders with FT-Transformers — brings vision/audio generative techniques into finance. **Finance transfer:** Scenario generation for stress testing, realistic synthetic training data for rare market events, and conditional forecasting (e.g., "generate return paths given a 50bp rate hike").

### Mixture-of-Experts Architectures (from NLP/LLMs)

MoHETS introduces mixture-of-heterogeneous-experts for time series, assigning different architectural specialists (attention-based, convolution-based, recurrence-based) to different segments of the input. **Finance transfer:** Different market regimes may be best modeled by different architectures; an MoE approach could route trend-following regimes to one expert and mean-reversion regimes to another, avoiding the one-size-fits-all problem.

---

## Implementation Recommendations

### Priority 1: Fine-tune Kronos on Proprietary Universe

**Why first:** Kronos offers the highest expected alpha contribution with relatively low implementation complexity. Pre-trained weights are open-source, the tokenizer is designed for OHLCV data, and the 93% RankIC improvement over general TSFMs is too large to ignore. Start with a pilot on the most liquid equity and futures universe, evaluate zero-shot performance, then fine-tune on proprietary data. Estimated time to first signal: 2-4 weeks.

### Priority 2: Implement Causal Factor Audit Using Lopez de Prado Protocol

**Why second:** This is a defensive alpha play — identifying and removing factor mirages from existing portfolios could reduce drawdowns and improve out-of-sample stability before any new signals are added. The methodology from SSRN 5931616 provides a concrete protocol. Start by auditing the top 10 factor exposures in current portfolios using DoWhy or CausalNex. This may reveal that some "alpha" is actually collider bias. Estimated time to first audit: 3-6 weeks.

---

## Competitive Landscape

- **Man Group** has moved to production deployment of agentic AI (AlphaGPT) for autonomous signal discovery and backtesting. This represents the most aggressive public adoption of LLM agents for alpha generation among major systematic funds.

- **AQR Capital Management** officially adopted AI/ML for investment decision-making in June 2025, a notable shift for a firm historically rooted in factor-based approaches. Their adoption of causal methodologies (aligned with the factor mirage research) is worth monitoring.

- **Two Sigma** experts are discussing AI's trajectory in quant investing for 2026, with emphasis on channeling new capabilities wisely rather than pursuing AI for its own sake.

- **Hiring signals:** Man Group is recruiting PhD-level quantitative research interns for 2026 with explicit focus on deep learning for quant strategies, suggesting continued investment in ML-based alpha generation. The Oxford-Man Institute continues to be a pipeline for cutting-edge research.

- **Industry-wide trend:** The shift toward "LLM-driven Alpha" — using large language models to parse earnings calls and social sentiment in real-time — is accelerating. The SEC's evolving stance on ETF share classes may open new distribution channels for quant strategies to retail investors.

---

*Report generated: April 13, 2026*
*Next scan scheduled: April 20, 2026*
