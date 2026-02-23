from pathlib import Path
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Alpha Paper Dashboard", layout="wide")

nav_path = Path("outputs/perf/nav_timeseries.csv")
trades_path = Path("trades.csv") if Path("trades.csv").exists() else Path("outputs/ledger/trades.csv")

st.title("Alpha Sleeve — Paper Trading Dashboard")

# ---- NAV ----
if nav_path.exists():
    nav = pd.read_csv(nav_path)
    nav.columns = [c.lower() for c in nav.columns]

    date_col = next(c for c in nav.columns if "date" in c or "dt" in c)
    value_col = "equity" if "equity" in nav.columns else next(
        c for c in nav.columns if "nav" in c or "equity" in c or "portfolio" in c
    )

    nav[date_col] = pd.to_datetime(nav[date_col])
    nav = nav.sort_values(date_col)

    st.line_chart(nav.set_index(date_col)[value_col])
    st.metric("Latest Portfolio Equity", f"${float(nav[value_col].iloc[-1]):,.2f}")
else:
    st.warning("nav_timeseries.csv not found.")

# ---- Trades ----
st.subheader("Recent Trades")

if trades_path.exists():
    tr = pd.read_csv(trades_path)
    st.dataframe(tr.tail(20), use_container_width=True)
else:
    st.warning("trades.csv not found.")
