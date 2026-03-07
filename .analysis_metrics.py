#!/usr/bin/env python
import pandas as pd

# Load canonical performance
df = pd.read_csv('outputs/alpha_assessment/canonical_performance.csv')

# Convert numeric columns
numeric_cols = ['strategy_nav', 'strategy_return', 'spy_close', 'spy_return', 'vix_close', 'premarket_score']
for col in numeric_cols:
    df[col] = pd.to_numeric(df[col], errors='coerce')

# Calculate fill rates
total_rows = len(df)
fill_rates = {}

for col in numeric_cols:
    fill_count = df[col].notna().sum()
    fill_pct = (fill_count / total_rows * 100) if total_rows > 0 else 0
    fill_rates[col] = {'count': int(fill_count), 'pct': round(fill_pct, 2)}

# Special fields
vix_regime_pct = (df['vix_regime'].notna().sum() / total_rows * 100) if total_rows > 0 else 0
fill_rates['vix_regime'] = {'count': int(df['vix_regime'].notna().sum()), 'pct': round(vix_regime_pct, 2)}

# Evaluation overlap - rows with both score and spy_return
eval_mask = df['premarket_score'].notna() & df['spy_return'].notna()
eval_count = eval_mask.sum()

print(f"FILL RATE REPORT FOR {total_rows} ROWS")
print("=" * 60)
for field in ['strategy_nav', 'strategy_return', 'spy_close', 'spy_return', 'vix_close', 'vix_regime', 'premarket_score']:
    metrics = fill_rates.get(field, {'count': 0, 'pct': 0})
    print(f"{field:20s}: {metrics['count']:3d} rows ({metrics['pct']:5.1f}%)")
print("=" * 60)
print(f"{'Evaluation overlap':20s}: {int(eval_count):3d} rows (score+spy_return)")

# Classification metrics if we have eval data
if eval_count >= 3:
    df_eval = df[eval_mask].copy()
    df_eval['pred_bearish'] = df_eval['premarket_score'] <= 0.5
    df_eval['actual_down'] = df_eval['spy_return'] < 0
    
    fp = int((df_eval['pred_bearish'] & ~df_eval['actual_down']).sum())
    tn = int((~df_eval['pred_bearish'] & ~df_eval['actual_down']).sum())
    tp = int((df_eval['pred_bearish'] & df_eval['actual_down']).sum())
    fn = int((~df_eval['pred_bearish'] & df_eval['actual_down']).sum())
    
    print("\nCLASSIFICATION METRICS (premarket_score <= 0.5 = bearish):")
    print(f"  True Positive  (pred bearish, spy down):  {tp}")
    print(f"  True Negative  (pred bullish, spy up):    {tn}")
    print(f"  False Positive (pred bearish, spy up):    {fp}")
    print(f"  False Negative (pred bullish, spy down):  {fn}")
    
    accuracy = (tp + tn) / eval_count if eval_count > 0 else 0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    
    print(f"\n  Accuracy:  {accuracy:.3f}")
    print(f"  Precision: {precision:.3f}")
    print(f"  Recall:    {recall:.3f}")
