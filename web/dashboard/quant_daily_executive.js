'use strict';

const SHADOW_SERIES_COLORS = {
  caerus_polaris: '#4fd1ff',
  caerus_orion: '#8b7dff',
  caerus_lyra: '#f3a712',
};
const SHADOW_FALLBACK_COLORS = ['#7dd3fc', '#c084fc', '#fbbf24', '#34d399', '#fb7185', '#a3e635'];

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

async function loadDashboardPayload() {
  try {
    return await fetchJSON(resolveDataPath());
  } catch (error) {
    if (window.DASHBOARD_V1) {
      return window.DASHBOARD_V1;
    }
    throw error;
  }
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

function fmtBps(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(1)} bps`;
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

function asNumber(value) {
  if (value == null || value === '') return null;
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : null;
}

function statusClass(value) {
  const text = String(value || '').toUpperCase();
  if (text.includes('PENDING') || text.includes('ACCEPTED') || text.includes('NEW')) return 'neutral';
  if (text.includes('FILLED') || text.includes('CLEAN')) return 'pos';
  if (text.includes('REJECT') || text.includes('FAIL') || text.includes('BLOCKED')) return 'neg';
  if (['PASS', 'OK', 'GREEN', 'CONTROL', 'PROMOTION_ELIGIBLE', 'HIGH', 'PRESENT', 'READY', 'ACTIVE', 'BASELINE', 'SUBMITTED', 'CLEAN', 'NONE'].includes(text)) return 'pos';
  if (['FAIL', 'ERROR', 'RED', 'NO_DATA', 'NO_PRIOR', 'BROKEN_CHAIN', 'NOT_READY', 'BLOCKED', 'DEPENDENCY_BLOCKED', 'FAILED_RECONCILIATION'].includes(text)) return 'neg';
  if (['ACTION_REQUIRED', 'WATCH', 'WARNING', 'PARTIAL', 'IDLE', 'IN_PROGRESS'].includes(text)) return 'neutral';
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

function statePill(label, value, detail, cls = '') {
  return `
    <div class="state-pill ${cls}">
      <strong class="${cls}">${value}</strong>
      <span>${label}${detail ? ` · ${detail}` : ''}</span>
    </div>
  `;
}

function emptyState(title, reason, path, blocksPilot = false) {
  return `
    <div class="empty-state">
      <strong>${title}</strong>
      <div class="matrix-detail">${reason || 'Data unavailable.'}</div>
      <div class="matrix-detail">Expected artifact: <code>${path || 'not specified'}</code></div>
      <div class="matrix-detail">Blocks pilot: ${blocksPilot ? 'yes' : 'no'}</div>
    </div>
  `;
}

function formatCardValue(card) {
  if (!card) return '—';
  if (card.value_format === 'money') return fmtMoney(card.value);
  if (card.value_format === 'percent') return fmtPct(card.value);
  if (card.value_format === 'integer') return card.value == null ? '—' : String(card.value);
  return card.value == null || card.value === '' ? '—' : String(card.value);
}

function fmtMaybePctDelta(a, b) {
  if (a == null || b == null || Number.isNaN(Number(a)) || Number.isNaN(Number(b))) return '—';
  return fmtPct(Number(a) - Number(b));
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

function maxAbs(values) {
  const numeric = values.map(asNumber).filter(value => value != null);
  if (!numeric.length) return 1;
  return Math.max(...numeric.map(value => Math.abs(value)), 0.000001);
}

function barRow({ label, value, valueText, detail, maxValue, positiveOnly = false, tone = '', valueClass = '' }) {
  const numeric = asNumber(value);
  const signed = !positiveOnly;
  const scale = positiveOnly ? 100 : 50;
  const width = numeric == null ? 0 : Math.min(scale, (Math.abs(numeric) / Math.max(maxValue || 1, 0.000001)) * scale);
  const direction = signed && numeric < 0 ? 'neg' : 'pos';
  const fillTone = tone || (numeric == null ? 'neutral' : colorClass(numeric));
  const displayClass = valueClass || (numeric == null ? 'neutral' : colorClass(numeric));
  return `
    <div class="bar-row ${numeric == null ? 'collecting' : ''}">
      <div class="bar-row-head">
        <strong>${label || '—'}</strong>
        <span class="bar-value ${displayClass}">${valueText || (numeric == null ? 'collecting evidence' : fmtPct(numeric))}</span>
      </div>
      <div class="bar-track ${positiveOnly ? 'positive-only' : 'signed'}">
        <span class="bar-fill ${direction} ${fillTone}" style="--bar-width: ${width.toFixed(2)}%;"></span>
      </div>
      <div class="bar-detail">${detail || ' '}</div>
    </div>
  `;
}

function checkpointText(checkpoints, fallbackDays = 0) {
  if (!Array.isArray(checkpoints) || !checkpoints.length) return `${fallbackDays || 0}d`;
  return checkpoints.map(check => `${check.observed_days || 0}/${check.trading_days || 0}d`).join(' · ');
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
  const tower = payload.sections?.operator_control_tower || {};
  const cards = Object.fromEntries((tower.cards || []).map(card => [card.id, card]));
  const paper = cards.paper_nav || {};
  const live = cards.live_capital || {};
  const order = cards.latest_order || {};
  const sleeves = cards.sleeves || {};
  const validation = cards.validation || {};
  const operator = cards.operator_action || {};

  setText('tower-paper-nav', formatCardValue(paper), statusClass(paper.status));
  setText('tower-paper-return', payload.sections?.nav?.day_return == null ? 'day return unavailable' : `Day ${fmtPct(payload.sections.nav.day_return)}`);
  setText('tower-live-deployed', formatCardValue(live), statusClass(live.status));
  setText('tower-live-cash', `cash ${fmtMoney(payload.sections?.live_pilot?.account?.cash)} · equity ${fmtMoney(payload.sections?.live_pilot?.account?.equity)}`);
  setText('tower-live-order-status', formatCardValue(order), statusClass(order.status));
  setText('tower-live-order-detail', order.detail || 'no live-pilot order artifact');
  setText('tower-sleeve-count', formatCardValue(sleeves), statusClass(sleeves.status));
  setText('tower-sleeve-detail', sleeves.detail || 'registry unavailable');
  setText('tower-validation-status', formatCardValue(validation), statusClass(validation.status));
  setText('tower-validation-detail', validation.detail || 'validation unavailable');
  setText('tower-operator-action', formatCardValue(operator), statusClass(operator.status));
  setText('tower-operator-detail', operator.detail || 'No operator action required.');
}

function renderTopPositions(payload) {
  const section = payload.sections?.positions || {};
  const summary = section.summary || {};
  const nav = payload.sections?.nav || {};
  const rows = (section.rows || [])
    .slice()
    .sort((a, b) => (asNumber(b.weight) || 0) - (asNumber(a.weight) || 0))
    .slice(0, 8);
  setText(
    'top-positions-summary',
    `${summary.positions_count || rows.length} positions · top 5 ${fmtPct(summary.top5_concentration)} · cash ${fmtMoney(summary.cash ?? nav.cash)}`,
    rows.length ? 'neutral' : 'neg'
  );
  if (!rows.length) {
    setHTML('top-positions-bars', emptyState('No top positions available', 'Broker position snapshot is missing or empty.', 'outputs/broker/posttrade_positions.json', false));
    return;
  }
  const maxWeight = maxAbs(rows.map(row => row.weight));
  setHTML('top-positions-bars', rows.map(row => {
    const dailyMove = row.daily_return ?? row.day_return ?? row.daily_move;
    const detail = [
      fmtMoney(row.market_value),
      `P&L ${fmtMoney(row.unrealized_pnl)}`,
      dailyMove == null ? 'daily move unavailable' : `day ${fmtPct(dailyMove)}`,
    ].join(' · ');
    return barRow({
      label: row.ticker || '—',
      value: row.weight,
      valueText: fmtPct(row.weight),
      detail,
      maxValue: maxWeight,
      positiveOnly: true,
      tone: 'exposure',
      valueClass: colorClass(row.unrealized_pnl),
    });
  }).join(''));
}

function renderSleeveBarCharts(payload) {
  const sleeveRows = payload.sections?.sleeve_inventory?.rows || [];
  const strategyRows = payload.sections?.shadow_command_center?.strategies || [];
  const alphaPairs = payload.sections?.baseline_alpha_comparison?.pairs || [];

  const returnMax = maxAbs(sleeveRows.map(row => row.since_inception_return));
  const returnRows = sleeveRows.map(row => barRow({
    label: row.display_name || row.sleeve_id || '—',
    value: row.since_inception_return,
    valueText: asNumber(row.since_inception_return) == null ? 'collecting evidence' : fmtPct(row.since_inception_return),
    detail: `${row.lifecycle_stage || '—'} · ${row.variant_class || row.role || '—'} · ${row.promotion_readiness || '—'}`,
    maxValue: returnMax,
  }));
  setText(
    'sleeve-return-summary',
    `${sleeveRows.filter(row => asNumber(row.since_inception_return) != null).length}/${sleeveRows.length} with return history`,
    sleeveRows.length ? 'neutral' : 'neg'
  );
  setHTML(
    'sleeve-return-bars',
    returnRows.length ? returnRows.join('') : emptyState('No sleeve return rows available', 'Sleeve inventory is missing registered rows.', 'sections.sleeve_inventory.rows', false)
  );

  const excessMax = maxAbs(strategyRows.map(row => row.excess_return_vs_spy));
  const excessRows = strategyRows.map(row => barRow({
    label: row.name || row.slug || '—',
    value: row.excess_return_vs_spy,
    valueText: asNumber(row.excess_return_vs_spy) == null ? 'collecting evidence' : fmtPct(row.excess_return_vs_spy),
    detail: `${row.role || '—'} · ${row.valid_evaluation_days ?? 0} valid days · ${row.data_status || '—'}`,
    maxValue: excessMax,
  }));
  setText(
    'sleeve-excess-summary',
    `${strategyRows.filter(row => asNumber(row.excess_return_vs_spy) != null).length}/${strategyRows.length} with SPY comparison`,
    strategyRows.length ? 'neutral' : 'neg'
  );
  setHTML(
    'sleeve-excess-bars',
    excessRows.length ? excessRows.join('') : emptyState('No sleeve excess rows available', 'Shadow command center has no strategy comparison rows.', 'sections.shadow_command_center.strategies', false)
  );

  const deltaMax = maxAbs(alphaPairs.map(row => row.return_delta));
  const deltaRows = alphaPairs.map(row => {
    const value = asNumber(row.return_delta);
    const checkpoints = row.review_checkpoints || [];
    const firstCheckpoint = checkpoints.find(check => Number(check.trading_days) === 20) || checkpoints[0] || {};
    const observed = Number(firstCheckpoint.observed_days || row.evidence_window_days || 0);
    const target = Number(firstCheckpoint.trading_days || 20);
    const collecting = value == null || observed < target;
    return barRow({
      label: `${row.baseline_name || 'Baseline'} → ${row.alpha_name || 'Alpha'}`,
      value: value,
      valueText: value == null ? 'collecting evidence' : fmtPct(value),
      detail: `${collecting ? 'collecting evidence' : 'checkpoint ready'} · ${checkpointText(checkpoints, row.evidence_window_days)}`,
      maxValue: deltaMax,
      valueClass: value == null ? 'neutral' : colorClass(value),
    });
  });
  setText(
    'alpha-delta-summary',
    `${alphaPairs.length} alpha pairs · 20/60d review`,
    alphaPairs.length ? 'neutral' : 'neg'
  );
  setHTML(
    'alpha-delta-bars',
    deltaRows.length ? deltaRows.join('') : emptyState('No baseline-vs-alpha delta rows available', 'Alpha comparison artifact has no registered pairs.', 'sections.baseline_alpha_comparison.pairs', false)
  );
}

function renderOperatorControlTower(payload) {
  const tower = payload.sections?.operator_control_tower || {};
  const summary = tower.summary || {};
  const actions = tower.operator_actions || [];
  setText(
    'operator-action-summary',
    `${summary.operator_action_required ? 'action required' : 'no blocking action'} · live ${summary.live_pilot_state || '—'}`,
    summary.operator_action_required ? 'neutral' : 'pos'
  );
  setHTML('operator-context-matrix', [
    matrixItem('Live Pilot', summary.live_pilot_state || '—', `deployed ${fmtPct(summary.live_pilot_deployed_pct)}`, statusClass(summary.live_pilot_state)),
    matrixItem('Open Orders', String(summary.live_pilot_open_orders ?? 0), `latest ${summary.latest_order_status || '—'}`, (summary.live_pilot_open_orders || 0) ? 'neutral' : 'pos'),
    matrixItem('Alpha Pairs', String(summary.alpha_pair_count ?? 0), 'Polaris and Orion comparisons', (summary.alpha_pair_count || 0) ? 'pos' : 'neutral'),
    matrixItem('FR-068 Impact', summary.fr068_pilot_blocking ? 'pilot-blocking' : 'not pilot-blocking', 'promotion/scaling gate', summary.fr068_pilot_blocking ? 'neg' : 'pos'),
  ].join(''));
  setHTML('operator-action-list', actions.length ? actions.map(action => `
    <div class="check-item ${action.severity === 'action' || action.severity === 'critical' ? 'action' : action.severity === 'info' ? 'info' : statusClass(action.status)}">
      <strong>${action.title || '—'}</strong>
      <div class="matrix-value ${statusClass(action.status)}">${action.status || '—'}</div>
      <div class="check-detail">${action.detail || '—'}</div>
      <div class="check-detail">Action: ${action.operator_action || '—'}</div>
      <div class="check-detail">Expected artifact: ${action.expected_artifact || '—'} · Blocks pilot: ${action.blocks_pilot ? 'yes' : 'no'}</div>
    </div>
  `).join('') : emptyState('No operator action data', 'operator_control_tower.operator_actions was empty.', 'sections.operator_control_tower.operator_actions', false));
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
  `).join('') : emptyState('No system health data', 'Optional operational health artifacts are unavailable.', 'outputs/health/caerus_daily_health_check/latest/health_check.json', false));
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
    body.innerHTML = `<tr><td colspan="12">${emptyState('No shadow strategy data available', 'Shadow evaluation artifact is missing or has no strategy rows.', 'outputs/shadow_candidates/<date>/shadow_evaluation.json', false)}</td></tr>`;
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

