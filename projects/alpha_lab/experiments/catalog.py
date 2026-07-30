"""Frozen experiment identities and minimum point-in-time input contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class DataAsset:
    asset_id: str
    provider_id: str
    dataset_id: str
    patterns: Tuple[str, ...]
    required_fields: Tuple[str, ...]
    # A missing primary event tape is enough to block a source-readiness run.
    # Checking it first avoids hashing large, already-certified price panels
    # before the experiment's defining source even exists.
    short_circuit_on_unready: bool = False

    @property
    def certification_path(self) -> str:
        return "outputs/research/alpha_lab/provider_readiness/{}.json".format(
            self.asset_id
        )


@dataclass(frozen=True)
class ExperimentLane:
    hypothesis_id: str
    experiment_id: str
    slug: str
    title: str
    spec_path: str
    local_readiness: str
    assets: Tuple[DataAsset, ...]


PIT_SECURITY_MASTER = DataAsset(
    asset_id="pit_security_master_v1",
    provider_id="caerus.fr068",
    dataset_id="effective_dated_security_identity",
    patterns=("data/pit_universe/security_master.csv",),
    required_fields=(
        "security_id",
        "permaticker",
        "cik",
        "cusip",
        "figi",
        "ticker",
        "effective_start",
        "effective_end",
        "firstpricedate",
        "lastpricedate",
        "relatedtickers",
        "source",
    ),
)

PIT_MEMBERSHIP = DataAsset(
    asset_id="pit_membership_v1",
    provider_id="caerus.fr068",
    dataset_id="survivorship_free_universe_membership",
    patterns=("data/pit_universe/membership_universe*.csv",),
    required_fields=(
        "security_id",
        "membership_start_date",
        "membership_end_date",
        "membership_family",
    ),
)

PIT_PRICES = DataAsset(
    asset_id="pit_prices_liquidity_v1",
    provider_id="caerus.fr068",
    dataset_id="prices_liquidity_corporate_actions",
    patterns=(
        "outputs/research/pit_liquidity/pit_liquidity_panel.csv",
        "outputs/research/pit_liquidity/pit_liquidity_panel.parquet",
    ),
    required_fields=(
        "security_id",
        "date",
        "open",
        "close",
        "closeadj",
        "volume",
        "dollar_ADV_20",
        "split_factor",
        "cash_dividend",
        "corporate_action_id",
        "delisting_return",
        "terminal_return",
        "adjustment_available_at",
        "available_at",
    ),
)

PIT_OBSERVED_PRICES = DataAsset(
    asset_id="pit_observed_prices_v1",
    provider_id="caerus.alpha_lab",
    dataset_id="observed_prices_without_terminal_settlement",
    patterns=("outputs/research/pit_liquidity/pit_liquidity_panel.parquet",),
    required_fields=(
        "security_id",
        "date",
        "open",
        "close",
        "closeadj",
        "volume",
        "dollar_ADV_20",
        "last_observed_total_return",
        "available_at",
    ),
)

TERMINAL_RETURN_SENSITIVITY = DataAsset(
    asset_id="terminal_return_sensitivity_v1",
    provider_id="caerus.alpha_lab",
    dataset_id="unverified_terminal_return_sensitivity_envelope",
    patterns=(
        "outputs/research/alpha_lab/data_spine/"
        "terminal_return_sensitivity/*/data/terminal_return_sensitivity.parquet",
    ),
    required_fields=(
        "security_id",
        "last_observed_date",
        "last_observed_close",
        "provider_final_day_total_return",
        "verified_terminal_return",
        "pessimistic_total_loss_return",
        "zero_incremental_return",
        "terminal_return_status",
        "use_in_primary_point_estimate",
    ),
)

FACTOR_PANEL = DataAsset(
    asset_id="factor_panel_v1",
    provider_id="caerus.alpha_lab",
    dataset_id="common_factor_controls",
    patterns=("outputs/research/alpha_lab/shared/factor_panel.*",),
    required_fields=(
        "date",
        "MKT_RF",
        "SMB",
        "HML",
        "RMW",
        "CMA",
        "UMD",
        "LOW_VOL_BAB",
    ),
)

PIT_CHARACTERISTICS = DataAsset(
    asset_id="pit_characteristics_v1",
    provider_id="caerus.alpha_lab",
    dataset_id="effective_dated_security_characteristics",
    patterns=("outputs/research/alpha_lab/shared/pit_characteristics.*",),
    required_fields=(
        "security_id",
        "date",
        "available_at",
        "sector_id",
        "market_cap",
        "book_to_market",
        "beta_252d",
        "realized_volatility_20d",
        "prior_return_5d",
        "prior_return_20d",
        "prior_return_60d",
    ),
)

SECTOR_RETURNS = DataAsset(
    asset_id="sector_returns_v1",
    provider_id="caerus.alpha_lab",
    dataset_id="point_in_time_sector_returns",
    patterns=("outputs/research/alpha_lab/shared/sector_returns.*",),
    required_fields=("date", "available_at", "sector_id", "sector_return"),
)

COMMODITY_CONTROLS = DataAsset(
    asset_id="commodity_controls_v1",
    provider_id="caerus.alpha_lab",
    dataset_id="point_in_time_commodity_input_returns",
    patterns=("outputs/research/alpha_lab/shared/commodity_controls.*",),
    required_fields=(
        "date",
        "available_at",
        "industry_id",
        "commodity_series_id",
        "commodity_return",
    ),
)

EARNINGS_EVENTS = DataAsset(
    asset_id="pit_earnings_events_v1",
    provider_id="sec.edgar",
    dataset_id="earnings_event_availability",
    patterns=(
        "outputs/research/cygnus/*event*tape*.json",
        "outputs/research/cygnus/*event*tape*.jsonl",
        "outputs/research/cygnus/*event*tape*.jsonl.gz",
    ),
    required_fields=(
        "security_id",
        "event_id",
        "announcement_time",
        "acceptance_datetime_utc",
        "available_at",
        "fiscal_period",
        "reported_eps",
        "reported_revenue",
        "guidance_signal",
        "items",
        "event_class",
        "is_material_8k",
        "scheduled_announcement_at",
        "schedule_available_at",
        "source_sha256",
    ),
)

ANALYST_ESTIMATES = DataAsset(
    asset_id="analyst_estimate_history_v1",
    provider_id="licensed.estimates",
    dataset_id="analyst_level_estimate_revisions",
    patterns=("outputs/research/alpha_lab/vendor_inputs/analyst_estimates/**/*",),
    required_fields=(
        "security_id",
        "analyst_id",
        "broker_id",
        "measure",
        "fiscal_period",
        "estimate_value",
        "currency",
        "unit",
        "accounting_basis",
        "published_at",
        "available_at",
        "revision_id",
        "supersedes_id",
        "withdrawal_status",
        "correction_status",
        "per_share_adjustment_basis",
        "contributor_count",
    ),
)

FORM4_EVENTS = DataAsset(
    asset_id="form4_event_tape_v1",
    provider_id="sec.edgar",
    dataset_id="form4_original_filings_and_amendments",
    patterns=(
        "outputs/research/alpha_lab/data_spine/"
        "form4_original_event_tape/*/data/events.jsonl.gz",
    ),
    required_fields=(
        "security_id",
        "issuer_cik",
        "owner_cik",
        "accession_number",
        "acceptance_datetime_utc",
        "available_at",
        "transaction_date",
        "transaction_code",
        "acquired_disposed_code",
        "transaction_shares",
        "transaction_price",
        "transaction_value",
        "is_derivative",
        "is_director",
        "is_officer",
        "is_ten_percent_owner",
        "officer_title",
        "is_natural_person",
        "ownership_nature",
        "control_group_id",
        "is_10b5_1",
        "footnote_text",
        "source_document",
        "parse_status",
        "amendment_lineage",
        "source_sha256",
    ),
)

CIK_IDENTITY_INPUT = DataAsset(
    asset_id="cik_identity_input_v1",
    provider_id="caerus.fr068",
    dataset_id="effective_dated_cik_security_mapping",
    patterns=("cik_mapping_results.csv",),
    required_fields=("security_id", "cik", "effective_start", "effective_end"),
)

OPTION_TAPE = DataAsset(
    asset_id="option_trade_quote_tape_v1",
    provider_id="licensed.opra",
    dataset_id="option_trades_quotes_surfaces",
    patterns=("outputs/research/alpha_lab/vendor_inputs/options/**/*",),
    required_fields=(
        "option_security_id",
        "underlying_security_id",
        "option_type",
        "strike",
        "expiration",
        "deliverable",
        "multiplier",
        "exchange_timestamp",
        "available_at",
        "trade_id",
        "trade_sequence_id",
        "trade_price",
        "trade_size",
        "exchange",
        "sale_condition",
        "bid",
        "ask",
        "bid_size",
        "ask_size",
        "quote_timestamp",
        "quote_sequence_id",
        "quote_condition",
        "underlying_quote_timestamp",
        "underlying_bid",
        "underlying_ask",
        "underlying_spot",
        "delta",
        "implied_volatility",
        "risk_free_rate",
        "dividend_yield",
        "borrow_assumption",
        "greeks_methodology_version",
        "open_interest",
        "open_interest_available_at",
        "complex_order_indicator",
        "nonstandard_deliverable_flag",
        "occ_adjustment_id",
        "condition_code",
        "correction_status",
        "cancel_bust_status",
    ),
)

SUPPLY_CHAIN_GRAPH = DataAsset(
    asset_id="supply_chain_graph_v1",
    provider_id="licensed.supply_chain",
    dataset_id="effective_dated_customer_supplier_edges",
    patterns=("outputs/research/alpha_lab/vendor_inputs/supply_chain/**/*",),
    required_fields=(
        "edge_id",
        "customer_security_id",
        "supplier_security_id",
        "effective_start",
        "effective_end",
        "first_observed_at",
        "available_at",
        "revenue_dependency_pct",
        "relationship_confidence",
        "last_confirmed_at",
        "deleted_at",
        "source_document",
        "source_sha256",
    ),
)

CAERUS_DECISION_TAPE = DataAsset(
    asset_id="caerus_research_decision_tape_v1",
    provider_id="caerus.alpha_lab",
    dataset_id="frozen_caerus_component_decision_tape",
    patterns=("outputs/research/alpha_lab/shared/caerus_decision_tape.parquet",),
    required_fields=(
        "security_id",
        "decision_at",
        "available_at",
        "eligible",
        "momentum_score",
        "quality_score",
        "thematic_score",
        "regime_budget",
        "combined_score",
        "selected",
        "target_weight",
        "exit_rule_state",
    ),
)

CROSS_ASSET_PANEL = DataAsset(
    asset_id="cross_asset_price_panel_v1",
    provider_id="licensed.cross_asset",
    dataset_id="pit_liquid_cross_asset_total_returns",
    patterns=("outputs/research/alpha_lab/vendor_inputs/cross_asset/**/*",),
    required_fields=(
        "instrument_id",
        "asset_group",
        "date",
        "available_at",
        "total_return_index",
        "inception_date",
        "termination_date",
        "replacement_instrument_id",
    ),
)

EXECUTIVE_TRANSCRIPTS = DataAsset(
    asset_id="executive_transcript_history_v1",
    provider_id="licensed.transcripts",
    dataset_id="pit_executive_transcripts",
    patterns=("outputs/research/alpha_lab/vendor_inputs/transcripts/**/*",),
    required_fields=(
        "security_id",
        "event_id",
        "speaker_id",
        "speaker_role",
        "segment_type",
        "published_at",
        "available_at",
        "correction_status",
        "text",
        "source_sha256",
    ),
)

PIT_NET_PAYOUT_FEATURES = DataAsset(
    asset_id="pit_net_payout_features_v1",
    provider_id="sec.edgar",
    dataset_id="filing_time_net_payout_features",
    patterns=("outputs/research/alpha_lab/shared/pit_net_payout_features.parquet",),
    required_fields=(
        "security_id",
        "fiscal_period_end",
        "available_at",
        "net_payout_yield",
        "share_change_1y",
        "source_accessions",
    ),
)

PIT_ASSET_GROWTH_FEATURES = DataAsset(
    asset_id="pit_asset_growth_features_v1",
    provider_id="sec.edgar",
    dataset_id="filing_time_asset_growth_features",
    patterns=("outputs/research/alpha_lab/shared/pit_asset_growth_features.parquet",),
    required_fields=(
        "security_id",
        "fiscal_period_end",
        "available_at",
        "asset_growth_1y",
        "asset_growth_2y",
        "source_accessions",
    ),
)

AI_POWER_GRID_EVENTS = DataAsset(
    asset_id="ai_power_grid_event_tape_v1",
    provider_id="public.utility_iso_puc_ferc_sec",
    dataset_id="source_audited_ai_power_grid_commitment_events",
    patterns=(
        "outputs/research/alpha_lab/data_spine/"
        "ai_power_grid_events/*/data/events.jsonl.gz",
    ),
    required_fields=(
        "event_id",
        "project_id",
        "issuer_security_id",
        "issuer_cik",
        "exposure_stratum",
        "event_type",
        "official_source",
        "source_url",
        "source_payload_sha256",
        "official_published_at",
        "availability_timestamp",
        "availability_rule",
        "location_or_serving_system",
        "ai_data_center_evidence",
        "commitment_evidence",
        "event_novelty_status",
        "supersedes_event_id",
        "correction_status",
        "mapping_evidence",
    ),
    short_circuit_on_unready=True,
)


SHARED = (
    PIT_SECURITY_MASTER,
    PIT_MEMBERSHIP,
    PIT_PRICES,
    PIT_CHARACTERISTICS,
    FACTOR_PANEL,
    SECTOR_RETURNS,
)

SENSITIVITY_SHARED = (
    PIT_SECURITY_MASTER,
    PIT_MEMBERSHIP,
    PIT_OBSERVED_PRICES,
    TERMINAL_RETURN_SENSITIVITY,
    PIT_CHARACTERISTICS,
    FACTOR_PANEL,
    SECTOR_RETURNS,
)

LANES = (
    ExperimentLane(
        hypothesis_id="HYP-2026-001",
        experiment_id="EXP-2026-0001",
        slug="caerus_decomposition",
        title="Current Caerus Decomposition",
        spec_path=(
            "projects/alpha_lab/hypotheses/"
            "HYP-2026-001_caerus_decomposition.md"
        ),
        local_readiness="BLOCKED_DERIVED_ASSET",
        assets=SENSITIVITY_SHARED + (CAERUS_DECISION_TAPE,),
    ),
    ExperimentLane(
        hypothesis_id="HYP-2026-002",
        experiment_id="EXP-2026-0002",
        slug="earnings_revision_drift",
        title="Earnings-Revision Drift",
        spec_path=(
            "projects/alpha_lab/hypotheses/"
            "HYP-2026-002_earnings_revision_drift.md"
        ),
        local_readiness="BLOCKED_VENDOR",
        assets=SHARED + (EARNINGS_EVENTS, ANALYST_ESTIMATES),
    ),
    ExperimentLane(
        hypothesis_id="HYP-2026-003",
        experiment_id="EXP-2026-0003",
        slug="insider_conviction_clusters",
        title="Insider-Conviction Clusters",
        spec_path=(
            "projects/alpha_lab/hypotheses/"
            "HYP-2026-003_insider_conviction_clusters.md"
        ),
        local_readiness="PARTIAL_LOCAL",
        assets=SHARED + (CIK_IDENTITY_INPUT, FORM4_EVENTS),
    ),
    ExperimentLane(
        hypothesis_id="HYP-2026-004",
        experiment_id="EXP-2026-0004",
        slug="options_information_lead",
        title="Options-Information Lead",
        spec_path=(
            "projects/alpha_lab/hypotheses/"
            "HYP-2026-004_options_information_lead.md"
        ),
        local_readiness="BLOCKED_VENDOR",
        assets=SHARED + (EARNINGS_EVENTS, OPTION_TAPE),
    ),
    ExperimentLane(
        hypothesis_id="HYP-2026-005",
        experiment_id="EXP-2026-0005",
        slug="supply_chain_shock_diffusion",
        title="Supply-Chain Shock Diffusion",
        spec_path=(
            "projects/alpha_lab/hypotheses/"
            "HYP-2026-005_supply_chain_shock_diffusion.md"
        ),
        local_readiness="BLOCKED_VENDOR",
        assets=SHARED
        + (
            EARNINGS_EVENTS,
            ANALYST_ESTIMATES,
            SUPPLY_CHAIN_GRAPH,
            COMMODITY_CONTROLS,
        ),
    ),
    ExperimentLane(
        hypothesis_id="HYP-2026-006",
        experiment_id="EXP-2026-0006",
        slug="residual_momentum",
        title="Residual Momentum",
        spec_path=(
            "projects/alpha_lab/hypotheses/HYP-2026-006_residual_momentum.md"
        ),
        local_readiness="READY_AFTER_MARKET_REBUILD",
        assets=SENSITIVITY_SHARED,
    ),
    ExperimentLane(
        hypothesis_id="HYP-2026-007",
        experiment_id="EXP-2026-0007",
        slug="stock_specific_seasonality",
        title="Stock-Specific Return Seasonality",
        spec_path=(
            "projects/alpha_lab/hypotheses/"
            "HYP-2026-007_stock_specific_seasonality.md"
        ),
        local_readiness="READY_AFTER_MARKET_REBUILD",
        assets=SENSITIVITY_SHARED,
    ),
    ExperimentLane(
        hypothesis_id="HYP-2026-008",
        experiment_id="EXP-2026-0008",
        slug="short_horizon_reversal",
        title="Short-Horizon Reversal",
        spec_path=(
            "projects/alpha_lab/hypotheses/"
            "HYP-2026-008_short_horizon_reversal.md"
        ),
        local_readiness="READY_AFTER_MARKET_REBUILD",
        assets=SENSITIVITY_SHARED,
    ),
    ExperimentLane(
        hypothesis_id="HYP-2026-009",
        experiment_id="EXP-2026-0009",
        slug="cross_asset_trend",
        title="Cross-Asset Trend",
        spec_path=(
            "projects/alpha_lab/hypotheses/HYP-2026-009_cross_asset_trend.md"
        ),
        local_readiness="BLOCKED_VENDOR",
        assets=(CROSS_ASSET_PANEL, FACTOR_PANEL),
    ),
    ExperimentLane(
        hypothesis_id="HYP-2026-010",
        experiment_id="EXP-2026-0010",
        slug="executive_tone_surprise",
        title="Executive and Managerial Tone Surprise",
        spec_path=(
            "projects/alpha_lab/hypotheses/"
            "HYP-2026-010_executive_tone_surprise.md"
        ),
        local_readiness="BLOCKED_VENDOR",
        assets=SENSITIVITY_SHARED + (EARNINGS_EVENTS, EXECUTIVE_TRANSCRIPTS),
    ),
    ExperimentLane(
        hypothesis_id="HYP-2026-011",
        experiment_id="EXP-2026-0011",
        slug="net_payout_share_issuance",
        title="Net Payout and Share Issuance",
        spec_path=(
            "projects/alpha_lab/hypotheses/"
            "HYP-2026-011_net_payout_share_issuance.md"
        ),
        local_readiness="BLOCKED_DERIVED_ASSET",
        assets=SENSITIVITY_SHARED + (PIT_NET_PAYOUT_FEATURES,),
    ),
    ExperimentLane(
        hypothesis_id="HYP-2026-012",
        experiment_id="EXP-2026-0012",
        slug="asset_growth_investment",
        title="Asset Growth and Investment",
        spec_path=(
            "projects/alpha_lab/hypotheses/"
            "HYP-2026-012_asset_growth_investment.md"
        ),
        local_readiness="BLOCKED_DERIVED_ASSET",
        assets=SENSITIVITY_SHARED + (PIT_ASSET_GROWTH_FEATURES,),
    ),
    ExperimentLane(
        hypothesis_id="HYP-2026-013",
        experiment_id="EXP-2026-0013",
        slug="ai_power_grid_commitments",
        title="AI Power/Grid Commitment Events",
        spec_path=(
            "projects/alpha_lab/hypotheses/"
            "HYP-2026-013_ai_power_grid_commitments.md"
        ),
        local_readiness="BLOCKED_SOURCE_AUDITED_EVENT_TAPE",
        assets=(
            AI_POWER_GRID_EVENTS,
            PIT_SECURITY_MASTER,
            PIT_MEMBERSHIP,
            PIT_OBSERVED_PRICES,
            TERMINAL_RETURN_SENSITIVITY,
            PIT_CHARACTERISTICS,
            FACTOR_PANEL,
            SECTOR_RETURNS,
        ),
    ),
)

LANE_BY_HYPOTHESIS = {lane.hypothesis_id: lane for lane in LANES}
