"""
Alpha Stack — Features Layer
==============================
Derived signal computation on top of the DataStore.

Each module takes raw price/fundamental/macro data and computes
normalised, cross-sectionally ranked features ready for sleeve scoring.

Modules:
    trend       — Momentum, EMA ratios, ATR-adjusted trend signals
    value       — Earnings yield, FCF yield, B/P, SHY (requires fundamentals)
    quality     — ROE, ROIC, leverage, margin stability (requires fundamentals)
    volatility  — Realised vol, ATR, vol regime metrics
    breadth     — % above MA, A/D proxy
"""
