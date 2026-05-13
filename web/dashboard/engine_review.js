'use strict';

function resolveDataPath() {
  let pageBase;
  try {
    pageBase = new URL('.', window.location.href).href;
  } catch (_) {
    pageBase = window.location.href.replace(/\/[^/?#]*([?#].*)?$/, '/');
  }
  let params;
  try {
    params = new URL(window.location.href).searchParams;
  } catch (_) {
    params = new URLSearchParams(window.location.search || '');
  }
  const dataParam = String(params.get('data') || 'dashboard_data.json').trim() || 'dashboard_data.json';
  return new URL(dataParam, pageBase).href;
}

async function fetchJSON(url) {
  try {
    const target = new URL(url, window.location.href);
    target.searchParams.set('_ts', String(Date.now()));
    const response = await fetch(target.href, { cache: 'no-store' });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function fmtPct(v, { decimals = 2, sign = true } = {}) {
  if (v == null || isNaN(v)) return '—';
  const n = Number(v) * 100;
  const s = Math.abs(n).toFixed(decimals) + '%';
  return (n > 0 && sign ? '+' : n < 0 ? '-' : '') + s;
}

function fmtPctRaw(v, { decimals = 2, sign = true } = {}) {
  if (v == null || isNaN(v)) return '—';
  const n = Number(v);
  const s = Math.abs(n).toFixed(decimals) + '%';
  return (n > 0 && sign ? '+' : n < 0 ? '-' : '') + s;
}

function fmtNum(v, { decimals = 2, sign = false } = {}) {
  if (v == null || isNaN(v)) return '—';
  const n = Number(v);
  const s = Math.abs(n).toFixed(decimals);
  return (n > 0 && sign ? '+' : n < 0 ? '-' : '') + s;
}

function fmt$(v, { decimals = 0, sign = false } = {}) {
  if (v == null || isNaN(v)) return '—';
  const n = Number(v);
  const abs = Math.abs(n).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  const prefix = n < 0 ? '-$' : (sign && n > 0 ? '+$' : '$');
  return prefix + abs;
}

function fmtMultiple(v) {
  if (v == null || isNaN(v)) return '—';
  return Number(v).toFixed(2) + 'x';
}

function colorClass(v) {
  if (v == null || isNaN(v)) return '';
  const n = Number(v);
  return n > 0 ? 'pos' : n < 0 ? 'neg' : '';
}

function renderHeader(data) {
  const meta = data.run_meta || {};
  const attr = data.attribution || {};
  const pill = document.getElementById('review-pill');
  const detail = document.getElementById('review-detail');
  const banner = document.getElementById('review-banner');
  const alpha = attr.cumulative_alpha;
  const status = alpha != null && alpha < 0 ? 'warning' : 'operational';

  if (pill) {
    pill.className = 'status-pill ' + status;
    pill.textContent = alpha != null && alpha < 0 ? 'UNDERPERFORMING' : 'REVIEW READY';
  }
  if (detail) {
    detail.textContent = attr.window_start && attr.window_end
      ? `Aligned window ${attr.window_start} to ${attr.window_end} (${attr.n_days || 0} return days)`
      : 'Attribution window unavailable';
  }
  if (banner && meta.status_banner) {
    banner.textContent = meta.status_banner;
    banner.classList.remove('hidden');
  }

  setText('meta-window', attr.window_start && attr.window_end ? `${attr.window_start} → ${attr.window_end}` : '—');
  setText('meta-portfolio-date', meta.portfolio_asof_date || '—');
  setText('meta-benchmark-date', meta.benchmark_asof_date || '—');
  setText('meta-source', meta.performance_source ? String(meta.performance_source).replace(/_/g, ' ') : '—');
  setText('footer-run', meta.run_id ? 'Run: ' + meta.run_id : '');
  setText('footer-updated', meta.last_updated ? 'Updated: ' + meta.last_updated : '');
}

function renderCards(data) {
  const attr = data.attribution || {};
  const edge = data.edge_diagnostics || {};
  const cardMap = {
    'kpi-cum-alpha': [fmtPct(attr.cumulative_alpha), colorClass(attr.cumulative_alpha)],
    'kpi-alpha-ann': [fmtPct(attr.alpha_ann_since), colorClass(attr.alpha_ann_since)],
    'kpi-beta': [fmtNum(attr.beta_since), colorClass((attr.beta_since ?? 1) - 1)],
    'kpi-info-ratio': [fmtNum(attr.info_ratio), colorClass(attr.info_ratio)],
    'kpi-upside': [fmtMultiple(attr.upside_capture), colorClass((attr.upside_capture ?? 1) - 1)],
    'kpi-downside': [fmtMultiple(attr.downside_capture), edge.downside_capture != null && edge.downside_capture < 1 ? 'pos' : ''],
    'kpi-cash-ratio': [fmtPct(edge.current_cash_ratio, { sign: false }), ''],
    'kpi-turnover': [fmtPct(edge.executed_turnover_pct, { sign: false }), ''],
  };
  Object.entries(cardMap).forEach(([id, [value, cls]]) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = value;
    el.className = 'kpi-value' + (cls ? ' ' + cls : '');
  });
}

function renderSignals(data) {
  const attr = data.attribution || {};
  const edge = data.edge_diagnostics || {};
  const signals = Array.isArray(edge.signals) ? edge.signals : [];
  const el = document.getElementById('signals-list');
  if (!el) return;
  setText('signals-window', attr.window_end ? `as of ${attr.window_end}` : '—');
  if (!signals.length) {
    el.innerHTML = '<div class="empty-state">No edge signals available</div>';
    return;
  }
  el.innerHTML = signals.map(item => {
    const status = (item.status || 'pass').toLowerCase();
    return `
      <div class="edge-item ${status}">
        <div class="edge-head">
          <span class="edge-label">${esc(item.label || '')}</span>
          <span class="edge-badge ${status}">${esc(status.toUpperCase())}</span>
        </div>
        <div class="edge-detail">${esc(item.detail || '')}</div>
      </div>
    `;
  }).join('');
}

function renderRecommendations(data) {
  const recs = (data.edge_diagnostics || {}).recommendations || [];
  const el = document.getElementById('recommendations-list');
  if (!el) return;
  if (!recs.length) {
    el.innerHTML = '<div class="empty-state">No recommendations generated</div>';
    return;
  }
  el.innerHTML = recs.map(item => `
    <div class="edge-item warning">
      <div class="edge-head">
        <span class="edge-label">${esc(item.label || '')}</span>
        <span class="edge-badge warning">TEST</span>
      </div>
      <div class="edge-detail">${esc(item.detail || '')}</div>
    </div>
  `).join('');
}

function renderContribution(data) {
  const snap = data.contribution_snapshot || {};
  const el = document.getElementById('contribution-panel');
  if (!el) return;
  const meta = snap.asof_date
    ? `${snap.asof_date}${snap.source_mode ? ' · ' + String(snap.source_mode).replace(/_/g, ' ') : ''}${snap.age_days != null ? ' · ' + snap.age_days + 'd stale' : ''}`
    : 'no contribution artifact';
  setText('contribution-meta', meta);

  if (!(snap.ticker_rows > 0)) {
    el.innerHTML = '<div class="empty-state">No contribution snapshot available</div>';
    return;
  }

  function rows(items, negative) {
    if (!items || !items.length) return '<div class="empty-state">No rows</div>';
    return items.map(item => `
      <div class="contrib-row">
        <div>
          <div class="contrib-name">${esc(item.ticker || item.sleeve || '')}</div>
          <div class="contrib-meta">
            ${item.weight_start != null ? `Wt ${fmtPct(item.weight_start, { sign: false })}` : ''}
            ${item.return != null ? ` · Ret ${fmtPct(item.return)}` : ''}
            ${item.sleeve_return != null ? ` · Ret ${fmtPct(item.sleeve_return)}` : ''}
          </div>
        </div>
        <div class="contrib-val ${negative ? 'neg' : colorClass(item.contribution)}">${fmtPct(item.contribution)}</div>
      </div>
    `).join('');
  }

  el.innerHTML = `
    <div class="contribution-summary">
      <div class="contrib-stat">
        <div class="contrib-stat-label">Net Contribution</div>
        <div class="contrib-stat-value ${colorClass(snap.net_contribution)}">${fmtPct(snap.net_contribution)}</div>
      </div>
      <div class="contrib-stat">
        <div class="contrib-stat-label">Positive Names</div>
        <div class="contrib-stat-value">${esc(String(snap.positive_contributors || 0))}</div>
      </div>
      <div class="contrib-stat">
        <div class="contrib-stat-label">Negative Names</div>
        <div class="contrib-stat-value">${esc(String(snap.negative_contributors || 0))}</div>
      </div>
    </div>
    <div class="contribution-grid">
      <div class="contrib-col">
        <div class="contrib-title">Top Winners</div>
        ${rows(snap.top_winners, false)}
      </div>
      <div class="contrib-col">
        <div class="contrib-title">Top Laggards</div>
        ${rows(snap.top_laggards, true)}
      </div>
    </div>
  `;
}

function renderPositions(data) {
  const positions = (data.edge_diagnostics || {}).top_positions || [];
  const tbody = document.getElementById('positions-body');
  if (!tbody) return;
  if (!positions.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty-state">No live positions available</td></tr>';
    return;
  }
  tbody.innerHTML = positions.map(item => `
    <tr>
      <td style="font-family:var(--font-mono);font-weight:600">${esc(item.ticker || '')}</td>
      <td style="font-family:var(--font-mono)">${fmtPct(item.weight, { sign: false })}</td>
      <td style="font-family:var(--font-mono)">${fmt$(item.market_value)}</td>
      <td class="${colorClass(item.unrealized_plpc)}" style="font-family:var(--font-mono)">${fmtPct(item.unrealized_plpc)}</td>
    </tr>
  `).join('');
}

function renderRelativeDays(data) {
  const attr = data.attribution || {};
  const best = attr.best_relative_day || null;
  const worst = attr.worst_relative_day || null;
  const el = document.getElementById('relative-days');
  if (!el) return;
  if (!best && !worst) {
    el.innerHTML = '<div class="empty-state">No relative-day diagnostics available</div>';
    return;
  }
  const rows = [
    { label: 'Best Relative Day', value: best ? `${best.date} · ${fmtPct(best.spread)}` : '—', cls: best ? colorClass(best.spread) : '' },
    { label: 'Best Day Detail', value: best ? `Port ${fmtPct(best.port_return)} vs SPY ${fmtPct(best.benchmark_return)}` : '—', cls: '' },
    { label: 'Worst Relative Day', value: worst ? `${worst.date} · ${fmtPct(worst.spread)}` : '—', cls: worst ? colorClass(worst.spread) : '' },
    { label: 'Worst Day Detail', value: worst ? `Port ${fmtPct(worst.port_return)} vs SPY ${fmtPct(worst.benchmark_return)}` : '—', cls: '' },
  ];
  el.innerHTML = rows.map(row => `
    <div class="stat-row">
      <span class="stat-label">${esc(row.label)}</span>
      <span class="stat-val ${row.cls}">${esc(row.value)}</span>
    </div>
  `).join('');
}

async function boot() {
  const dataPath = resolveDataPath();
  const data = await fetchJSON(dataPath);
  if (!data) {
    const pill = document.getElementById('review-pill');
    if (pill) {
      pill.className = 'status-pill degraded';
      pill.textContent = 'NO DATA';
    }
    setText('review-detail', 'Could not load dashboard_data.json');
    return;
  }
  renderHeader(data);
  renderCards(data);
  renderSignals(data);
  renderRecommendations(data);
  renderContribution(data);
  renderPositions(data);
  renderRelativeDays(data);
}

document.addEventListener('DOMContentLoaded', boot);