function renderLivePilot(payload) {
  const section = payload.sections?.live_pilot || {};
  const tower = payload.sections?.operator_control_tower || {};
  const towerSummary = tower.summary || {};
  const latest = tower.latest_order || {};
  const account = section.account || {};
  const metrics = section.metrics || {};
  const recon = section.reconciliation || {};
  const policy = section.policy || {};
  setText(
    'live-pilot-summary',
    `${towerSummary.live_pilot_state || section.status || 'NO_DATA'} · run ${section.run_id || '—'}`,
    statusClass(towerSummary.live_pilot_state || section.status)
  );
  setHTML('live-pilot-state-strip', [
    statePill('state', towerSummary.live_pilot_state || '—', section.status || '—', statusClass(towerSummary.live_pilot_state || section.status)),
    statePill('deployed', fmtPct(towerSummary.live_pilot_deployed_pct), `cap ${fmtMoney(metrics.capital_cap_usd)}`, 'neutral'),
    statePill('open orders', String(metrics.open_order_count ?? 0), `${metrics.blocking_open_order_count ?? 0} blocking`, (metrics.blocking_open_order_count || 0) ? 'neg' : 'pos'),
    statePill('latest order', latest.ticker || '—', latest.status || section.latest_fill_status || '—', statusClass(latest.status || section.latest_fill_status)),
  ].join(''));
  setHTML('live-pilot-matrix', [
    matrixItem('Cash', fmtMoney(account.cash), `equity ${fmtMoney(account.equity)} · BP ${fmtMoney(account.buying_power)}`, 'neutral'),
    matrixItem('Deployed', fmtPct(towerSummary.live_pilot_deployed_pct), `positions ${(section.positions || []).length}`, statusClass(towerSummary.live_pilot_state)),
    matrixItem('Latest Order', `${latest.ticker || '—'} ${latest.side || ''}`.trim(), `${fmtNum(latest.qty, 4)} ${latest.order_type || '—'} · ${latest.status || '—'}`, statusClass(latest.status || section.status)),
    matrixItem('Filled Qty', fmtNum(latest.filled_qty, 4), `fill ${fmtMoney(latest.fill_price)} · expected ${fmtMoney(latest.expected_price)}`, statusClass((metrics.filled_count || 0) > 0 ? 'PASS' : section.status)),
    matrixItem('Fill Rate', fmtPct(metrics.fill_rate), `${metrics.filled_count ?? 0} filled / ${metrics.submitted_count ?? 0} submitted`, statusClass((metrics.filled_count || 0) > 0 ? 'PASS' : section.status)),
    matrixItem('Slippage', fmtBps(metrics.slippage_bps), `avg fill time ${fmtNum(metrics.average_time_to_fill_seconds, 1)}s`, 'neutral'),
    matrixItem('Recon', recon.status || '—', recon.operator_action || metrics.idle_cash_reason || '—', statusClass(recon.status || section.status)),
    matrixItem('Idle Cash Reason', metrics.idle_cash_reason || '—', policy.scope || 'FR-104 LIVE_PILOT only', 'neutral'),
  ].join(''));

  const body = document.getElementById('live-pilot-orders-body');
  if (!body) return;
  const submitted = section.submitted_orders || [];
  const openOrders = section.open_orders || [];
  const rows = submitted.length ? submitted : openOrders.map(order => ({
    symbol: order.symbol,
    status: order.status,
    order_type: order.type || order.order_type || 'open',
    qty: order.qty,
    expected_price: order.limit_price,
    fill_price: order.filled_avg_price,
  }));
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="7">${emptyState('No live-pilot orders available', metrics.idle_cash_reason || 'No submitted/open live-pilot order artifact is available.', section.plan_path || 'outputs/live_pilot/plans/live_pilot_plan_<date>.json', false)}</td></tr>`;
    return;
  }
  body.innerHTML = rows.map(row => {
    const nested = row.order || {};
    const symbol = row.symbol || row.ticker || nested.symbol || '—';
    const status = row.status || nested.status || '—';
    return `
      <tr>
        <td>${symbol}</td>
        <td>${row.side || nested.side || '—'}</td>
        <td class="${statusClass(status)}">${status}</td>
        <td>${row.submitted_order_type || row.order_type || nested.type || '—'}</td>
        <td class="num">${fmtNum(row.qty || row.shares, 4)}</td>
        <td class="num">${fmtNum(row.filled_qty || nested.filled_qty, 4)}</td>
        <td class="num">${fmtMoney(row.fill_price || nested.filled_avg_price || row.expected_price || row.cap_enforcement_price || row.limit_price)}</td>
      </tr>
    `;
  }).join('');
}

function renderAccountLayers(payload) {
  const rows = payload.sections?.account_layers?.rows || [];
  const body = document.getElementById('account-layers-body');
  if (!body) return;
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="5">${emptyState('No account-layer data available', 'Dashboard could not derive paper/live/shadow account layers.', 'sections.account_layers.rows', false)}</td></tr>`;
    return;
  }
  body.innerHTML = rows.map(row => `
    <tr>
      <td>${row.layer || '—'}</td>
      <td class="${statusClass(row.status)}">${row.status || '—'}</td>
      <td class="num">${fmtMoney(row.cash)}</td>
      <td class="num">${fmtMoney(row.equity)}</td>
      <td class="num">${row.positions_count ?? '—'}</td>
    </tr>
  `).join('');
}

