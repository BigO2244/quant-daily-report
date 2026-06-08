from pathlib import Path


def test_dashboard_terminal_shell_present():
    html = Path("web/dashboard/index.html").read_text(encoding="utf-8")

    assert "terminal-shell" in html
    assert 'id="performance-matrix"' in html
    assert 'id="shadow-strategy-body"' in html
    assert 'id="positions-body"' in html
    assert 'id="fills-body"' in html
    assert 'id="decision-grade-summary"' in html


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
