from pathlib import Path


def test_dashboard_terminal_shell_present():
    html = Path("web/dashboard/index.html").read_text(encoding="utf-8")

    assert "terminal-shell" in html
    assert "CAERUS EVIDENCE CONTROL TOWER" in html
    assert 'id="operator-action-list"' in html
    assert 'id="operator-context-matrix"' in html
    assert 'id="live-pilot-state-strip"' in html
    assert 'id="top-positions-bars"' in html
    assert 'id="sleeve-return-bars"' in html
    assert 'id="sleeve-excess-bars"' in html
    assert 'id="alpha-delta-bars"' in html
    assert 'id="cio-dashboard-section"' in html
    assert 'id="audit-appendix-section"' in html
    assert 'id="performance-matrix"' in html
    assert 'id="shadow-strategy-body"' in html
    assert 'id="positions-body"' in html
    assert 'id="fills-body"' in html
    assert 'id="decision-grade-summary"' in html
    assert 'id="live-pilot-summary"' in html
    assert 'id="sleeve-inventory-body"' in html
    assert 'id="alpha-comparison-body"' in html
    assert 'id="governance-state-list"' in html
    assert html.index('id="top-positions-bars"') < html.index('id="sleeve-inventory-body"')
    assert html.index('id="alpha-delta-bars"') < html.index('id="source-list"')


def test_dashboard_redirect_file_points_to_terminal_root():
    html = Path("web/dashboard/quant_daily_executive.html").read_text(encoding="utf-8")

    assert 'http-equiv="refresh"' in html
    assert 'url=./' in html
    assert "Redirecting to Dashboard V1" in html


def test_dashboard_js_current_render_contract():
    js = Path("web/dashboard/quant_daily_executive.js").read_text(encoding="utf-8")

    assert "function boot()" in js
    assert "function renderShadowCommand" in js
    assert "function renderPositions" in js
    assert "function renderFills" in js
    assert "function renderPerformanceMatrix" in js
    assert "function renderHealthMatrix" in js
    assert "function renderDecisionGrade" in js
    assert "function renderOperatorControlTower" in js
    assert "function renderLivePilot" in js
    assert "function renderTopPositions" in js
    assert "function renderSleeveBarCharts" in js
    assert "function barRow" in js
    assert "function renderSleeveInventory" in js
    assert "function renderAlphaComparison" in js
    assert "function renderGovernanceState" in js


def test_dashboard_css_has_lightweight_bar_charts():
    css = Path("web/dashboard/quant_daily_executive.css").read_text(encoding="utf-8")

    assert ".cockpit-grid" in css
    assert ".cockpit-chart-grid" in css
    assert ".bar-track.signed::before" in css
    assert ".bar-track.positive-only" in css
    assert ".bar-fill.exposure" in css