function renderSleeveInventory(payload) {
  const section = payload.sections?.sleeve_inventory || {};
  const summary = section.summary || {};
  const counts = summary.by_lifecycle_stage || {};
  setText(
    'sleeve-inventory-summary',
    `${summary.total_registered || 0} registered · ${counts.paper || 0} paper · ${counts.shadow || 0} shadow · ${counts.research || 0} research`,
    statusClass(section.status)
  );
  const body = document.getElementById('sleeve-inventory-body');
  if (!body) return;
  const rows = section.rows || [];
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="9">${emptyState('No registered sleeves available', 'Strategy registry or sleeve manifest could not produce dashboard rows.', 'config/research/strategy_registry.json + research_registry/sleeves/manifest.json', false)}</td></tr>`;
    return;
  }
  body.innerHTML = rows.map(row => `
    <tr>
      <td>${row.display_name || row.sleeve_id || '—'}</td>
      <td>${row.lifecycle_stage || '—'}</td>
      <td>${row.variant_class || '—'}</td>
      <td class="num ${colorClass(row.today_return)}">${fmtPct(row.today_return)}</td>
      <td class="num ${colorClass(row.since_inception_return)}">${fmtPct(row.since_inception_return)}</td>
      <td class="num ${colorClass(row.drawdown)}">${fmtPct(row.drawdown)}</td>
      <td class="num">${fmtPct(row.turnover)}</td>
      <td class="num">${fmtPct(row.concentration)}</td>
      <td class="${statusClass(row.promotion_readiness)}">${row.promotion_readiness || '—'}</td>
    </tr>
  `).join('');
}

function renderAlphaComparison(payload) {
  const section = payload.sections?.baseline_alpha_comparison || {};
  setText('alpha-comparison-summary', `${section.summary?.pair_count || 0} alpha pairs · 20/60 day checkpoints`, statusClass(section.status));
  const body = document.getElementById('alpha-comparison-body');
  if (!body) return;
  const rows = section.pairs || [];
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="7">${emptyState('No baseline-vs-alpha pairs available', 'Alpha variants are missing a baseline_strategy_id or comparison metrics.', 'config/research/strategy_registry.json shadow_tracking.baseline_strategy_id', false)}</td></tr>`;
    return;
  }
  body.innerHTML = rows.map(row => {
    const checkpoints = row.review_checkpoints || [];
    const windowText = checkpoints.length
      ? checkpoints.map(check => `${check.observed_days}/${check.trading_days}`).join(' · ')
      : `${row.evidence_window_days || 0}d`;
    return `
      <tr>
        <td>${row.baseline_name || '—'} → ${row.alpha_name || '—'}</td>
        <td class="num ${colorClass(row.return_delta)}">${fmtPct(row.return_delta)}</td>
        <td class="num ${colorClass(-Number(row.drawdown_delta || 0))}">${fmtPct(row.drawdown_delta)}</td>
        <td class="num">${fmtMaybePctDelta(row.alpha_turnover, row.baseline_turnover)}</td>
        <td class="num">${fmtMaybePctDelta(row.alpha_concentration, row.baseline_concentration)}</td>
        <td class="num">${fmtNum(row.alpha_effective_n, 1)}</td>
        <td>${windowText}</td>
      </tr>
    `;
  }).join('');
}

