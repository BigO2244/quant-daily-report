window.DASHBOARD_V1 = {
  "environment": "paper",
  "generated_at": "2026-06-23T18:57:36+00:00",
  "report_date": "2026-04-09",
  "schema_version": "dashboard-v2-prototype",
  "sections": {
    "account_layers": {
      "as_of": "2026-06-23T18:57:36+00:00",
      "is_stale": false,
      "rows": [
        {
          "buying_power": 12101.58,
          "capital_behavior": "paper only",
          "cash": 2388.18,
          "equity": 9713.4,
          "layer": "Paper account",
          "positions_count": 16,
          "source": "broker paper/account artifacts",
          "status": "PAPER_OBSERVED"
        },
        {
          "buying_power": null,
          "capital_behavior": "FR-104 capped pilot only",
          "cash": null,
          "equity": null,
          "layer": "Live pilot account",
          "positions_count": 0,
          "source": "outputs/live_pilot/plans/live_pilot_plan_2026-03-24.json",
          "status": "BLOCKED_NO_QUALIFYING_ORDER"
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
      "as_of": "2026-06-08",
      "is_stale": true,
      "pairs": [
        {
          "alpha_alpha_per_dollar_proxy": -4.984764,
          "alpha_concentration": 0.6,
          "alpha_drawdown": 0.0,
          "alpha_effective_n": 4.0,
          "alpha_name": "Polaris_Alpha",
          "alpha_return": 0.0588013215,
          "alpha_strategy_id": "caerus_polaris_alpha",
          "alpha_turnover": 0.0,
          "baseline_alpha_per_dollar_proxy": 34.707049,
          "baseline_concentration": 0.3,
          "baseline_drawdown": -0.1083554602,
          "baseline_effective_n": 10.0,
          "baseline_name": "Caerus Polaris",
          "baseline_return": 38.7536617571,
          "baseline_strategy_id": "caerus_polaris",
          "baseline_turnover": 0.0785714286,
          "drawdown_delta": 0.1083554602,
          "evidence_window_days": 1,
          "return_delta": -38.6948604356,
          "review_checkpoints": [
            {
              "observed_days": 1,
              "status": "IN_PROGRESS",
              "trading_days": 20
            },
            {
              "observed_days": 1,
              "status": "IN_PROGRESS",
              "trading_days": 60
            }
          ],
          "status": "IN_PROGRESS"
        },
        {
          "alpha_alpha_per_dollar_proxy": -5.337749,
          "alpha_concentration": 0.75,
          "alpha_drawdown": 0.0,
          "alpha_effective_n": 3.000003,
          "alpha_name": "Orion_Alpha",
          "alpha_return": 0.0433009774,
          "alpha_strategy_id": "caerus_orion_alpha",
          "alpha_turnover": 0.0,
          "baseline_alpha_per_dollar_proxy": 167.713982,
          "baseline_concentration": 0.6,
          "baseline_drawdown": -0.1352922746,
          "baseline_effective_n": 5.0,
          "baseline_name": "Caerus Orion",
          "baseline_return": 171.7605944241,
          "baseline_strategy_id": "caerus_orion",
          "baseline_turnover": 0.0142857143,
          "drawdown_delta": 0.1352922746,
          "evidence_window_days": 1,
          "return_delta": -171.71729344669998,
          "review_checkpoints": [
            {
              "observed_days": 1,
              "status": "IN_PROGRESS",
              "trading_days": 20
            },
            {
              "observed_days": 1,
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
      "as_of": "2026-04-09",
      "is_stale": false,
      "laggards": [
        {
          "avg_entry_price": 49.821,
          "cost_basis": 498.21,
          "last_price": 48.0,
          "market_value": 480.0,
          "qty": 10.0,
          "side": "long",
          "ticker": "VZ",
          "unrealized_pnl": -18.21,
          "unrealized_pnl_pct": -0.03655,
          "weight": 0.049416270306998585
        },
        {
          "avg_entry_price": 28.2225,
          "cost_basis": 451.56,
          "last_price": 27.335,
          "market_value": 437.36,
          "qty": 16.0,
          "side": "long",
          "ticker": "PFE",
          "unrealized_pnl": -14.2,
          "unrealized_pnl_pct": -0.03145,
          "weight": 0.04502645829472687
        },
        {
          "avg_entry_price": 261.23,
          "cost_basis": 522.46,
          "last_price": 257.62,
          "market_value": 515.24,
          "qty": 2.0,
          "side": "long",
          "ticker": "AAPL",
          "unrealized_pnl": -7.22,
          "unrealized_pnl_pct": -0.01382,
          "weight": 0.05304424815203739
        }
      ],
      "largest_decreases": [
        {
          "client_order_id": null,
          "fill_price": 140.89,
          "filled_at": "2026-04-09T13:35:28.084745Z",
          "notional": 563.56,
          "order_id": "c182c2ab-5af4-49f8-8710-48e9166953b6",
          "qty": 4.0,
          "side": "sell",
          "source_execution_id": "20260409093528084::e156b6e8-a1c5-44bb-b0cc-c975f2d2f0e3",
          "ticker": "GILD"
        },
        {
          "client_order_id": null,
          "fill_price": 49.4,
          "filled_at": "2026-04-09T13:35:29.597397Z",
          "notional": 296.4,
          "order_id": "3ea56dd3-5909-4ccb-8bc1-f726d0d49cb5",
          "qty": 6.0,
          "side": "sell",
          "source_execution_id": "20260409093529597::0fb4fb52-9b8f-4f6e-8b8b-1167c5738989",
          "ticker": "TFC"
        },
        {
          "client_order_id": null,
          "fill_price": 123.79,
          "filled_at": "2026-04-09T13:35:27.739359Z",
          "notional": 247.58,
          "order_id": "d171c760-1c06-467d-8d03-4ffa28fdad5f",
          "qty": 2.0,
          "side": "sell",
          "source_execution_id": "20260409093527739::c070c6ab-100e-4974-b09f-e427c3beddf5",
          "ticker": "MRK"
        },
        {
          "client_order_id": null,
          "fill_price": 122.42,
          "filled_at": "2026-04-09T13:35:29.019475Z",
          "notional": 244.84,
          "order_id": "8d9da9d2-11d0-4d9b-9652-3b0ac618263d",
          "qty": 2.0,
          "side": "sell",
          "source_execution_id": "20260409093529019::ae64f4ea-7108-44f7-af71-f537f8b8551c",
          "ticker": "C"
        },
        {
          "client_order_id": null,
          "fill_price": 219.84,
          "filled_at": "2026-04-09T13:35:27.336027Z",
          "notional": 219.84,
          "order_id": "8ccaf70c-d20a-423b-9fe0-beb51453c689",
          "qty": 1.0,
          "side": "sell",
          "source_execution_id": "20260409093527336::206e949e-3778-4fb1-871c-b9f2dc04eed1",
          "ticker": "PNC"
        }
      ],
      "largest_increases": [
        {
          "client_order_id": null,
          "fill_price": 156.83,
          "filled_at": "2026-04-09T13:35:34.186233Z",
          "notional": 627.32,
          "order_id": "14b67e33-964c-4270-bc53-626b1a614323",
          "qty": 4.0,
          "side": "buy",
          "source_execution_id": "20260409093534186::461c3057-45cd-411f-a966-f34023b9b2fc",
          "ticker": "BDX"
        },
        {
          "client_order_id": null,
          "fill_price": 232.5,
          "filled_at": "2026-04-09T13:35:33.493421Z",
          "notional": 465.0,
          "order_id": "e2e856bc-921e-49ec-aec8-f8ceba1ed341",
          "qty": 2.0,
          "side": "buy",
          "source_execution_id": "20260409093533493::638b6f83-0df4-49ab-b26d-cd0a5a37293f",
          "ticker": "ADSK"
        },
        {
          "client_order_id": null,
          "fill_price": 352.0,
          "filled_at": "2026-04-09T13:35:34.000754Z",
          "notional": 352.0,
          "order_id": "1773ad65-4ce5-4969-ad25-1d935591111d",
          "qty": 1.0,
          "side": "buy",
          "source_execution_id": "20260409093534000::5e2300f5-c2b1-46bb-8c3d-64adb8009af9",
          "ticker": "AMGN"
        },
        {
          "client_order_id": null,
          "fill_price": 178.0,
          "filled_at": "2026-04-09T13:35:33.825099Z",
          "notional": 178.0,
          "order_id": "e0c10f9f-6586-4ca9-b9a7-6ec9310b42c6",
          "qty": 1.0,
          "side": "buy",
          "source_execution_id": "20260409093533825::8bf2a739-7d16-46a6-888d-499d4402918d",
          "ticker": "BKNG"
        },
        {
          "client_order_id": null,
          "fill_price": 178.0,
          "filled_at": "2026-04-09T13:35:34.865078Z",
          "notional": 178.0,
          "order_id": "e0c10f9f-6586-4ca9-b9a7-6ec9310b42c6",
          "qty": 1.0,
          "side": "buy",
          "source_execution_id": "20260409093534865::9928d04a-ef6e-4608-949d-6d31da743a93",
          "ticker": "BKNG"
        }
      ],
      "leaders": [
        {
          "avg_entry_price": 73.614286,
          "cost_basis": 883.371432,
          "last_price": 75.77,
          "market_value": 909.24,
          "qty": 12.0,
          "side": "long",
          "ticker": "GM",
          "unrealized_pnl": 25.868568,
          "unrealized_pnl_pct": 0.02928,
          "weight": 0.09360677002903206
        },
        {
          "avg_entry_price": 53.638889,
          "cost_basis": 482.750001,
          "last_price": 55.3,
          "market_value": 497.7,
          "qty": 9.0,
          "side": "long",
          "ticker": "USB",
          "unrealized_pnl": 14.949999,
          "unrealized_pnl_pct": 0.03097,
          "weight": 0.051238495274569154
        },
        {
          "avg_entry_price": 205.47,
          "cost_basis": 410.94,
          "last_price": 212.025,
          "market_value": 424.05,
          "qty": 2.0,
          "side": "long",
          "ticker": "ALL",
          "unrealized_pnl": 13.11,
          "unrealized_pnl_pct": 0.0319,
          "weight": 0.04365618629933906
        }
      ],
      "notes": [
        {
          "kind": "return",
          "label": "Portfolio daily return",
          "value": 0.01233079541653681
        },
        {
          "detail": 627.32,
          "kind": "trade",
          "label": "Largest buy",
          "value": "BDX"
        },
        {
          "detail": 563.56,
          "kind": "trade",
          "label": "Largest sell",
          "value": "GILD"
        }
      ],
      "summary": {
        "buy_count": 6,
        "latest_daily_return": 0.01233079541653681,
        "sell_count": 14,
        "turnover_proxy_notional": 4748.4
      }
    },
    "decision_grade": {
      "confidence_summary": {
        "argo_recommendation_confidence": null,
        "model_quality_packet_status": null,
        "multi_asset_status": null,
        "phoenix_confidence": null,
        "strategy_differentiation_counts": null
      },
      "decision_grade_strategy_change": false,
      "latest_model_quality_date": null,
      "promotion_ready_count": 0,
      "reason_codes": [
        "MODEL_QUALITY_PACKET_MISSING",
        "MODEL_TOURNAMENT_MISSING"
      ],
      "source_paths": {},
      "status": "PARTIAL",
      "top_blockers": [
        "MODEL_QUALITY_PACKET_MISSING",
        "MODEL_TOURNAMENT_MISSING"
      ]
    },
    "governance_state": {
      "as_of": "2026-06-23T18:57:36+00:00",
      "is_stale": false,
      "rows": [
        {
          "detail": "Level 2.5 capped live-pilot evidence can continue when approval, cap, account, market-hours, and reconciliation gates pass.",
          "name": "FR-104 pilot evidence collection",
          "pilot_blocking": false,
          "production_scaling_blocking": false,
          "promotion_blocking": false,
          "status": "READY"
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
        "account_id_hash": null,
        "buying_power": null,
        "cash": null,
        "equity": null,
        "portfolio_value": null,
        "status": null
      },
      "as_of": "2026-06-20T12:05:32+00:00",
      "blocking_open_orders": [],
      "is_stale": false,
      "latest_fill_status": null,
      "latest_submitted_order": null,
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
        "submitted_count": null
      },
      "open_orders": [],
      "paper_live_comparability": {
        "available": false,
        "reason": "paper_live_divergence_artifact_not_available_for_live_pilot_section"
      },
      "plan_path": "outputs/live_pilot/plans/live_pilot_plan_2026-03-24.json",
      "plan_status": "BLOCKED_NO_QUALIFYING_ORDER",
      "policy": {
        "cap_enforced_before_submission": true,
        "capital_behavior_changed": false,
        "duplicate_open_order_policy": "skip_if_open_live_pilot_order_detected",
        "normal_market_hours_only": true,
        "order_type": null,
        "paper_or_production_impact": "none",
        "scope": "FR-104 LIVE_PILOT only",
        "time_in_force": null
      },
      "positions": [],
      "reconciliation": {
        "open_count": null,
        "operator_action": null,
        "rejected_count": null,
        "state": null,
        "status": null,
        "unresolved_count": null
      },
      "run_id": null,
      "run_root": null,
      "selected_order": null,
      "status": "BLOCKED_NO_QUALIFYING_ORDER",
      "submitted_orders": []
    },
    "live_readiness": {
      "as_of": "2026-06-23T18:57:36+00:00",
      "criteria": [
        {
          "detail": "1 blocking errors",
          "name": "Validation integrity",
          "status": "FAIL"
        },
        {
          "detail": "canonical dashboard sources loaded",
          "name": "Artifact completeness",
          "status": "PASS"
        },
        {
          "detail": "NAV through 2026-06-05",
          "name": "Shadow continuity",
          "status": "WARN"
        },
        {
          "detail": "0 fail \u00b7 3 warn",
          "name": "Operational health",
          "status": "WARN"
        }
      ],
      "is_stale": false,
      "summary": {
        "artifact_completeness_streak": 26,
        "consecutive_healthy_days": 26,
        "deployment_confidence": "WATCH",
        "shadow_evaluation_continuity": "2026-06-05",
        "successful_execution_streak": null
      }
    },
    "nav": {
      "as_of": "2026-04-09T13:50:35.044822+00:00",
      "buying_power": 12101.58,
      "cash": 2388.18,
      "day_pnl": -38.57,
      "day_return": -0.00395509830321461,
      "equity": 9713.4,
      "gross_exposure": 0.7541314060987914,
      "is_stale": true,
      "long_market_value": 7325.18,
      "net_exposure": 0.7541314060987914,
      "short_market_value": 0.0,
      "source_type": "broker_account",
      "trust_level": "canonical"
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
          "spy_close": 673.77001953125,
          "spy_return": 0.022071614142098683
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
          "equity": 9715.45,
          "return_1d": 0.01233079541653681
        }
      ],
      "as_of": "2026-04-08",
      "is_stale": true,
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
            "value": 0.01233079541653681
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
            "value": -0.028454999999999897
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
            "value": -0.018812624397267053
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
            "value": 9715.45
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
            "value": 97.15450000000001
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
            "value": 673.77001953125
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
            "value": 99.03576243972674
          }
        ]
      },
      "source_type": "alpaca_portfolio_history",
      "summary": {
        "excess_since_inception_return": -0.018812624397267275,
        "inception_date": "2026-03-03",
        "latest_nav": 9715.45,
        "max_drawdown": -0.05016799999999999,
        "since_inception_return": -0.028454999999999897,
        "spy_since_inception_return": -0.009642375602732622
      },
      "trust_level": "canonical"
    },
    "positions": {
      "as_of": "2026-04-09T13:50:35.044822+00:00",
      "is_stale": true,
      "rows": [
        {
          "avg_entry_price": 73.614286,
          "cost_basis": 883.371432,
          "last_price": 75.77,
          "market_value": 909.24,
          "qty": 12.0,
          "side": "long",
          "ticker": "GM",
          "unrealized_pnl": 25.868568,
          "unrealized_pnl_pct": 0.02928,
          "weight": 0.09360677002903206
        },
        {
          "avg_entry_price": 128.262,
          "cost_basis": 641.31,
          "last_price": 127.56,
          "market_value": 637.8,
          "qty": 5.0,
          "side": "long",
          "ticker": "QCOM",
          "unrealized_pnl": -3.51,
          "unrealized_pnl_pct": -0.00547,
          "weight": 0.06566186917042437
        },
        {
          "avg_entry_price": 156.83,
          "cost_basis": 627.32,
          "last_price": 155.435,
          "market_value": 621.74,
          "qty": 4.0,
          "side": "long",
          "ticker": "BDX",
          "unrealized_pnl": -5.58,
          "unrealized_pnl_pct": -0.00889,
          "weight": 0.0640084831264027
        },
        {
          "avg_entry_price": 261.23,
          "cost_basis": 522.46,
          "last_price": 257.62,
          "market_value": 515.24,
          "qty": 2.0,
          "side": "long",
          "ticker": "AAPL",
          "unrealized_pnl": -7.22,
          "unrealized_pnl_pct": -0.01382,
          "weight": 0.05304424815203739
        },
        {
          "avg_entry_price": 127.27,
          "cost_basis": 509.08,
          "last_price": 127.0,
          "market_value": 508.0,
          "qty": 4.0,
          "side": "long",
          "ticker": "COP",
          "unrealized_pnl": -1.08,
          "unrealized_pnl_pct": -0.00212,
          "weight": 0.052298886074906833
        },
        {
          "avg_entry_price": 53.638889,
          "cost_basis": 482.750001,
          "last_price": 55.3,
          "market_value": 497.7,
          "qty": 9.0,
          "side": "long",
          "ticker": "USB",
          "unrealized_pnl": 14.949999,
          "unrealized_pnl_pct": 0.03097,
          "weight": 0.051238495274569154
        },
        {
          "avg_entry_price": 49.821,
          "cost_basis": 498.21,
          "last_price": 48.0,
          "market_value": 480.0,
          "qty": 10.0,
          "side": "long",
          "ticker": "VZ",
          "unrealized_pnl": -18.21,
          "unrealized_pnl_pct": -0.03655,
          "weight": 0.049416270306998585
        },
        {
          "avg_entry_price": 64.64,
          "cost_basis": 452.48,
          "last_price": 66.485,
          "market_value": 465.395,
          "qty": 7.0,
          "side": "long",
          "ticker": "MO",
          "unrealized_pnl": 12.915,
          "unrealized_pnl_pct": 0.02854,
          "weight": 0.047912677332345006
        },
        {
          "avg_entry_price": 232.5,
          "cost_basis": 465.0,
          "last_price": 229.22,
          "market_value": 458.44,
          "qty": 2.0,
          "side": "long",
          "ticker": "ADSK",
          "unrealized_pnl": -6.56,
          "unrealized_pnl_pct": -0.01411,
          "weight": 0.04719665616570923
        },
        {
          "avg_entry_price": 28.2225,
          "cost_basis": 451.56,
          "last_price": 27.335,
          "market_value": 437.36,
          "qty": 16.0,
          "side": "long",
          "ticker": "PFE",
          "unrealized_pnl": -14.2,
          "unrealized_pnl_pct": -0.03145,
          "weight": 0.04502645829472687
        },
        {
          "avg_entry_price": 205.47,
          "cost_basis": 410.94,
          "last_price": 212.025,
          "market_value": 424.05,
          "qty": 2.0,
          "side": "long",
          "ticker": "ALL",
          "unrealized_pnl": 13.11,
          "unrealized_pnl_pct": 0.0319,
          "weight": 0.04365618629933906
        },
        {
          "avg_entry_price": 81.444,
          "cost_basis": 407.22,
          "last_price": 82.07,
          "market_value": 410.35,
          "qty": 5.0,
          "side": "long",
          "ticker": "FTNT",
          "unrealized_pnl": 3.13,
          "unrealized_pnl_pct": 0.00769,
          "weight": 0.04224576358432681
        },
        {
          "avg_entry_price": 178.0,
          "cost_basis": 356.0,
          "last_price": 177.88,
          "market_value": 355.76,
          "qty": 2.0,
          "side": "long",
          "ticker": "BKNG",
          "unrealized_pnl": -0.24,
          "unrealized_pnl_pct": -0.00067,
          "weight": 0.03662569234253711
        },
        {
          "avg_entry_price": 352.0,
          "cost_basis": 352.0,
          "last_price": 352.34,
          "market_value": 352.34,
          "qty": 1.0,
          "side": "long",
          "ticker": "AMGN",
          "unrealized_pnl": 0.34,
          "unrealized_pnl_pct": 0.00097,
          "weight": 0.03627360141659975
        },
        {
          "avg_entry_price": 170.01,
          "cost_basis": 170.01,
          "last_price": 169.25,
          "market_value": 169.25,
          "qty": 1.0,
          "side": "long",
          "ticker": "PANW",
          "unrealized_pnl": -0.76,
          "unrealized_pnl_pct": -0.00447,
          "weight": 0.01742438281137398
        },
        {
          "avg_entry_price": 27.48,
          "cost_basis": 82.44,
          "last_price": 27.505,
          "market_value": 82.515,
          "qty": 3.0,
          "side": "long",
          "ticker": "WBD",
          "unrealized_pnl": 0.075,
          "unrealized_pnl_pct": 0.00091,
          "weight": 0.008494965717462475
        }
      ],
      "source_type": "broker_positions",
      "summary": {
        "cash": 2388.18,
        "gross_market_value": 7325.18,
        "largest_position_weight": 0.09360677002903206,
        "net_market_value": 7325.18,
        "positions_count": 16,
        "top5_concentration": 0.32862025655280336
      },
      "trust_level": "canonical"
    },
    "regime_market_state": {
      "as_of": null,
      "checks": [
        {
          "blocking": true,
          "current": "neutral_mixed",
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
          "current": "/Users/brettolson/Documents/Caerus/quant-daily-report-main/signals/2026-04-10.json",
          "name": "signal_snapshot_present",
          "note": "If the signal snapshot is missing, the allocator decision is not auditable.",
          "status": "pass",
          "threshold": "existing signal snapshot artifact"
        },
        {
          "blocking": true,
          "current": {
            "max_abs_gap": 0.306699,
            "total_abs_gap": 1.0
          },
          "name": "shadow_vs_live_alignment",
          "note": "Compares the model target book to the live broker book at the decision point.",
          "status": "fail",
          "threshold": {
            "max_single_sleeve_gap": 0.15,
            "max_total_allocation_gap": 0.3
          }
        },
        {
          "blocking": false,
          "current": 0.0,
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
          "current": 1.0,
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
      "promotion_gate_blockers": [
        "shadow_vs_live_alignment"
      ],
      "vix": 18.139999389648438
    },
    "shadow_command_center": {
      "as_of": "2026-06-08",
      "is_stale": true,
      "rolling_excess_series": [
        {
          "caerus_lyra": 0.07795848828565166,
          "caerus_orion": 0.09357996634036336,
          "caerus_orion_alpha": 0.04760580417937854,
          "caerus_polaris": 0.07376895060350575,
          "caerus_polaris_alpha": 0.05377348932928139,
          "date": "2026-02-11"
        },
        {
          "caerus_lyra": 0.03994278070043,
          "caerus_orion": 0.039732207894533245,
          "caerus_orion_alpha": 0.01719556119598664,
          "caerus_polaris": 0.0386678835758314,
          "caerus_polaris_alpha": 0.025364033369267958,
          "date": "2026-02-12"
        },
        {
          "caerus_lyra": 0.03670795915622527,
          "caerus_orion": 0.03685590574727382,
          "caerus_orion_alpha": 0.024374199402717678,
          "caerus_polaris": 0.026046620715033275,
          "caerus_polaris_alpha": 0.031678081838088734,
          "date": "2026-02-13"
        },
        {
          "caerus_lyra": 0.09363893841443016,
          "caerus_orion": 0.09322268060781591,
          "caerus_orion_alpha": 0.06702842920703878,
          "caerus_polaris": 0.060761676775772044,
          "caerus_polaris_alpha": 0.08123710404170181,
          "date": "2026-02-17"
        },
        {
          "caerus_lyra": 0.029665865745714615,
          "caerus_orion": 0.029665865745714615,
          "caerus_orion_alpha": 0.027323080841656133,
          "caerus_polaris": 0.025464352775513954,
          "caerus_polaris_alpha": 0.027659792745791445,
          "date": "2026-02-18"
        },
        {
          "caerus_lyra": 0.0029074473377992405,
          "caerus_orion": 0.0029074473377992405,
          "caerus_orion_alpha": -0.01651909098840665,
          "caerus_polaris": 0.011998935884493855,
          "caerus_polaris_alpha": -0.008632219533138752,
          "date": "2026-02-19"
        },
        {
          "caerus_lyra": 0.006578620259852785,
          "caerus_orion": 0.006578620259852785,
          "caerus_orion_alpha": -0.004433549915733637,
          "caerus_polaris": -0.0015145332654064037,
          "caerus_polaris_alpha": 0.0009818957930021188,
          "date": "2026-02-20"
        },
        {
          "caerus_lyra": -0.0067268890603106035,
          "caerus_orion": -0.0067268890603104925,
          "caerus_orion_alpha": -0.026885822825102546,
          "caerus_polaris": 0.010825168980816535,
          "caerus_polaris_alpha": -0.013881778593784255,
          "date": "2026-02-23"
        },
        {
          "caerus_lyra": -0.001831699905857187,
          "caerus_orion": -0.001831699905856965,
          "caerus_orion_alpha": -0.014133306364498832,
          "caerus_polaris": 0.015479582627867083,
          "caerus_polaris_alpha": -0.009455112175400293,
          "date": "2026-02-24"
        },
        {
          "caerus_lyra": -0.005180066796321325,
          "caerus_orion": -0.005180066796321103,
          "caerus_orion_alpha": -0.005416618635632586,
          "caerus_polaris": 0.0038821747870232404,
          "caerus_polaris_alpha": -0.014736944030440013,
          "date": "2026-02-25"
        },
        {
          "caerus_lyra": -0.0204572445946426,
          "caerus_orion": -0.020457244594642376,
          "caerus_orion_alpha": -0.0061815164507109666,
          "caerus_polaris": 0.0013180625739313,
          "caerus_polaris_alpha": -0.019375594045905387,
          "date": "2026-02-26"
        },
        {
          "caerus_lyra": -0.04214655185280758,
          "caerus_orion": -0.04214655185280747,
          "caerus_orion_alpha": -0.03469858244150836,
          "caerus_polaris": -0.012866746720966105,
          "caerus_polaris_alpha": -0.045483812958200476,
          "date": "2026-02-27"
        },
        {
          "caerus_lyra": -0.08349878309549619,
          "caerus_orion": -0.07006947302247857,
          "caerus_orion_alpha": -0.0396164075572647,
          "caerus_polaris": -0.06463271263727388,
          "caerus_polaris_alpha": -0.0696902067610149,
          "date": "2026-03-02"
        },
        {
          "caerus_lyra": -0.08181165224039944,
          "caerus_orion": -0.07122504730422918,
          "caerus_orion_alpha": -0.04965380379560491,
          "caerus_polaris": -0.05990750576996029,
          "caerus_polaris_alpha": -0.06545652246118538,
          "date": "2026-03-03"
        },
        {
          "caerus_lyra": -0.0757154120520297,
          "caerus_orion": -0.05953988200445326,
          "caerus_orion_alpha": -0.041203351042182756,
          "caerus_polaris": -0.0568468288963363,
          "caerus_polaris_alpha": -0.054317781784928076,
          "date": "2026-03-04"
        },
        {
          "caerus_lyra": -0.09801969311917924,
          "caerus_orion": -0.08304598601128821,
          "caerus_orion_alpha": -0.047096001497129225,
          "caerus_polaris": -0.08542436852750135,
          "caerus_polaris_alpha": -0.08164697672666321,
          "date": "2026-03-05"
        },
        {
          "caerus_lyra": -0.0430327625476824,
          "caerus_orion": -0.029356214975272965,
          "caerus_orion_alpha": -0.003897885538886592,
          "caerus_polaris": -0.04457674115039545,
          "caerus_polaris_alpha": -0.024241534317854696,
          "date": "2026-03-06"
        },
        {
          "caerus_lyra": 0.040044527914824046,
          "caerus_orion": 0.04009601881541669,
          "caerus_orion_alpha": 0.03562641133531508,
          "caerus_polaris": 0.027355369932508866,
          "caerus_polaris_alpha": 0.0432298342818076,
          "date": "2026-03-09"
        },
        {
          "caerus_lyra": 0.02625870881160408,
          "caerus_orion": 0.02945554973967568,
          "caerus_orion_alpha": 0.027042095716855008,
          "caerus_polaris": 0.01374786729598465,
          "caerus_polaris_alpha": 0.03042375554337473,
          "date": "2026-03-10"
        },
        {
          "caerus_lyra": 0.026457104868847003,
          "caerus_orion": 0.02364092199903378,
          "caerus_orion_alpha": 0.024778781144530693,
          "caerus_polaris": 0.01454975182343865,
          "caerus_polaris_alpha": 0.027781885303743814,
          "date": "2026-03-11"
        },
        {
          "caerus_lyra": 0.09319825514280422,
          "caerus_orion": 0.09096558885040607,
          "caerus_orion_alpha": 0.05655596727610146,
          "caerus_polaris": 0.06295080935910546,
          "caerus_polaris_alpha": 0.09724007491825348,
          "date": "2026-03-12"
        },
        {
          "caerus_lyra": 0.0771689859892134,
          "caerus_orion": 0.07758642420654838,
          "caerus_orion_alpha": 0.055868916247211575,
          "caerus_polaris": 0.04405846475182751,
          "caerus_polaris_alpha": 0.07951406482502998,
          "date": "2026-03-13"
        },
        {
          "caerus_lyra": 0.09712692511716448,
          "caerus_orion": 0.10194478653182348,
          "caerus_orion_alpha": 0.09152697062812876,
          "caerus_polaris": 0.04393262119860197,
          "caerus_polaris_alpha": 0.10301335901627118,
          "date": "2026-03-16"
        },
        {
          "caerus_lyra": 0.07931366367208126,
          "caerus_orion": 0.0817863723586123,
          "caerus_orion_alpha": 0.0830463181143255,
          "caerus_polaris": 0.03684857917963047,
          "caerus_polaris_alpha": 0.08546301373205378,
          "date": "2026-03-17"
        },
        {
          "caerus_lyra": 0.12856103972341926,
          "caerus_orion": 0.1279988038387937,
          "caerus_orion_alpha": 0.10909796829027918,
          "caerus_polaris": 0.06675063317778018,
          "caerus_polaris_alpha": 0.12649003113233348,
          "date": "2026-03-18"
        },
        {
          "caerus_lyra": 0.052607752385309015,
          "caerus_orion": 0.06437666279120924,
          "caerus_orion_alpha": 0.05404387963509416,
          "caerus_polaris": 0.031852312276325234,
          "caerus_polaris_alpha": 0.0624671872316076,
          "date": "2026-03-19"
        },
        {
          "caerus_lyra": 0.02082630207071956,
          "caerus_orion": 0.021718409955796103,
          "caerus_orion_alpha": 0.008323371411500746,
          "caerus_polaris": 0.019444968473884883,
          "caerus_polaris_alpha": 0.022681018895188476,
          "date": "2026-03-20"
        },
        {
          "caerus_lyra": 0.016679501463392588,
          "caerus_orion": -0.003828929191720065,
          "caerus_orion_alpha": -0.02030180886801436,
          "caerus_polaris": 0.023687840227319845,
          "caerus_polaris_alpha": -0.0011751743172186968,
          "date": "2026-03-23"
        },
        {
          "caerus_lyra": -0.0022348949898011172,
          "caerus_orion": -0.027199393442905007,
          "caerus_orion_alpha": -0.043013786577740465,
          "caerus_polaris": 0.01307433458854279,
          "caerus_polaris_alpha": -0.026188460791135904,
          "date": "2026-03-24"
        },
        {
          "caerus_lyra": -0.09035609363864539,
          "caerus_orion": -0.09704327849951344,
          "caerus_orion_alpha": -0.09787923601637327,
          "caerus_polaris": -0.041407643197691635,
          "caerus_polaris_alpha": -0.09024277580232265,
          "date": "2026-03-25"
        },
        {
          "caerus_lyra": -0.03302669681943249,
          "caerus_orion": -0.05316534289223762,
          "caerus_orion_alpha": -0.05069997794868786,
          "caerus_polaris": -0.003316290297644353,
          "caerus_polaris_alpha": -0.045296892801569344,
          "date": "2026-03-26"
        },
        {
          "caerus_lyra": -0.08800950071887481,
          "caerus_orion": -0.08632634482670154,
          "caerus_orion_alpha": -0.07855655987321819,
          "caerus_polaris": -0.04584323928721279,
          "caerus_polaris_alpha": -0.08026320015352917,
          "date": "2026-03-27"
        },
        {
          "caerus_lyra": -0.09004960515727434,
          "caerus_orion": -0.08007191886608445,
          "caerus_orion_alpha": -0.07619404468569224,
          "caerus_polaris": -0.047338152857254845,
          "caerus_polaris_alpha": -0.0788316319164144,
          "date": "2026-03-30"
        },
        {
          "caerus_lyra": -0.014128434685870661,
          "caerus_orion": -0.005250122146124303,
          "caerus_orion_alpha": 0.002895580631414596,
          "caerus_polaris": 0.0019060647366239136,
          "caerus_polaris_alpha": -0.0030480567043126294,
          "date": "2026-03-31"
        },
        {
          "caerus_lyra": 0.057184033129161316,
          "caerus_orion": 0.041093478203857,
          "caerus_orion_alpha": 0.045163730660212176,
          "caerus_polaris": 0.044117681721698165,
          "caerus_polaris_alpha": 0.05151407070014935,
          "date": "2026-04-01"
        },
        {
          "caerus_lyra": 0.054577595078031615,
          "caerus_orion": 0.043273961486917534,
          "caerus_orion_alpha": 0.049800624197107624,
          "caerus_polaris": 0.03349993159140241,
          "caerus_polaris_alpha": 0.046437773813819305,
          "date": "2026-04-02"
        },
        {
          "caerus_lyra": 0.15300284695024913,
          "caerus_orion": 0.12179830031542527,
          "caerus_orion_alpha": 0.1297567067101384,
          "caerus_polaris": 0.09561370820666282,
          "caerus_polaris_alpha": 0.126542841769818,
          "date": "2026-04-06"
        },
        {
          "caerus_lyra": 0.17917093639981063,
          "caerus_orion": 0.13412430879913884,
          "caerus_orion_alpha": 0.13799603817706818,
          "caerus_polaris": 0.10662059217222852,
          "caerus_polaris_alpha": 0.122039040330431,
          "date": "2026-04-07"
        },
        {
          "caerus_lyra": 0.12755014542263665,
          "caerus_orion": 0.08775841740182022,
          "caerus_orion_alpha": 0.07724402062433011,
          "caerus_polaris": 0.0769996761444518,
          "caerus_polaris_alpha": 0.07443095108400244,
          "date": "2026-04-08"
        },
        {
          "caerus_lyra": 0.13437494370048642,
          "caerus_orion": 0.10198807331814086,
          "caerus_orion_alpha": 0.0838390064776422,
          "caerus_polaris": 0.0898396492587421,
          "caerus_polaris_alpha": 0.07283483470290175,
          "date": "2026-04-09"
        },
        {
          "caerus_lyra": 0.12275744361904617,
          "caerus_orion": 0.08221081203075231,
          "caerus_orion_alpha": 0.061132663866174886,
          "caerus_polaris": 0.08419034707266237,
          "caerus_polaris_alpha": 0.058323029304778506,
          "date": "2026-04-10"
        },
        {
          "caerus_lyra": 0.1317230743234945,
          "caerus_orion": 0.09668385032051963,
          "caerus_orion_alpha": 0.08084197513782221,
          "caerus_polaris": 0.0802378793462537,
          "caerus_polaris_alpha": 0.06525588520827119,
          "date": "2026-04-13"
        },
        {
          "caerus_lyra": 0.033102226425642645,
          "caerus_orion": 0.026304461307991467,
          "caerus_orion_alpha": 0.02594678292026753,
          "caerus_polaris": 0.015342660586929346,
          "caerus_polaris_alpha": 0.022455370015652454,
          "date": "2026-04-14"
        },
        {
          "caerus_lyra": 0.008725758901621639,
          "caerus_orion": 0.012204047564121634,
          "caerus_orion_alpha": 0.02251279538697948,
          "caerus_polaris": 0.0005319195756106065,
          "caerus_polaris_alpha": 0.011970468661175904,
          "date": "2026-04-15"
        },
        {
          "caerus_lyra": 0.0005169440136596481,
          "caerus_orion": 0.00918907382186851,
          "caerus_orion_alpha": 0.01885643682488447,
          "caerus_polaris": -0.004079834178644326,
          "caerus_polaris_alpha": 0.005594440900910325,
          "date": "2026-04-16"
        },
        {
          "caerus_lyra": -0.013231782407655324,
          "caerus_orion": -0.0013918503639953617,
          "caerus_orion_alpha": 0.01032890984107726,
          "caerus_polaris": -0.009628052625673567,
          "caerus_polaris_alpha": -0.004910091070978062,
          "date": "2026-04-17"
        },
        {
          "caerus_lyra": -0.021907391346386862,
          "caerus_orion": -0.01244325071161434,
          "caerus_orion_alpha": 0.0016185126610444023,
          "caerus_polaris": -0.006545056221280099,
          "caerus_polaris_alpha": -0.00853832976361657,
          "date": "2026-04-20"
        },
        {
          "caerus_lyra": 0.029565856064865503,
          "caerus_orion": 0.035224197181261685,
          "caerus_orion_alpha": 0.04660988517705866,
          "caerus_polaris": 0.04194935654185494,
          "caerus_polaris_alpha": 0.033918386626086816,
          "date": "2026-04-21"
        },
        {
          "caerus_lyra": 0.037612665238065945,
          "caerus_orion": 0.03907448025270277,
          "caerus_orion_alpha": 0.05857478521526782,
          "caerus_polaris": 0.050573724641706175,
          "caerus_polaris_alpha": 0.03775783737084115,
          "date": "2026-04-22"
        },
        {
          "caerus_lyra": 0.04246923982335704,
          "caerus_orion": 0.04121614031880361,
          "caerus_orion_alpha": 0.05652542519251802,
          "caerus_polaris": 0.05781590753885668,
          "caerus_polaris_alpha": 0.04385164571789146,
          "date": "2026-04-23"
        },
        {
          "caerus_lyra": 0.05128512286267051,
          "caerus_orion": 0.05170581184797807,
          "caerus_orion_alpha": 0.07662966526750448,
          "caerus_polaris": 0.04906730910982704,
          "caerus_polaris_alpha": 0.0563707357463652,
          "date": "2026-04-24"
        },
        {
          "caerus_lyra": 0.0036015696337092784,
          "caerus_orion": 0.016071886366576305,
          "caerus_orion_alpha": 0.033101352387463034,
          "caerus_polaris": 0.0054244654347939125,
          "caerus_polaris_alpha": 0.006229383072352324,
          "date": "2026-04-27"
        },
        {
          "caerus_lyra": 0.019430131128409744,
          "caerus_orion": 0.031499653500607794,
          "caerus_orion_alpha": 0.058671695936770796,
          "caerus_polaris": 0.006092082745586369,
          "caerus_polaris_alpha": 0.021486318809709193,
          "date": "2026-04-28"
        },
        {
          "caerus_lyra": 0.04292374130412968,
          "caerus_orion": 0.046218728415578614,
          "caerus_orion_alpha": 0.06047054182789102,
          "caerus_polaris": 0.02289093384818197,
          "caerus_polaris_alpha": 0.024753182472048385,
          "date": "2026-04-29"
        },
        {
          "caerus_lyra": 0.04936040646812767,
          "caerus_orion": 0.05965628082213481,
          "caerus_orion_alpha": 0.08938952650253529,
          "caerus_polaris": 0.00869885337977494,
          "caerus_polaris_alpha": 0.032659673088858865,
          "date": "2026-04-30"
        },
        {
          "caerus_lyra": 0.07386676079419341,
          "caerus_orion": 0.08359583504604329,
          "caerus_orion_alpha": 0.10560245759100129,
          "caerus_polaris": 0.03226095709529986,
          "caerus_polaris_alpha": 0.05499538194735942,
          "date": "2026-05-01"
        },
        {
          "caerus_lyra": 0.15176774590412934,
          "caerus_orion": 0.15986061238027216,
          "caerus_orion_alpha": 0.1770542716009289,
          "caerus_polaris": 0.11097694321988971,
          "caerus_polaris_alpha": 0.13034028564560574,
          "date": "2026-05-04"
        },
        {
          "caerus_lyra": 0.14379194883555435,
          "caerus_orion": 0.14279781703452565,
          "caerus_orion_alpha": 0.13568559705898786,
          "caerus_polaris": 0.11772305039257369,
          "caerus_polaris_alpha": 0.12323626554778566,
          "date": "2026-05-05"
        },
        {
          "caerus_lyra": 0.09532907907925847,
          "caerus_orion": 0.09481272278320385,
          "caerus_orion_alpha": 0.09413807709247535,
          "caerus_polaris": 0.059787028171041534,
          "caerus_polaris_alpha": 0.09438884487261867,
          "date": "2026-05-06"
        },
        {
          "caerus_lyra": 0.12383212499459528,
          "caerus_orion": 0.11609699821837993,
          "caerus_orion_alpha": 0.11288635996582208,
          "caerus_polaris": 0.1080113415919699,
          "caerus_polaris_alpha": 0.12272780088944746,
          "date": "2026-05-07"
        },
        {
          "caerus_lyra": 0.16367900015163817,
          "caerus_orion": 0.13357856325145678,
          "caerus_orion_alpha": 0.1347525399952838,
          "caerus_polaris": 0.13774196823225715,
          "caerus_polaris_alpha": 0.16136940489011886,
          "date": "2026-05-08"
        },
        {
          "caerus_lyra": 0.078179297571348,
          "caerus_orion": 0.04952214570668989,
          "caerus_orion_alpha": 0.05446699683097078,
          "caerus_polaris": 0.05831443458626051,
          "caerus_polaris_alpha": 0.08378403944839974,
          "date": "2026-05-11"
        },
        {
          "caerus_lyra": 0.0684314095569174,
          "caerus_orion": 0.0406693255423225,
          "caerus_orion_alpha": 0.05500851415567509,
          "caerus_polaris": 0.03521457102481618,
          "caerus_polaris_alpha": 0.06925853891334888,
          "date": "2026-05-12"
        },
        {
          "caerus_lyra": 0.06939457362242218,
          "caerus_orion": 0.04727266360211657,
          "caerus_orion_alpha": 0.053556845074083315,
          "caerus_polaris": 0.05208743675289251,
          "caerus_polaris_alpha": 0.06681164544133034,
          "date": "2026-05-13"
        },
        {
          "caerus_lyra": -0.004915909720930256,
          "caerus_orion": -0.010273109438201478,
          "caerus_orion_alpha": -0.0033218535217571787,
          "caerus_polaris": -0.018773334793744656,
          "caerus_polaris_alpha": 0.0026931273668326927,
          "date": "2026-05-14"
        },
        {
          "caerus_lyra": -0.11233391952950156,
          "caerus_orion": -0.0857638308429618,
          "caerus_orion_alpha": -0.09103451358350023,
          "caerus_polaris": -0.0870240321743524,
          "caerus_polaris_alpha": -0.10027223214761516,
          "date": "2026-05-15"
        },
        {
          "caerus_lyra": -0.06960053265341126,
          "caerus_orion": -0.0551571384336087,
          "caerus_orion_alpha": -0.05599692086665664,
          "caerus_polaris": -0.05586144114006475,
          "caerus_polaris_alpha": -0.06601729888695274,
          "date": "2026-05-18"
        },
        {
          "caerus_lyra": -0.0654822593304254,
          "caerus_orion": -0.04729170472242106,
          "caerus_orion_alpha": -0.05822798898428383,
          "caerus_polaris": -0.040369051207982065,
          "caerus_polaris_alpha": -0.0713068510320457,
          "date": "2026-05-19"
        },
        {
          "caerus_lyra": -0.007837635627843342,
          "caerus_orion": 0.00733356326180179,
          "caerus_orion_alpha": 0.004517187409777579,
          "caerus_polaris": -0.004846540134263444,
          "caerus_polaris_alpha": -0.010777452557450995,
          "date": "2026-05-20"
        },
        {
          "caerus_lyra": 0.02267062438623424,
          "caerus_orion": 0.019551125353149512,
          "caerus_orion_alpha": 0.007886427701341292,
          "caerus_polaris": 0.024136450889524763,
          "caerus_polaris_alpha": 0.007019435791247641,
          "date": "2026-05-21"
        },
        {
          "caerus_lyra": 0.15219109899519712,
          "caerus_orion": 0.1338164980440395,
          "caerus_orion_alpha": 0.13170137047042174,
          "caerus_polaris": 0.11614048566653046,
          "caerus_polaris_alpha": 0.12215503186924437,
          "date": "2026-05-22"
        },
        {
          "caerus_lyra": 0.15023194449552646,
          "caerus_orion": 0.14529896284588673,
          "caerus_orion_alpha": 0.14460529798865074,
          "caerus_polaris": 0.10823977496870052,
          "caerus_polaris_alpha": 0.1308620838070338,
          "date": "2026-05-26"
        },
        {
          "caerus_lyra": 0.10459380219240755,
          "caerus_orion": 0.11388196447484522,
          "caerus_orion_alpha": 0.12858159954686932,
          "caerus_polaris": 0.06847452093463358,
          "caerus_polaris_alpha": 0.11134669072992875,
          "date": "2026-05-27"
        },
        {
          "caerus_lyra": 0.05133297627583944,
          "caerus_orion": 0.08033300588989212,
          "caerus_orion_alpha": 0.09289299437159015,
          "caerus_polaris": 0.03324761243110985,
          "caerus_polaris_alpha": 0.058779221427738726,
          "date": "2026-05-28"
        },
        {
          "caerus_lyra": 0.06627427988103207,
          "caerus_orion": 0.1158716965148352,
          "caerus_orion_alpha": 0.13820320936134411,
          "caerus_polaris": 0.03779825528789993,
          "caerus_polaris_alpha": 0.0815958848127718,
          "date": "2026-05-29"
        },
        {
          "caerus_lyra": 0.027378662695163536,
          "caerus_orion": 0.054507931568799295,
          "caerus_orion_alpha": 0.06752511590234556,
          "caerus_polaris": 0.01535704238029334,
          "caerus_polaris_alpha": 0.02268603189480012,
          "date": "2026-06-01"
        },
        {
          "caerus_lyra": 0.06322645861850229,
          "caerus_orion": 0.08968934615957425,
          "caerus_orion_alpha": 0.08488829374007434,
          "caerus_polaris": 0.06002517780590022,
          "caerus_polaris_alpha": 0.05084994045948843,
          "date": "2026-06-02"
        },
        {
          "caerus_lyra": 0.04201577060243067,
          "caerus_orion": 0.057575824247020346,
          "caerus_orion_alpha": 0.050794215859022884,
          "caerus_polaris": 0.0453697852592132,
          "caerus_polaris_alpha": 0.024550552071292975,
          "date": "2026-06-03"
        },
        {
          "caerus_lyra": -0.04133509053800444,
          "caerus_orion": -0.037854466972637724,
          "caerus_orion_alpha": -0.01934071090566314,
          "caerus_polaris": -0.01818816937773482,
          "caerus_polaris_alpha": -0.03754753241663367,
          "date": "2026-06-04"
        },
        {
          "caerus_lyra": 0.007540970594898888,
          "caerus_orion": -0.00022873225050790147,
          "caerus_orion_alpha": -0.015886599955035607,
          "caerus_polaris": 0.02467967317067865,
          "caerus_polaris_alpha": -0.004753668185915494,
          "date": "2026-06-05"
        }
      ],
      "status": "OK",
      "strategies": [
        {
          "alpha_per_dollar_deployed_proxy": 34.707049,
          "avg_cash_weight": 0.0,
          "avg_effective_n": 10.0,
          "avg_hhi": 0.1,
          "avg_top_3_concentration": 0.3,
          "avg_turnover": 0.0785714286,
          "cumulative_return": 38.7536617571,
          "daily_return": 0.0582178463,
          "data_reason": null,
          "data_status": "OK",
          "excess_return_vs_spy": 34.7070492556,
          "failed_criteria": [
            "INSUFFICIENT_VALID_DAYS"
          ],
          "max_drawdown": -0.1083554602,
          "name": "Caerus Polaris",
          "promotion_readiness": "CONTROL",
          "realized_volatility_ann": 0.5602559725,
          "role": "CONTROL",
          "rolling_20d_excess": 0.06879900303971054,
          "rolling_5d_excess": 0.02467967317067865,
          "slug": "caerus_polaris",
          "status": "OK",
          "valid_evaluation_days": 28
        },
        {
          "alpha_per_dollar_deployed_proxy": -4.984764,
          "avg_cash_weight": 0.2,
          "avg_effective_n": 4.0,
          "avg_hhi": 0.25,
          "avg_top_3_concentration": 0.6,
          "avg_turnover": 0.0,
          "cumulative_return": 0.0588013215,
          "daily_return": 0.0588013215,
          "data_reason": null,
          "data_status": "OK",
          "excess_return_vs_spy": -3.98781118,
          "failed_criteria": [
            "INSUFFICIENT_VALID_DAYS",
            "BEHIND_POLARIS_EXCESS"
          ],
          "max_drawdown": 0.0,
          "name": "Polaris_Alpha",
          "promotion_readiness": "NOT_READY",
          "realized_volatility_ann": null,
          "role": "CHALLENGER",
          "rolling_20d_excess": 0.0859088597205433,
          "rolling_5d_excess": -0.004753668185915494,
          "slug": "caerus_polaris_alpha",
          "status": "OK",
          "valid_evaluation_days": 1
        },
        {
          "alpha_per_dollar_deployed_proxy": 167.713982,
          "avg_cash_weight": 0.0,
          "avg_effective_n": 5.0,
          "avg_hhi": 0.2,
          "avg_top_3_concentration": 0.6,
          "avg_turnover": 0.0142857143,
          "cumulative_return": 171.7605944241,
          "daily_return": 0.0749711904,
          "data_reason": null,
          "data_status": "OK",
          "excess_return_vs_spy": 167.7139819226,
          "failed_criteria": [
            "INSUFFICIENT_VALID_DAYS"
          ],
          "max_drawdown": -0.1352922746,
          "name": "Caerus Orion",
          "promotion_readiness": "WATCHLIST",
          "realized_volatility_ann": 0.6119340545,
          "role": "CHALLENGER",
          "rolling_20d_excess": 0.12439459150849785,
          "rolling_5d_excess": -0.00022873225050790147,
          "slug": "caerus_orion",
          "status": "OK",
          "valid_evaluation_days": 28
        },
        {
          "alpha_per_dollar_deployed_proxy": -5.337749,
          "avg_cash_weight": 0.25,
          "avg_effective_n": 3.000003,
          "avg_hhi": 0.333333,
          "avg_top_3_concentration": 0.75,
          "avg_turnover": 0.0,
          "cumulative_return": 0.0433009774,
          "daily_return": 0.0433009774,
          "data_reason": null,
          "data_status": "OK",
          "excess_return_vs_spy": -4.0033115241,
          "failed_criteria": [
            "INSUFFICIENT_VALID_DAYS",
            "BEHIND_POLARIS_EXCESS"
          ],
          "max_drawdown": 0.0,
          "name": "Orion_Alpha",
          "promotion_readiness": "NOT_READY",
          "realized_volatility_ann": null,
          "role": "CHALLENGER",
          "rolling_20d_excess": 0.12321958212035122,
          "rolling_5d_excess": -0.015886599955035607,
          "slug": "caerus_orion_alpha",
          "status": "OK",
          "valid_evaluation_days": 1
        },
        {
          "alpha_per_dollar_deployed_proxy": 172.231761,
          "avg_cash_weight": 0.0,
          "avg_effective_n": 5.0,
          "avg_hhi": 0.2,
          "avg_top_3_concentration": 0.6,
          "avg_turnover": 0.0285714286,
          "cumulative_return": 176.278373003,
          "daily_return": 0.0713139924,
          "data_reason": null,
          "data_status": "OK",
          "excess_return_vs_spy": 172.2317605015,
          "failed_criteria": [
            "INSUFFICIENT_VALID_DAYS"
          ],
          "max_drawdown": -0.1348606728,
          "name": "Caerus Lyra",
          "promotion_readiness": "WATCHLIST",
          "realized_volatility_ann": 0.6490185521,
          "role": "CHALLENGER",
          "rolling_20d_excess": 0.09269407497927173,
          "rolling_5d_excess": 0.007540970594898888,
          "slug": "caerus_lyra",
          "status": "OK",
          "valid_evaluation_days": 28
        }
      ],
      "summary": {
        "benchmark": "SPY",
        "candidate_count": 4,
        "control": "caerus_polaris",
        "latest_nav_date": "2026-06-05"
      }
    },
    "sleeve_inventory": {
      "as_of": "2026-06-08",
      "is_stale": true,
      "rows": [
        {
          "alpha_per_dollar_proxy": 34.707049,
          "artifact_status": "PRESENT",
          "baseline_strategy_id": null,
          "concentration": 0.3,
          "construction": {},
          "current_lifecycle_status": "paper",
          "data_status": "OK",
          "display_name": "Caerus Polaris",
          "drawdown": -0.1083554602,
          "effective_n": 10.0,
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
          "since_inception_return": 38.7536617571,
          "sleeve_id": "polaris",
          "source_variant": "baseline_top10_daily",
          "strategy_id": "caerus_polaris",
          "strategy_type": "security_selection",
          "today_return": 0.0582178463,
          "turnover": 0.0785714286,
          "variant_class": "baseline"
        },
        {
          "alpha_per_dollar_proxy": -4.984764,
          "artifact_status": "PRESENT",
          "baseline_strategy_id": "caerus_polaris",
          "concentration": 0.6,
          "construction": {
            "cash_residual_allowed": true,
            "max_position_weight": 0.2,
            "top_n": 4,
            "weighting": "equal"
          },
          "current_lifecycle_status": "shadow",
          "data_status": "OK",
          "display_name": "Polaris_Alpha",
          "drawdown": 0.0,
          "effective_n": 4.0,
          "eligible_for_promotion": true,
          "eligible_for_shadow": true,
          "execution_impact": "NON_EXECUTIONAL",
          "family": "core_momentum",
          "lifecycle_stage": "shadow",
          "manifest_lifecycle_stage": "shadow_observed",
          "promotion_readiness": "NOT_READY",
          "review_checkpoints": [
            {
              "observed_days": 1,
              "status": "IN_PROGRESS",
              "trading_days": 20
            },
            {
              "observed_days": 1,
              "status": "IN_PROGRESS",
              "trading_days": 60
            }
          ],
          "role": "challenger",
          "short_name": "polaris_alpha",
          "since_inception_return": 0.0588013215,
          "sleeve_id": "polaris_alpha",
          "source_variant": "polaris_alpha_top4_cap20_daily",
          "strategy_id": "caerus_polaris_alpha",
          "strategy_type": "security_selection",
          "today_return": 0.0588013215,
          "turnover": 0.0,
          "variant_class": "alpha"
        },
        {
          "alpha_per_dollar_proxy": 167.713982,
          "artifact_status": "PRESENT",
          "baseline_strategy_id": null,
          "concentration": 0.6,
          "construction": {},
          "current_lifecycle_status": "shadow",
          "data_status": "OK",
          "display_name": "Caerus Orion",
          "drawdown": -0.1352922746,
          "effective_n": 5.0,
          "eligible_for_promotion": true,
          "eligible_for_shadow": true,
          "execution_impact": "NON_EXECUTIONAL",
          "family": "core_momentum",
          "lifecycle_stage": "shadow",
          "manifest_lifecycle_stage": "shadow_observed",
          "promotion_readiness": "WATCHLIST",
          "review_checkpoints": [],
          "role": "challenger",
          "short_name": "orion",
          "since_inception_return": 171.7605944241,
          "sleeve_id": "orion",
          "source_variant": "h2_rank_decay_exit_h6_top5",
          "strategy_id": "caerus_orion",
          "strategy_type": "security_selection",
          "today_return": 0.0749711904,
          "turnover": 0.0142857143,
          "variant_class": "standard"
        },
        {
          "alpha_per_dollar_proxy": -5.337749,
          "artifact_status": "PRESENT",
          "baseline_strategy_id": "caerus_orion",
          "concentration": 0.75,
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
          "drawdown": 0.0,
          "effective_n": 3.000003,
          "eligible_for_promotion": true,
          "eligible_for_shadow": true,
          "execution_impact": "NON_EXECUTIONAL",
          "family": "core_momentum",
          "lifecycle_stage": "shadow",
          "manifest_lifecycle_stage": "shadow_observed",
          "promotion_readiness": "NOT_READY",
          "review_checkpoints": [
            {
              "observed_days": 1,
              "status": "IN_PROGRESS",
              "trading_days": 20
            },
            {
              "observed_days": 1,
              "status": "IN_PROGRESS",
              "trading_days": 60
            }
          ],
          "role": "challenger",
          "short_name": "orion_alpha",
          "since_inception_return": 0.0433009774,
          "sleeve_id": "orion_alpha",
          "source_variant": "orion_alpha_rank_decay_top3_cap25",
          "strategy_id": "caerus_orion_alpha",
          "strategy_type": "security_selection",
          "today_return": 0.0433009774,
          "turnover": 0.0,
          "variant_class": "alpha"
        },
        {
          "alpha_per_dollar_proxy": 172.231761,
          "artifact_status": "PRESENT",
          "baseline_strategy_id": null,
          "concentration": 0.6,
          "construction": {},
          "current_lifecycle_status": "shadow",
          "data_status": "OK",
          "display_name": "Caerus Lyra",
          "drawdown": -0.1348606728,
          "effective_n": 5.0,
          "eligible_for_promotion": true,
          "eligible_for_shadow": true,
          "execution_impact": "NON_EXECUTIONAL",
          "family": "core_momentum",
          "lifecycle_stage": "shadow",
          "manifest_lifecycle_stage": "shadow_observed",
          "promotion_readiness": "WATCHLIST",
          "review_checkpoints": [],
          "role": "challenger",
          "short_name": "lyra",
          "since_inception_return": 176.278373003,
          "sleeve_id": "lyra",
          "source_variant": "h1_weekly_h6_top5",
          "strategy_id": "caerus_lyra",
          "strategy_type": "security_selection",
          "today_return": 0.0713139924,
          "turnover": 0.0285714286,
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
      "as_of": "2026-06-23T18:57:36+00:00",
      "checks": [
        {
          "detail": "0 fail \u00b7 2 warn \u00b7 6 checks",
          "name": "Daily health",
          "status": "WARN"
        },
        {
          "detail": "max cache 2026-06-08",
          "name": "Hydration",
          "status": "OK"
        },
        {
          "detail": "generated 2026-04-30T15:15:51Z",
          "name": "Reconciliation",
          "status": "NOT_COMPARABLE"
        },
        {
          "detail": "1 errors \u00b7 1 warnings",
          "name": "Dashboard validation",
          "status": "canonical"
        },
        {
          "detail": "outputs/runs/2026-04-10T164001-0400_0d4dcad/trading_day_summary.json",
          "name": "Latest execution artifact",
          "status": "PRESENT"
        }
      ],
      "is_stale": true,
      "summary": {
        "failed_pipeline_count": 0,
        "hydration_max_cache_date": "2026-06-08",
        "latest_successful_execution": "outputs/runs/2026-04-10T164001-0400_0d4dcad/trading_day_summary.json",
        "shadow_generation_date": "2026-06-08",
        "status": "WARN",
        "warning_count": 3
      }
    },
    "trades_today": {
      "as_of": "2026-04-09T13:50:35.044822+00:00",
      "is_stale": true,
      "rows": [
        {
          "client_order_id": null,
          "fill_price": 127.4,
          "filled_at": "2026-04-09T13:35:27.091548Z",
          "notional": 127.4,
          "order_id": "26d57be2-3310-460b-a8e2-b7b8b38f3822",
          "qty": 1.0,
          "side": "sell",
          "source_execution_id": "20260409093527091::b67f0515-6931-45b7-8b4f-4aff87c59c42",
          "ticker": "COP"
        },
        {
          "client_order_id": null,
          "fill_price": 219.84,
          "filled_at": "2026-04-09T13:35:27.336027Z",
          "notional": 219.84,
          "order_id": "8ccaf70c-d20a-423b-9fe0-beb51453c689",
          "qty": 1.0,
          "side": "sell",
          "source_execution_id": "20260409093527336::206e949e-3778-4fb1-871c-b9f2dc04eed1",
          "ticker": "PNC"
        },
        {
          "client_order_id": null,
          "fill_price": 75.67,
          "filled_at": "2026-04-09T13:35:27.727294Z",
          "notional": 151.34,
          "order_id": "727c678b-d0cb-4211-898d-21ee4d8b4cd0",
          "qty": 2.0,
          "side": "sell",
          "source_execution_id": "20260409093527727::700e0044-3fd4-4558-b3e4-7650872557e6",
          "ticker": "GM"
        },
        {
          "client_order_id": null,
          "fill_price": 123.79,
          "filled_at": "2026-04-09T13:35:27.739359Z",
          "notional": 247.58,
          "order_id": "d171c760-1c06-467d-8d03-4ffa28fdad5f",
          "qty": 2.0,
          "side": "sell",
          "source_execution_id": "20260409093527739::c070c6ab-100e-4974-b09f-e427c3beddf5",
          "ticker": "MRK"
        },
        {
          "client_order_id": null,
          "fill_price": 49.4,
          "filled_at": "2026-04-09T13:35:27.776043Z",
          "notional": 49.4,
          "order_id": "3ea56dd3-5909-4ccb-8bc1-f726d0d49cb5",
          "qty": 1.0,
          "side": "sell",
          "source_execution_id": "20260409093527776::996f023b-2a7d-42f1-ae98-4749fc61b9e4",
          "ticker": "TFC"
        },
        {
          "client_order_id": null,
          "fill_price": 122.5,
          "filled_at": "2026-04-09T13:35:27.927139Z",
          "notional": 122.5,
          "order_id": "8d9da9d2-11d0-4d9b-9652-3b0ac618263d",
          "qty": 1.0,
          "side": "sell",
          "source_execution_id": "20260409093527927::84d0355b-b2b5-4cb3-be46-ffa45e3b3306",
          "ticker": "C"
        },
        {
          "client_order_id": null,
          "fill_price": 140.89,
          "filled_at": "2026-04-09T13:35:28.084745Z",
          "notional": 563.56,
          "order_id": "c182c2ab-5af4-49f8-8710-48e9166953b6",
          "qty": 4.0,
          "side": "sell",
          "source_execution_id": "20260409093528084::e156b6e8-a1c5-44bb-b0cc-c975f2d2f0e3",
          "ticker": "GILD"
        },
        {
          "client_order_id": null,
          "fill_price": 219.84,
          "filled_at": "2026-04-09T13:35:28.462122Z",
          "notional": 219.84,
          "order_id": "8ccaf70c-d20a-423b-9fe0-beb51453c689",
          "qty": 1.0,
          "side": "sell",
          "source_execution_id": "20260409093528462::26f927ac-c8d2-4402-b47a-8d67b9f83dd1",
          "ticker": "PNC"
        },
        {
          "client_order_id": null,
          "fill_price": 123.86,
          "filled_at": "2026-04-09T13:35:28.882584Z",
          "notional": 123.86,
          "order_id": "d171c760-1c06-467d-8d03-4ffa28fdad5f",
          "qty": 1.0,
          "side": "sell",
          "source_execution_id": "20260409093528882::0ae43282-8172-4ce5-8d86-1aa3844166cb",
          "ticker": "MRK"
        },
        {
          "client_order_id": null,
          "fill_price": 49.4,
          "filled_at": "2026-04-09T13:35:28.907249Z",
          "notional": 148.2,
          "order_id": "3ea56dd3-5909-4ccb-8bc1-f726d0d49cb5",
          "qty": 3.0,
          "side": "sell",
          "source_execution_id": "20260409093528907::eb2d80ed-2242-4d48-953c-68052e2c7168",
          "ticker": "TFC"
        },
        {
          "client_order_id": null,
          "fill_price": 122.42,
          "filled_at": "2026-04-09T13:35:29.019475Z",
          "notional": 244.84,
          "order_id": "8d9da9d2-11d0-4d9b-9652-3b0ac618263d",
          "qty": 2.0,
          "side": "sell",
          "source_execution_id": "20260409093529019::ae64f4ea-7108-44f7-af71-f537f8b8551c",
          "ticker": "C"
        },
        {
          "client_order_id": null,
          "fill_price": 140.89,
          "filled_at": "2026-04-09T13:35:29.157749Z",
          "notional": 140.89,
          "order_id": "c182c2ab-5af4-49f8-8710-48e9166953b6",
          "qty": 1.0,
          "side": "sell",
          "source_execution_id": "20260409093529157::961d7be3-9343-4102-ae1b-0e139a53232e",
          "ticker": "GILD"
        },
        {
          "client_order_id": null,
          "fill_price": 49.4,
          "filled_at": "2026-04-09T13:35:29.597397Z",
          "notional": 296.4,
          "order_id": "3ea56dd3-5909-4ccb-8bc1-f726d0d49cb5",
          "qty": 6.0,
          "side": "sell",
          "source_execution_id": "20260409093529597::0fb4fb52-9b8f-4f6e-8b8b-1167c5738989",
          "ticker": "TFC"
        },
        {
          "client_order_id": null,
          "fill_price": 122.42,
          "filled_at": "2026-04-09T13:35:29.684837Z",
          "notional": 122.42,
          "order_id": "8d9da9d2-11d0-4d9b-9652-3b0ac618263d",
          "qty": 1.0,
          "side": "sell",
          "source_execution_id": "20260409093529684::c496d068-b538-4486-a078-eeed63f45d77",
          "ticker": "C"
        },
        {
          "client_order_id": null,
          "fill_price": 232.5,
          "filled_at": "2026-04-09T13:35:33.493421Z",
          "notional": 465.0,
          "order_id": "e2e856bc-921e-49ec-aec8-f8ceba1ed341",
          "qty": 2.0,
          "side": "buy",
          "source_execution_id": "20260409093533493::638b6f83-0df4-49ab-b26d-cd0a5a37293f",
          "ticker": "ADSK"
        },
        {
          "client_order_id": null,
          "fill_price": 178.0,
          "filled_at": "2026-04-09T13:35:33.825099Z",
          "notional": 178.0,
          "order_id": "e0c10f9f-6586-4ca9-b9a7-6ec9310b42c6",
          "qty": 1.0,
          "side": "buy",
          "source_execution_id": "20260409093533825::8bf2a739-7d16-46a6-888d-499d4402918d",
          "ticker": "BKNG"
        },
        {
          "client_order_id": null,
          "fill_price": 352.0,
          "filled_at": "2026-04-09T13:35:34.000754Z",
          "notional": 352.0,
          "order_id": "1773ad65-4ce5-4969-ad25-1d935591111d",
          "qty": 1.0,
          "side": "buy",
          "source_execution_id": "20260409093534000::5e2300f5-c2b1-46bb-8c3d-64adb8009af9",
          "ticker": "AMGN"
        },
        {
          "client_order_id": null,
          "fill_price": 156.83,
          "filled_at": "2026-04-09T13:35:34.186233Z",
          "notional": 627.32,
          "order_id": "14b67e33-964c-4270-bc53-626b1a614323",
          "qty": 4.0,
          "side": "buy",
          "source_execution_id": "20260409093534186::461c3057-45cd-411f-a966-f34023b9b2fc",
          "ticker": "BDX"
        },
        {
          "client_order_id": null,
          "fill_price": 170.01,
          "filled_at": "2026-04-09T13:35:34.488197Z",
          "notional": 170.01,
          "order_id": "fc54ff02-4a46-4c95-82da-9689ee43e72d",
          "qty": 1.0,
          "side": "buy",
          "source_execution_id": "20260409093534488::14508164-e8e8-4b48-b84d-4a749af43d85",
          "ticker": "PANW"
        },
        {
          "client_order_id": null,
          "fill_price": 178.0,
          "filled_at": "2026-04-09T13:35:34.865078Z",
          "notional": 178.0,
          "order_id": "e0c10f9f-6586-4ca9-b9a7-6ec9310b42c6",
          "qty": 1.0,
          "side": "buy",
          "source_execution_id": "20260409093534865::9928d04a-ef6e-4608-949d-6d31da743a93",
          "ticker": "BKNG"
        }
      ],
      "source_type": "alpaca_fills",
      "summary": {
        "buy_count": 6,
        "buy_notional": 1970.33,
        "fills_count": 20,
        "sell_count": 14,
        "sell_notional": 2778.07
      },
      "trust_level": "canonical"
    }
  },
  "sources": [
    {
      "as_of": "2026-04-09T13:50:35.044822+00:00",
      "label": "broker account snapshot",
      "path": "outputs/broker/broker_snapshot_latest.json",
      "section": "nav",
      "source_type": "broker_account",
      "trust_level": "authoritative",
      "used": true
    },
    {
      "as_of": "2026-04-09T13:50:35.044822+00:00",
      "label": "broker positions snapshot",
      "path": "outputs/broker/posttrade_positions.json",
      "section": "positions",
      "source_type": "broker_positions",
      "trust_level": "authoritative",
      "used": true
    },
    {
      "as_of": "2026-04-09T13:50:35.044822+00:00",
      "label": "alpaca fills snapshot",
      "path": "outputs/broker_snapshot/broker_snapshot_2026-04-09.json",
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
      "as_of": "2026-06-08",
      "label": "shadow evaluation",
      "path": "outputs/shadow_candidates/2026-06-08/shadow_evaluation.json",
      "section": "shadow_command_center",
      "source_type": "shadow_evaluation",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": "2026-06-05",
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
      "as_of": "2026-04-09",
      "label": "engine review",
      "path": "outputs/engine_review/live_regime_review_latest.json",
      "section": "regime_market_state",
      "source_type": "engine_review",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": "2026-06-20T12:05:32+00:00",
      "label": "live pilot plan",
      "path": "outputs/live_pilot/plans/live_pilot_plan_2026-03-24.json",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "runtime",
      "used": true
    },
    {
      "as_of": null,
      "label": "live pilot preflight",
      "path": "",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "missing",
      "used": false
    },
    {
      "as_of": null,
      "label": "live pilot operator summary",
      "path": "",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "missing",
      "used": false
    },
    {
      "as_of": null,
      "label": "live pilot evidence metrics",
      "path": "",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "missing",
      "used": false
    },
    {
      "as_of": null,
      "label": "live pilot reconciliation",
      "path": "",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "missing",
      "used": false
    },
    {
      "as_of": null,
      "label": "live pilot submitted orders",
      "path": "",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "missing",
      "used": false
    },
    {
      "as_of": null,
      "label": "live pilot open order check",
      "path": "",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "missing",
      "used": false
    },
    {
      "as_of": null,
      "label": "live pilot broker snapshot",
      "path": "",
      "section": "live_pilot",
      "source_type": "live_pilot_artifact",
      "trust_level": "missing",
      "used": false
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
      "as_of": "2026-04-28",
      "label": "daily health check",
      "path": "outputs/health/caerus_daily_health_check/latest/health_check.json",
      "section": "system_health_console",
      "source_type": "health_check",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": "2026-06-08",
      "label": "hydration status",
      "path": "outputs/price_hydration/2026-06-08/status.json",
      "section": "system_health_console",
      "source_type": "price_hydration",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": "2026-04-30T15:15:51Z",
      "label": "live vs shadow reconciliation",
      "path": "outputs/reconciliation/live_vs_shadow/latest/live_vs_shadow_reconciliation.json",
      "section": "system_health_console",
      "source_type": "reconciliation",
      "trust_level": "diagnostic",
      "used": true
    },
    {
      "as_of": null,
      "label": "model quality packet",
      "path": "outputs/model_quality/2026-04-09/model_quality_packet.json",
      "section": "decision_grade",
      "source_type": "model_quality",
      "trust_level": "missing",
      "used": false
    },
    {
      "as_of": null,
      "label": "model tournament",
      "path": "outputs/model_quality/2026-04-09/model_tournament.json",
      "section": "decision_grade",
      "source_type": "model_quality",
      "trust_level": "missing",
      "used": false
    },
    {
      "as_of": null,
      "label": "argo phase b validation",
      "path": "outputs/model_quality/2026-04-09/argo_phase_b_validation.json",
      "section": "decision_grade",
      "source_type": "model_quality",
      "trust_level": "missing",
      "used": false
    },
    {
      "as_of": null,
      "label": "strategy differentiation deep dive",
      "path": "outputs/model_quality/2026-04-09/strategy_differentiation_deep_dive.json",
      "section": "decision_grade",
      "source_type": "model_quality",
      "trust_level": "missing",
      "used": false
    },
    {
      "as_of": null,
      "label": "phoenix phase b review",
      "path": "outputs/model_quality/2026-04-09/phoenix_phase_b_review.json",
      "section": "decision_grade",
      "source_type": "model_quality",
      "trust_level": "missing",
      "used": false
    },
    {
      "as_of": null,
      "label": "multi asset research framework",
      "path": "outputs/model_quality/2026-04-09/multi_asset_research_framework.json",
      "section": "decision_grade",
      "source_type": "model_quality",
      "trust_level": "missing",
      "used": false
    }
  ],
  "status": {
    "errors": [
      {
        "code": "history_latest_nav_matches_nav_section",
        "message": "Latest portfolio history NAV does not match current NAV."
      }
    ],
    "level": "error",
    "summary": "Blocking validation failed.",
    "warnings": [
      {
        "code": "shadow_nav_current",
        "message": "Shadow NAV latest date lags latest evaluation date."
      },
      {
        "code": "decision_grade_model_quality_present",
        "message": "Decision-grade model-quality artifacts are incomplete."
      },
      {
        "code": "positions_timestamp_fresh",
        "message": "positions timestamp is stale."
      },
      {
        "code": "nav_timestamp_fresh",
        "message": "nav timestamp is stale."
      },
      {
        "code": "trades_today_timestamp_fresh",
        "message": "trades_today timestamp is stale."
      },
      {
        "code": "performance_timestamp_fresh",
        "message": "Performance history latest date lags report date."
      }
    ]
  },
  "terminal": {
    "benchmark": {
      "down_days": 12,
      "excess_since_inception_return": -0.018812624397267275,
      "history_points": 26,
      "max_drawdown": -0.05016799999999999,
      "rolling_20d_excess_return": -0.014470482388119432,
      "rolling_20d_return": -0.019506031549956337,
      "rolling_20d_spy_return": -0.005035549161836905,
      "rolling_5d_excess_return": -0.02470496616366402,
      "rolling_5d_return": 0.01132232987879278,
      "rolling_5d_spy_return": 0.0360272960424568,
      "since_inception_return": -0.028454999999999897,
      "spy_close": 673.77001953125,
      "spy_since_inception_return": -0.009642375602732622,
      "up_days": 12
    },
    "headline": {
      "cash": 2388.18,
      "day_pnl": -38.57,
      "day_return": -0.00395509830321461,
      "fills_count": 20,
      "gross_exposure": 0.7541314060987914,
      "nav": 9713.4,
      "positions_count": 16,
      "validation_status": "error"
    },
    "health": {
      "blocking_failures": 1,
      "sources_total": 28,
      "sources_used": 15,
      "stale_sections": [
        "positions",
        "nav",
        "trades_today",
        "performance_history",
        "shadow_command_center",
        "sleeve_inventory",
        "baseline_alpha_comparison",
        "system_health_console"
      ],
      "warnings": 6
    },
    "leaders": {
      "laggards": [
        {
          "avg_entry_price": 49.821,
          "cost_basis": 498.21,
          "last_price": 48.0,
          "market_value": 480.0,
          "qty": 10.0,
          "side": "long",
          "ticker": "VZ",
          "unrealized_pnl": -18.21,
          "unrealized_pnl_pct": -0.03655,
          "weight": 0.049416270306998585
        },
        {
          "avg_entry_price": 28.2225,
          "cost_basis": 451.56,
          "last_price": 27.335,
          "market_value": 437.36,
          "qty": 16.0,
          "side": "long",
          "ticker": "PFE",
          "unrealized_pnl": -14.2,
          "unrealized_pnl_pct": -0.03145,
          "weight": 0.04502645829472687
        },
        {
          "avg_entry_price": 261.23,
          "cost_basis": 522.46,
          "last_price": 257.62,
          "market_value": 515.24,
          "qty": 2.0,
          "side": "long",
          "ticker": "AAPL",
          "unrealized_pnl": -7.22,
          "unrealized_pnl_pct": -0.01382,
          "weight": 0.05304424815203739
        },
        {
          "avg_entry_price": 232.5,
          "cost_basis": 465.0,
          "last_price": 229.22,
          "market_value": 458.44,
          "qty": 2.0,
          "side": "long",
          "ticker": "ADSK",
          "unrealized_pnl": -6.56,
          "unrealized_pnl_pct": -0.01411,
          "weight": 0.04719665616570923
        },
        {
          "avg_entry_price": 156.83,
          "cost_basis": 627.32,
          "last_price": 155.435,
          "market_value": 621.74,
          "qty": 4.0,
          "side": "long",
          "ticker": "BDX",
          "unrealized_pnl": -5.58,
          "unrealized_pnl_pct": -0.00889,
          "weight": 0.0640084831264027
        }
      ],
      "winners": [
        {
          "avg_entry_price": 73.614286,
          "cost_basis": 883.371432,
          "last_price": 75.77,
          "market_value": 909.24,
          "qty": 12.0,
          "side": "long",
          "ticker": "GM",
          "unrealized_pnl": 25.868568,
          "unrealized_pnl_pct": 0.02928,
          "weight": 0.09360677002903206
        },
        {
          "avg_entry_price": 53.638889,
          "cost_basis": 482.750001,
          "last_price": 55.3,
          "market_value": 497.7,
          "qty": 9.0,
          "side": "long",
          "ticker": "USB",
          "unrealized_pnl": 14.949999,
          "unrealized_pnl_pct": 0.03097,
          "weight": 0.051238495274569154
        },
        {
          "avg_entry_price": 205.47,
          "cost_basis": 410.94,
          "last_price": 212.025,
          "market_value": 424.05,
          "qty": 2.0,
          "side": "long",
          "ticker": "ALL",
          "unrealized_pnl": 13.11,
          "unrealized_pnl_pct": 0.0319,
          "weight": 0.04365618629933906
        },
        {
          "avg_entry_price": 64.64,
          "cost_basis": 452.48,
          "last_price": 66.485,
          "market_value": 465.395,
          "qty": 7.0,
          "side": "long",
          "ticker": "MO",
          "unrealized_pnl": 12.915,
          "unrealized_pnl_pct": 0.02854,
          "weight": 0.047912677332345006
        },
        {
          "avg_entry_price": 81.444,
          "cost_basis": 407.22,
          "last_price": 82.07,
          "market_value": 410.35,
          "qty": 5.0,
          "side": "long",
          "ticker": "FTNT",
          "unrealized_pnl": 3.13,
          "unrealized_pnl_pct": 0.00769,
          "weight": 0.04224576358432681
        }
      ]
    },
    "positioning": {
      "average_position_weight": 0.047133212881174454,
      "cash_ratio": 0.24586447587868304,
      "gross_market_value": 7325.18,
      "invested_ratio": 0.7541314060987914,
      "largest_position_weight": 0.09360677002903206,
      "median_position_weight": 0.047554666749027114,
      "top10_concentration": 0.5694108139271521,
      "top5_concentration": 0.32862025655280336
    },
    "tape": {
      "buy_notional": 1970.33,
      "buy_symbols": [
        "ADSK",
        "AMGN",
        "BDX",
        "BKNG",
        "PANW"
      ],
      "last_fill_at": "2026-04-09T13:35:34.865078Z",
      "net_notional": -807.7400000000002,
      "sell_notional": 2778.07,
      "sell_symbols": [
        "C",
        "COP",
        "GILD",
        "GM",
        "MRK",
        "PNC",
        "TFC"
      ]
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
        "detail": "Latest portfolio history NAV does not match current NAV.",
        "latest_nav": 9715.45,
        "latest_nav_date": "2026-04-08",
        "name": "history_latest_nav_matches_nav_section",
        "nav_equity": 9713.4,
        "report_date": "2026-04-09",
        "severity": "blocking",
        "status": "fail"
      },
      {
        "detail": "Shadow evaluation artifact loaded.",
        "name": "shadow_command_center_source_present",
        "severity": "non_blocking",
        "status": "pass"
      },
      {
        "detail": "Shadow NAV latest date lags latest evaluation date.",
        "evaluation_date": "2026-06-08",
        "latest_nav_date": "2026-06-05",
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
        "detail": "positions timestamp is stale.",
        "name": "positions_timestamp_fresh",
        "severity": "non_blocking",
        "status": "warn"
      },
      {
        "detail": "nav timestamp is stale.",
        "name": "nav_timestamp_fresh",
        "severity": "non_blocking",
        "status": "warn"
      },
      {
        "detail": "trades_today timestamp is stale.",
        "name": "trades_today_timestamp_fresh",
        "severity": "non_blocking",
        "status": "warn"
      },
      {
        "detail": "Performance history latest date lags report date.",
        "name": "performance_timestamp_fresh",
        "severity": "non_blocking",
        "status": "warn"
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
