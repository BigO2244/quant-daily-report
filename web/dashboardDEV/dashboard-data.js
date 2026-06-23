window.DASHBOARD_V1 = {
  "environment": "paper",
  "generated_at": "2026-06-23T20:06:00+00:00",
  "report_date": "2026-06-23",
  "schema_version": "dashboard-v2-prototype",
  "sections": {
    "account_layers": {
      "as_of": "2026-06-23T20:06:00+00:00",
      "is_stale": false,
      "rows": [
        {
          "buying_power": 31396.03,
          "capital_behavior": "paper only",
          "cash": 552.51,
          "equity": 10976.08,
          "layer": "Paper account",
          "positions_count": 25,
          "source": "broker paper/account artifacts",
          "status": "PAPER_OBSERVED"
        },
        {
          "buying_power": 399.99,
          "capital_behavior": "FR-104 capped pilot only",
          "cash": 500.0,
          "equity": 500.0,
          "layer": "Live pilot account",
          "positions_count": 0,
          "source": "outputs/live_pilot/runs/2026-06-23T100012-0400_59e97cb",
          "status": "SUBMITTED"
        },
        {
          "buying_power": null,
          "capital_behavior": "non-capital shadow/research",
          "cash": null,
          "equity": null,
          "layer": "Shadow/research sleeves",
          "positions_count": 8,
          "source": "strategy registry + sleeve manifest + shadow artifacts",
          "status": "OBSERVED"
        }
      ],
      "status": "OK"
    },
    "baseline_alpha_comparison": {
      "as_of": "2026-06-23",
      "is_stale": true,
      "pairs": [
        {
          "alpha_alpha_per_dollar_proxy": null,
          "alpha_concentration": null,
          "alpha_drawdown": null,
          "alpha_effective_n": null,
          "alpha_name": "Polaris_Alpha",
          "alpha_return": null,
          "alpha_strategy_id": "caerus_polaris_alpha",
          "alpha_turnover": null,
          "baseline_alpha_per_dollar_proxy": null,
          "baseline_concentration": 0.3,
          "baseline_drawdown": -0.1085908947,
          "baseline_effective_n": null,
          "baseline_name": "Caerus Polaris",
          "baseline_return": 0.8922922055,
          "baseline_strategy_id": "caerus_polaris",
          "baseline_turnover": 0.0744186047,
          "drawdown_delta": null,
          "evidence_window_days": 0,
          "return_delta": null,
          "review_checkpoints": [
            {
              "observed_days": 0,
              "status": "IN_PROGRESS",
              "trading_days": 20
            },
            {
              "observed_days": 0,
              "status": "IN_PROGRESS",
              "trading_days": 60
            }
          ],
          "status": "IN_PROGRESS"
        },
        {
          "alpha_alpha_per_dollar_proxy": null,
          "alpha_concentration": null,
          "alpha_drawdown": null,
          "alpha_effective_n": null,
          "alpha_name": "Orion_Alpha",
          "alpha_return": null,
          "alpha_strategy_id": "caerus_orion_alpha",
          "alpha_turnover": null,
          "baseline_alpha_per_dollar_proxy": null,
          "baseline_concentration": 0.6,
          "baseline_drawdown": -0.1353372101,
          "baseline_effective_n": null,
          "baseline_name": "Caerus Orion",
          "baseline_return": 1.2878179278,
          "baseline_strategy_id": "caerus_orion",
          "baseline_turnover": 0.0093023256,
          "drawdown_delta": null,
          "evidence_window_days": 0,
          "return_delta": null,
          "review_checkpoints": [
            {
              "observed_days": 0,
              "status": "IN_PROGRESS",
              "trading_days": 20
            },
            {
              "observed_days": 0,
              "status": "IN_PROGRESS",
              "trading_days": 60
            }
          ],
          "status": "IN_PROGRESS"
        }
      ],
      "status": "OK",
      "summary": {
        "pair_count": 2,
        "review_checkpoints": [
          20,
          60
        ]
      }
    },
    "daily_decision_intelligence": {
      "as_of": "2026-06-23",
      "is_stale": false,
      "laggards": [
        {
          "avg_entry_price": 397.29926698375897,
          "cost_basis": 643.695647,
          "last_price": 386.83,
          "market_value": 626.733568,
          "qty": 1.62017829,
          "side": "positionside.long",
          "ticker": "MAR",
          "unrealized_pnl": -16.962079,
          "unrealized_pnl_pct": -0.02635,
          "weight": 0.057099945335675396
        },
        {
          "avg_entry_price": 80.3917039690212,
          "cost_basis": 837.685792,
          "last_price": 78.95,
          "market_value": 822.663161,
          "qty": 10.4200527,
          "side": "positionside.long",
          "ticker": "GM",
          "unrealized_pnl": -15.022631,
          "unrealized_pnl_pct": -0.01793,
          "weight": 0.07495054345449377
        },
        {
          "avg_entry_price": 146.87094507731067,
          "cost_basis": 617.114218,
          "last_price": 144.97,
          "market_value": 609.126932,
          "qty": 4.20174472,
          "side": "positionside.long",
          "ticker": "C",
          "unrealized_pnl": -7.987286,
          "unrealized_pnl_pct": -0.01294,
          "weight": 0.0554958538931932
        }
      ],
      "largest_decreases": [],
      "largest_increases": [],
      "leaders": [
        {
          "avg_entry_price": 706.3942863793549,
          "cost_basis": 875.151904,
          "last_price": 1044.5,
          "market_value": 1294.031083,
          "qty": 1.238900032,
          "side": "positionside.long",
          "ticker": "STX",
          "unrealized_pnl": 418.879179,
          "unrealized_pnl_pct": 0.47864,
          "weight": 0.11789555861473314
        },
        {
          "avg_entry_price": 130.035,
          "cost_basis": 780.21,
          "last_price": 148.18,
          "market_value": 889.08,
          "qty": 6.0,
          "side": "positionside.long",
          "ticker": "FTNT",
          "unrealized_pnl": 108.87,
          "unrealized_pnl_pct": 0.13954,
          "weight": 0.08100159619827844
        },
        {
          "avg_entry_price": 91.7543149833832,
          "cost_basis": 996.639723,
          "last_price": 93.68,
          "market_value": 1017.556605,
          "qty": 10.862047449,
          "side": "positionside.long",
          "ticker": "MNST",
          "unrealized_pnl": 20.916882,
          "unrealized_pnl_pct": 0.02099,
          "weight": 0.09270674093118855
        }
      ],
      "notes": [
        {
          "kind": "return",
          "label": "Portfolio daily return",
          "value": -0.012057562349010564
        }
      ],
      "summary": {
        "buy_count": 0,
        "latest_daily_return": -0.012057562349010564,
        "sell_count": 0,
        "turnover_proxy_notional": 0
      }
    },
    "decision_grade": {
      "confidence_summary": {
        "argo_recommendation_confidence": "LOW",
        "model_quality_packet_status": "PARTIAL",
        "multi_asset_status": "PARTIAL",
        "phoenix_confidence": "LOW",
        "strategy_differentiation_counts": {
          "DISTINCT": 2,
          "INSUFFICIENT_EVIDENCE": 12,
          "NEAR_DUPLICATE": 0,
          "PARTIALLY_OVERLAPPING": 1
        }
      },
      "decision_grade_strategy_change": false,
      "latest_model_quality_date": "2026-06-08",
      "promotion_ready_count": 0,
      "reason_codes": [
        "MODEL_QUALITY_DATE_DIFFERS_FROM_REPORT_DATE",
        "NO_DECISION_GRADE_RECOMMENDATION",
        "NO_DECISION_GRADE_STRATEGY_CHANGE"
      ],
      "source_paths": {
        "argo_phase_b_validation": "outputs/model_quality/2026-06-08/argo_phase_b_validation.json",
        "model_quality_packet": "outputs/model_quality/2026-06-08/model_quality_packet.json",
        "model_tournament": "outputs/model_quality/2026-06-08/model_tournament.json",
        "multi_asset_research_framework": "outputs/model_quality/2026-06-08/multi_asset_research_framework.json",
        "phoenix_phase_b_review": "outputs/model_quality/2026-06-08/phoenix_phase_b_review.json",
        "strategy_differentiation_deep_dive": "outputs/model_quality/2026-06-08/strategy_differentiation_deep_dive.json"
      },
      "status": "PARTIAL",
      "top_blockers": [
        "MODEL_QUALITY_DATE_DIFFERS_FROM_REPORT_DATE",
        "NO_DECISION_GRADE_RECOMMENDATION",
        "NO_DECISION_GRADE_STRATEGY_CHANGE"
      ]
    },
    "governance_state": {
      "as_of": "2026-06-23T20:06:00+00:00",
      "is_stale": false,
      "rows": [
        {
          "detail": "Level 2.5 capped live-pilot evidence can continue when approval, cap, account, market-hours, and reconciliation gates pass.",
          "name": "FR-104 pilot evidence collection",
          "pilot_blocking": false,
          "production_scaling_blocking": false,
          "promotion_blocking": false,
          "status": "ACTIVE"
        },
        {
          "detail": "PIT date-effective large-cap membership authority remains unresolved; this blocks promotion and scaling, not FR-104 pilot evidence collection.",
          "name": "FR-068 decision-grade PIT membership",
          "pilot_blocking": false,
          "production_scaling_blocking": true,
          "promotion_blocking": true,
          "status": "DEPENDENCY_BLOCKED"
        },
        {
          "detail": "Polaris_Alpha and Orion_Alpha are SHADOW only until 20/60-day forward evidence and decision-grade PIT infrastructure are available.",
          "name": "Shadow alpha promotion",
          "pilot_blocking": false,
          "production_scaling_blocking": true,
          "promotion_blocking": true,
          "status": "BLOCKED"
        },
        {
          "detail": "No allocator, scheduler, broker, paper, live, or production behavior changes are authorized by dashboard reporting.",
          "name": "Production allocator replacement",
          "pilot_blocking": false,
          "production_scaling_blocking": true,
          "promotion_blocking": true,
          "status": "BLOCKED"
        }
      ],
      "status": "OK",
      "summary": {
        "fr068_pilot_blocking": false,
        "pilot_blocked": false,
        "production_scaling_blocked": true,
        "promotion_blocked": true
      }
    },
    "live_pilot": {
      "account": {
        "account_id_hash": "cfdc5d0aa0e3fdc38adadc78f1ebc30cbc83df187a4223c22597e787cd8a7c85",
        "buying_power": 399.99,
        "cash": 500.0,
        "equity": 500.0,
        "portfolio_value": 500.0,
        "status": "AccountStatus.ACTIVE"
      },
      "as_of": "2026-06-23T14:00:13+00:00",
      "blocking_open_orders": [],
      "is_stale": false,
      "latest_fill_status": "OrderStatus.PENDING_NEW",
      "latest_submitted_order": {
        "client_order_id": "caerus-live-pilot-2026-06-23t100012-0400_59e97cb",
        "limit_price": 86.08,
        "normalized_limit_price": 86.08,
        "notional": 99.999998,
        "order": {
          "client_order_id": "caerus-live-pilot-2026-06-23t100012-0400_59e97cb",
          "filled_at": "",
          "filled_qty": "0",
          "id": "e5966ed8-198b-4be3-a9f0-30e616ac6d35",
          "qty": "1.161710012",
          "raw": {
            "asset_class": "us_equity",
            "asset_id": "ac20a75d-5bb4-4d10-96e8-f5e58e730139",
            "canceled_at": null,
            "client_order_id": "caerus-live-pilot-2026-06-23t100012-0400_59e97cb",
            "created_at": "2026-06-23 14:00:13.526161+00:00",
            "expired_at": null,
            "expires_at": "2026-06-23 20:00:00+00:00",
            "extended_hours": false,
            "failed_at": null,
            "filled_at": null,
            "filled_avg_price": null,
            "filled_qty": "0",
            "hwm": null,
            "id": "e5966ed8-198b-4be3-a9f0-30e616ac6d35",
            "legs": null,
            "limit_price": "86.08",
            "notional": null,
            "order_class": "simple",
            "order_type": "limit",
            "position_intent": "buy_to_open",
            "qty": "1.161710012",
            "ratio_qty": null,
            "replaced_at": null,
            "replaced_by": null,
            "replaces": null,
            "side": "buy",
            "status": "pending_new",
            "stop_price": null,
            "submitted_at": "2026-06-23 14:00:13.526161+00:00",
            "symbol": "NEE",
            "time_in_force": "day",
            "trail_percent": null,
            "trail_price": null,
            "type": "limit",
            "updated_at": "2026-06-23 14:00:13.526950+00:00"
          },
          "side": "OrderSide.BUY",
          "status": "OrderStatus.PENDING_NEW",
          "submitted_at": "2026-06-23 14:00:13.526161+00:00",
          "symbol": "NEE"
        },
        "order_type": "limit",
        "original_limit_price": 86.08000183105469,
        "qty": 1.161710012,
        "side": "BUY",
        "status": "OrderStatus.PENDING_NEW",
        "symbol": "NEE"
      },
      "metrics": {
        "accepted_count": null,
        "average_time_to_fill_seconds": null,
        "blocking_open_order_count": 0,
        "capital_cap_usd": 100.0,
        "cash_deployment_rate": null,
        "fill_rate": null,
        "filled_count": null,
        "filled_notional_usd": null,
        "idle_cash_reason": null,
        "open_order_count": 0,
        "reconciliation_clean": null,
        "reconciliation_clean_rate": null,
        "rejected_count": null,
        "slippage_bps": null,
        "submitted_count": 1
      },
      "open_orders": [],
      "paper_live_comparability": {
        "available": false,
        "reason": "paper_live_divergence_artifact_not_available_for_live_pilot_section"
      },
      "plan_path": "outputs/live_pilot/plans/live_pilot_plan_2026-06-23.json",
      "plan_status": "READY_FOR_MANUAL_APPROVAL",
      "policy": {
        "cap_enforced_before_submission": true,
        "capital_behavior_changed": false,
        "duplicate_open_order_policy": "skip_if_open_live_pilot_order_detected",
        "normal_market_hours_only": true,
        "order_type": "limit",
        "paper_or_production_impact": "none",
        "scope": "FR-104 LIVE_PILOT only",
        "time_in_force": null
      },
      "positions": [],
      "reconciliation": {
        "open_count": 1,
        "operator_action": "Monitor broker terminal states and preserve all live pilot artifacts.",
        "rejected_count": 0,
        "state": "CLEAN",
        "status": "CLEAN",
        "unresolved_count": 0
      },
      "run_id": "2026-06-23T100012-0400_59e97cb",
      "run_root": "outputs/live_pilot/runs/2026-06-23T100012-0400_59e97cb",
      "selected_order": {
        "approved_sleeve_override": "orion",
        "final_qty": 1.161710012,
        "limit_price": 86.08,
        "limit_price_source": "entry_price",
        "normalized_limit_price": 86.08,
        "notional": 99.99999783296,
        "order_type": "limit",
        "original_limit_price": 86.08000183105469,
        "original_qty": 8.0,
        "pilot_notional_cap": 100.0,
        "pilot_qty": 1.161710012,
        "pre_normalization_qty": 1.161710012,
        "qty": 1.161710012,
        "scale_reason": "live_pilot_cap",
        "scaled_to_pilot_cap": true,
        "shares": 1.161710012,
        "side": "BUY",
        "sleeve": "orion",
        "sleeve_source": "missing_in_source_overridden_for_live_pilot",
        "source_notional": 688.640015,
        "source_order_qty": 8.0,
        "source_precompute_index": 2,
        "source_reason": "rebalance_to_target",
        "stop_loss": 82.93285696847099,
        "symbol": "NEE",
        "take_profit": 90.80071912493024,
        "ticker": "NEE"
      },
      "status": "SUBMITTED",
      "submitted_orders": [
        {
          "client_order_id": "caerus-live-pilot-2026-06-23t100012-0400_59e97cb",
          "limit_price": 86.08,
          "normalized_limit_price": 86.08,
          "notional": 99.999998,
          "order": {
            "client_order_id": "caerus-live-pilot-2026-06-23t100012-0400_59e97cb",
            "filled_at": "",
            "filled_qty": "0",
            "id": "e5966ed8-198b-4be3-a9f0-30e616ac6d35",
            "qty": "1.161710012",
            "raw": {
              "asset_class": "us_equity",
              "asset_id": "ac20a75d-5bb4-4d10-96e8-f5e58e730139",
              "canceled_at": null,
              "client_order_id": "caerus-live-pilot-2026-06-23t100012-0400_59e97cb",
              "created_at": "2026-06-23 14:00:13.526161+00:00",
              "expired_at": null,
              "expires_at": "2026-06-23 20:00:00+00:00",
              "extended_hours": false,
              "failed_at": null,
              "filled_at": null,
              "filled_avg_price": null,
              "filled_qty": "0",
              "hwm": null,
              "id": "e5966ed8-198b-4be3-a9f0-30e616ac6d35",
              "legs": null,
              "limit_price": "86.08",
              "notional": null,
              "order_class": "simple",
              "order_type": "limit",
              "position_intent": "buy_to_open",
              "qty": "1.161710012",
              "ratio_qty": null,
              "replaced_at": null,
              "replaced_by": null,
              "replaces": null,
              "side": "buy",
              "status": "pending_new",
              "stop_price": null,
              "submitted_at": "2026-06-23 14:00:13.526161+00:00",
              "symbol": "NEE",
              "time_in_force": "day",
              "trail_percent": null,
              "trail_price": null,
              "type": "limit",
              "updated_at": "2026-06-23 14:00:13.526950+00:00"
            },
            "side": "OrderSide.BUY",
            "status": "OrderStatus.PENDING_NEW",
            "submitted_at": "2026-06-23 14:00:13.526161+00:00",
            "symbol": "NEE"
          },
          "order_type": "limit",
          "original_limit_price": 86.08000183105469,
          "qty": 1.161710012,
          "side": "BUY",
          "status": "OrderStatus.PENDING_NEW",
          "symbol": "NEE"
        }
      ]
    },
    "live_readiness": {
      "as_of": "2026-06-23T20:06:00+00:00",
      "criteria": [
        {
          "detail": "0 blocking errors",
          "name": "Validation integrity",
          "status": "PASS"
        },
        {
          "detail": "canonical dashboard sources loaded",
          "name": "Artifact completeness",
          "status": "PASS"
        },
        {
          "detail": "NAV through 2026-06-22",
          "name": "Shadow continuity",
          "status": "WARN"
        },
        {
          "detail": "0 fail \u00b7 2 warn",
          "name": "Operational health",
          "status": "WARN"
        }
      ],
      "is_stale": false,
      "summary": {
        "artifact_completeness_streak": 78,
        "consecutive_healthy_days": 78,
        "deployment_confidence": "WATCH",
        "shadow_evaluation_continuity": "2026-06-22",
        "successful_execution_streak": null
      }
    },
    "nav": {
      "as_of": "2026-06-23T20:05:54.766588+00:00",
      "buying_power": 31396.03,
      "cash": 552.51,
      "day_pnl": -133.96,
      "day_return": -0.012057834745384799,
      "equity": 10976.08,
      "gross_exposure": 0.9496623566883623,
      "is_stale": false,
      "long_market_value": 10423.57,
      "net_exposure": 0.9496623566883623,
      "short_market_value": 0.0,
      "source_type": "broker_account",
      "trust_level": "canonical"
    },
    "operator_control_tower": {
      "as_of": "2026-06-23T20:06:00+00:00",
      "cards": [
        {
          "detail": "Day return -0.012057834745384799",
          "id": "paper_nav",
          "label": "Paper NAV / Return",
          "status": "OK",
          "value": 10976.08,
          "value_format": "money"
        },
        {
          "detail": "Cash 500.0 \u00b7 Equity 500.0",
          "id": "live_capital",
          "label": "Live Pilot Capital",
          "status": "ACTIVE",
          "value": 0.0,
          "value_format": "percent"
        },
        {
          "detail": "NEE BUY 1.161710012 limit",
          "id": "latest_order",
          "label": "Latest Live Order",
          "status": "OrderStatus.PENDING_NEW",
          "value": "OrderStatus.PENDING_NEW",
          "value_format": "text"
        },
        {
          "detail": "paper 1 \u00b7 research 4 \u00b7 shadow 4",
          "id": "sleeves",
          "label": "Sleeves by Lifecycle",
          "status": "OK",
          "value": 9,
          "value_format": "integer"
        },
        {
          "detail": "0 fail \u00b7 2 warn",
          "id": "validation",
          "label": "Validation Status",
          "status": "WARNING",
          "value": "WARNING",
          "value_format": "text"
        },
        {
          "detail": "Live pilot order open",
          "id": "operator_action",
          "label": "Operator Action",
          "status": "ACTION_REQUIRED",
          "value": "REQUIRED",
          "value_format": "text"
        }
      ],
      "is_stale": false,
      "latest_order": {
        "expected_price": 86.08,
        "fill_price": null,
        "filled_qty": 0.0,
        "order_type": "limit",
        "qty": 1.161710012,
        "side": "BUY",
        "status": "OrderStatus.PENDING_NEW",
        "ticker": "NEE"
      },
      "operator_actions": [
        {
          "blocks_pilot": false,
          "detail": "Latest FR-104 live-pilot order is still open or pending broker terminal state.",
          "expected_artifact": "outputs/live_pilot/runs/2026-06-23T100012-0400_59e97cb",
          "operator_action": "Monitor broker truth and do not submit duplicate exposure.",
          "severity": "action",
          "status": "ACTION_REQUIRED",
          "title": "Live pilot order open"
        },
        {
          "blocks_pilot": false,
          "detail": "Shadow NAV latest date lags the latest shadow evaluation date.",
          "expected_artifact": "outputs/shadow_candidates/performance/shadow_nav_series.csv",
          "operator_action": "Refresh shadow scorecard artifacts before judging alpha-vs-baseline evidence.",
          "severity": "watch",
          "status": "WATCH",
          "title": "Shadow NAV stale"
        },
        {
          "blocks_pilot": false,
          "detail": "PIT date-effective membership remains a promotion/scaling blocker only.",
          "expected_artifact": "reports/fr068_requirement_replacement_remediation_2026-06-23.md",
          "operator_action": "Continue FR-104 evidence collection; do not promote or scale.",
          "severity": "info",
          "status": "INFO",
          "title": "FR-068 blocked but not pilot-blocking"
        },
        {
          "blocks_pilot": false,
          "detail": "MODEL_QUALITY_DATE_DIFFERS_FROM_REPORT_DATE, NO_DECISION_GRADE_RECOMMENDATION, NO_DECISION_GRADE_STRATEGY_CHANGE",
          "expected_artifact": "outputs/model_quality/<date>/",
          "operator_action": "Use this as a promotion-readiness warning, not a pilot stop.",
          "severity": "watch",
          "status": "PARTIAL",
          "title": "Decision-grade evidence incomplete"
        }
      ],
      "status": "ACTION_REQUIRED",
      "summary": {
        "alpha_pair_count": 2,
        "fr068_pilot_blocking": false,
        "latest_order_status": "OrderStatus.PENDING_NEW",
        "live_pilot_deployed_pct": 0.0,
        "live_pilot_open_orders": 0,
        "live_pilot_state": "ACTIVE",
        "operator_action_required": true,
        "primary_action": "Live pilot order open",
        "sleeve_count_by_lifecycle": {
          "paper": 1,
          "research": 4,
          "shadow": 4
        },
        "validation_status": "WARNING"
      }
    },
    "performance_history": {
      "_bench_rows": [
        {
          "date": "2026-03-03",
          "spy_close": 680.3300170898438,
          "spy_return": null
        },
        {
          "date": "2026-03-04",
          "spy_close": 685.1300048828125,
          "spy_return": 0.007055381465455479
        },
        {
          "date": "2026-03-05",
          "spy_close": 681.3099975585938,
          "spy_return": -0.0055755948462250515
        },
        {
          "date": "2026-03-06",
          "spy_close": 672.3800048828125,
          "spy_return": -0.013107091790493297
        },
        {
          "date": "2026-03-09",
          "spy_close": 678.27001953125,
          "spy_return": 0.008759949144329537
        },
        {
          "date": "2026-03-10",
          "spy_close": 677.1799926757812,
          "spy_return": -0.001607069196751576
        },
        {
          "date": "2026-03-11",
          "spy_close": 676.3300170898438,
          "spy_return": -0.0012551693717042234
        },
        {
          "date": "2026-03-12",
          "spy_close": 666.0599975585938,
          "spy_return": -0.0151849234423167
        },
        {
          "date": "2026-03-13",
          "spy_close": 662.2899780273438,
          "spy_return": -0.005660180081477395
        },
        {
          "date": "2026-03-16",
          "spy_close": 669.030029296875,
          "spy_return": 0.010176888512803295
        },
        {
          "date": "2026-03-17",
          "spy_close": 670.7899780273438,
          "spy_return": 0.0026305975119209624
        },
        {
          "date": "2026-03-18",
          "spy_close": 661.4299926757812,
          "spy_return": -0.013953675007322364
        },
        {
          "date": "2026-03-19",
          "spy_close": 659.7999877929688,
          "spy_return": -0.002464364937880159
        },
        {
          "date": "2026-03-20",
          "spy_close": 648.5700073242188,
          "spy_return": -0.017020279897722146
        },
        {
          "date": "2026-03-23",
          "spy_close": 655.3800048828125,
          "spy_return": 0.01050001924493782
        },
        {
          "date": "2026-03-24",
          "spy_close": 653.1799926757812,
          "spy_return": -0.0033568497522664664
        },
        {
          "date": "2026-03-25",
          "spy_close": 656.8200073242188,
          "spy_return": 0.0055727589473859585
        },
        {
          "date": "2026-03-26",
          "spy_close": 645.0900268554688,
          "spy_return": -0.017858744158138706
        },
        {
          "date": "2026-03-27",
          "spy_close": 634.0900268554688,
          "spy_return": -0.017051883523327982
        },
        {
          "date": "2026-03-30",
          "spy_close": 631.969970703125,
          "spy_return": -0.0033434623831846144
        },
        {
          "date": "2026-03-31",
          "spy_close": 650.3400268554688,
          "spy_return": 0.02906792569891481
        },
        {
          "date": "2026-04-01",
          "spy_close": 655.239990234375,
          "spy_return": 0.007534463782890022
        },
        {
          "date": "2026-04-02",
          "spy_close": 655.8300170898438,
          "spy_return": 0.0009004744280911581
        },
        {
          "date": "2026-04-06",
          "spy_close": 658.9299926757812,
          "spy_return": 0.004726797348638012
        },
        {
          "date": "2026-04-07",
          "spy_close": 659.219970703125,
          "spy_return": 0.0004400741058487867
        },
        {
          "date": "2026-04-08",
          "spy_close": 676.010009765625,
          "spy_return": 0.025469554638327674
        },
        {
          "date": "2026-04-09",
          "spy_close": 679.9099731445312,
          "spy_return": 0.00576909117108837
        },
        {
          "date": "2026-04-10",
          "spy_close": 679.4600219726562,
          "spy_return": -0.0006617805145495703
        },
        {
          "date": "2026-04-13",
          "spy_close": 686.0999755859375,
          "spy_return": 0.009772397784351794
        },
        {
          "date": "2026-04-14",
          "spy_close": 694.4600219726562,
          "spy_return": 0.01218488075237012
        },
        {
          "date": "2026-04-15",
          "spy_close": 699.9400024414062,
          "spy_return": 0.007890994867038925
        },
        {
          "date": "2026-04-16",
          "spy_close": 701.6599731445312,
          "spy_return": 0.002457311622604319
        },
        {
          "date": "2026-04-17",
          "spy_close": 710.1400146484375,
          "spy_return": 0.012085685130224011
        },
        {
          "date": "2026-04-20",
          "spy_close": 708.719970703125,
          "spy_return": -0.001999667552905704
        },
        {
          "date": "2026-04-21",
          "spy_close": 704.0800170898438,
          "spy_return": -0.006546949154936255
        },
        {
          "date": "2026-04-22",
          "spy_close": 711.2100219726562,
          "spy_return": 0.010126696838070659
        },
        {
          "date": "2026-04-23",
          "spy_close": 708.4500122070312,
          "spy_return": -0.0038807239498251933
        },
        {
          "date": "2026-04-24",
          "spy_close": 713.9400024414062,
          "spy_return": 0.007749297960024215
        },
        {
          "date": "2026-04-27",
          "spy_close": 715.1699829101562,
          "spy_return": 0.0017228064887020444
        },
        {
          "date": "2026-04-28",
          "spy_close": 711.6900024414062,
          "spy_return": -0.004865948728146163
        },
        {
          "date": "2026-04-29",
          "spy_close": 711.5800170898438,
          "spy_return": -0.00015454109399481997
        },
        {
          "date": "2026-04-30",
          "spy_close": 718.6599731445312,
          "spy_return": 0.009949627427204177
        },
        {
          "date": "2026-05-01",
          "spy_close": 720.6500244140625,
          "spy_return": 0.0027691138283709726
        },
        {
          "date": "2026-05-04",
          "spy_close": 718.010009765625,
          "spy_return": -0.0036633796697419507
        },
        {
          "date": "2026-05-05",
          "spy_close": 723.77001953125,
          "spy_return": 0.008022185884992261
        },
        {
          "date": "2026-05-06",
          "spy_close": 733.8300170898438,
          "spy_return": 0.013899439445017592
        },
        {
          "date": "2026-05-07",
          "spy_close": 731.5800170898438,
          "spy_return": -0.0030661051573263043
        },
        {
          "date": "2026-05-08",
          "spy_close": 737.6199951171875,
          "spy_return": 0.008256073001242203
        },
        {
          "date": "2026-05-11",
          "spy_close": 739.2999877929688,
          "spy_return": 0.002277585595431564
        },
        {
          "date": "2026-05-12",
          "spy_close": 738.1799926757812,
          "spy_return": -0.0015149399914519135
        },
        {
          "date": "2026-05-13",
          "spy_close": 742.3099975585938,
          "spy_return": 0.005594848036780231
        },
        {
          "date": "2026-05-14",
          "spy_close": 748.1699829101562,
          "spy_return": 0.007894256268722755
        },
        {
          "date": "2026-05-15",
          "spy_close": 739.1699829101562,
          "spy_return": -0.012029351892724582
        },
        {
          "date": "2026-05-18",
          "spy_close": 738.6500244140625,
          "spy_return": -0.0007034356211904624
        },
        {
          "date": "2026-05-19",
          "spy_close": 733.72998046875,
          "spy_return": -0.006660859382243145
        },
        {
          "date": "2026-05-20",
          "spy_close": 741.25,
          "spy_return": 0.010249028568310337
        },
        {
          "date": "2026-05-21",
          "spy_close": 742.719970703125,
          "spy_return": 0.0019830970699832307
        },
        {
          "date": "2026-05-22",
          "spy_close": 745.6400146484375,
          "spy_return": 0.00393155436839554
        },
        {
          "date": "2026-05-26",
          "spy_close": 750.5900268554688,
          "spy_return": 0.0066386085909901915
        },
        {
          "date": "2026-05-27",
          "spy_close": 750.4600219726562,
          "spy_return": -0.0001732035840619206
        },
        {
          "date": "2026-05-28",
          "spy_close": 754.5999755859375,
          "spy_return": 0.0055165545026623075
        },
        {
          "date": "2026-05-29",
          "spy_close": 756.47998046875,
          "spy_return": 0.002491392716190699
        },
        {
          "date": "2026-06-01",
          "spy_close": 758.5399780273438,
          "spy_return": 0.00272313559086812
        },
        {
          "date": "2026-06-02",
          "spy_close": 759.5700073242188,
          "spy_return": 0.0013579103629497435
        },
        {
          "date": "2026-06-03",
          "spy_close": 754.239990234375,
          "spy_return": -0.007017150543661033
        },
        {
          "date": "2026-06-04",
          "spy_close": 757.0900268554688,
          "spy_return": 0.003778686701839007
        },
        {
          "date": "2026-06-05",
          "spy_close": 737.5499877929688,
          "spy_return": -0.0258093996346227
        },
        {
          "date": "2026-06-08",
          "spy_close": 739.219970703125,
          "spy_return": 0.0022642301373410056
        },
        {
          "date": "2026-06-09",
          "spy_close": 737.0499877929688,
          "spy_return": -0.002935503633772485
        },
        {
          "date": "2026-06-10",
          "spy_close": 725.4299926757812,
          "spy_return": -0.015765545498457323
        },
        {
          "date": "2026-06-11",
          "spy_close": 737.760009765625,
          "spy_return": 0.016996839411566045
        },
        {
          "date": "2026-06-12",
          "spy_close": 741.75,
          "spy_return": 0.005408249541260179
        },
        {
          "date": "2026-06-15",
          "spy_close": 754.8300170898438,
          "spy_return": 0.017633996750716197
        },
        {
          "date": "2026-06-16",
          "spy_close": 750.3300170898438,
          "spy_return": -0.005961607114339795
        },
        {
          "date": "2026-06-17",
          "spy_close": 740.9600219726562,
          "spy_return": -0.012487831892330603
        },
        {
          "date": "2026-06-18",
          "spy_close": 746.739990234375,
          "spy_return": 0.007800647930141791
        },
        {
          "date": "2026-06-22",
          "spy_close": 744.3900146484375,
          "spy_return": -0.003146979693962715
        },
        {
          "date": "2026-06-23",
          "spy_close": 733.72998046875,
          "spy_return": -0.014320495936155253
        }
      ],
      "_nav_rows": [
        {
          "date": "2026-03-03",
          "equity": 10000.0,
          "return_1d": null
        },
        {
          "date": "2026-03-04",
          "equity": 10000.0,
          "return_1d": 0.0
        },
        {
          "date": "2026-03-05",
          "equity": 9970.24,
          "return_1d": -0.0029759999999999787
        },
        {
          "date": "2026-03-06",
          "equity": 9891.61,
          "return_1d": -0.007886470135122003
        },
        {
          "date": "2026-03-09",
          "equity": 9902.02,
          "return_1d": 0.0010524070399056118
        },
        {
          "date": "2026-03-10",
          "equity": 9908.73,
          "return_1d": 0.0006776395119378886
        },
        {
          "date": "2026-03-11",
          "equity": 9918.66,
          "return_1d": 0.0010021465919447525
        },
        {
          "date": "2026-03-12",
          "equity": 9809.79,
          "return_1d": -0.01097628107022508
        },
        {
          "date": "2026-03-13",
          "equity": 9683.13,
          "return_1d": -0.012911591379632159
        },
        {
          "date": "2026-03-16",
          "equity": 9831.57,
          "return_1d": 0.01532975391221636
        },
        {
          "date": "2026-03-17",
          "equity": 9862.23,
          "return_1d": 0.0031185253219983
        },
        {
          "date": "2026-03-18",
          "equity": 9750.91,
          "return_1d": -0.011287507997684076
        },
        {
          "date": "2026-03-19",
          "equity": 9700.14,
          "return_1d": -0.005206693529116846
        },
        {
          "date": "2026-03-20",
          "equity": 9577.23,
          "return_1d": -0.012670951140911324
        },
        {
          "date": "2026-03-23",
          "equity": 9605.55,
          "return_1d": 0.0029570136667909086
        },
        {
          "date": "2026-03-24",
          "equity": 9666.26,
          "return_1d": 0.0063203044073478765
        },
        {
          "date": "2026-03-25",
          "equity": 9647.97,
          "return_1d": -0.0018921485662500848
        },
        {
          "date": "2026-03-26",
          "equity": 9588.41,
          "return_1d": -0.0061733193614822435
        },
        {
          "date": "2026-03-27",
          "equity": 9498.32,
          "return_1d": -0.009395718372493422
        },
        {
          "date": "2026-03-30",
          "equity": 9516.84,
          "return_1d": 0.0019498184942179364
        },
        {
          "date": "2026-03-31",
          "equity": 9606.68,
          "return_1d": 0.009440108271232983
        },
        {
          "date": "2026-04-01",
          "equity": 9596.24,
          "return_1d": -0.0010867438074340097
        },
        {
          "date": "2026-04-02",
          "equity": 9610.63,
          "return_1d": 0.0014995456553816844
        },
        {
          "date": "2026-04-06",
          "equity": 9640.13,
          "return_1d": 0.003069517815169176
        },
        {
          "date": "2026-04-07",
          "equity": 9597.11,
          "return_1d": -0.004462595421430904
        },
        {
          "date": "2026-04-08",
          "equity": 9751.97,
          "return_1d": 0.016136107640737585
        },
        {
          "date": "2026-04-09",
          "equity": 9717.87,
          "return_1d": -0.00349672937878176
        },
        {
          "date": "2026-04-10",
          "equity": 9584.4,
          "return_1d": -0.01373449120023229
        },
        {
          "date": "2026-04-13",
          "equity": 9679.09,
          "return_1d": 0.009879596010183178
        },
        {
          "date": "2026-04-14",
          "equity": 9692.89,
          "return_1d": 0.0014257538673572157
        },
        {
          "date": "2026-04-15",
          "equity": 9715.71,
          "return_1d": 0.002354302999415081
        },
        {
          "date": "2026-04-16",
          "equity": 9758.59,
          "return_1d": 0.004413470554390786
        },
        {
          "date": "2026-04-17",
          "equity": 9781.01,
          "return_1d": 0.002297463055625837
        },
        {
          "date": "2026-04-20",
          "equity": 9787.87,
          "return_1d": 0.0007013590621010035
        },
        {
          "date": "2026-04-21",
          "equity": 9702.98,
          "return_1d": -0.008672979923109003
        },
        {
          "date": "2026-04-22",
          "equity": 9696.49,
          "return_1d": -0.0006688666780720887
        },
        {
          "date": "2026-04-23",
          "equity": 9674.9,
          "return_1d": -0.0022265788960748045
        },
        {
          "date": "2026-04-24",
          "equity": 9678.58,
          "return_1d": 0.00038036568853416775
        },
        {
          "date": "2026-04-27",
          "equity": 9732.29,
          "return_1d": 0.005549367779157821
        },
        {
          "date": "2026-04-28",
          "equity": 9708.7,
          "return_1d": -0.0024238899580674156
        },
        {
          "date": "2026-04-29",
          "equity": 9696.6,
          "return_1d": -0.0012463048605889648
        },
        {
          "date": "2026-04-30",
          "equity": 9944.74,
          "return_1d": 0.02559041313450061
        },
        {
          "date": "2026-05-01",
          "equity": 9977.44,
          "return_1d": 0.003288170429795123
        },
        {
          "date": "2026-05-04",
          "equity": 9977.44,
          "return_1d": 0.0
        },
        {
          "date": "2026-05-05",
          "equity": 10102.64,
          "return_1d": 0.0125483089850702
        },
        {
          "date": "2026-05-06",
          "equity": 10199.77,
          "return_1d": 0.009614318633545338
        },
        {
          "date": "2026-05-07",
          "equity": 10265.62,
          "return_1d": 0.00645602793004163
        },
        {
          "date": "2026-05-08",
          "equity": 10396.94,
          "return_1d": 0.012792213232128091
        },
        {
          "date": "2026-05-11",
          "equity": 10526.0,
          "return_1d": 0.01241326774993401
        },
        {
          "date": "2026-05-12",
          "equity": 10444.21,
          "return_1d": -0.00777028310849337
        },
        {
          "date": "2026-05-13",
          "equity": 10539.14,
          "return_1d": 0.009089246577768995
        },
        {
          "date": "2026-05-14",
          "equity": 10531.27,
          "return_1d": -0.000746740246357791
        },
        {
          "date": "2026-05-15",
          "equity": 10444.61,
          "return_1d": -0.00822882710252415
        },
        {
          "date": "2026-05-18",
          "equity": 10374.4,
          "return_1d": -0.006722127489681373
        },
        {
          "date": "2026-05-19",
          "equity": 10309.54,
          "return_1d": -0.006251927822331749
        },
        {
          "date": "2026-05-20",
          "equity": 10359.31,
          "return_1d": 0.004827567476337391
        },
        {
          "date": "2026-05-21",
          "equity": 10443.89,
          "return_1d": 0.008164636447794305
        },
        {
          "date": "2026-05-22",
          "equity": 10592.41,
          "return_1d": 0.014220754910287292
        },
        {
          "date": "2026-05-26",
          "equity": 10665.09,
          "return_1d": 0.006861516878595264
        },
        {
          "date": "2026-05-27",
          "equity": 10685.9,
          "return_1d": 0.0019512259155805012
        },
        {
          "date": "2026-05-28",
          "equity": 10716.78,
          "return_1d": 0.0028897893485808623
        },
        {
          "date": "2026-05-29",
          "equity": 10730.85,
          "return_1d": 0.0013128943581934838
        },
        {
          "date": "2026-06-01",
          "equity": 10804.37,
          "return_1d": 0.006851274596141099
        },
        {
          "date": "2026-06-02",
          "equity": 10712.51,
          "return_1d": -0.008502115347771344
        },
        {
          "date": "2026-06-03",
          "equity": 10729.32,
          "return_1d": 0.0015691934009862685
        },
        {
          "date": "2026-06-04",
          "equity": 10753.75,
          "return_1d": 0.0022769383334637627
        },
        {
          "date": "2026-06-05",
          "equity": 10578.03,
          "return_1d": -0.016340346390793847
        },
        {
          "date": "2026-06-08",
          "equity": 10596.62,
          "return_1d": 0.0017574160784190607
        },
        {
          "date": "2026-06-09",
          "equity": 10549.0,
          "return_1d": -0.004493885786222451
        },
        {
          "date": "2026-06-10",
          "equity": 10468.29,
          "return_1d": -0.007650962176509513
        },
        {
          "date": "2026-06-11",
          "equity": 10658.5,
          "return_1d": 0.018170111832973568
        },
        {
          "date": "2026-06-12",
          "equity": 10886.32,
          "return_1d": 0.021374489843786648
        },
        {
          "date": "2026-06-15",
          "equity": 11061.0,
          "return_1d": 0.01604582632147511
        },
        {
          "date": "2026-06-16",
          "equity": 10956.49,
          "return_1d": -0.009448512792695096
        },
        {
          "date": "2026-06-17",
          "equity": 10951.64,
          "return_1d": -0.00044266001246751063
        },
        {
          "date": "2026-06-18",
          "equity": 10963.6,
          "return_1d": 0.001092073881172162
        },
        {
          "date": "2026-06-22",
          "equity": 11110.04,
          "return_1d": 0.013356926556970405
        },
        {
          "date": "2026-06-23",
          "equity": 10976.08,
          "return_1d": -0.012057562349010564
        }
      ],
      "as_of": "2026-06-23",
      "is_stale": false,
      "series": {
        "daily_return": [
          {
            "date": "2026-03-04",
            "value": 0.0
          },
          {
            "date": "2026-03-05",
            "value": -0.0029759999999999787
          },
          {
            "date": "2026-03-06",
            "value": -0.007886470135122003
          },
          {
            "date": "2026-03-09",
            "value": 0.0010524070399056118
          },
          {
            "date": "2026-03-10",
            "value": 0.0006776395119378886
          },
          {
            "date": "2026-03-11",
            "value": 0.0010021465919447525
          },
          {
            "date": "2026-03-12",
            "value": -0.01097628107022508
          },
          {
            "date": "2026-03-13",
            "value": -0.012911591379632159
          },
          {
            "date": "2026-03-16",
            "value": 0.01532975391221636
          },
          {
            "date": "2026-03-17",
            "value": 0.0031185253219983
          },
          {
            "date": "2026-03-18",
            "value": -0.011287507997684076
          },
          {
            "date": "2026-03-19",
            "value": -0.005206693529116846
          },
          {
            "date": "2026-03-20",
            "value": -0.012670951140911324
          },
          {
            "date": "2026-03-23",
            "value": 0.0029570136667909086
          },
          {
            "date": "2026-03-24",
            "value": 0.0063203044073478765
          },
          {
            "date": "2026-03-25",
            "value": -0.0018921485662500848
          },
          {
            "date": "2026-03-26",
            "value": -0.0061733193614822435
          },
          {
            "date": "2026-03-27",
            "value": -0.009395718372493422
          },
          {
            "date": "2026-03-30",
            "value": 0.0019498184942179364
          },
          {
            "date": "2026-03-31",
            "value": 0.009440108271232983
          },
          {
            "date": "2026-04-01",
            "value": -0.0010867438074340097
          },
          {
            "date": "2026-04-02",
            "value": 0.0014995456553816844
          },
          {
            "date": "2026-04-06",
            "value": 0.003069517815169176
          },
          {
            "date": "2026-04-07",
            "value": -0.004462595421430904
          },
          {
            "date": "2026-04-08",
            "value": 0.016136107640737585
          },
          {
            "date": "2026-04-09",
            "value": -0.00349672937878176
          },
          {
            "date": "2026-04-10",
            "value": -0.01373449120023229
          },
          {
            "date": "2026-04-13",
            "value": 0.009879596010183178
          },
          {
            "date": "2026-04-14",
            "value": 0.0014257538673572157
          },
          {
            "date": "2026-04-15",
            "value": 0.002354302999415081
          },
          {
            "date": "2026-04-16",
            "value": 0.004413470554390786
          },
          {
            "date": "2026-04-17",
            "value": 0.002297463055625837
          },
          {
            "date": "2026-04-20",
            "value": 0.0007013590621010035
          },
          {
            "date": "2026-04-21",
            "value": -0.008672979923109003
          },
          {
            "date": "2026-04-22",
            "value": -0.0006688666780720887
          },
          {
            "date": "2026-04-23",
            "value": -0.0022265788960748045
          },
          {
            "date": "2026-04-24",
            "value": 0.00038036568853416775
          },
          {
            "date": "2026-04-27",
            "value": 0.005549367779157821
          },
          {
            "date": "2026-04-28",
            "value": -0.0024238899580674156
          },
          {
            "date": "2026-04-29",
            "value": -0.0012463048605889648
          },
          {
            "date": "2026-04-30",
            "value": 0.02559041313450061
          },
          {
            "date": "2026-05-01",
            "value": 0.003288170429795123
          },
          {
            "date": "2026-05-04",
            "value": 0.0
          },
          {
            "date": "2026-05-05",
            "value": 0.0125483089850702
          },
          {
            "date": "2026-05-06",
            "value": 0.009614318633545338
          },
          {
            "date": "2026-05-07",
            "value": 0.00645602793004163
          },
          {
            "date": "2026-05-08",
            "value": 0.012792213232128091
          },
          {
            "date": "2026-05-11",
            "value": 0.01241326774993401
          },
          {
            "date": "2026-05-12",
            "value": -0.00777028310849337
          },
          {
            "date": "2026-05-13",
            "value": 0.009089246577768995
          },
          {
            "date": "2026-05-14",
            "value": -0.000746740246357791
          },
          {
            "date": "2026-05-15",
            "value": -0.00822882710252415
          },
          {
            "date": "2026-05-18",
            "value": -0.006722127489681373
          },
          {
            "date": "2026-05-19",
            "value": -0.006251927822331749
          },
          {
            "date": "2026-05-20",
            "value": 0.004827567476337391
          },
          {
            "date": "2026-05-21",
            "value": 0.008164636447794305
          },
          {
            "date": "2026-05-22",
            "value": 0.014220754910287292
          },
          {
            "date": "2026-05-26",
            "value": 0.006861516878595264
          },
          {
            "date": "2026-05-27",
            "value": 0.0019512259155805012
          },
          {
            "date": "2026-05-28",
            "value": 0.0028897893485808623
          },
          {
            "date": "2026-05-29",
            "value": 0.0013128943581934838
          },
          {
            "date": "2026-06-01",
            "value": 0.006851274596141099
          },
          {
            "date": "2026-06-02",
            "value": -0.008502115347771344
          },
          {
            "date": "2026-06-03",
            "value": 0.0015691934009862685
          },
          {
            "date": "2026-06-04",
            "value": 0.0022769383334637627
          },
          {
            "date": "2026-06-05",
            "value": -0.016340346390793847
          },
          {
            "date": "2026-06-08",
            "value": 0.0017574160784190607
          },
          {
            "date": "2026-06-09",
            "value": -0.004493885786222451
          },
          {
            "date": "2026-06-10",
            "value": -0.007650962176509513
          },
          {
            "date": "2026-06-11",
            "value": 0.018170111832973568
          },
          {
            "date": "2026-06-12",
            "value": 0.021374489843786648
          },
          {
            "date": "2026-06-15",
            "value": 0.01604582632147511
          },
          {
            "date": "2026-06-16",
            "value": -0.009448512792695096
          },
          {
            "date": "2026-06-17",
            "value": -0.00044266001246751063
          },
          {
            "date": "2026-06-18",
            "value": 0.001092073881172162
          },
          {
            "date": "2026-06-22",
            "value": 0.013356926556970405
          },
          {
            "date": "2026-06-23",
            "value": -0.012057562349010564
          }
        ],
        "drawdown": [
          {
            "date": "2026-03-03",
            "value": 0.0
          },
          {
            "date": "2026-03-04",
            "value": 0.0
          },
          {
            "date": "2026-03-05",
            "value": -0.0029759999999999787
          },
          {
            "date": "2026-03-06",
            "value": -0.010838999999999932
          },
          {
            "date": "2026-03-09",
            "value": -0.009797999999999973
          },
          {
            "date": "2026-03-10",
            "value": -0.009126999999999996
          },
          {
            "date": "2026-03-11",
            "value": -0.008133999999999975
          },
          {
            "date": "2026-03-12",
            "value": -0.019020999999999955
          },
          {
            "date": "2026-03-13",
            "value": -0.03168700000000013
          },
          {
            "date": "2026-03-16",
            "value": -0.016843000000000052
          },
          {
            "date": "2026-03-17",
            "value": -0.01377700000000004
          },
          {
            "date": "2026-03-18",
            "value": -0.02490900000000007
          },
          {
            "date": "2026-03-19",
            "value": -0.029986000000000068
          },
          {
            "date": "2026-03-20",
            "value": -0.04227700000000001
          },
          {
            "date": "2026-03-23",
            "value": -0.03944500000000006
          },
          {
            "date": "2026-03-24",
            "value": -0.033374000000000015
          },
          {
            "date": "2026-03-25",
            "value": -0.035203000000000095
          },
          {
            "date": "2026-03-26",
            "value": -0.04115900000000006
          },
          {
            "date": "2026-03-27",
            "value": -0.05016799999999999
          },
          {
            "date": "2026-03-30",
            "value": -0.048316000000000026
          },
          {
            "date": "2026-03-31",
            "value": -0.03933199999999992
          },
          {
            "date": "2026-04-01",
            "value": -0.04037599999999997
          },
          {
            "date": "2026-04-02",
            "value": -0.03893700000000011
          },
          {
            "date": "2026-04-06",
            "value": -0.0359870000000001
          },
          {
            "date": "2026-04-07",
            "value": -0.04028899999999991
          },
          {
            "date": "2026-04-08",
            "value": -0.02480300000000002
          },
          {
            "date": "2026-04-09",
            "value": -0.028212999999999933
          },
          {
            "date": "2026-04-10",
            "value": -0.04156000000000004
          },
          {
            "date": "2026-04-13",
            "value": -0.03209099999999998
          },
          {
            "date": "2026-04-14",
            "value": -0.030711000000000044
          },
          {
            "date": "2026-04-15",
            "value": -0.028429000000000038
          },
          {
            "date": "2026-04-16",
            "value": -0.024140999999999968
          },
          {
            "date": "2026-04-17",
            "value": -0.021899000000000002
          },
          {
            "date": "2026-04-20",
            "value": -0.021212999999999926
          },
          {
            "date": "2026-04-21",
            "value": -0.029702000000000006
          },
          {
            "date": "2026-04-22",
            "value": -0.030351000000000017
          },
          {
            "date": "2026-04-23",
            "value": -0.03251000000000004
          },
          {
            "date": "2026-04-24",
            "value": -0.032142000000000004
          },
          {
            "date": "2026-04-27",
            "value": -0.026770999999999878
          },
          {
            "date": "2026-04-28",
            "value": -0.029129999999999878
          },
          {
            "date": "2026-04-29",
            "value": -0.030339999999999923
          },
          {
            "date": "2026-04-30",
            "value": -0.005526000000000031
          },
          {
            "date": "2026-05-01",
            "value": -0.0022559999999999247
          },
          {
            "date": "2026-05-04",
            "value": -0.0022559999999999247
          },
          {
            "date": "2026-05-05",
            "value": 0.0
          },
          {
            "date": "2026-05-06",
            "value": 0.0
          },
          {
            "date": "2026-05-07",
            "value": 0.0
          },
          {
            "date": "2026-05-08",
            "value": 0.0
          },
          {
            "date": "2026-05-11",
            "value": 0.0
          },
          {
            "date": "2026-05-12",
            "value": -0.00777028310849337
          },
          {
            "date": "2026-05-13",
            "value": 0.0
          },
          {
            "date": "2026-05-14",
            "value": -0.000746740246357791
          },
          {
            "date": "2026-05-15",
            "value": -0.008969422552504214
          },
          {
            "date": "2026-05-18",
            "value": -0.01563125644027874
          },
          {
            "date": "2026-05-19",
            "value": -0.021785458775573585
          },
          {
            "date": "2026-05-20",
            "value": -0.01706306207147834
          },
          {
            "date": "2026-05-21",
            "value": -0.009037739322183769
          },
          {
            "date": "2026-05-22",
            "value": 0.0
          },
          {
            "date": "2026-05-26",
            "value": 0.0
          },
          {
            "date": "2026-05-27",
            "value": 0.0
          },
          {
            "date": "2026-05-28",
            "value": 0.0
          },
          {
            "date": "2026-05-29",
            "value": 0.0
          },
          {
            "date": "2026-06-01",
            "value": 0.0
          },
          {
            "date": "2026-06-02",
            "value": -0.008502115347771344
          },
          {
            "date": "2026-06-03",
            "value": -0.006946263410083198
          },
          {
            "date": "2026-06-04",
            "value": -0.004685141290052153
          },
          {
            "date": "2026-06-05",
            "value": -0.020948930849276737
          },
          {
            "date": "2026-06-08",
            "value": -0.01922833075875785
          },
          {
            "date": "2026-06-09",
            "value": -0.023635806622690714
          },
          {
            "date": "2026-06-10",
            "value": -0.03110593213671875
          },
          {
            "date": "2026-06-11",
            "value": -0.013501018569338274
          },
          {
            "date": "2026-06-12",
            "value": 0.0
          },
          {
            "date": "2026-06-15",
            "value": 0.0
          },
          {
            "date": "2026-06-16",
            "value": -0.009448512792695096
          },
          {
            "date": "2026-06-17",
            "value": -0.00988699032637197
          },
          {
            "date": "2026-06-18",
            "value": -0.008805713769098555
          },
          {
            "date": "2026-06-22",
            "value": 0.0
          },
          {
            "date": "2026-06-23",
            "value": -0.012057562349010564
          }
        ],
        "excess_return_cumulative": [
          {
            "date": "2026-03-04",
            "value": -0.007055381465455479
          },
          {
            "date": "2026-03-05",
            "value": -0.0044164486706933515
          },
          {
            "date": "2026-03-06",
            "value": 0.000846523212746364
          },
          {
            "date": "2026-03-09",
            "value": -0.006770061342514566
          },
          {
            "date": "2026-03-10",
            "value": -0.004496858252709179
          },
          {
            "date": "2026-03-11",
            "value": -0.002254500493112821
          },
          {
            "date": "2026-03-12",
            "value": 0.0019541431993127523
          },
          {
            "date": "2026-03-13",
            "value": -0.005170399806952886
          },
          {
            "date": "2026-03-16",
            "value": -0.0002334318358530041
          },
          {
            "date": "2026-03-17",
            "value": 0.0002456637409126161
          },
          {
            "date": "2026-03-18",
            "value": 0.002871671055657332
          },
          {
            "date": "2026-03-19",
            "value": 0.0001905742818369971
          },
          {
            "date": "2026-03-20",
            "value": 0.0044062404389279
          },
          {
            "date": "2026-03-23",
            "value": -0.002771603882985141
          },
          {
            "date": "2026-03-24",
            "value": 0.00653313878861117
          },
          {
            "date": "2026-03-25",
            "value": -0.0006462272940260805
          },
          {
            "date": "2026-03-26",
            "value": 0.010639376301425751
          },
          {
            "date": "2026-03-27",
            "value": 0.01779899994536438
          },
          {
            "date": "2026-03-30",
            "value": 0.022767217220933778
          },
          {
            "date": "2026-03-31",
            "value": 0.004749533198637024
          },
          {
            "date": "2026-04-01",
            "value": -0.0034967998688736346
          },
          {
            "date": "2026-04-02",
            "value": -0.0029250655203182374
          },
          {
            "date": "2026-04-06",
            "value": -0.004531641752538507
          },
          {
            "date": "2026-04-07",
            "value": -0.009259873169732313
          },
          {
            "date": "2026-04-08",
            "value": -0.018453129766876963
          },
          {
            "date": "2026-04-09",
            "value": -0.0275955879576657
          },
          {
            "date": "2026-04-10",
            "value": -0.04028121603437529
          },
          {
            "date": "2026-04-13",
            "value": -0.0405721170331349
          },
          {
            "date": "2026-04-14",
            "value": -0.051480339185200785
          },
          {
            "date": "2026-04-15",
            "value": -0.05725322480114181
          },
          {
            "date": "2026-04-16",
            "value": -0.05549336652636261
          },
          {
            "date": "2026-04-17",
            "value": -0.06571596648651157
          },
          {
            "date": "2026-04-20",
            "value": -0.06294267956745592
          },
          {
            "date": "2026-04-21",
            "value": -0.0646115283221399
          },
          {
            "date": "2026-04-22",
            "value": -0.07574074337028902
          },
          {
            "date": "2026-04-23",
            "value": -0.07384287435629044
          },
          {
            "date": "2026-04-24",
            "value": -0.08154447307524593
          },
          {
            "date": "2026-04-27",
            "value": -0.07798139046511976
          },
          {
            "date": "2026-04-28",
            "value": -0.07522525460262208
          },
          {
            "date": "2026-04-29",
            "value": -0.07627358989755306
          },
          {
            "date": "2026-04-30",
            "value": -0.061866239430631964
          },
          {
            "date": "2026-05-01",
            "value": -0.06152136579510392
          },
          {
            "date": "2026-05-04",
            "value": -0.05764087458918832
          },
          {
            "date": "2026-05-05",
            "value": -0.053587368233352084
          },
          {
            "date": "2026-05-06",
            "value": -0.058661305904610606
          },
          {
            "date": "2026-05-07",
            "value": -0.0487690874319866
          },
          {
            "date": "2026-05-08",
            "value": -0.04451509939033027
          },
          {
            "date": "2026-05-11",
            "value": -0.03407847841753764
          },
          {
            "date": "2026-05-12",
            "value": -0.0406112257327329
          },
          {
            "date": "2026-05-13",
            "value": -0.03718881615071701
          },
          {
            "date": "2026-05-14",
            "value": -0.046589261396935644
          },
          {
            "date": "2026-05-15",
            "value": -0.042026387506440166
          },
          {
            "date": "2026-05-18",
            "value": -0.04828311357609416
          },
          {
            "date": "2026-05-19",
            "value": -0.04753726458851237
          },
          {
            "date": "2026-05-20",
            "value": -0.05361375236995314
          },
          {
            "date": "2026-05-21",
            "value": -0.047316425375993454
          },
          {
            "date": "2026-05-22",
            "value": -0.03675652461013157
          },
          {
            "date": "2026-05-26",
            "value": -0.036764423192712226
          },
          {
            "date": "2026-05-27",
            "value": -0.0344923322816153
          },
          {
            "date": "2026-05-28",
            "value": -0.03748954608857069
          },
          {
            "date": "2026-05-29",
            "value": -0.03884591803393067
          },
          {
            "date": "2026-06-01",
            "value": -0.03452185669141561
          },
          {
            "date": "2026-06-02",
            "value": -0.04522187087717944
          },
          {
            "date": "2026-06-03",
            "value": -0.03570641266432073
          },
          {
            "date": "2026-06-04",
            "value": -0.03745260989140342
          },
          {
            "date": "2026-06-05",
            "value": -0.026303197383274135
          },
          {
            "date": "2026-06-08",
            "value": -0.026898863307467602
          },
          {
            "date": "2026-06-09",
            "value": -0.02847125994491373
          },
          {
            "date": "2026-06-10",
            "value": -0.01946232105453105
          },
          {
            "date": "2026-06-11",
            "value": -0.018564903404441502
          },
          {
            "date": "2026-06-12",
            "value": -0.001647689808314201
          },
          {
            "date": "2026-06-15",
            "value": -0.003405678315765881
          },
          {
            "date": "2026-06-16",
            "value": -0.00724224137051821
          },
          {
            "date": "2026-06-17",
            "value": 0.006045479047240621
          },
          {
            "date": "2026-06-18",
            "value": -0.0012543510889087006
          },
          {
            "date": "2026-06-22",
            "value": 0.016843818985770254
          },
          {
            "date": "2026-06-23",
            "value": 0.019116735411487173
          }
        ],
        "nav": [
          {
            "date": "2026-03-03",
            "value": 10000.0
          },
          {
            "date": "2026-03-04",
            "value": 10000.0
          },
          {
            "date": "2026-03-05",
            "value": 9970.24
          },
          {
            "date": "2026-03-06",
            "value": 9891.61
          },
          {
            "date": "2026-03-09",
            "value": 9902.02
          },
          {
            "date": "2026-03-10",
            "value": 9908.73
          },
          {
            "date": "2026-03-11",
            "value": 9918.66
          },
          {
            "date": "2026-03-12",
            "value": 9809.79
          },
          {
            "date": "2026-03-13",
            "value": 9683.13
          },
          {
            "date": "2026-03-16",
            "value": 9831.57
          },
          {
            "date": "2026-03-17",
            "value": 9862.23
          },
          {
            "date": "2026-03-18",
            "value": 9750.91
          },
          {
            "date": "2026-03-19",
            "value": 9700.14
          },
          {
            "date": "2026-03-20",
            "value": 9577.23
          },
          {
            "date": "2026-03-23",
            "value": 9605.55
          },
          {
            "date": "2026-03-24",
            "value": 9666.26
          },
          {
            "date": "2026-03-25",
            "value": 9647.97
          },
          {
            "date": "2026-03-26",
            "value": 9588.41
          },
          {
            "date": "2026-03-27",
            "value": 9498.32
          },
          {
            "date": "2026-03-30",
            "value": 9516.84
          },
          {
            "date": "2026-03-31",
            "value": 9606.68
          },
          {
            "date": "2026-04-01",
            "value": 9596.24
          },
          {
            "date": "2026-04-02",
            "value": 9610.63
          },
          {
            "date": "2026-04-06",
            "value": 9640.13
          },
          {
            "date": "2026-04-07",
            "value": 9597.11
          },
          {
            "date": "2026-04-08",
            "value": 9751.97
          },
          {
            "date": "2026-04-09",
            "value": 9717.87
          },
          {
            "date": "2026-04-10",
            "value": 9584.4
          },
          {
            "date": "2026-04-13",
            "value": 9679.09
          },
          {
            "date": "2026-04-14",
            "value": 9692.89
          },
          {
            "date": "2026-04-15",
            "value": 9715.71
          },
          {
            "date": "2026-04-16",
            "value": 9758.59
          },
          {
            "date": "2026-04-17",
            "value": 9781.01
          },
          {
            "date": "2026-04-20",
            "value": 9787.87
          },
          {
            "date": "2026-04-21",
            "value": 9702.98
          },
          {
            "date": "2026-04-22",
            "value": 9696.49
          },
          {
            "date": "2026-04-23",
            "value": 9674.9
          },
          {
            "date": "2026-04-24",
            "value": 9678.58
          },
          {
            "date": "2026-04-27",
            "value": 9732.29
          },
          {
            "date": "2026-04-28",
            "value": 9708.7
          },
          {
            "date": "2026-04-29",
            "value": 9696.6
          },
          {
            "date": "2026-04-30",
            "value": 9944.74
          },
          {
            "date": "2026-05-01",
            "value": 9977.44
          },
          {
            "date": "2026-05-04",
            "value": 9977.44
          },
          {
            "date": "2026-05-05",
            "value": 10102.64
          },
          {
            "date": "2026-05-06",
            "value": 10199.77
          },
          {
            "date": "2026-05-07",
            "value": 10265.62
          },
          {
            "date": "2026-05-08",
            "value": 10396.94
          },
          {
            "date": "2026-05-11",
            "value": 10526.0
          },
          {
            "date": "2026-05-12",
            "value": 10444.21
          },
          {
            "date": "2026-05-13",
            "value": 10539.14
          },
          {
            "date": "2026-05-14",
            "value": 10531.27
          },
          {
            "date": "2026-05-15",
            "value": 10444.61
          },
          {
            "date": "2026-05-18",
            "value": 10374.4
          },
          {
            "date": "2026-05-19",
            "value": 10309.54
          },
          {
            "date": "2026-05-20",
            "value": 10359.31
          },
          {
            "date": "2026-05-21",
            "value": 10443.89
          },
          {
            "date": "2026-05-22",
            "value": 10592.41
          },
          {
            "date": "2026-05-26",
            "value": 10665.09
          },
          {
            "date": "2026-05-27",
            "value": 10685.9
          },
          {
            "date": "2026-05-28",
            "value": 10716.78
          },
          {
            "date": "2026-05-29",
            "value": 10730.85
          },
          {
            "date": "2026-06-01",
            "value": 10804.37
          },
          {
            "date": "2026-06-02",
            "value": 10712.51
          },
          {
            "date": "2026-06-03",
            "value": 10729.32
          },
          {
            "date": "2026-06-04",
            "value": 10753.75
          },
          {
            "date": "2026-06-05",
            "value": 10578.03
          },
          {
            "date": "2026-06-08",
            "value": 10596.62
          },
          {
            "date": "2026-06-09",
            "value": 10549.0
          },
          {
            "date": "2026-06-10",
            "value": 10468.29
          },
          {
            "date": "2026-06-11",
            "value": 10658.5
          },
          {
            "date": "2026-06-12",
            "value": 10886.32
          },
          {
            "date": "2026-06-15",
            "value": 11061.0
          },
          {
            "date": "2026-06-16",
            "value": 10956.49
          },
          {
            "date": "2026-06-17",
            "value": 10951.64
          },
          {
            "date": "2026-06-18",
            "value": 10963.6
          },
          {
            "date": "2026-06-22",
            "value": 11110.04
          },
          {
            "date": "2026-06-23",
            "value": 10976.08
          }
        ],
        "nav_indexed": [
          {
            "date": "2026-03-03",
            "value": 100.0
          },
          {
            "date": "2026-03-04",
            "value": 100.0
          },
          {
            "date": "2026-03-05",
            "value": 99.7024
          },
          {
            "date": "2026-03-06",
            "value": 98.9161
          },
          {
            "date": "2026-03-09",
            "value": 99.0202
          },
          {
            "date": "2026-03-10",
            "value": 99.0873
          },
          {
            "date": "2026-03-11",
            "value": 99.1866
          },
          {
            "date": "2026-03-12",
            "value": 98.09790000000001
          },
          {
            "date": "2026-03-13",
            "value": 96.83129999999998
          },
          {
            "date": "2026-03-16",
            "value": 98.31569999999999
          },
          {
            "date": "2026-03-17",
            "value": 98.6223
          },
          {
            "date": "2026-03-18",
            "value": 97.50909999999999
          },
          {
            "date": "2026-03-19",
            "value": 97.00139999999999
          },
          {
            "date": "2026-03-20",
            "value": 95.7723
          },
          {
            "date": "2026-03-23",
            "value": 96.0555
          },
          {
            "date": "2026-03-24",
            "value": 96.6626
          },
          {
            "date": "2026-03-25",
            "value": 96.4797
          },
          {
            "date": "2026-03-26",
            "value": 95.88409999999999
          },
          {
            "date": "2026-03-27",
            "value": 94.9832
          },
          {
            "date": "2026-03-30",
            "value": 95.16839999999999
          },
          {
            "date": "2026-03-31",
            "value": 96.0668
          },
          {
            "date": "2026-04-01",
            "value": 95.9624
          },
          {
            "date": "2026-04-02",
            "value": 96.10629999999999
          },
          {
            "date": "2026-04-06",
            "value": 96.40129999999999
          },
          {
            "date": "2026-04-07",
            "value": 95.9711
          },
          {
            "date": "2026-04-08",
            "value": 97.5197
          },
          {
            "date": "2026-04-09",
            "value": 97.1787
          },
          {
            "date": "2026-04-10",
            "value": 95.844
          },
          {
            "date": "2026-04-13",
            "value": 96.79090000000001
          },
          {
            "date": "2026-04-14",
            "value": 96.9289
          },
          {
            "date": "2026-04-15",
            "value": 97.1571
          },
          {
            "date": "2026-04-16",
            "value": 97.58590000000001
          },
          {
            "date": "2026-04-17",
            "value": 97.8101
          },
          {
            "date": "2026-04-20",
            "value": 97.87870000000001
          },
          {
            "date": "2026-04-21",
            "value": 97.0298
          },
          {
            "date": "2026-04-22",
            "value": 96.9649
          },
          {
            "date": "2026-04-23",
            "value": 96.749
          },
          {
            "date": "2026-04-24",
            "value": 96.7858
          },
          {
            "date": "2026-04-27",
            "value": 97.32290000000002
          },
          {
            "date": "2026-04-28",
            "value": 97.08700000000002
          },
          {
            "date": "2026-04-29",
            "value": 96.96600000000001
          },
          {
            "date": "2026-04-30",
            "value": 99.4474
          },
          {
            "date": "2026-05-01",
            "value": 99.77440000000001
          },
          {
            "date": "2026-05-04",
            "value": 99.77440000000001
          },
          {
            "date": "2026-05-05",
            "value": 101.02640000000001
          },
          {
            "date": "2026-05-06",
            "value": 101.99770000000001
          },
          {
            "date": "2026-05-07",
            "value": 102.6562
          },
          {
            "date": "2026-05-08",
            "value": 103.96940000000001
          },
          {
            "date": "2026-05-11",
            "value": 105.25999999999999
          },
          {
            "date": "2026-05-12",
            "value": 104.44209999999998
          },
          {
            "date": "2026-05-13",
            "value": 105.3914
          },
          {
            "date": "2026-05-14",
            "value": 105.31270000000002
          },
          {
            "date": "2026-05-15",
            "value": 104.4461
          },
          {
            "date": "2026-05-18",
            "value": 103.74399999999999
          },
          {
            "date": "2026-05-19",
            "value": 103.09540000000001
          },
          {
            "date": "2026-05-20",
            "value": 103.59309999999999
          },
          {
            "date": "2026-05-21",
            "value": 104.4389
          },
          {
            "date": "2026-05-22",
            "value": 105.92409999999998
          },
          {
            "date": "2026-05-26",
            "value": 106.6509
          },
          {
            "date": "2026-05-27",
            "value": 106.859
          },
          {
            "date": "2026-05-28",
            "value": 107.16780000000001
          },
          {
            "date": "2026-05-29",
            "value": 107.30850000000001
          },
          {
            "date": "2026-06-01",
            "value": 108.04370000000002
          },
          {
            "date": "2026-06-02",
            "value": 107.12509999999999
          },
          {
            "date": "2026-06-03",
            "value": 107.2932
          },
          {
            "date": "2026-06-04",
            "value": 107.5375
          },
          {
            "date": "2026-06-05",
            "value": 105.78030000000001
          },
          {
            "date": "2026-06-08",
            "value": 105.96620000000001
          },
          {
            "date": "2026-06-09",
            "value": 105.49
          },
          {
            "date": "2026-06-10",
            "value": 104.6829
          },
          {
            "date": "2026-06-11",
            "value": 106.585
          },
          {
            "date": "2026-06-12",
            "value": 108.8632
          },
          {
            "date": "2026-06-15",
            "value": 110.61000000000001
          },
          {
            "date": "2026-06-16",
            "value": 109.5649
          },
          {
            "date": "2026-06-17",
            "value": 109.5164
          },
          {
            "date": "2026-06-18",
            "value": 109.636
          },
          {
            "date": "2026-06-22",
            "value": 111.10040000000001
          },
          {
            "date": "2026-06-23",
            "value": 109.76079999999999
          }
        ],
        "spy_close": [
          {
            "date": "2026-03-03",
            "value": 680.3300170898438
          },
          {
            "date": "2026-03-04",
            "value": 685.1300048828125
          },
          {
            "date": "2026-03-05",
            "value": 681.3099975585938
          },
          {
            "date": "2026-03-06",
            "value": 672.3800048828125
          },
          {
            "date": "2026-03-09",
            "value": 678.27001953125
          },
          {
            "date": "2026-03-10",
            "value": 677.1799926757812
          },
          {
            "date": "2026-03-11",
            "value": 676.3300170898438
          },
          {
            "date": "2026-03-12",
            "value": 666.0599975585938
          },
          {
            "date": "2026-03-13",
            "value": 662.2899780273438
          },
          {
            "date": "2026-03-16",
            "value": 669.030029296875
          },
          {
            "date": "2026-03-17",
            "value": 670.7899780273438
          },
          {
            "date": "2026-03-18",
            "value": 661.4299926757812
          },
          {
            "date": "2026-03-19",
            "value": 659.7999877929688
          },
          {
            "date": "2026-03-20",
            "value": 648.5700073242188
          },
          {
            "date": "2026-03-23",
            "value": 655.3800048828125
          },
          {
            "date": "2026-03-24",
            "value": 653.1799926757812
          },
          {
            "date": "2026-03-25",
            "value": 656.8200073242188
          },
          {
            "date": "2026-03-26",
            "value": 645.0900268554688
          },
          {
            "date": "2026-03-27",
            "value": 634.0900268554688
          },
          {
            "date": "2026-03-30",
            "value": 631.969970703125
          },
          {
            "date": "2026-03-31",
            "value": 650.3400268554688
          },
          {
            "date": "2026-04-01",
            "value": 655.239990234375
          },
          {
            "date": "2026-04-02",
            "value": 655.8300170898438
          },
          {
            "date": "2026-04-06",
            "value": 658.9299926757812
          },
          {
            "date": "2026-04-07",
            "value": 659.219970703125
          },
          {
            "date": "2026-04-08",
            "value": 676.010009765625
          },
          {
            "date": "2026-04-09",
            "value": 679.9099731445312
          },
          {
            "date": "2026-04-10",
            "value": 679.4600219726562
          },
          {
            "date": "2026-04-13",
            "value": 686.0999755859375
          },
          {
            "date": "2026-04-14",
            "value": 694.4600219726562
          },
          {
            "date": "2026-04-15",
            "value": 699.9400024414062
          },
          {
            "date": "2026-04-16",
            "value": 701.6599731445312
          },
          {
            "date": "2026-04-17",
            "value": 710.1400146484375
          },
          {
            "date": "2026-04-20",
            "value": 708.719970703125
          },
          {
            "date": "2026-04-21",
            "value": 704.0800170898438
          },
          {
            "date": "2026-04-22",
            "value": 711.2100219726562
          },
          {
            "date": "2026-04-23",
            "value": 708.4500122070312
          },
          {
            "date": "2026-04-24",
            "value": 713.9400024414062
          },
          {
            "date": "2026-04-27",
            "value": 715.1699829101562
          },
          {
            "date": "2026-04-28",
            "value": 711.6900024414062
          },
          {
            "date": "2026-04-29",
            "value": 711.5800170898438
          },
          {
            "date": "2026-04-30",
            "value": 718.6599731445312
          },
          {
            "date": "2026-05-01",
            "value": 720.6500244140625
          },
          {
            "date": "2026-05-04",
            "value": 718.010009765625
          },
          {
            "date": "2026-05-05",
            "value": 723.77001953125
          },
          {
            "date": "2026-05-06",
            "value": 733.8300170898438
          },
          {
            "date": "2026-05-07",
            "value": 731.5800170898438
          },
          {
            "date": "2026-05-08",
            "value": 737.6199951171875
          },
          {
            "date": "2026-05-11",
            "value": 739.2999877929688
          },
          {
            "date": "2026-05-12",
            "value": 738.1799926757812
          },
          {
            "date": "2026-05-13",
            "value": 742.3099975585938
          },
          {
            "date": "2026-05-14",
            "value": 748.1699829101562
          },
          {
            "date": "2026-05-15",
            "value": 739.1699829101562
          },
          {
            "date": "2026-05-18",
            "value": 738.6500244140625
          },
          {
            "date": "2026-05-19",
            "value": 733.72998046875
          },
          {
            "date": "2026-05-20",
            "value": 741.25
          },
          {
            "date": "2026-05-21",
            "value": 742.719970703125
          },
          {
            "date": "2026-05-22",
            "value": 745.6400146484375
          },
          {
            "date": "2026-05-26",
            "value": 750.5900268554688
          },
          {
            "date": "2026-05-27",
            "value": 750.4600219726562
          },
          {
            "date": "2026-05-28",
            "value": 754.5999755859375
          },
          {
            "date": "2026-05-29",
            "value": 756.47998046875
          },
          {
            "date": "2026-06-01",
            "value": 758.5399780273438
          },
          {
            "date": "2026-06-02",
            "value": 759.5700073242188
          },
          {
            "date": "2026-06-03",
            "value": 754.239990234375
          },
          {
            "date": "2026-06-04",
            "value": 757.0900268554688
          },
          {
            "date": "2026-06-05",
            "value": 737.5499877929688
          },
          {
            "date": "2026-06-08",
            "value": 739.219970703125
          },
          {
            "date": "2026-06-09",
            "value": 737.0499877929688
          },
          {
            "date": "2026-06-10",
            "value": 725.4299926757812
          },
          {
            "date": "2026-06-11",
            "value": 737.760009765625
          },
          {
            "date": "2026-06-12",
            "value": 741.75
          },
          {
            "date": "2026-06-15",
            "value": 754.8300170898438
          },
          {
            "date": "2026-06-16",
            "value": 750.3300170898438
          },
          {
            "date": "2026-06-17",
            "value": 740.9600219726562
          },
          {
            "date": "2026-06-18",
            "value": 746.739990234375
          },
          {
            "date": "2026-06-22",
            "value": 744.3900146484375
          },
          {
            "date": "2026-06-23",
            "value": 733.72998046875
          }
        ],
        "spy_indexed": [
          {
            "date": "2026-03-03",
            "value": 100.0
          },
          {
            "date": "2026-03-04",
            "value": 100.70553814654555
          },
          {
            "date": "2026-03-05",
            "value": 100.14404486706935
          },
          {
            "date": "2026-03-06",
            "value": 98.8314476787254
          },
          {
            "date": "2026-03-09",
            "value": 99.6972061342515
          },
          {
            "date": "2026-03-10",
            "value": 99.53698582527095
          },
          {
            "date": "2026-03-11",
            "value": 99.41205004931133
          },
          {
            "date": "2026-03-12",
            "value": 97.90248568006878
          },
          {
            "date": "2026-03-13",
            "value": 97.34833998069533
          },
          {
            "date": "2026-03-16",
            "value": 98.33904318358535
          },
          {
            "date": "2026-03-17",
            "value": 98.59773362590877
          },
          {
            "date": "2026-03-18",
            "value": 97.22193289443429
          },
          {
            "date": "2026-03-19",
            "value": 96.98234257181632
          },
          {
            "date": "2026-03-20",
            "value": 95.33167595610722
          },
          {
            "date": "2026-03-23",
            "value": 96.33266038829854
          },
          {
            "date": "2026-03-24",
            "value": 96.0092861211389
          },
          {
            "date": "2026-03-25",
            "value": 96.54432272940262
          },
          {
            "date": "2026-03-26",
            "value": 94.82016236985744
          },
          {
            "date": "2026-03-27",
            "value": 93.20330000546359
          },
          {
            "date": "2026-03-30",
            "value": 92.89167827790665
          },
          {
            "date": "2026-03-31",
            "value": 95.59184668013633
          },
          {
            "date": "2026-04-01",
            "value": 96.3120799868874
          },
          {
            "date": "2026-04-02",
            "value": 96.39880655203186
          },
          {
            "date": "2026-04-06",
            "value": 96.85446417525387
          },
          {
            "date": "2026-04-07",
            "value": 96.89708731697326
          },
          {
            "date": "2026-04-08",
            "value": 99.36501297668772
          },
          {
            "date": "2026-04-09",
            "value": 99.93825879576661
          },
          {
            "date": "2026-04-10",
            "value": 99.87212160343756
          },
          {
            "date": "2026-04-13",
            "value": 100.84811170331352
          },
          {
            "date": "2026-04-14",
            "value": 102.07693391852008
          },
          {
            "date": "2026-04-15",
            "value": 102.88242248011422
          },
          {
            "date": "2026-04-16",
            "value": 103.13523665263628
          },
          {
            "date": "2026-04-17",
            "value": 104.38169664865117
          },
          {
            "date": "2026-04-20",
            "value": 104.17296795674562
          },
          {
            "date": "2026-04-21",
            "value": 103.490952832214
          },
          {
            "date": "2026-04-22",
            "value": 104.53897433702892
          },
          {
            "date": "2026-04-23",
            "value": 104.13328743562906
          },
          {
            "date": "2026-04-24",
            "value": 104.94024730752459
          },
          {
            "date": "2026-04-27",
            "value": 105.12103904651198
          },
          {
            "date": "2026-04-28",
            "value": 104.60952546026219
          },
          {
            "date": "2026-04-29",
            "value": 104.59335898975527
          },
          {
            "date": "2026-04-30",
            "value": 105.63402394306316
          },
          {
            "date": "2026-05-01",
            "value": 105.92653657951037
          },
          {
            "date": "2026-05-04",
            "value": 105.53848745891882
          },
          {
            "date": "2026-05-05",
            "value": 106.38513682333519
          },
          {
            "date": "2026-05-06",
            "value": 107.86383059046105
          },
          {
            "date": "2026-05-07",
            "value": 107.53310874319865
          },
          {
            "date": "2026-05-08",
            "value": 108.42090993903304
          },
          {
            "date": "2026-05-11",
            "value": 108.66784784175377
          },
          {
            "date": "2026-05-12",
            "value": 108.50322257327328
          },
          {
            "date": "2026-05-13",
            "value": 109.11028161507168
          },
          {
            "date": "2026-05-14",
            "value": 109.97162613969356
          },
          {
            "date": "2026-05-15",
            "value": 108.64873875064403
          },
          {
            "date": "2026-05-18",
            "value": 108.57231135760941
          },
          {
            "date": "2026-05-19",
            "value": 107.84912645885127
          },
          {
            "date": "2026-05-20",
            "value": 108.95447523699535
          },
          {
            "date": "2026-05-21",
            "value": 109.17054253759937
          },
          {
            "date": "2026-05-22",
            "value": 109.5997524610132
          },
          {
            "date": "2026-05-26",
            "value": 110.32734231927128
          },
          {
            "date": "2026-05-27",
            "value": 110.30823322816157
          },
          {
            "date": "2026-05-28",
            "value": 110.91675460885708
          },
          {
            "date": "2026-05-29",
            "value": 111.19309180339312
          },
          {
            "date": "2026-06-01",
            "value": 111.49588566914161
          },
          {
            "date": "2026-06-02",
            "value": 111.64728708771801
          },
          {
            "date": "2026-06-03",
            "value": 110.86384126643213
          },
          {
            "date": "2026-06-04",
            "value": 111.28276098914039
          },
          {
            "date": "2026-06-05",
            "value": 108.41061973832747
          },
          {
            "date": "2026-06-08",
            "value": 108.65608633074679
          },
          {
            "date": "2026-06-09",
            "value": 108.33712599449137
          },
          {
            "date": "2026-06-10",
            "value": 106.62913210545311
          },
          {
            "date": "2026-06-11",
            "value": 108.44149034044418
          },
          {
            "date": "2026-06-12",
            "value": 109.02796898083142
          },
          {
            "date": "2026-06-15",
            "value": 110.95056783157659
          },
          {
            "date": "2026-06-16",
            "value": 110.28912413705183
          },
          {
            "date": "2026-06-17",
            "value": 108.91185209527596
          },
          {
            "date": "2026-06-18",
            "value": 109.76143510889086
          },
          {
            "date": "2026-06-22",
            "value": 109.41601810142299
          },
          {
            "date": "2026-06-23",
            "value": 107.84912645885127
          }
        ]
      },
      "source_type": "alpaca_portfolio_history",
      "summary": {
        "excess_since_inception_return": 0.019116735411487173,
        "inception_date": "2026-03-03",
        "latest_nav": 10976.08,
        "max_drawdown": -0.05016799999999999,
        "since_inception_return": 0.09760799999999992,
        "spy_since_inception_return": 0.07849126458851274
      },
      "trust_level": "canonical"
    },
    "positions": {
      "as_of": "2026-06-23T20:05:54.766588+00:00",
      "is_stale": false,
      "rows": [
        {
          "avg_entry_price": 706.3942863793549,
          "cost_basis": 875.151904,
          "last_price": 1044.5,
          "market_value": 1294.031083,
          "qty": 1.238900032,
          "side": "positionside.long",
          "ticker": "STX",
          "unrealized_pnl": 418.879179,
          "unrealized_pnl_pct": 0.47864,
          "weight": 0.11789555861473314
        },
        {
          "avg_entry_price": 91.7543149833832,
          "cost_basis": 996.639723,
          "last_price": 93.68,
          "market_value": 1017.556605,
          "qty": 10.862047449,
          "side": "positionside.long",
          "ticker": "MNST",
          "unrealized_pnl": 20.916882,
          "unrealized_pnl_pct": 0.02099,
          "weight": 0.09270674093118855
        },
        {
          "avg_entry_price": 130.035,
          "cost_basis": 780.21,
          "last_price": 148.18,
          "market_value": 889.08,
          "qty": 6.0,
          "side": "positionside.long",
          "ticker": "FTNT",
          "unrealized_pnl": 108.87,
          "unrealized_pnl_pct": 0.13954,
          "weight": 0.08100159619827844
        },
        {
          "avg_entry_price": 80.3917039690212,
          "cost_basis": 837.685792,
          "last_price": 78.95,
          "market_value": 822.663161,
          "qty": 10.4200527,
          "side": "positionside.long",
          "ticker": "GM",
          "unrealized_pnl": -15.022631,
          "unrealized_pnl_pct": -0.01793,
          "weight": 0.07495054345449377
        },
        {
          "avg_entry_price": 1100.9859997524675,
          "cost_basis": 759.451512,
          "last_price": 1115.93,
          "market_value": 769.759766,
          "qty": 0.689792161,
          "side": "positionside.long",
          "ticker": "EQIX",
          "unrealized_pnl": 10.308254,
          "unrealized_pnl_pct": 0.01357,
          "weight": 0.07013066285960015
        },
        {
          "avg_entry_price": 240.44933694447803,
          "cost_basis": 661.122575,
          "last_price": 245.6,
          "market_value": 675.284476,
          "qty": 2.749529624,
          "side": "positionside.long",
          "ticker": "KLAC",
          "unrealized_pnl": 14.161901,
          "unrealized_pnl_pct": 0.02142,
          "weight": 0.06152328299356419
        },
        {
          "avg_entry_price": 99.04295692856739,
          "cost_basis": 616.319532,
          "last_price": 101.57,
          "market_value": 632.044689,
          "qty": 6.222749715,
          "side": "positionside.long",
          "ticker": "CVS",
          "unrealized_pnl": 15.725157,
          "unrealized_pnl_pct": 0.02551,
          "weight": 0.05758382673960102
        },
        {
          "avg_entry_price": 397.29926698375897,
          "cost_basis": 643.695647,
          "last_price": 386.83,
          "market_value": 626.733568,
          "qty": 1.62017829,
          "side": "positionside.long",
          "ticker": "MAR",
          "unrealized_pnl": -16.962079,
          "unrealized_pnl_pct": -0.02635,
          "weight": 0.057099945335675396
        },
        {
          "avg_entry_price": 146.87094507731067,
          "cost_basis": 617.114218,
          "last_price": 144.97,
          "market_value": 609.126932,
          "qty": 4.20174472,
          "side": "positionside.long",
          "ticker": "C",
          "unrealized_pnl": -7.987286,
          "unrealized_pnl_pct": -0.01294,
          "weight": 0.0554958538931932
        },
        {
          "avg_entry_price": 221.2216479662892,
          "cost_basis": 589.156996,
          "last_price": 226.03,
          "market_value": 601.962588,
          "qty": 2.663197754,
          "side": "positionside.long",
          "ticker": "MS",
          "unrealized_pnl": 12.805592,
          "unrealized_pnl_pct": 0.02174,
          "weight": 0.054843130516541425
        },
        {
          "avg_entry_price": 204.8543528691729,
          "cost_basis": 456.592469,
          "last_price": 203.01,
          "market_value": 452.481657,
          "qty": 2.228863886,
          "side": "positionside.long",
          "ticker": "QCOM",
          "unrealized_pnl": -4.110812,
          "unrealized_pnl_pct": -0.009,
          "weight": 0.04122434029270924
        },
        {
          "avg_entry_price": 364.12,
          "cost_basis": 364.12,
          "last_price": 356.3,
          "market_value": 356.3,
          "qty": 1.0,
          "side": "positionside.long",
          "ticker": "GE",
          "unrealized_pnl": -7.82,
          "unrealized_pnl_pct": -0.02148,
          "weight": 0.032461498094037214
        },
        {
          "avg_entry_price": 398.66614170564924,
          "cost_basis": 239.46755,
          "last_price": 395.1,
          "market_value": 237.325469,
          "qty": 0.600671903,
          "side": "positionside.long",
          "ticker": "ELV",
          "unrealized_pnl": -2.142081,
          "unrealized_pnl_pct": -0.00895,
          "weight": 0.021622060790373248
        },
        {
          "avg_entry_price": 229.4870591329288,
          "cost_basis": 223.743997,
          "last_price": 239.08,
          "market_value": 233.096868,
          "qty": 0.974974353,
          "side": "positionside.long",
          "ticker": "JNJ",
          "unrealized_pnl": 9.352871,
          "unrealized_pnl_pct": 0.0418,
          "weight": 0.021236804760898245
        },
        {
          "avg_entry_price": 406.20999975214903,
          "cost_basis": 198.933133,
          "last_price": 407.55,
          "market_value": 199.589371,
          "qty": 0.489729778,
          "side": "positionside.long",
          "ticker": "UNH",
          "unrealized_pnl": 0.656238,
          "unrealized_pnl_pct": 0.0033,
          "weight": 0.018184030273102964
        },
        {
          "avg_entry_price": 223.8400002877967,
          "cost_basis": 191.269509,
          "last_price": 231.55,
          "market_value": 197.857643,
          "qty": 0.854492087,
          "side": "positionside.long",
          "ticker": "ALL",
          "unrealized_pnl": 6.588134,
          "unrealized_pnl_pct": 0.03444,
          "weight": 0.0180262573705731
        },
        {
          "avg_entry_price": 214.00000044528946,
          "cost_basis": 191.273338,
          "last_price": 216.74,
          "market_value": 193.722351,
          "qty": 0.893800643,
          "side": "positionside.long",
          "ticker": "SPG",
          "unrealized_pnl": 2.449013,
          "unrealized_pnl_pct": 0.0128,
          "weight": 0.017649502463538896
        },
        {
          "avg_entry_price": 141.2999998580857,
          "cost_basis": 134.216489,
          "last_price": 145.25,
          "market_value": 137.968472,
          "qty": 0.949868996,
          "side": "positionside.long",
          "ticker": "PLD",
          "unrealized_pnl": 3.751983,
          "unrealized_pnl_pct": 0.02795,
          "weight": 0.012569922230887529
        },
        {
          "avg_entry_price": 69.50000027591653,
          "cost_basis": 121.787738,
          "last_price": 71.61,
          "market_value": 125.485178,
          "qty": 1.752341547,
          "side": "positionside.long",
          "ticker": "MO",
          "unrealized_pnl": 3.69744,
          "unrealized_pnl_pct": 0.03036,
          "weight": 0.011432604171981255
        },
        {
          "avg_entry_price": 85.89999996633473,
          "cost_basis": 107.166811,
          "last_price": 86.43,
          "market_value": 107.828027,
          "qty": 1.24757638,
          "side": "positionside.long",
          "ticker": "NEE",
          "unrealized_pnl": 0.661216,
          "unrealized_pnl_pct": 0.00617,
          "weight": 0.009823910448903433
        },
        {
          "avg_entry_price": 142.60999956566707,
          "cost_basis": 93.88956,
          "last_price": 147.29,
          "market_value": 96.970713,
          "qty": 0.658365895,
          "side": "positionside.long",
          "ticker": "BNY",
          "unrealized_pnl": 3.081153,
          "unrealized_pnl_pct": 0.03282,
          "weight": 0.008834730887529974
        },
        {
          "avg_entry_price": 330.7600009160886,
          "cost_basis": 64.08036,
          "last_price": 332.11,
          "market_value": 64.341904,
          "qty": 0.193736727,
          "side": "positionside.long",
          "ticker": "CB",
          "unrealized_pnl": 0.261544,
          "unrealized_pnl_pct": 0.00408,
          "weight": 0.005862011209830832
        },
        {
          "avg_entry_price": 72.42000028259989,
          "cost_basis": 34.698061,
          "last_price": 75.79,
          "market_value": 36.312704,
          "qty": 0.47912263,
          "side": "positionside.long",
          "ticker": "WMB",
          "unrealized_pnl": 1.614643,
          "unrealized_pnl_pct": 0.04653,
          "weight": 0.0033083490645111915
        },
        {
          "avg_entry_price": 47.61999984670411,
          "cost_basis": 31.088959,
          "last_price": 46.68,
          "market_value": 30.475275,
          "qty": 0.652855084,
          "side": "positionside.long",
          "ticker": "VZ",
          "unrealized_pnl": -0.613684,
          "unrealized_pnl_pct": -0.01974,
          "weight": 0.0027765172083293855
        },
        {
          "avg_entry_price": 171.89015106204758,
          "cost_basis": 15.711885,
          "last_price": 170.34,
          "market_value": 15.570191,
          "qty": 0.091406546,
          "side": "positionside.long",
          "ticker": "PSX",
          "unrealized_pnl": -0.141694,
          "unrealized_pnl_pct": -0.00902,
          "weight": 0.001418556624951713
        }
      ],
      "source_type": "broker_positions",
      "summary": {
        "cash": 552.51,
        "gross_market_value": 10423.57,
        "largest_position_weight": 0.11789555861473314,
        "net_market_value": 10423.57,
        "positions_count": 25,
        "top5_concentration": 0.43668510205829403
      },
      "trust_level": "canonical"
    },
    "regime_market_state": {
      "as_of": null,
      "checks": [
        {
          "blocking": true,
          "current": "risk_on_trending",
          "name": "regime_data_available",
          "note": "The allocator needs a real regime state before its sleeve budgets mean anything.",
          "status": "pass",
          "threshold": "known composite regime"
        },
        {
          "blocking": true,
          "current": 4,
          "name": "multi_sleeve_participation",
          "note": "Stage 1B requires more than a nominal single-sleeve book.",
          "status": "pass",
          "threshold": 2
        },
        {
          "blocking": true,
          "current": "/home/brettolson/quant-daily-report/signals/2026-06-23.json",
          "name": "signal_snapshot_present",
          "note": "If the signal snapshot is missing, the allocator decision is not auditable.",
          "status": "pass",
          "threshold": "existing signal snapshot artifact"
        },
        {
          "blocking": true,
          "current": {
            "max_abs_gap": 0.087763,
            "total_abs_gap": 0.233428
          },
          "name": "shadow_vs_live_alignment",
          "note": "Compares the model target book to the live broker book at the decision point.",
          "status": "pass",
          "threshold": {
            "max_single_sleeve_gap": 0.15,
            "max_total_allocation_gap": 0.3
          }
        },
        {
          "blocking": false,
          "current": 0.0751353562299076,
          "name": "overlap_complexity",
          "note": "Heavy overlap across sleeves makes attribution harder even when allocation math is correct.",
          "status": "pass",
          "threshold": 0.35
        },
        {
          "blocking": false,
          "current": 0,
          "name": "benchmark_evidence_window",
          "note": "A short benchmark window is directionally useful but not promotion-grade evidence.",
          "status": "fail",
          "threshold": 20
        },
        {
          "blocking": false,
          "current": null,
          "name": "benchmark_relative_alpha",
          "note": "The allocator should not be promoted further while it is persistently losing relative ground.",
          "status": "warn",
          "threshold": ">= 0.0 over validated window"
        },
        {
          "blocking": false,
          "current": 0.1381754304807613,
          "name": "cash_discipline",
          "note": "Idle cash should be a deliberate risk-off decision, not a quiet participation failure.",
          "status": "fail",
          "threshold": 0.05
        }
      ],
      "confidence_state": "AVAILABLE",
      "current_regime": "LOW",
      "is_stale": false,
      "max_positions": 10.0,
      "portfolio_scale": 1.0,
      "promotion_gate_blockers": [],
      "vix": 19.959999084472656
    },
    "shadow_command_center": {
      "as_of": "2026-06-23",
      "is_stale": true,
      "rolling_excess_series": [
        {
          "caerus_lyra": -0.06566785055305435,
          "caerus_orion": -0.0551559858920313,
          "caerus_polaris": -0.06028756951445369,
          "date": "2026-05-19"
        },
        {
          "caerus_lyra": -0.06151364103515555,
          "caerus_orion": -0.04729170482064693,
          "caerus_polaris": -0.04489056537435898,
          "date": "2026-05-20"
        },
        {
          "caerus_lyra": -0.003648747932148866,
          "caerus_orion": 0.007333563224359074,
          "caerus_polaris": -0.009861759434722006,
          "date": "2026-05-21"
        },
        {
          "caerus_lyra": 0.027057491187397087,
          "caerus_orion": 0.019551125182330154,
          "caerus_polaris": 0.02077496127578482,
          "date": "2026-05-22"
        },
        {
          "caerus_lyra": 0.1526569545400669,
          "caerus_orion": 0.13381649790459016,
          "caerus_polaris": 0.11618522327100367,
          "date": "2026-05-26"
        },
        {
          "caerus_lyra": 0.15023193750316688,
          "caerus_orion": 0.14529896279087406,
          "caerus_polaris": 0.10805768810845984,
          "date": "2026-05-27"
        },
        {
          "caerus_lyra": 0.10459380835948417,
          "caerus_orion": 0.11388196440256815,
          "caerus_polaris": 0.06829960752777176,
          "date": "2026-05-28"
        },
        {
          "caerus_lyra": 0.05100768098901032,
          "caerus_orion": 0.08033300584632097,
          "caerus_polaris": 0.035907583156503886,
          "date": "2026-05-29"
        },
        {
          "caerus_lyra": 0.06594480788963497,
          "caerus_orion": 0.10364531686669598,
          "caerus_polaris": 0.03535269282104481,
          "date": "2026-06-01"
        },
        {
          "caerus_lyra": 0.02874657455857843,
          "caerus_orion": 0.04576019829173239,
          "caerus_polaris": 0.011813370881906149,
          "date": "2026-06-02"
        },
        {
          "caerus_lyra": 0.06295578315562222,
          "caerus_orion": 0.07836901091939485,
          "caerus_polaris": 0.05631474588416774,
          "date": "2026-06-03"
        },
        {
          "caerus_lyra": 0.04175091509407669,
          "caerus_orion": 0.04660554099259562,
          "caerus_polaris": 0.04171646352020919,
          "date": "2026-06-04"
        },
        {
          "caerus_lyra": -0.04133631561240858,
          "caerus_orion": -0.047593242471177244,
          "caerus_polaris": -0.023761791087204553,
          "date": "2026-06-05"
        },
        {
          "caerus_lyra": 0.0070772423146832075,
          "caerus_orion": -0.0010793707355574167,
          "caerus_polaris": 0.02561950881947228,
          "date": "2026-06-08"
        },
        {
          "caerus_lyra": -0.04758717200144391,
          "caerus_orion": -0.025540046891655233,
          "caerus_polaris": -0.018971571009021027,
          "date": "2026-06-09"
        },
        {
          "caerus_lyra": -0.10068561010271615,
          "caerus_orion": -0.08142711075624876,
          "caerus_polaris": -0.07039350673369038,
          "date": "2026-06-10"
        },
        {
          "caerus_lyra": -0.015601280932661776,
          "caerus_orion": 0.020991504780790926,
          "caerus_polaris": 0.008031686322421572,
          "date": "2026-06-11"
        },
        {
          "caerus_lyra": 0.11334071013930802,
          "caerus_orion": 0.15448045529413723,
          "caerus_polaris": 0.10935376572910971,
          "date": "2026-06-12"
        },
        {
          "caerus_lyra": 0.12046935511278156,
          "caerus_orion": 0.16197066426531026,
          "caerus_polaris": 0.10459559202376956,
          "date": "2026-06-15"
        },
        {
          "caerus_lyra": 0.1266228690504163,
          "caerus_orion": 0.1500195835163236,
          "caerus_polaris": 0.09799990164338013,
          "date": "2026-06-16"
        },
        {
          "caerus_lyra": 0.19482780518301857,
          "caerus_orion": 0.22129312968945536,
          "caerus_polaris": 0.1595417836747266,
          "date": "2026-06-17"
        },
        {
          "caerus_lyra": 0.1936235117098426,
          "caerus_orion": 0.18638270728150896,
          "caerus_polaris": 0.13363793663196377,
          "date": "2026-06-18"
        },
        {
          "caerus_lyra": 0.2019578795785293,
          "caerus_orion": 0.18996389041747697,
          "caerus_polaris": 0.14939999754427946,
          "date": "2026-06-22"
        }
      ],
      "status": "NO_DATA",
      "strategies": [
        {
          "alpha_per_dollar_deployed_proxy": null,
          "avg_cash_weight": null,
          "avg_effective_n": null,
          "avg_hhi": null,
          "avg_top_3_concentration": 0.3,
          "avg_turnover": 0.0744186047,
          "cumulative_return": 0.8922922055,
          "daily_return": 0.0,
          "data_reason": "PRICE_CACHE_STALE",
          "data_status": "NO_DATA",
          "excess_return_vs_spy": 0.6068625165,
          "failed_criteria": [
            "PRICE_CACHE_STALE"
          ],
          "max_drawdown": -0.1085908947,
          "name": "Caerus Polaris",
          "promotion_readiness": "CONTROL",
          "realized_volatility_ann": 0.5599950025,
          "role": "CONTROL",
          "rolling_20d_excess": 0.287204334569759,
          "rolling_5d_excess": 0.14939999754427946,
          "slug": "caerus_polaris",
          "status": "OK",
          "valid_evaluation_days": 44
        },
        {
          "alpha_per_dollar_deployed_proxy": null,
          "avg_cash_weight": null,
          "avg_effective_n": null,
          "avg_hhi": null,
          "avg_top_3_concentration": null,
          "avg_turnover": null,
          "cumulative_return": null,
          "daily_return": null,
          "data_reason": null,
          "data_status": null,
          "excess_return_vs_spy": null,
          "failed_criteria": [
            "INSUFFICIENT_VALID_DAYS"
          ],
          "max_drawdown": null,
          "name": "Polaris Alpha",
          "promotion_readiness": "WATCHLIST",
          "realized_volatility_ann": null,
          "role": "CHALLENGER",
          "rolling_20d_excess": null,
          "rolling_5d_excess": null,
          "slug": "caerus_polaris_alpha",
          "status": null,
          "valid_evaluation_days": null
        },
        {
          "alpha_per_dollar_deployed_proxy": null,
          "avg_cash_weight": null,
          "avg_effective_n": null,
          "avg_hhi": null,
          "avg_top_3_concentration": 0.6,
          "avg_turnover": 0.0093023256,
          "cumulative_return": 1.2878179278,
          "daily_return": 0.0,
          "data_reason": "PRICE_CACHE_STALE",
          "data_status": "NO_DATA",
          "excess_return_vs_spy": 1.0023882388,
          "failed_criteria": [
            "PRICE_CACHE_STALE"
          ],
          "max_drawdown": -0.1353372101,
          "name": "Caerus Orion",
          "promotion_readiness": "NOT_READY",
          "realized_volatility_ann": 0.6136615919,
          "role": "CHALLENGER",
          "rolling_20d_excess": 0.408844357236946,
          "rolling_5d_excess": 0.18996389041747697,
          "slug": "caerus_orion",
          "status": "OK",
          "valid_evaluation_days": 44
        },
        {
          "alpha_per_dollar_deployed_proxy": null,
          "avg_cash_weight": null,
          "avg_effective_n": null,
          "avg_hhi": null,
          "avg_top_3_concentration": null,
          "avg_turnover": null,
          "cumulative_return": null,
          "daily_return": null,
          "data_reason": null,
          "data_status": null,
          "excess_return_vs_spy": null,
          "failed_criteria": [
            "INSUFFICIENT_VALID_DAYS"
          ],
          "max_drawdown": null,
          "name": "Orion Alpha",
          "promotion_readiness": "WATCHLIST",
          "realized_volatility_ann": null,
          "role": "CHALLENGER",
          "rolling_20d_excess": null,
          "rolling_5d_excess": null,
          "slug": "caerus_orion_alpha",
          "status": null,
          "valid_evaluation_days": null
        },
        {
          "alpha_per_dollar_deployed_proxy": null,
          "avg_cash_weight": null,
          "avg_effective_n": null,
          "avg_hhi": null,
          "avg_top_3_concentration": 0.6,
          "avg_turnover": 0.0279069767,
          "cumulative_return": 1.3482407593,
          "daily_return": 0.0,
          "data_reason": "PRICE_CACHE_STALE",
          "data_status": "NO_DATA",
          "excess_return_vs_spy": 1.0628110703,
          "failed_criteria": [
            "PRICE_CACHE_STALE"
          ],
          "max_drawdown": -0.138882998,
          "name": "Caerus Lyra",
          "promotion_readiness": "NOT_READY",
          "realized_volatility_ann": 0.6485728748,
          "role": "CHALLENGER",
          "rolling_20d_excess": 0.34482466977517,
          "rolling_5d_excess": 0.2019578795785293,
          "slug": "caerus_lyra",
          "status": "OK",
          "valid_evaluation_days": 44
        }
      ],
      "summary": {
        "benchmark": "SPY",
        "candidate_count": 4,
        "control": "caerus_polaris",
        "latest_nav_date": "2026-06-22"
      }
    },
    "sleeve_inventory": {
      "as_of": "2026-06-23",
      "is_stale": true,
      "rows": [
        {
          "alpha_per_dollar_proxy": null,
          "artifact_status": "PRESENT",
          "baseline_strategy_id": null,
          "concentration": 0.3,
          "construction": {},
          "current_lifecycle_status": "paper",
          "data_status": "NO_DATA",
          "display_name": "Caerus Polaris",
          "drawdown": -0.1085908947,
          "effective_n": null,
          "eligible_for_promotion": false,
          "eligible_for_shadow": true,
          "execution_impact": "NON_EXECUTIONAL",
          "family": "core_momentum",
          "lifecycle_stage": "paper",
          "manifest_lifecycle_stage": "paper_observed",
          "promotion_readiness": "CONTROL",
          "review_checkpoints": [],
          "role": "baseline",
          "short_name": "polaris",
          "since_inception_return": 0.8922922055,
          "sleeve_id": "polaris",
          "source_variant": "baseline_top10_daily",
          "strategy_id": "caerus_polaris",
          "strategy_type": "security_selection",
          "today_return": 0.0,
          "turnover": 0.0744186047,
          "variant_class": "baseline"
        },
        {
          "alpha_per_dollar_proxy": null,
          "artifact_status": "PRESENT",
          "baseline_strategy_id": "caerus_polaris",
          "concentration": null,
          "construction": {
            "cash_residual_allowed": true,
            "max_position_weight": 0.2,
            "top_n": 4,
            "weighting": "equal"
          },
          "current_lifecycle_status": "shadow",
          "data_status": "OK",
          "display_name": "Polaris_Alpha",
          "drawdown": null,
          "effective_n": null,
          "eligible_for_promotion": true,
          "eligible_for_shadow": true,
          "execution_impact": "NON_EXECUTIONAL",
          "family": "core_momentum",
          "lifecycle_stage": "shadow",
          "manifest_lifecycle_stage": "shadow_observed",
          "promotion_readiness": "WATCHLIST",
          "review_checkpoints": [
            {
              "observed_days": 0,
              "status": "IN_PROGRESS",
              "trading_days": 20
            },
            {
              "observed_days": 0,
              "status": "IN_PROGRESS",
              "trading_days": 60
            }
          ],
          "role": "challenger",
          "short_name": "polaris_alpha",
          "since_inception_return": null,
          "sleeve_id": "polaris_alpha",
          "source_variant": "polaris_alpha_top4_cap20_daily",
          "strategy_id": "caerus_polaris_alpha",
          "strategy_type": "security_selection",
          "today_return": null,
          "turnover": null,
          "variant_class": "alpha"
        },
        {
          "alpha_per_dollar_proxy": null,
          "artifact_status": "PRESENT",
          "baseline_strategy_id": null,
          "concentration": 0.6,
          "construction": {},
          "current_lifecycle_status": "shadow",
          "data_status": "NO_DATA",
          "display_name": "Caerus Orion",
          "drawdown": -0.1353372101,
          "effective_n": null,
          "eligible_for_promotion": true,
          "eligible_for_shadow": true,
          "execution_impact": "NON_EXECUTIONAL",
          "family": "core_momentum",
          "lifecycle_stage": "shadow",
          "manifest_lifecycle_stage": "shadow_observed",
          "promotion_readiness": "NOT_READY",
          "review_checkpoints": [],
          "role": "challenger",
          "short_name": "orion",
          "since_inception_return": 1.2878179278,
          "sleeve_id": "orion",
          "source_variant": "h2_rank_decay_exit_h6_top5",
          "strategy_id": "caerus_orion",
          "strategy_type": "security_selection",
          "today_return": 0.0,
          "turnover": 0.0093023256,
          "variant_class": "standard"
        },
        {
          "alpha_per_dollar_proxy": null,
          "artifact_status": "PRESENT",
          "baseline_strategy_id": "caerus_orion",
          "concentration": null,
          "construction": {
            "cash_residual_allowed": true,
            "max_position_weight": 0.25,
            "rank_decay_exit": true,
            "top_n": 3,
            "weighting": "equal"
          },
          "current_lifecycle_status": "shadow",
          "data_status": "OK",
          "display_name": "Orion_Alpha",
          "drawdown": null,
          "effective_n": null,
          "eligible_for_promotion": true,
          "eligible_for_shadow": true,
          "execution_impact": "NON_EXECUTIONAL",
          "family": "core_momentum",
          "lifecycle_stage": "shadow",
          "manifest_lifecycle_stage": "shadow_observed",
          "promotion_readiness": "WATCHLIST",
          "review_checkpoints": [
            {
              "observed_days": 0,
              "status": "IN_PROGRESS",
              "trading_days": 20
            },
            {
              "observed_days": 0,
              "status": "IN_PROGRESS",
              "trading_days": 60
            }
          ],
          "role": "challenger",
          "short_name": "orion_alpha",
          "since_inception_return": null,
          "sleeve_id": "orion_alpha",
          "source_variant": "orion_alpha_rank_decay_top3_cap25",
          "strategy_id": "caerus_orion_alpha",
          "strategy_type": "security_selection",
          "today_return": null,
          "turnover": null,
          "variant_class": "alpha"
        },
        {
          "alpha_per_dollar_proxy": null,
          "artifact_status": "PRESENT",
          "baseline_strategy_id": null,
          "concentration": 0.6,
          "construction": {},
          "current_lifecycle_status": "shadow",
          "data_status": "NO_DATA",
          "display_name": "Caerus Lyra",
          "drawdown": -0.138882998,
          "effective_n": null,
          "eligible_for_promotion": true,
          "eligible_for_shadow": true,
          "execution_impact": "NON_EXECUTIONAL",
          "family": "core_momentum",
          "lifecycle_stage": "shadow",
          "manifest_lifecycle_stage": "shadow_observed",
          "promotion_readiness": "NOT_READY",
          "review_checkpoints": [],
          "role": "challenger",
          "short_name": "lyra",
          "since_inception_return": 1.3482407593,
          "sleeve_id": "lyra",
          "source_variant": "h1_weekly_h6_top5",
          "strategy_id": "caerus_lyra",
          "strategy_type": "security_selection",
          "today_return": 0.0,
          "turnover": 0.0279069767,
          "variant_class": "standard"
        },
        {
          "alpha_per_dollar_proxy": null,
          "artifact_status": "NOT_REQUIRED",
          "baseline_strategy_id": null,
          "concentration": null,
          "construction": {},
          "current_lifecycle_status": "research",
          "data_status": "NOT_REQUIRED",
          "display_name": "Caerus Phoenix",
          "drawdown": null,
          "effective_n": null,
          "eligible_for_promotion": false,
          "eligible_for_shadow": true,
          "execution_impact": "NON_EXECUTIONAL",
          "family": "crisis_reversal",
          "lifecycle_stage": "research",
          "manifest_lifecycle_stage": "spec_only",
          "promotion_readiness": "RESEARCH",
          "review_checkpoints": [],
          "role": "research_candidate",
          "short_name": "phoenix",
          "since_inception_return": null,
          "sleeve_id": "phoenix",
          "source_variant": null,
          "strategy_id": "caerus_phoenix",
          "strategy_type": "security_selection",
          "today_return": null,
          "turnover": null,
          "variant_class": "standard"
        },
        {
          "alpha_per_dollar_proxy": null,
          "artifact_status": "NOT_REQUIRED",
          "baseline_strategy_id": null,
          "concentration": null,
          "construction": {},
          "current_lifecycle_status": "research",
          "data_status": "NOT_REQUIRED",
          "display_name": "Caerus Cygnus",
          "drawdown": null,
          "effective_n": null,
          "eligible_for_promotion": false,
          "eligible_for_shadow": true,
          "execution_impact": "NON_EXECUTIONAL",
          "family": "earnings_drift",
          "lifecycle_stage": "research",
          "manifest_lifecycle_stage": "shelved_v0",
          "promotion_readiness": "RESEARCH",
          "review_checkpoints": [],
          "role": "research_candidate",
          "short_name": "cygnus",
          "since_inception_return": null,
          "sleeve_id": "cygnus",
          "source_variant": null,
          "strategy_id": "caerus_cygnus",
          "strategy_type": "security_selection",
          "today_return": null,
          "turnover": null,
          "variant_class": "standard"
        },
        {
          "alpha_per_dollar_proxy": null,
          "artifact_status": "NOT_REQUIRED",
          "baseline_strategy_id": null,
          "concentration": null,
          "construction": {},
          "current_lifecycle_status": "research",
          "data_status": "NOT_REQUIRED",
          "display_name": "Caerus Cassiopeia",
          "drawdown": null,
          "effective_n": null,
          "eligible_for_promotion": false,
          "eligible_for_shadow": false,
          "execution_impact": "NON_EXECUTIONAL",
          "family": "event_driven",
          "lifecycle_stage": "research",
          "manifest_lifecycle_stage": "spec_only",
          "promotion_readiness": "RESEARCH",
          "review_checkpoints": [],
          "role": "research_candidate",
          "short_name": "cassiopeia",
          "since_inception_return": null,
          "sleeve_id": "cassiopeia",
          "source_variant": null,
          "strategy_id": "caerus_cassiopeia",
          "strategy_type": "security_selection",
          "today_return": null,
          "turnover": null,
          "variant_class": "standard"
        },
        {
          "alpha_per_dollar_proxy": null,
          "artifact_status": "NOT_REQUIRED",
          "baseline_strategy_id": null,
          "concentration": null,
          "construction": {},
          "current_lifecycle_status": "research",
          "data_status": "NOT_REQUIRED",
          "display_name": "Caerus Argo",
          "drawdown": null,
          "effective_n": null,
          "eligible_for_promotion": false,
          "eligible_for_shadow": false,
          "execution_impact": "NON_EXECUTIONAL",
          "family": "regime_overlay",
          "lifecycle_stage": "research",
          "manifest_lifecycle_stage": "spec_only",
          "promotion_readiness": "RESEARCH",
          "review_checkpoints": [],
          "role": "selector",
          "short_name": "argo",
          "since_inception_return": null,
          "sleeve_id": "argo",
          "source_variant": null,
          "strategy_id": "caerus_argo",
          "strategy_type": "meta_model",
          "today_return": null,
          "turnover": null,
          "variant_class": "standard"
        }
      ],
      "status": "OK",
      "summary": {
        "alpha_variants": 2,
        "by_lifecycle_stage": {
          "paper": 1,
          "research": 4,
          "shadow": 4
        },
        "paper_or_live_capital_behavior_changed": false,
        "total_registered": 9
      }
    },
    "system_health_console": {
      "as_of": "2026-06-23T20:06:00+00:00",
      "checks": [
        {
          "detail": "0 fail \u00b7 1 warn \u00b7 6 checks",
          "name": "Daily health",
          "status": "WARN"
        },
        {
          "detail": "max cache 2026-06-22",
          "name": "Hydration",
          "status": "OK"
        },
        {
          "detail": "generated 2026-06-23T11:06:42Z",
          "name": "Reconciliation",
          "status": "NOT_COMPARABLE"
        },
        {
          "detail": "0 errors \u00b7 1 warnings",
          "name": "Dashboard validation",
          "status": "canonical"
        },
        {
          "detail": "outputs/runs/2026-06-23T093506-0400_2858433/trading_day_summary.json",
          "name": "Latest execution artifact",
          "status": "PRESENT"
        }
      ],
      "is_stale": true,
      "summary": {
        "failed_pipeline_count": 0,
        "hydration_max_cache_date": "2026-06-22",
        "latest_successful_execution": "outputs/runs/2026-06-23T093506-0400_2858433/trading_day_summary.json",
        "shadow_generation_date": "2026-06-23",
        "status": "WARN",
        "warning_count": 2
      }
    },
    "trades_today": {
      "as_of": "2026-06-23T20:05:54.766588+00:00",
      "is_stale": false,
      "rows": [],
      "source_type": "alpaca_fills",
      "summary": {
        "buy_count": 0,
        "buy_notional": 0.0,
        "fills_count": 0,
        "sell_count": 0,
        "sell_notional": 0.0
      },
      "trust_level": "canonical"
    }
  },
  "sources": [
    {
      "as_of": "2026-06-23T20:05:54.766588+00:00",
      "label": "broker account snapshot",
      "path": "outputs/broker/broker_snapshot_latest.json",
      "section": "nav",
      "source_type": "broker_account",
      "trust_level": "authoritative",
      "used": true
    },
    {
      "as_of": "2026-06-23T20:05:54.766588+00:00",
      "label": "broker positions snapshot",
      "path": "outputs/broker/posttrade_positions.json",
      "section": "positions",
      "source_type": "broker_positions",
      "trust_level": "authoritative",
      "used": true
    },
    {
      "as_of": "2026-06-23T20:05:54.766588+00:00",
      "label": "alpaca fills snapshot",
      "path": "outputs/broker_snapshot/broker_snapshot_2026-06-23.json",
      "section": "trades_today",
      "source_type": "alpaca_fills",
      "trust_level": "canonical",
      "used": true
    },
    {
      "as_of": null,
      "label": "portfolio history",
      "path": "outputs/perf/live_overlay_nav_series.csv",
      "section": "performance_history",
      "source_type": "alpaca_portfolio_history",
      "trust_level": "canonical",
      "used": true
    },
    {
      "as_of": null,
      "label": "benchmark history",
      "path": "outputs/perf/live_overlay_benchmark_close_history.csv",
      "section": "performance_history",
      "source_type": "benchmark_history",
      "trust_level": "canonical",
      "used": true
    },
    {
      "as_of": "2026-06-23",
      "label": "shadow evaluation",
      "path": "outputs/shadow_candidates/2026-06-23/shadow_evaluation.json",
      "section": "shadow_command_center",
      "source_type": "shadow_evaluation",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": "2026-06-22",
      "label": "shadow nav series",
      "path": "outputs/shadow_candidates/performance/shadow_nav_series.csv",
      "section": "shadow_command_center",
      "source_type": "shadow_nav_series",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": null,
      "label": "vix regime",
      "path": "outputs/vix_regime/regime_current.json",
      "section": "regime_market_state",
      "source_type": "vix_regime",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": "2026-06-22",
      "label": "engine review",
      "path": "outputs/engine_review/live_regime_review_latest.json",
      "section": "regime_market_state",
      "source_type": "engine_review",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": "2026-06-23T14:00:10+00:00",
      "label": "live pilot plan",
      "path": "outputs/live_pilot/plans/live_pilot_plan_2026-06-23.json",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "runtime",
      "used": true
    },
    {
      "as_of": "2026-06-23T14:00:13+00:00",
      "label": "live pilot preflight",
      "path": "outputs/live_pilot/runs/2026-06-23T100012-0400_59e97cb/live_pilot_preflight.json",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "runtime",
      "used": true
    },
    {
      "as_of": "2026-06-23T14:00:13+00:00",
      "label": "live pilot operator summary",
      "path": "outputs/live_pilot/runs/2026-06-23T100012-0400_59e97cb/live_pilot_operator_summary.json",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "runtime",
      "used": true
    },
    {
      "as_of": null,
      "label": "live pilot evidence metrics",
      "path": "outputs/live_pilot/runs/2026-06-23T100012-0400_59e97cb/live_pilot_evidence_metrics.json",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "missing",
      "used": false
    },
    {
      "as_of": "2026-06-23T14:00:13+00:00",
      "label": "live pilot reconciliation",
      "path": "outputs/live_pilot/runs/2026-06-23T100012-0400_59e97cb/live_pilot_reconciliation.json",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "runtime",
      "used": true
    },
    {
      "as_of": null,
      "label": "live pilot submitted orders",
      "path": "outputs/live_pilot/runs/2026-06-23T100012-0400_59e97cb/live_pilot_orders_submitted.json",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "runtime",
      "used": true
    },
    {
      "as_of": null,
      "label": "live pilot open order check",
      "path": "outputs/live_pilot/runs/2026-06-23T100012-0400_59e97cb/live_pilot_open_order_check.json",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "missing",
      "used": false
    },
    {
      "as_of": null,
      "label": "live pilot broker snapshot",
      "path": "outputs/live_pilot/runs/2026-06-23T100012-0400_59e97cb/live_pilot_broker_snapshot_post.json",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "runtime",
      "used": true
    },
    {
      "as_of": null,
      "label": "strategy registry",
      "path": "config/research/strategy_registry.json",
      "section": "sleeve_inventory",
      "source_type": "strategy_registry",
      "trust_level": "governance",
      "used": true
    },
    {
      "as_of": "2026-06-12-fr069-phase-b",
      "label": "sleeve manifest",
      "path": "research_registry/sleeves/manifest.json",
      "section": "sleeve_inventory",
      "source_type": "sleeve_manifest",
      "trust_level": "governance",
      "used": true
    },
    {
      "as_of": "2026-04-30",
      "label": "daily health check",
      "path": "outputs/health/caerus_daily_health_check/latest/health_check.json",
      "section": "system_health_console",
      "source_type": "health_check",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": "2026-06-22",
      "label": "hydration status",
      "path": "outputs/price_hydration/2026-06-22/status.json",
      "section": "system_health_console",
      "source_type": "price_hydration",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": "2026-06-23T11:06:42Z",
      "label": "live vs shadow reconciliation",
      "path": "outputs/reconciliation/live_vs_shadow/latest/live_vs_shadow_reconciliation.json",
      "section": "system_health_console",
      "source_type": "reconciliation",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": "2026-06-08",
      "label": "model quality packet",
      "path": "outputs/model_quality/2026-06-08/model_quality_packet.json",
      "section": "decision_grade",
      "source_type": "model_quality",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": "2026-06-08",
      "label": "model tournament",
      "path": "outputs/model_quality/2026-06-08/model_tournament.json",
      "section": "decision_grade",
      "source_type": "model_quality",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": "2026-06-08",
      "label": "argo phase b validation",
      "path": "outputs/model_quality/2026-06-08/argo_phase_b_validation.json",
      "section": "decision_grade",
      "source_type": "model_quality",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": "2026-06-08",
      "label": "strategy differentiation deep dive",
      "path": "outputs/model_quality/2026-06-08/strategy_differentiation_deep_dive.json",
      "section": "decision_grade",
      "source_type": "model_quality",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": "2026-06-08",
      "label": "phoenix phase b review",
      "path": "outputs/model_quality/2026-06-08/phoenix_phase_b_review.json",
      "section": "decision_grade",
      "source_type": "model_quality",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": "2026-06-08",
      "label": "multi asset research framework",
      "path": "outputs/model_quality/2026-06-08/multi_asset_research_framework.json",
      "section": "decision_grade",
      "source_type": "model_quality",
      "trust_level": "diagnostic",
      "used": true
    }
  ],
  "status": {
    "errors": [],
    "level": "warning",
    "summary": "Dashboard published with warnings.",
    "warnings": [
      {
        "code": "shadow_nav_current",
        "message": "Shadow NAV latest date lags latest evaluation date."
      },
      {
        "code": "decision_grade_model_quality_present",
        "message": "Decision-grade model-quality artifacts are incomplete."
      }
    ]
  },
  "terminal": {
    "benchmark": {
      "down_days": 31,
      "excess_since_inception_return": 0.019116735411487173,
      "history_points": 78,
      "max_drawdown": -0.05016799999999999,
      "rolling_20d_excess_return": 0.05219411983745459,
      "rolling_20d_return": 0.036221218778351716,
      "rolling_20d_spy_return": -0.015972901059102873,
      "rolling_5d_excess_return": 0.020275936686435347,
      "rolling_5d_return": -0.007677425187596065,
      "rolling_5d_spy_return": -0.027953361874031413,
      "since_inception_return": 0.09760799999999992,
      "spy_close": 733.72998046875,
      "spy_since_inception_return": 0.07849126458851274,
      "up_days": 44
    },
    "headline": {
      "cash": 552.51,
      "day_pnl": -133.96,
      "day_return": -0.012057834745384799,
      "fills_count": 0,
      "gross_exposure": 0.9496623566883623,
      "nav": 10976.08,
      "positions_count": 25,
      "validation_status": "ok"
    },
    "health": {
      "blocking_failures": 0,
      "sources_total": 28,
      "sources_used": 26,
      "stale_sections": [
        "shadow_command_center",
        "sleeve_inventory",
        "baseline_alpha_comparison",
        "system_health_console"
      ],
      "warnings": 2
    },
    "leaders": {
      "laggards": [
        {
          "avg_entry_price": 397.29926698375897,
          "cost_basis": 643.695647,
          "last_price": 386.83,
          "market_value": 626.733568,
          "qty": 1.62017829,
          "side": "positionside.long",
          "ticker": "MAR",
          "unrealized_pnl": -16.962079,
          "unrealized_pnl_pct": -0.02635,
          "weight": 0.057099945335675396
        },
        {
          "avg_entry_price": 80.3917039690212,
          "cost_basis": 837.685792,
          "last_price": 78.95,
          "market_value": 822.663161,
          "qty": 10.4200527,
          "side": "positionside.long",
          "ticker": "GM",
          "unrealized_pnl": -15.022631,
          "unrealized_pnl_pct": -0.01793,
          "weight": 0.07495054345449377
        },
        {
          "avg_entry_price": 146.87094507731067,
          "cost_basis": 617.114218,
          "last_price": 144.97,
          "market_value": 609.126932,
          "qty": 4.20174472,
          "side": "positionside.long",
          "ticker": "C",
          "unrealized_pnl": -7.987286,
          "unrealized_pnl_pct": -0.01294,
          "weight": 0.0554958538931932
        },
        {
          "avg_entry_price": 364.12,
          "cost_basis": 364.12,
          "last_price": 356.3,
          "market_value": 356.3,
          "qty": 1.0,
          "side": "positionside.long",
          "ticker": "GE",
          "unrealized_pnl": -7.82,
          "unrealized_pnl_pct": -0.02148,
          "weight": 0.032461498094037214
        },
        {
          "avg_entry_price": 204.8543528691729,
          "cost_basis": 456.592469,
          "last_price": 203.01,
          "market_value": 452.481657,
          "qty": 2.228863886,
          "side": "positionside.long",
          "ticker": "QCOM",
          "unrealized_pnl": -4.110812,
          "unrealized_pnl_pct": -0.009,
          "weight": 0.04122434029270924
        }
      ],
      "winners": [
        {
          "avg_entry_price": 706.3942863793549,
          "cost_basis": 875.151904,
          "last_price": 1044.5,
          "market_value": 1294.031083,
          "qty": 1.238900032,
          "side": "positionside.long",
          "ticker": "STX",
          "unrealized_pnl": 418.879179,
          "unrealized_pnl_pct": 0.47864,
          "weight": 0.11789555861473314
        },
        {
          "avg_entry_price": 130.035,
          "cost_basis": 780.21,
          "last_price": 148.18,
          "market_value": 889.08,
          "qty": 6.0,
          "side": "positionside.long",
          "ticker": "FTNT",
          "unrealized_pnl": 108.87,
          "unrealized_pnl_pct": 0.13954,
          "weight": 0.08100159619827844
        },
        {
          "avg_entry_price": 91.7543149833832,
          "cost_basis": 996.639723,
          "last_price": 93.68,
          "market_value": 1017.556605,
          "qty": 10.862047449,
          "side": "positionside.long",
          "ticker": "MNST",
          "unrealized_pnl": 20.916882,
          "unrealized_pnl_pct": 0.02099,
          "weight": 0.09270674093118855
        },
        {
          "avg_entry_price": 99.04295692856739,
          "cost_basis": 616.319532,
          "last_price": 101.57,
          "market_value": 632.044689,
          "qty": 6.222749715,
          "side": "positionside.long",
          "ticker": "CVS",
          "unrealized_pnl": 15.725157,
          "unrealized_pnl_pct": 0.02551,
          "weight": 0.05758382673960102
        },
        {
          "avg_entry_price": 240.44933694447803,
          "cost_basis": 661.122575,
          "last_price": 245.6,
          "market_value": 675.284476,
          "qty": 2.749529624,
          "side": "positionside.long",
          "ticker": "KLAC",
          "unrealized_pnl": 14.161901,
          "unrealized_pnl_pct": 0.02142,
          "weight": 0.06152328299356419
        }
      ]
    },
    "positioning": {
      "average_position_weight": 0.037986489497161095,
      "cash_ratio": 0.05033764331163767,
      "gross_market_value": 10423.57,
      "invested_ratio": 0.9496623566883623,
      "largest_position_weight": 0.11789555861473314,
      "median_position_weight": 0.021622060790373248,
      "top10_concentration": 0.7232311415368693,
      "top5_concentration": 0.43668510205829403
    },
    "tape": {
      "buy_notional": 0.0,
      "buy_symbols": [],
      "last_fill_at": null,
      "net_notional": 0.0,
      "sell_notional": 0.0,
      "sell_symbols": []
    }
  },
  "validation": {
    "checks": [
      {
        "detail": "Broker account snapshot loaded.",
        "name": "nav_source_present",
        "severity": "blocking",
        "status": "pass"
      },
      {
        "detail": "Broker positions snapshot loaded.",
        "name": "positions_source_present",
        "severity": "blocking",
        "status": "pass"
      },
      {
        "detail": "Sum of position market values plus cash matches equity within tolerance.",
        "name": "positions_sum_matches_nav",
        "severity": "blocking",
        "status": "pass",
        "tolerance": 1.0
      },
      {
        "detail": "Position weights match gross exposure.",
        "name": "positions_weights_sum_reasonable",
        "severity": "blocking",
        "status": "pass"
      },
      {
        "detail": "Alpaca fills snapshot loaded.",
        "name": "trades_source_present",
        "severity": "blocking",
        "status": "pass"
      },
      {
        "detail": "All fills match the report date.",
        "name": "trades_are_report_date_only",
        "severity": "blocking",
        "status": "pass"
      },
      {
        "detail": "Performance history sources loaded.",
        "name": "performance_source_present",
        "severity": "blocking",
        "status": "pass"
      },
      {
        "detail": "Portfolio history dates are ordered and unique.",
        "name": "performance_series_monotonic_dates",
        "severity": "blocking",
        "status": "pass"
      },
      {
        "detail": "SPY comparison series are aligned to portfolio dates.",
        "name": "spy_dates_aligned",
        "severity": "blocking",
        "status": "pass"
      },
      {
        "detail": "Latest portfolio history NAV matches current NAV.",
        "name": "history_latest_nav_matches_nav_section",
        "severity": "blocking",
        "status": "pass"
      },
      {
        "detail": "Shadow evaluation artifact loaded.",
        "name": "shadow_command_center_source_present",
        "severity": "non_blocking",
        "status": "pass"
      },
      {
        "detail": "Shadow NAV latest date lags latest evaluation date.",
        "evaluation_date": "2026-06-23",
        "latest_nav_date": "2026-06-22",
        "name": "shadow_nav_current",
        "severity": "non_blocking",
        "status": "warn"
      },
      {
        "detail": "Live-pilot plan or run artifacts loaded.",
        "name": "live_pilot_artifacts_present",
        "severity": "non_blocking",
        "status": "pass"
      },
      {
        "detail": "Registered sleeve inventory loaded.",
        "name": "sleeve_inventory_artifact_coverage",
        "severity": "non_blocking",
        "status": "pass"
      },
      {
        "detail": "Decision-grade model-quality artifacts are incomplete.",
        "name": "decision_grade_model_quality_present",
        "severity": "non_blocking",
        "status": "warn"
      },
      {
        "detail": "positions timestamp is fresh.",
        "name": "positions_timestamp_fresh",
        "severity": "non_blocking",
        "status": "pass"
      },
      {
        "detail": "nav timestamp is fresh.",
        "name": "nav_timestamp_fresh",
        "severity": "non_blocking",
        "status": "pass"
      },
      {
        "detail": "trades_today timestamp is fresh.",
        "name": "trades_today_timestamp_fresh",
        "severity": "non_blocking",
        "status": "pass"
      },
      {
        "detail": "Performance history latest date matches report date.",
        "name": "performance_timestamp_fresh",
        "severity": "non_blocking",
        "status": "pass"
      },
      {
        "detail": "Buying power present.",
        "name": "buying_power_present",
        "severity": "non_blocking",
        "status": "pass"
      }
    ]
  }
};