function renderGovernanceState(payload) {
  const section = payload.sections?.governance_state || {};
  const summary = section.summary || {};
  setText(
    'governance-state-summary',
    `pilot blocked: ${summary.pilot_blocked ? 'yes' : 'no'} · promotion blocked: ${summary.promotion_blocked ? 'yes' : 'no'}`,
    summary.pilot_blocked ? 'neg' : 'pos'
  );
  setHTML('governance-state-list', (section.rows || []).map(row => `
    <div class="check-item ${statusClass(row.status)}">
      <strong>${row.name || '—'}</strong>
      <div class="matrix-value ${statusClass(row.status)}">${row.status || '—'}</div>
      <div class="check-detail">${row.detail || '—'}</div>
      <div class="check-detail">Blocks pilot: ${row.pilot_blocking ? 'yes' : 'no'} · Blocks promotion: ${row.promotion_blocking ? 'yes' : 'no'}</div>
    </div>
  `).join('') || emptyState('No governance rows available', 'Governance state could not be derived from dashboard sections.', 'sections.governance_state.rows', false));
}

function renderEvidenceCollection(payload) {
  const live = payload.sections?.live_pilot || {};
  const metrics = live.metrics || {};
  const alpha = payload.sections?.baseline_alpha_comparison || {};
  const governance = payload.sections?.governance_state || {};
  setHTML('evidence-collection-matrix', [
    matrixItem('FR-104 Status', live.status || 'NO_DATA', metrics.idle_cash_reason || 'capped pilot evidence only', statusClass(live.status)),
    matrixItem('Submitted', String(metrics.submitted_count ?? 0), `${metrics.accepted_count ?? 0} accepted · ${metrics.rejected_count ?? 0} rejected`, 'neutral'),
    matrixItem('Clean Recon Rate', fmtPct(metrics.reconciliation_clean_rate), `latest ${live.reconciliation?.status || '—'}`, statusClass(live.reconciliation?.status)),
    matrixItem('Cash Deploy Rate', fmtPct(metrics.cash_deployment_rate), `filled ${fmtMoney(metrics.filled_notional_usd)}`, 'neutral'),
    matrixItem('Alpha Pairs', String(alpha.summary?.pair_count ?? 0), 'shadow-only forward evidence', statusClass(alpha.status)),
    matrixItem('FR-068 Pilot Impact', governance.summary?.fr068_pilot_blocking ? 'pilot-blocking' : 'not pilot-blocking', 'promotion and scaling remain blocked', governance.summary?.fr068_pilot_blocking ? 'neg' : 'pos'),
  ].join(''));
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
  setHTML('decision-intelligence', items.length ? items.join('') : emptyState('No same-day decision changes available', 'No fills or daily return notes were available for the report date.', 'outputs/broker_snapshot/broker_snapshot_<date>.json', false));
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
  `).join('') : emptyState('No readiness criteria available', 'Live readiness section did not produce criteria.', 'sections.live_readiness.criteria', false));
}

function renderDecisionGrade(payload) {
  const section = payload.sections?.decision_grade || {};
  const blockers = section.top_blockers || [];
  const confidence = section.confidence_summary || {};
  setText('decision-grade-summary', `${section.status || 'PARTIAL'} · MQ ${section.latest_model_quality_date || '—'}`, statusClass(section.status));
  const rows = [
    { name: 'Strategy Change', status: section.decision_grade_strategy_change ? 'READY' : 'BLOCKED', detail: section.decision_grade_strategy_change ? 'decision-grade evidence present' : 'no decision-grade change' },
    { name: 'Promotion Ready', status: section.promotion_ready_count > 0 ? 'READY' : 'PARTIAL', detail: `${section.promotion_ready_count || 0} strategies` },
    { name: 'Argo Confidence', status: confidence.argo_recommendation_confidence || 'PARTIAL', detail: `packet ${confidence.model_quality_packet_status || '—'}` },
    { name: 'Phoenix Evidence', status: confidence.phoenix_confidence || 'PARTIAL', detail: `multi-asset ${confidence.multi_asset_status || '—'}` },
  ];
  if (blockers.length) {
    blockers.slice(0, 4).forEach(blocker => rows.push({ name: 'Blocker', status: 'WARN', detail: blocker }));
  }
  setHTML('decision-grade-list', rows.map(row => `
    <div class="check-item ${statusClass(row.status)}">
      <strong>${row.name || '—'}</strong>
      <div class="matrix-value ${statusClass(row.status)}">${row.status || '—'}</div>
      <div class="check-detail">${row.detail || '—'}</div>
    </div>
  `).join('') || emptyState('No decision-grade rows available', 'Decision-grade model-quality artifacts did not produce dashboard rows.', 'outputs/model_quality/<date>/', false));
}

function renderPositions(payload) {
  const section = payload.sections?.positions || {};
  setText('positions-asof', section.as_of ? `as of ${fmtDateTime(section.as_of)}` : 'as of unavailable');
  const body = document.getElementById('positions-body');
  if (!body) return;
  const rows = section.rows || [];
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="6">${emptyState('No positions available', 'Broker position snapshot is missing or empty.', 'outputs/broker/posttrade_positions.json', false)}</td></tr>`;
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
    body.innerHTML = `<tr><td colspan="6">${emptyState('No fills for this report date', 'No broker fills were found for the dashboard report date.', 'outputs/broker_snapshot/broker_snapshot_<date>.json', false)}</td></tr>`;
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
  setHTML('leaders-list', leaders.length ? leaders.map(rankItem).join('') : emptyState('No leader data', 'No current position unrealized P&L is available.', 'outputs/broker/posttrade_positions.json', false));
  setHTML('laggards-list', laggards.length ? laggards.map(rankItem).join('') : emptyState('No laggard data', 'No current position unrealized P&L is available.', 'outputs/broker/posttrade_positions.json', false));
}

