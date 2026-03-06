# ALPHA LAB V1
Technical Build Plan

Purpose

Create the infrastructure required to measure alpha generated from research signals.

Alpha Lab is the bridge between research discovery and tradable strategies.

------------------------------------------------------------

1. CORE OBJECTIVE

Transform research signals into validated investment strategies.

Pipeline

Research → Signal Registry → Alpha Lab → Strategy Promotion

------------------------------------------------------------

2. DIRECTORY STRUCTURE

alpha_lab/

registry/  
register_signal.py  
signal_schema.py  

evaluation/  
evaluate_signal_returns.py  
calculate_metrics.py  

reports/  
generate_alpha_report.py  

data/

signals/  
signal_performance/  
market_prices/  

outputs/

alpha_reports/

------------------------------------------------------------

3. SIGNAL REGISTRY

Signals must be stored immediately when discovered.

Location

data/signals/

Schema

signal_id  
date  
ticker  
signal_type  
source  
confidence  
entry_rule  
exit_rule  
holding_period  

Example JSON

{
 "signal_id": "AI_INFRA_20260305_SMCI",
 "ticker": "SMCI",
 "date": "2026-03-05",
 "signal_type": "AI_INFRA",
 "confidence": 0.88,
 "entry_rule": "next_open",
 "holding_period": 10
}

------------------------------------------------------------

4. MARKET DATA INTEGRATION

Alpha Lab requires price data.

Possible sources

Polygon  
Alpaca data  
Yahoo Finance  
Tiingo  

Dataset format

date  
ticker  
open  
high  
low  
close  
volume  

Storage

data/market_prices/

------------------------------------------------------------

5. SIGNAL PERFORMANCE EVALUATION

Script

evaluate_signal_returns.py

For each signal

1 determine entry price  
2 compute forward returns  
3 record results  

Metrics computed

1d_return  
3d_return  
5d_return  
10d_return  
20d_return  
max_drawdown  
max_runup  
volatility  

Output file

data/signal_performance/signals_performance.csv

------------------------------------------------------------

6. ALPHA METRICS

Metrics calculated across signals.

Hit Rate

percentage of signals producing positive return

Average Return

mean return across signals

Sharpe Ratio

mean_return / volatility

Maximum Drawdown

worst observed decline

Turnover Impact

how frequently signals trigger trades

------------------------------------------------------------

7. ALPHA LEADERBOARD

Script

generate_alpha_report.py

Outputs

outputs/alpha_reports/alpha_leaderboard.csv  
outputs/alpha_reports/alpha_report.md  

Example leaderboard

Signal | Sharpe | Win Rate | Avg Return  
AI_INFRA | 1.82 | 61% | 4.3%  
EARNINGS_SURPRISE | 1.45 | 58% | 3.1%  

------------------------------------------------------------

8. STRATEGY PROMOTION RULES

Signals become strategies when they meet thresholds.

Example promotion criteria

Sharpe > 1.2  
Win Rate > 55%  
Sample Size > 50 signals  

Promoted strategies move into

strategies/

------------------------------------------------------------

9. STRATEGY TEMPLATE

Example file

strategies/ai_infrastructure_strategy.py

Example logic

if signal_score > 0.75:
    enter_position()

exit_after_days(10)

------------------------------------------------------------

10. WEEKLY ALPHA REPORT

Generated automatically.

Includes

top performing signals  
failing signals  
strategy candidates  
portfolio integration suggestions  

Stored

outputs/alpha_reports/

------------------------------------------------------------

11. PHASE 1 DEVELOPMENT PLAN

Step 1

Build signal registry

register_signal.py

Step 2

Build evaluation engine

evaluate_signal_returns.py

Step 3

Build alpha leaderboard

generate_alpha_report.py

------------------------------------------------------------

12. FUTURE ENHANCEMENTS

Monte Carlo robustness tests  
Factor exposure analysis  
Signal clustering  
Portfolio simulation  
Machine learning signal ranking  

------------------------------------------------------------

13. SUCCESS CRITERIA

Alpha Lab is successful when it can

track every signal discovered  
evaluate signal returns automatically  
rank signals by alpha  
promote winning signals to strategies