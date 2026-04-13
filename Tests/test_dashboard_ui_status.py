from pathlib import Path


def test_run_status_wrapping_rules_present():
    repo_root = Path(__file__).resolve().parents[1]
    js = (repo_root / "web/dashboard/quant_daily_executive.js").read_text(encoding="utf-8")
    css = (repo_root / "web/dashboard/quant_daily_executive.css").read_text(encoding="utf-8")

    assert "function statusLengthClass" in js
    assert "status-wrap" in js
    assert "status-long" in js
    assert "status-very-long" in js

    assert ".kpi-value.status-wrap" in css
    assert ".kpi-value.status-long" in css
    assert ".kpi-value.status-very-long" in css


def test_dashboard_js_supports_query_data_and_refresh():
    repo_root = Path(__file__).resolve().parents[1]
    js = (repo_root / "web/dashboard/quant_daily_executive.js").read_text(encoding="utf-8")

    assert "params.get('data')" in js
    assert "params.get('summary')" in js
    assert "params.get('refresh')" in js
    assert "cache: 'no-store'" in js
    assert "window.setTimeout(() => { void boot(); }" in js
    assert "alignState !== 'aligned' && alignState !== 'overlay'" in js
    assert "case 'overlay': return 'pos';" in js
    assert "reconStatus === 'OVERLAY_ONLY'" in js
    assert "comparisonMode === 'previous_trading_day'" in js
    assert "benchmark unavailable for current date" in js
    assert "SPY as of" in js
    assert "function renderAttribution" in js
    assert "function renderEdgeDiagnostics" in js
    assert "function renderContributionSnapshot" in js


def test_dashboard_index_redirects_to_monitor():
    repo_root = Path(__file__).resolve().parents[1]
    html = (repo_root / "web/dashboard/index.html").read_text(encoding="utf-8")

    assert "quant_daily_executive.html" in html
    assert "window.location.replace" in html


def test_dashboard_html_has_edge_diagnostic_sections():
    repo_root = Path(__file__).resolve().parents[1]
    main_html = (repo_root / "web/dashboard/quant_daily_executive.html").read_text(encoding="utf-8")
    review_html = (repo_root / "web/dashboard/engine_review.html").read_text(encoding="utf-8")

    assert 'id="attribution-stats"' not in main_html
    assert 'id="edge-list"' not in main_html
    assert 'id="contribution-panel"' not in main_html
    assert 'id="attribution-stats"' not in review_html
    assert 'id="signals-list"' in review_html
    assert 'id="recommendations-list"' in review_html
    assert 'id="contribution-panel"' in review_html