function renderSources(payload) {
  const sources = payload.sources || [];
  setHTML('source-list', sources.map(source => `
    <div class="source-item">
      <strong>${source.section}: ${source.label}</strong>
      <div class="matrix-detail">${source.source_type} · ${source.trust_level} · ${source.used ? 'used' : 'missing'}</div>
      <div class="source-path">${source.path || '—'}</div>
    </div>
  `).join('') || emptyState('No source records available', 'Dashboard builder did not record artifact lineage.', 'payload.sources', false));
}

function renderValidation(payload) {
  const checks = payload.validation?.checks || [];
  setText('validation-summary', `${checks.filter(c => c.status === 'pass').length} pass · ${checks.filter(c => c.status === 'warn').length} warn · ${checks.filter(c => c.status === 'fail').length} fail`);
  setHTML('validation-list', checks.map(check => `
    <div class="check-item ${check.status}">
      <strong>${check.name}</strong>
      <div class="check-detail">${check.detail}</div>
    </div>
  `).join('') || emptyState('No validation checks available', 'Dashboard validation tape is missing.', 'payload.validation.checks', true));
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
  const shadowStrategies = payload.sections?.shadow_command_center?.strategies || [];
  const dynamicShadowSeries = shadowStrategies.map((strategy, index) => ({
    color: SHADOW_SERIES_COLORS[strategy.slug] || SHADOW_FALLBACK_COLORS[index % SHADOW_FALLBACK_COLORS.length],
    points: shadowSeries.map(row => ({ date: row.date, value: row[strategy.slug] })),
  }));
  drawLineChart('shadow-excess-chart', dynamicShadowSeries, {
    xLabel: 'Date',
    yLabel: '5D XS %',
    yFormatter: (value) => `${(Number(value) * 100).toFixed(1)}%`,
    zeroLine: true,
  });
}

async function boot() {
  try {
    const payload = await loadDashboardPayload();
    renderStatus(payload);
    renderRibbon(payload);
    renderTopPositions(payload);
    renderSleeveBarCharts(payload);
    renderOperatorControlTower(payload);
    renderPerformanceMatrix(payload);
    renderHealthMatrix(payload);
    renderSystemHealth(payload);
    renderRegime(payload);
    renderShadowCommand(payload);
    renderLivePilot(payload);
    renderAccountLayers(payload);
    renderSleeveInventory(payload);
    renderAlphaComparison(payload);
    renderGovernanceState(payload);
    renderEvidenceCollection(payload);
    renderDecisionIntelligence(payload);
    renderLiveReadiness(payload);
    renderDecisionGrade(payload);
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
