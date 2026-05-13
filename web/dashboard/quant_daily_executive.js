'use strict';

function resolveDataPath() {
  const base = new URL('.', window.location.href);
  const params = new URL(window.location.href).searchParams;
  const dataFile = String(params.get('data') || 'dashboard_data.json').trim() || 'dashboard_data.json';
  return new URL(dataFile, base).href;
}

async function fetchJSON(url) {
  const target = new URL(url, window.location.href);
  target.searchParams.set('_ts', String(Date.now()));
  const response = await fetch(target.href, { cache: 'no-store' });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function fmtMoney(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  });
}

function fmtPct(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const n = Number(v) * 100;
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(2)}%`;
}

function fmtNum(v, decimals = 2) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(decimals);
}

function fmtDateTime(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function fmtTime(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  });
}

function colorClass(value) {
  if (value == null || Number.isNaN(Number(value))) return 'neutral';
  return Number(value) > 0 ? 'pos' : Number(value) < 0 ? 'neg' : 'neutral';
}

function statusClass(value) {
  const text = String(value || '').toUpperCase();
  if (['PASS', 'OK', 'GREEN', 'CONTROL', 'PROMOTION_ELIGIBLE', 'HIGH', 'PRESENT'].includes(text)) return 'pos';
  if (['FAIL', 'ERROR', 'RED', 'NO_DATA', 'NO_PRIOR', 'BROKEN_CHAIN', 'NOT_READY'].includes(text)) return 'neg';
  return 'neutral';
}

function setText(id, value, cls = '') {
  const node = document.getElementById(id);
  if (!node) return;
  node.textContent = value;
  node.className = cls;
}

function setHTML(id, value) {
  const node = document.getElementById(id);
  if (!node) return false;
  node.innerHTML = value;
  return true;
}

function matrixItem(label, value, detail, cls = '') {
  return `
    <div class="matrix-item ${cls}">
      <strong>${label}</strong>
      <div class="matrix-value ${cls}">${value}</div>
      <div class="matrix-detail">${detail || ' '}</div>
    </div>
  `;
}

function rankItem(row) {
  return `
    <div class="rank-item">
      <strong>${row.ticker || '—'}</strong>
      <div class="matrix-value ${colorClass(row.unrealized_pnl)}">${fmtMoney(row.unrealized_pnl)}</div>
      <div class="rank-sub">weight ${fmtPct(row.weight)} · value ${fmtMoney(row.market_value)}</div>
    </div>
  `;
}

function renderStatus(payload) {
  setText('meta-report-date', payload.report_date || '—');
  setText('meta-generated-at', fmtDateTime(payload.generated_at));
  setText('meta-status', String(payload.status?.level || '—').toUpperCase(), colorClass(payload.status?.level === 'ok' ? 1 : payload.status?.level === 'error' ? -1 : 0));
  const banner = document.getElementById('status-banner');
  const parts = [];
  if (payload.status?.summary) parts.push(payload.status.summary);
  const errors = payload.status?.errors || [];
  const warnings = payload.status?.warnings || [];
  if (errors.length) parts.push(`Errors: ${errors.map(item => item.message).join(' | ')}`);
  if (!errors.length && warnings.length) parts.push(`Warnings: ${warnings.map(item => item.message).join(' | ')}`);
  if (parts.length) {
    banner.textContent = parts.join(' ');
    banner.classList.remove('hidden');
  } else {
    banner.classList.add('hidden');
  }
}

function renderRibbon(payload) {
  const terminal = payload.terminal || {};
  const headline = terminal.headline || {};
  const benchmark = terminal.benchmark || {};
  const positioning = terminal.positioning || {};
  const tape = terminal.tape || {};
  const health = terminal.health || {};

  setText('metric-nav', fmtMoney(headline.nav), colorClass(headline.day_pnl));
  setText('metric-day', `${fmtMoney(headline.day_pnl)} · ${fmtPct(headline.day_return)}`);
  setText('metric-cash', fmtMoney(headline.cash));
  setText('metric-buying-power', `BP ${fmtMoney(payload.sections?.nav?.buying_power)}`);
  setText('metric-exposure', fmtPct(headline.gross_exposure));
  setText('metric-concentration', `top5 ${fmtPct(positioning.top5_concentration)}`);
  setText('metric-fills', String(headline.fills_count ?? '—'));
  setText('metric-tape', tape.last_fill_at ? `last ${fmtTime(tape.last_fill_at)}` : 'no same-day fills');
  setText('metric-excess', fmtPct(benchmark.excess_since_inception_return), colorClass(benchmark.excess_since_inception_return));
  setText('metric-rolling-excess', `5d ${fmtPct(benchmark.rolling_5d_excess_return)} · 20d ${fmtPct(benchmark.rolling_20d_excess_return)}`);
  setText('metric-validation', String(payload.status?.level || '—').toUpperCase(), colorClass(payload.status?.level === 'ok' ? 1 : payload.status?.level === 'error' ? -1 : 0));
  setText('metric-health', `${health.blocking_failures || 0} fail · ${health.warnings || 0} warn`);
}

function renderPerformanceMatrix(payload) {
  const benchmark = payload.terminal?.benchmark || {};
  setHTML('performance-matrix', [
    matrixItem('Since Inception', fmtPct(benchmark.since_inception_return), `SPY ${fmtPct(benchmark.spy_since_inception_return)}`, colorClass(benchmark.since_inception_return)),
    matrixItem('SI Excess', fmtPct(benchmark.excess_since_inception_return), 'portfolio minus SPY', colorClass(benchmark.excess_since_inception_return)),
    matrixItem('Rolling 5D', fmtPct(benchmark.rolling_5d_return), `SPY ${fmtPct(benchmark.rolling_5d_spy_return)}`, colorClass(benchmark.rolling_5d_return)),
    matrixItem('Rolling 20D', fmtPct(benchmark.rolling_20d_return), `SPY ${fmtPct(benchmark.rolling_20d_spy_return)}`, colorClass(benchmark.rolling_20d_return)),
    matrixItem('Max Drawdown', fmtPct(benchmark.max_drawdown), `${benchmark.history_points || 0} history points`, colorClass(benchmark.max_drawdown)),
    matrixItem('Market Tape', benchmark.spy_close == null ? '—' : fmtMoney(benchmark.spy_close), `${benchmark.up_days || 0} up days · ${benchmark.down_days || 0} down days`, 'neutral'),
  ].join(''));
}

function renderHealthMatrix(payload) {
  const health = payload.terminal?.health || {};
  setHTML('health-matrix', [
    matrixItem('Blocking Failures', String(health.blocking_failures ?? 0), 'publish-blocking checks', colorClass(-(health.blocking_failures || 0))),
    matrixItem('Warnings', String(health.warnings ?? 0), 'freshness / non-blocking checks', health.warnings ? 'neutral' : 'pos'),
    matrixItem('Sources Used', `${health.sources_used || 0}/${health.sources_total || 0}`, 'artifacts loaded', 'neutral'),
    matrixItem('Stale Sections', (health.stale_sections || []).length ? (health.stale_sections || []).join(', ') : 'none', 'timestamp monitor', (health.stale_sections || []).length ? 'neutral' : 'pos'),
  ].join(''));
}

function renderSystemHealth(payload) {
  const section = payload.sections?.system_health_console || {};
  const summary = section.summary || {};
  setText('system-health-summary', `${summary.status || 'UNKNOWN'} · ${summary.failed_pipeline_count || 0} fail · ${summary.warning_count || 0} warn`, statusClass(summary.status));
  const rows = section.checks || [];
  setHTML('system-health-console', rows.length ? rows.map(row => `
    <div class="check-item ${statusClass(row.status)}">
      <strong>${row.name || '—'}</strong>
      <div class="matrix-value ${statusClass(row.status)}">${String(row.status || '—').toUpperCase()}</div>
      <div class="check-detail">${row.detail || '—'}</div>
    </div>
  `).join('') : '<div class="check-item warn"><strong>No system health data.</strong><div class="check-detail">Optional operational artifacts are unavailable.</div></div>');
}

function renderRegime(payload) {
  const regime = payload.sections?.regime_market_state || {};
  setText('regime-asof', regime.as_of ? `as of ${regime.as_of}` : 'as of unavailable');
  const blockers = regime.promotion_gate_blockers || [];
  setHTML('regime-matrix', [
    matrixItem('Current Regime', String(regime.current_regime || '—'), `confidence ${regime.confidence_state || 'UNKNOWN'}`, statusClass(regime.confidence_state === 'AVAILABLE' ? 'PASS' : 'WARN')),
    matrixItem('VIX', fmtNum(regime.vix, 2), 'market volatility state', regime.vix >= 30 ? 'neg' : regime.vix >= 22 ? 'neutral' : 'pos'),
    matrixItem('Portfolio Scale', fmtPct(regime.portfolio_scale), 'regime-driven exposure scalar', statusClass(regime.portfolio_scale == null ? 'WARN' : 'PASS')),
    matrixItem('Max Positions', regime.max_positions == null ? '—' : fmtNum(regime.max_positions, 0), 'risk envelope', 'neutral'),
    matrixItem('Gate Blockers', blockers.length ? blockers.join(', ') : 'none', 'engine review', blockers.length ? 'neg' : 'pos'),
    matrixItem('Fallback State', regime.confidence_state || 'UNKNOWN', 'data availability', statusClass(regime.confidence_state === 'AVAILABLE' ? 'PASS' : 'WARN')),
  ].join(''));
}

function renderShadowCommand(payload) {
  const section = payload.sections?.shadow_command_center || {};
  const summary = section.summary || {};
  setText('shadow-command-summary', `data ${section.as_of || '—'} · NAV ${summary.latest_nav_date || '—'}`, statusClass(section.status));
  const rows = section.strategies || [];
  const body = document.getElementById('shadow-strategy-body');
  if (!body) return;
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="12">No shadow strategy data available.</td></tr>';
    return;
  }
  body.innerHTML = rows.map(row => `
    <tr class="${row.role === 'CONTROL' ? 'control-row' : ''}">
      <td>${row.name || row.slug || '—'}</td>
      <td class="${row.role === 'CONTROL' ? 'neutral' : 'pos'}">${row.role || '—'}</td>
      <td class="num ${colorClass(row.cumulative_return)}">${fmtPct(row.cumulative_return)}</td>
      <td class="num ${colorClass(row.excess_return_vs_spy)}">${fmtPct(row.excess_return_vs_spy)}</td>
      <td class="num ${colorClass(row.rolling_5d_excess)}">${fmtPct(row.rolling_5d_excess)}</td>
      <td class="num ${colorClass(row.rolling_20d_excess)}">${fmtPct(row.rolling_20d_excess)}</td>
      <td class="num ${colorClass(row.max_drawdown)}">${fmtPct(row.max_drawdown)}</td>
      <td class="num">${fmtPct(row.realized_volatility_ann)}</td>
      <td class="num">${fmtPct(row.avg_turnover)}</td>
      <td class="num">${fmtPct(row.avg_top_3_concentration)}</td>
      <td class="num">${row.valid_evaluation_days ?? '—'}</td>
      <td class="${statusClass(row.promotion_readiness)}">${row.promotion_readiness || '—'}</td>
    </tr>
  `).join('');
}

function renderPositioning(payload) {
  const pos = payload.terminal?.positioning || {};
  setHTML('positioning-matrix', [
    matrixItem('Cash Ratio', fmtPct(pos.cash_ratio), 'cash / equity', colorClass(pos.cash_ratio)),
    matrixItem('Invested Ratio', fmtPct(pos.invested_ratio), 'market value / equity', colorClass(pos.invested_ratio)),
    matrixItem('Largest Weight', fmtPct(pos.largest_position_weight), 'max single-name size', 'neutral'),
    matrixItem('Top 10 Conc.', fmtPct(pos.top10_concentration), 'sum of ten largest weights', 'neutral'),
    matrixItem('Avg Weight', fmtPct(pos.average_position_weight), 'mean across current positions', 'neutral'),
    matrixItem('Median Weight', fmtPct(pos.median_position_weight), 'middle position size', 'neutral'),
  ].join(''));
}

function renderDecisionIntelligence(payload) {
  const section = payload.sections?.daily_decision_intelligence || {};
  const summary = section.summary || {};
  setText('decision-summary', `${summary.buy_count || 0} buys · ${summary.sell_count || 0} sells · ${fmtPct(summary.latest_daily_return)}`);
  const items = [];
  (section.notes || []).forEach(note => {
    items.push(`
      <div class="rank-item">
        <strong>${note.label || '—'}</strong>
        <div class="matrix-value ${note.kind === 'return' ? colorClass(note.value) : 'neutral'}">${note.kind === 'return' ? fmtPct(note.value) : note.value || '—'}</div>
        <div class="rank-sub">${note.detail == null ? 'daily operator signal' : fmtMoney(note.detail)}</div>
      </div>
    `);
  });
  (section.largest_increases || []).slice(0, 3).forEach(row => {
    items.push(`
      <div class="rank-item">
        <strong>Increase · ${row.ticker || '—'}</strong>
        <div class="matrix-value pos">${fmtMoney(row.notional)}</div>
        <div class="rank-sub">${fmtNum(row.qty, 2)} @ ${fmtMoney(row.fill_price)}</div>
      </div>
    `);
  });
  (section.largest_decreases || []).slice(0, 3).forEach(row => {
    items.push(`
      <div class="rank-item">
        <strong>Decrease · ${row.ticker || '—'}</strong>
        <div class="matrix-value neg">${fmtMoney(row.notional)}</div>
        <div class="rank-sub">${fmtNum(row.qty, 2)} @ ${fmtMoney(row.fill_price)}</div>
      </div>
    `);
  });
  setHTML('decision-intelligence', items.length ? items.join('') : '<div class="rank-item">No same-day decision changes available.</div>');
}

function renderLiveReadiness(payload) {
  const section = payload.sections?.live_readiness || {};
  const summary = section.summary || {};
  setText('live-readiness-summary', `${summary.deployment_confidence || 'UNKNOWN'} · ${summary.consecutive_healthy_days || 0} obs`, statusClass(summary.deployment_confidence));
  const rows = section.criteria || [];
  setHTML('live-readiness-list', rows.length ? rows.map(row => `
    <div class="check-item ${statusClass(row.status)}">
      <strong>${row.name || '—'}</strong>
      <div class="matrix-value ${statusClass(row.status)}">${row.status || '—'}</div>
      <div class="check-detail">${row.detail || '—'}</div>
    </div>
  `).join('') : '<div class="check-item warn"><strong>No readiness criteria available.</strong></div>');
}

function renderPositions(payload) {
  const section = payload.sections?.positions || {};
  setText('positions-asof', section.as_of ? `as of ${fmtDateTime(section.as_of)}` : 'as of unavailable');
  const body = document.getElementById('positions-body');
  if (!body) return;
  const rows = section.rows || [];
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6">No positions available.</td></tr>';
    return;
  }
  body.innerHTML = rows.map(row => `
    <tr>
      <td>${row.ticker || '—'}</td>
      <td class="num">${fmtNum(row.qty, 2)}</td>
      <td class="num">${fmtMoney(row.last_price)}</td>
      <td class="num">${fmtMoney(row.market_value)}</td>
      <td class="num">${fmtPct(row.weight)}</td>
      <td class="num ${colorClass(row.unrealized_pnl)}">${fmtMoney(row.unrealized_pnl)}</td>
    </tr>
  `).join('');
}

function renderFills(payload) {
  const section = payload.sections?.trades_today || {};
  setText('fills-asof', section.as_of ? `as of ${fmtDateTime(section.as_of)}` : 'as of unavailable');
  const body = document.getElementById('fills-body');
  if (!body) return;
  const rows = section.rows || [];
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6">No fills for this report date.</td></tr>';
    return;
  }
  body.innerHTML = rows.map(row => `
    <tr>
      <td>${fmtTime(row.filled_at)}</td>
      <td>${row.ticker || '—'}</td>
      <td class="${colorClass(row.side === 'buy' ? 1 : row.side === 'sell' ? -1 : 0)}">${String(row.side || '—').toUpperCase()}</td>
      <td class="num">${fmtNum(row.qty, 2)}</td>
      <td class="num">${fmtMoney(row.fill_price)}</td>
      <td class="num">${fmtMoney(row.notional)}</td>
    </tr>
  `).join('');
}

function renderRanks(payload) {
  const leaders = payload.terminal?.leaders?.winners || [];
  const laggards = payload.terminal?.leaders?.laggards || [];
  setHTML('leaders-list', leaders.length ? leaders.map(rankItem).join('') : '<div class="rank-item">No leader data.</div>');
  setHTML('laggards-list', laggards.length ? laggards.map(rankItem).join('') : '<div class="rank-item">No laggard data.</div>');
}

function renderSources(payload) {
  const sources = payload.sources || [];
  setHTML('source-list', sources.map(source => `
    <div class="source-item">
      <strong>${source.section}: ${source.label}</strong>
      <div class="matrix-detail">${source.source_type} · ${source.trust_level} · ${source.used ? 'used' : 'missing'}</div>
      <div class="source-path">${source.path || '—'}</div>
    </div>
  `).join(''));
}

function renderValidation(payload) {
  const checks = payload.validation?.checks || [];
  setText('validation-summary', `${checks.filter(c => c.status === 'pass').length} pass · ${checks.filter(c => c.status === 'warn').length} warn · ${checks.filter(c => c.status === 'fail').length} fail`);
  setHTML('validation-list', checks.map(check => `
    <div class="check-item ${check.status}">
      <strong>${check.name}</strong>
      <div class="check-detail">${check.detail}</div>
    </div>
  `).join(''));
}

function drawLineChart(canvasId, seriesList, options = {}) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const width = canvas.clientWidth || 600;
  const height = canvas.height || 250;
  canvas.width = width * window.devicePixelRatio;
  canvas.height = height * window.devicePixelRatio;
  ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
  ctx.clearRect(0, 0, width, height);

  const padding = { top: 18, right: 12, bottom: 42, left: 62 };
  const points = seriesList.flatMap(series => series.points.filter(point => point.value != null));
  if (!points.length) {
    ctx.fillStyle = '#7e92ad';
    ctx.fillText('No chart data available.', 20, 28);
    return;
  }
  const values = points.map(point => point.value);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) { min -= 1; max += 1; }

  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const count = Math.max(...seriesList.map(series => series.points.length));

  ctx.strokeStyle = 'rgba(48, 71, 100, 0.8)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padding.left, padding.top);
  ctx.lineTo(padding.left, padding.top + plotHeight);
  ctx.lineTo(padding.left + plotWidth, padding.top + plotHeight);
  ctx.stroke();

  const zeroLineEnabled = Boolean(options.zeroLine);
  if (zeroLineEnabled && min <= 0 && max >= 0) {
    const yZero = padding.top + (1 - ((0 - min) / (max - min))) * plotHeight;
    ctx.strokeStyle = 'rgba(243, 167, 18, 0.45)';
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.moveTo(padding.left, yZero);
    ctx.lineTo(padding.left + plotWidth, yZero);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  ctx.fillStyle = '#7e92ad';
  ctx.font = '11px IBM Plex Mono, Menlo, monospace';
  const yFormatter = options.yFormatter || ((value) => fmtNum(value, 2));
  ctx.fillText(yFormatter(max), 8, padding.top + 4);
  ctx.fillText(yFormatter(min), 8, padding.top + plotHeight);
  if (zeroLineEnabled && min <= 0 && max >= 0) {
    const yZero = padding.top + (1 - ((0 - min) / (max - min))) * plotHeight;
    ctx.fillText(yFormatter(0), 8, yZero + 4);
  }
  ctx.fillText(options.yLabel || '', 8, 12);
  const xLabel = options.xLabel || 'Date';
  const labelWidth = ctx.measureText(xLabel).width;
  ctx.fillText(xLabel, padding.left + plotWidth - labelWidth, height - 10);

  seriesList.forEach(series => {
    ctx.beginPath();
    ctx.strokeStyle = series.color;
    ctx.lineWidth = 2;
    let started = false;
    series.points.forEach((point, index) => {
      if (point.value == null) return;
      const x = padding.left + (count <= 1 ? 0 : (index / (count - 1)) * plotWidth);
      const y = padding.top + (1 - ((point.value - min) / (max - min))) * plotHeight;
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
  });
}

function renderCharts(payload) {
  const series = payload.sections?.performance_history?.series || {};
  drawLineChart('performance-chart', [
    { color: '#4fd1ff', points: series.nav_indexed || [] },
    { color: '#f3a712', points: series.spy_indexed || [] },
  ], {
    xLabel: 'Date',
    yLabel: 'Indexed',
    yFormatter: (value) => fmtNum(value, 0),
  });
  drawLineChart('excess-chart', [
    { color: '#8b7dff', points: series.excess_return_cumulative || [] },
  ], {
    xLabel: 'Date',
    yLabel: 'Excess %',
    yFormatter: (value) => `${(Number(value) * 100).toFixed(1)}%`,
    zeroLine: true,
  });
  drawLineChart('drawdown-chart', [
    { color: '#ff6f61', points: series.drawdown || [] },
  ], {
    xLabel: 'Date',
    yLabel: 'DD %',
    yFormatter: (value) => `${(Number(value) * 100).toFixed(1)}%`,
    zeroLine: true,
  });
  const shadowSeries = payload.sections?.shadow_command_center?.rolling_excess_series || [];
  drawLineChart('shadow-excess-chart', [
    { color: '#4fd1ff', points: shadowSeries.map(row => ({ date: row.date, value: row.caerus_polaris })) },
    { color: '#8b7dff', points: shadowSeries.map(row => ({ date: row.date, value: row.caerus_orion })) },
    { color: '#f3a712', points: shadowSeries.map(row => ({ date: row.date, value: row.caerus_lyra })) },
  ], {
    xLabel: 'Date',
    yLabel: '5D XS %',
    yFormatter: (value) => `${(Number(value) * 100).toFixed(1)}%`,
    zeroLine: true,
  });
}

async function boot() {
  try {
    const payload = await fetchJSON(resolveDataPath());
    renderStatus(payload);
    renderRibbon(payload);
    renderPerformanceMatrix(payload);
    renderHealthMatrix(payload);
    renderSystemHealth(payload);
    renderRegime(payload);
    renderShadowCommand(payload);
    renderDecisionIntelligence(payload);
    renderLiveReadiness(payload);
    renderPositioning(payload);
    renderPositions(payload);
    renderFills(payload);
    renderRanks(payload);
    renderSources(payload);
    renderValidation(payload);
    renderCharts(payload);
  } catch (error) {
    const banner = document.getElementById('status-banner');
    if (!banner) throw error;
    banner.textContent = `Dashboard failed to load: ${error.message}`;
    banner.classList.remove('hidden');
  }
}

window.addEventListener('DOMContentLoaded', boot);
