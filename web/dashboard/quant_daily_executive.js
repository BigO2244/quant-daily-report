/* ═══════════════════════════════════════════════════════════════
   Caerus Quant — Portfolio Monitor  |  quant_daily_executive.js
   No external dependencies. Canvas charts, vanilla JS.
   ═══════════════════════════════════════════════════════════════ */

'use strict';

// ── Data path resolution ──────────────────────────────────────────────────────
// Uses window.location (always correct, always available) rather than
// document.currentScript (null inside async/deferred contexts).
// dashboard_data.json lives beside the HTML; trading_day_summary.json is at
// repo root /outputs/ — two directory levels up from web/dashboard/.
function resolveDataPaths() {
  let pageBase;
  try {
    pageBase = new URL('.', window.location.href).href;
  } catch (_) {
    pageBase = window.location.href.replace(/\/[^/?#]*([?#].*)?$/, '/');
  }
  return {
    dashData: pageBase + 'dashboard_data.json',
    summary:  new URL('../../outputs/trading_day_summary.json', pageBase).href,
  };
}

// ── Fetch helpers ─────────────────────────────────────────────────────────────

async function fetchJSON(url) {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

// ── Formatting ────────────────────────────────────────────────────────────────

function fmt$(v, { decimals = 0, sign = false } = {}) {
  if (v == null || isNaN(v)) return '—';
  const n   = Number(v);
  const abs = Math.abs(n).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  const prefix = n < 0 ? '-$' : (sign && n > 0 ? '+$' : '$');
  return prefix + abs;
}

// v is a fractional value (0.0075 = 0.75%); multiplied by 100 internally.
function fmtPct(v, { decimals = 2, sign = true } = {}) {
  if (v == null || isNaN(v)) return '—';
  const n = Number(v) * 100;
  const s = Math.abs(n).toFixed(decimals) + '%';
  return (n > 0 && sign ? '+' : n < 0 ? '-' : '') + s;
}

// v is already in percent units (5.1 = 5.1%).
function fmtPctRaw(v, { decimals = 2, sign = true } = {}) {
  if (v == null || isNaN(v)) return '—';
  const n = Number(v);
  const s = Math.abs(n).toFixed(decimals) + '%';
  return (n > 0 && sign ? '+' : n < 0 ? '-' : '') + s;
}

// Returns 'pos' / 'neg' / '' — zero is always neutral (not green).
function colorClass(v) {
  if (v == null || isNaN(v)) return '';
  const n = Number(v);
  return n > 0 ? 'pos' : n < 0 ? 'neg' : '';
}

function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── CSS variable reader ───────────────────────────────────────────────────────

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ── Merge data sources ────────────────────────────────────────────────────────

function mergeData(dash, summary) {
  if (!dash && !summary) return null;
  const d = dash ? Object.assign({}, dash) : {};  // shallow copy — don't mutate original
  const s = summary || {};

  const execSummary = s.execution_summary || {};
  const portState   = s.portfolio_state   || {};

  // Patch activity from trading_day_summary (execution_summary is more authoritative
  // because it comes from the actual broker response, not a derived estimate).
  if (d.activity) {
    if (execSummary.buy_orders    != null) d.activity = Object.assign({}, d.activity, { buys:          execSummary.buy_orders });
    if (execSummary.sell_orders   != null) d.activity = Object.assign({}, d.activity, { sells:         execSummary.sell_orders });
    if (execSummary.orders_accepted != null) d.activity = Object.assign({}, d.activity, { orders_filled: execSummary.orders_accepted });
  }

  // Track cash source explicitly so KPI rendering doesn't use a fragile heuristic.
  // Priority: broker execution result > governed snapshot > risk ratio.
  if (portState.cash_after != null) {
    d._cash     = portState.cash_after;
    d._cashSrc  = 'execution';
  } else if ((d.governed_snapshot || {}).cash != null) {
    d._cash     = d.governed_snapshot.cash;
    d._cashSrc  = 'governed';
  } else if ((d.risk || {}).cash_position != null) {
    d._cash     = d.risk.cash_position;
    d._cashSrc  = 'ratio';    // fractional 0–1
  } else {
    d._cash    = null;
    d._cashSrc = null;
  }

  d._summary = s;
  return d;
}

// ── Deterministic status classification ───────────────────────────────────────
//
// Rules (evaluated in order):
//   1. SHADOW / SIMULATED mode           → warning  (not a live run)
//   2. FAIL or ERROR overall status      → degraded (live run with hard failure)
//   3. PASS / OK / EXECUTED              → operational
//   4. Everything else                   → warning
//
// This ordering prevents a FAIL-status shadow run from showing as DEGRADED,
// which would be misleading — shadow failures are expected and non-actionable.

function classifyStatus(d) {
  const meta   = d.run_meta || {};
  const mode   = (meta.mode || '').toUpperCase();
  const status = (meta.overall_status || '').toUpperCase();
  const s      = d._summary || {};
  const exec   = (s.execution_summary || {}).status || '';

  if (mode === 'SHADOW' || mode === 'SIMULATED' || mode === 'ALPACA_PAPER_SHADOW') {
    return 'warning';
  }
  if (status === 'FAIL' || status === 'ERROR' || exec === 'FAILED') {
    return 'degraded';
  }
  if (status === 'PASS' || status === 'OK' || exec === 'EXECUTED' || exec === 'IDEMPOTENT_REPLAY') {
    return 'operational';
  }
  return 'warning';
}

// ── Header ────────────────────────────────────────────────────────────────────

function renderHeader(d) {
  const meta = d.run_meta || {};
  const s    = d._summary || {};
  const exec = s.execution_summary || {};

  setText('meta-run-id',  meta.run_id
    ? meta.run_id.slice(0, 32) + (meta.run_id.length > 32 ? '…' : '')
    : '—');
  setText('meta-date',    meta.report_date || '—');
  setText('meta-mode',    (meta.mode || '—').toUpperCase());
  setText('meta-updated', meta.last_updated ? fmtTimestamp(meta.last_updated) : '—');

  const tier   = classifyStatus(d);
  const pill   = document.getElementById('status-pill');
  const detail = document.getElementById('status-detail');

  if (pill) {
    pill.className   = 'status-pill ' + tier;
    pill.textContent = pillLabel(tier, meta.mode, exec.status);
  }
  if (detail) detail.textContent = detailText(d, tier);

  const banner = document.getElementById('status-banner');
  if (banner) {
    const msg = meta.status_banner || '';
    if (msg) {
      banner.textContent = msg;
      banner.classList.remove('hidden');
    } else {
      banner.classList.add('hidden');
    }
  }
}

function pillLabel(tier, mode, execStatus) {
  const m = (mode || '').toUpperCase();
  if (m === 'SHADOW')           return 'SHADOW MODE';
  if (m === 'SIMULATED')        return 'SIMULATED';
  if (execStatus === 'IDEMPOTENT_REPLAY') return 'REPLAY';
  if (tier === 'operational')   return 'OPERATIONAL';
  if (tier === 'degraded')      return 'DEGRADED';
  return 'WARNING';
}

function detailText(d, tier) {
  const meta = d.run_meta || {};
  const s    = d._summary || {};
  const exec = s.execution_summary || {};
  const mode = (meta.mode || '').toUpperCase();

  if (exec.status === 'EXECUTED') {
    return `${exec.orders_accepted || 0} orders filled — pipeline complete`;
  }
  if (exec.status === 'IDEMPOTENT_REPLAY') {
    return 'Orders confirmed from prior submission — no duplicate trades placed';
  }
  if (mode === 'SHADOW' || mode === 'SIMULATED') {
    const overall = (meta.overall_status || '').toUpperCase();
    if (overall === 'FAIL') return 'Shadow run ended with failures — review exceptions below';
    return 'Shadow simulation — no live orders placed';
  }
  if (tier === 'degraded') return 'Pipeline failure — review exceptions below';
  return meta.status_banner ? '' : 'Pipeline status loaded';
}

function fmtTimestamp(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
      + ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });
  } catch { return ts; }
}

// ── KPI Cards ─────────────────────────────────────────────────────────────────

function renderKPIs(d) {
  const kpi  = d.kpis              || {};
  const risk = d.risk              || {};
  const act  = d.activity          || {};
  const gov  = d.governed_snapshot || {};
  const meta = d.run_meta          || {};
  const s    = d._summary          || {};
  const exec = s.execution_summary || {};
  const port = s.portfolio_state   || {};

  // ── Portfolio Value ── governed snapshot is authoritative; kpis is fallback
  const pv = gov.portfolio_value ?? kpi.portfolio_value;
  setKPI('kpi-portfolio-value', fmt$(pv), 'governed NAV');

  // ── Daily P&L ── dollar amount + % return in subtitle
  const pl   = kpi.daily_pl;
  const plEl = document.getElementById('kpi-daily-pnl');
  if (plEl) {
    const val = plEl.querySelector('.kpi-value');
    const sub = plEl.querySelector('.kpi-sub');
    if (val) {
      val.textContent = fmt$(pl, { sign: true });
      val.className   = 'kpi-value ' + colorClass(pl);
    }
    if (sub) {
      const ret = kpi.daily_return;
      sub.textContent = ret != null ? fmtPct(ret) + ' vs prior close' : 'vs prior close';
    }
  }

  // ── Alpha vs SPY ── excess_return is fractional; show SPY return in subtitle
  const alphaEl = document.getElementById('kpi-vs-spy');
  if (alphaEl) {
    const alpha = kpi.excess_return;
    const val   = alphaEl.querySelector('.kpi-value');
    const sub   = alphaEl.querySelector('.kpi-sub');
    if (val) {
      val.textContent = fmtPct(alpha);
      val.className   = 'kpi-value ' + colorClass(alpha);
    }
    if (sub) {
      const bench = kpi.benchmark_return;
      sub.textContent = bench != null
        ? 'SPY ' + fmtPct(bench) + ' today'
        : "today's excess return";
    }
  }

  // ── Cash ── source tracked explicitly in mergeData; ratio renders as %
  const cashEl = document.getElementById('kpi-cash');
  if (cashEl) {
    const val = cashEl.querySelector('.kpi-value');
    const sub = cashEl.querySelector('.kpi-sub');
    const cash = d._cash;
    const src  = d._cashSrc;
    if (val) val.textContent = src === 'ratio' ? fmtPct(cash, { sign: false }) : fmt$(cash);
    if (sub) sub.textContent = src === 'execution' ? 'post-execution'
                             : src === 'governed'  ? 'governed snapshot'
                             : src === 'ratio'     ? 'cash ratio'
                             : 'after execution';
  }

  // ── Capital Deployed ── gross_exposure is fractional; turnover limit as context
  const deployedEl = document.getElementById('kpi-deployment');
  if (deployedEl) {
    const val = deployedEl.querySelector('.kpi-value');
    const sub = deployedEl.querySelector('.kpi-sub');
    const gross = risk.gross_exposure;
    if (val) val.textContent = gross != null ? fmtPct(gross, { sign: false }) : '—';
    if (sub) {
      const limit = risk.turnover_limit_pct;
      sub.textContent = limit != null
        ? `gross exposure · limit ${fmtPct(limit, { sign: false })}`
        : 'gross exposure';
    }
  }

  // ── Positions ── portfolio_state is more accurate (reflects actual broker state)
  const pos = port.positions_count ?? kpi.holdings;
  setKPI('kpi-positions', pos != null ? String(pos) : '—', 'held today');

  // ── Trades Executed ── accepted from execution_summary; falls back to activity
  const filled = exec.orders_accepted ?? act.orders_filled;
  setKPI('kpi-trades', filled != null ? String(filled) : '—', 'accepted orders');

  // ── System Status ── execution status is most granular; kpis.run_status is fallback
  const sysEl = document.getElementById('kpi-system');
  if (sysEl) {
    const val    = sysEl.querySelector('.kpi-value');
    const sub    = sysEl.querySelector('.kpi-sub');
    // Correct fallback chain: exec summary → kpis.run_status → mode
    const status = exec.status || kpi.run_status || meta.mode || '—';
    if (val) {
      val.textContent = status;
      val.className   = 'kpi-value kpi-value--status ' + statusColor(status);
    }
    if (sub) sub.textContent = 'pipeline health';
  }
}

function setKPI(id, value, sub) {
  const el = document.getElementById(id);
  if (!el) return;
  const val   = el.querySelector('.kpi-value');
  const subEl = el.querySelector('.kpi-sub');
  if (val)            val.textContent  = value;
  if (subEl && sub)   subEl.textContent = sub;
}

function statusColor(s) {
  s = (s || '').toUpperCase();
  if (['EXECUTED', 'OK', 'PASS', 'IDEMPOTENT_REPLAY'].includes(s)) return 'pos';
  if (['FAIL', 'FAILED', 'ERROR', 'DEGRADED'].includes(s))          return 'neg';
  return 'warn';
}

// ── Performance stats sidebar ─────────────────────────────────────────────────

function renderPerfStats(d) {
  const ps  = d.perf_summary || {};
  const s   = d._summary     || {};
  const ben = s.benchmark    || {};

  // Drawdown: stored as a positive fraction in some runs, negative in others.
  // Normalize to a negative display value.
  const dd = ps.current_drawdown;
  const ddDisplay = dd != null ? -Math.abs(dd) : null;

  const rows = [
    { label: 'MTD Return',        val: fmtPct(ps.mtd_return),             cls: colorClass(ps.mtd_return) },
    { label: 'QTD Return',        val: fmtPct(ps.qtd_return),             cls: colorClass(ps.qtd_return) },
    { label: 'Since Inception',   val: fmtPct(ps.since_inception_return), cls: colorClass(ps.since_inception_return) },
    { label: 'Alpha (inception)', val: fmtPct(ps.since_inception_alpha),  cls: colorClass(ps.since_inception_alpha) },
    { label: 'Drawdown',          val: fmtPct(ddDisplay),                 cls: ddDisplay ? 'neg' : '' },
    { label: 'Best Day',          val: fmtPct(ps.best_day),               cls: colorClass(ps.best_day) },
    { label: 'Worst Day',         val: fmtPct(ps.worst_day),              cls: colorClass(ps.worst_day) },
  ];

  const container = document.getElementById('perf-stats');
  if (!container) return;
  container.innerHTML = rows.map(r =>
    `<div class="stat-row">
      <span class="stat-label">${esc(r.label)}</span>
      <span class="stat-val ${r.cls}">${r.val}</span>
    </div>`
  ).join('');
}

// ── Canvas Charts ─────────────────────────────────────────────────────────────

const CHART_PAD = { top: 20, right: 16, bottom: 36, left: 58 };

function setupCanvas(canvas) {
  const dpr  = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  const w    = rect.width || 600;
  const h    = 240;
  canvas.width  = w * dpr;
  canvas.height = h * dpr;
  canvas.style.width  = w + 'px';
  canvas.style.height = h + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  return { ctx, w, h };
}

function chartArea(w, h, pad) {
  return {
    x0: pad.left,   y0: pad.top,
    x1: w - pad.right, y1: h - pad.bottom,
    width: w - pad.left - pad.right,
    height: h - pad.top - pad.bottom,
  };
}

function yScale(val, minV, maxV, area) {
  if (maxV === minV) return (area.y0 + area.y1) / 2;
  return area.y1 - ((val - minV) / (maxV - minV)) * area.height;
}

function xScale(i, total, area) {
  if (total <= 1) return (area.x0 + area.x1) / 2;
  return area.x0 + (i / (total - 1)) * area.width;
}

// Produce well-rounded tick values rather than evenly-spaced raw values.
function computeYTicks(minV, maxV, area, targetTicks = 5) {
  if (maxV === minV) return [{ v: minV, y: (area.y0 + area.y1) / 2 }];
  const range   = maxV - minV;
  const rawStep = range / (targetTicks - 1);
  const mag     = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm    = rawStep / mag;
  const step    = norm < 1.5 ? mag : norm < 3 ? 2 * mag : norm < 7 ? 5 * mag : 10 * mag;
  const start   = Math.ceil((minV - step * 0.001) / step) * step;
  const ticks   = [];
  for (let i = 0; i < 20; i++) {
    const v = start + i * step;
    if (v > maxV + step * 0.001) break;
    ticks.push({ v, y: yScale(v, minV, maxV, area) });
  }
  return ticks.length ? ticks : [{ v: minV, y: area.y1 }, { v: maxV, y: area.y0 }];
}

// Format a tick label using precision appropriate to the step size.
function tickFmtFn(step, suffix = '') {
  const d = step < 0.001 ? 4 : step < 0.01 ? 3 : step < 0.1 ? 2 : step < 1 ? 1 : 0;
  return v => v.toFixed(d) + suffix;
}

function drawGrid(ctx, area, ticks) {
  ctx.strokeStyle = cssVar('--c-border-soft');
  ctx.lineWidth   = 0.5;
  ticks.forEach(({ y }) => {
    ctx.beginPath();
    ctx.moveTo(area.x0, y);
    ctx.lineTo(area.x1, y);
    ctx.stroke();
  });
}

function drawYAxis(ctx, area, ticks, fmtFn) {
  ctx.fillStyle    = cssVar('--c-text-dim');
  ctx.font         = `11px ${cssVar('--font-mono')}`;
  ctx.textAlign    = 'right';
  ctx.textBaseline = 'middle';
  ticks.forEach(({ v, y }) => ctx.fillText(fmtFn(v), area.x0 - 6, y));
}

// Always include the first and last date label; fill intermediate slots evenly.
function drawXLabels(ctx, area, labels) {
  if (!labels.length) return;
  ctx.fillStyle    = cssVar('--c-text-dim');
  ctx.font         = `10px ${cssVar('--font-mono')}`;
  ctx.textBaseline = 'top';
  const n       = labels.length;
  const maxShow = 6;
  const indices = new Set([0, n - 1]);
  if (n > 2) {
    const step = Math.max(1, Math.floor((n - 1) / (maxShow - 1)));
    for (let i = step; i < n - 1; i += step) indices.add(i);
  }
  Array.from(indices).sort((a, b) => a - b).forEach(i => {
    ctx.textAlign = i === 0 ? 'left' : i === n - 1 ? 'right' : 'center';
    ctx.fillText(labels[i] || '', xScale(i, n, area), area.y1 + 6);
  });
}

function drawLine(ctx, points, color, lineWidth = 1.5) {
  if (!points.length) return;
  ctx.beginPath();
  ctx.strokeStyle = color;
  ctx.lineWidth   = lineWidth;
  ctx.lineJoin    = 'round';
  points.forEach(([x, y], i) => i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y));
  ctx.stroke();
}

function fillUnder(ctx, points, color, yBaseline) {
  if (points.length < 2) return;
  ctx.beginPath();
  ctx.moveTo(points[0][0], yBaseline);
  points.forEach(([x, y]) => ctx.lineTo(x, y));
  ctx.lineTo(points[points.length - 1][0], yBaseline);
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
}

function drawDot(ctx, x, y, color, r = 4) {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

function drawNoData(ctx, w, h) {
  ctx.fillStyle    = cssVar('--c-text-dim');
  ctx.font         = `12px ${cssVar('--font-sans')}`;
  ctx.textAlign    = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('No data available', w / 2, h / 2);
}

// NAV vs SPY — both series plotted on a shared date axis so dates align correctly.
// Each series is independently indexed to 100 at its own first data point.
function drawNavChart(d) {
  const canvas = document.getElementById('chart-nav');
  if (!canvas) return;

  const nav   = (d.series || {}).nav       || [];
  const bench = (d.series || {}).benchmark || [];

  // Build the union of all dates, sorted chronologically.
  // YYYY-MM-DD strings sort lexicographically = chronologically.
  const dateSet = new Set();
  nav.forEach(p   => dateSet.add(p.date));
  bench.forEach(p => dateSet.add(p.date));
  const dates = Array.from(dateSet).sort();

  const windowEl = document.getElementById('nav-chart-window');
  if (windowEl) {
    windowEl.textContent = dates.length > 1
      ? `${dates[0]} – ${dates[dates.length - 1]}`
      : (dates[0] || '—');
  }

  const { ctx, w, h } = setupCanvas(canvas);
  const area = chartArea(w, h, CHART_PAD);

  if (!dates.length) { drawNoData(ctx, w, h); return; }

  // Index each series to 100 at its own first point; produce date → value maps.
  function indexSeries(series) {
    if (!series.length) return new Map();
    const base = series[0].value || 1;
    const m = new Map();
    series.forEach(p => m.set(p.date, (p.value / base) * 100));
    return m;
  }

  const navMap   = indexSeries(nav);
  const benchMap = indexSeries(bench);

  const allV = [...navMap.values(), ...benchMap.values()];
  if (!allV.length) { drawNoData(ctx, w, h); return; }

  const minV = Math.min(...allV);
  const maxV = Math.max(...allV);
  const pad  = (maxV - minV) * 0.06 || 1;
  const lo   = minV - pad;
  const hi   = maxV + pad;

  const ticks = computeYTicks(lo, hi, area);
  const step  = ticks.length > 1 ? Math.abs(ticks[1].v - ticks[0].v) : 1;
  drawGrid(ctx, area, ticks);
  drawYAxis(ctx, area, ticks, tickFmtFn(step));

  // Map each series to screen coordinates at its known dates; skip missing dates.
  function seriesPts(map) {
    return dates
      .map((date, i) => map.has(date)
        ? [xScale(i, dates.length, area), yScale(map.get(date), lo, hi, area)]
        : null)
      .filter(Boolean);
  }

  const benchPts = seriesPts(benchMap);
  if (benchPts.length >= 2) {
    fillUnder(ctx, benchPts, 'rgba(100,116,139,0.08)', area.y1);
    drawLine(ctx, benchPts, cssVar('--c-muted'), 1.5);
  }

  const navPts = seriesPts(navMap);
  if (navPts.length >= 2) {
    fillUnder(ctx, navPts, 'rgba(59,130,246,0.10)', area.y1);
    drawLine(ctx, navPts, cssVar('--c-blue'), 2);
  } else if (navPts.length === 1) {
    // Single point: draw a dot and a horizontal reference at 100
    const [px, py] = navPts[0];
    ctx.setLineDash([3, 4]);
    ctx.strokeStyle = cssVar('--c-blue');
    ctx.lineWidth   = 1;
    ctx.beginPath(); ctx.moveTo(area.x0, py); ctx.lineTo(area.x1, py); ctx.stroke();
    ctx.setLineDash([]);
    drawDot(ctx, px, py, cssVar('--c-blue'), 4);
  }

  drawXLabels(ctx, area, dates.map(d => d.slice(5)));  // MM-DD labels
}

// Daily returns — vertical bar chart with a zero baseline.
function drawDailyChart(d) {
  const canvas = document.getElementById('chart-daily');
  if (!canvas) return;

  const series = (d.series || {}).daily_returns || [];
  const { ctx, w, h } = setupCanvas(canvas);
  if (!series.length) { drawNoData(ctx, w, h); return; }

  const area  = chartArea(w, h, CHART_PAD);
  const vals  = series.map(p => p.value * 100);        // fractional → %
  const rawLo = Math.min(0, ...vals);
  const rawHi = Math.max(0, ...vals);
  const pad   = Math.max(Math.abs(rawHi - rawLo) * 0.1, 0.05);
  const lo    = rawLo - pad;
  const hi    = rawHi + pad;

  const ticks = computeYTicks(lo, hi, area);
  const step  = ticks.length > 1 ? Math.abs(ticks[1].v - ticks[0].v) : 0.01;
  drawGrid(ctx, area, ticks);
  drawYAxis(ctx, area, ticks, tickFmtFn(step, '%'));

  const y0  = yScale(0, lo, hi, area);
  ctx.strokeStyle = cssVar('--c-border');
  ctx.lineWidth   = 0.8;
  ctx.beginPath(); ctx.moveTo(area.x0, y0); ctx.lineTo(area.x1, y0); ctx.stroke();

  const barW = Math.max(2, (area.width / series.length) * 0.7);
  vals.forEach((v, i) => {
    const x  = xScale(i, series.length, area);
    const yV = yScale(v, lo, hi, area);
    ctx.fillStyle = v > 0 ? cssVar('--c-green') : v < 0 ? cssVar('--c-red') : cssVar('--c-muted');
    ctx.fillRect(x - barW / 2, Math.min(y0, yV), barW, Math.abs(y0 - yV) || 1);
  });

  drawXLabels(ctx, area, series.map(p => p.date.slice(5)));
}

// Drawdown — area chart, always ≤ 0.
function drawDrawdownChart(d) {
  const canvas = document.getElementById('chart-drawdown');
  if (!canvas) return;

  const series = (d.series || {}).drawdown || [];
  const { ctx, w, h } = setupCanvas(canvas);
  if (!series.length) { drawNoData(ctx, w, h); return; }

  const area = chartArea(w, h, CHART_PAD);
  const vals  = series.map(p => -(Math.abs(p.value)) * 100);  // always ≤ 0 in %
  const lo    = Math.min(...vals, -0.1) * 1.12;
  const hi    = 0;

  const ticks = computeYTicks(lo, hi, area, 4);
  const step  = ticks.length > 1 ? Math.abs(ticks[1].v - ticks[0].v) : 0.01;
  drawGrid(ctx, area, ticks);
  drawYAxis(ctx, area, ticks, tickFmtFn(step, '%'));

  const pts   = vals.map((v, i) => [xScale(i, vals.length, area), yScale(v, lo, hi, area)]);
  const yZero = yScale(0, lo, hi, area);
  fillUnder(ctx, pts, 'rgba(248,113,113,0.15)', yZero);
  drawLine(ctx, pts, cssVar('--c-red'), 1.5);

  drawXLabels(ctx, area, series.map(p => p.date.slice(5)));
}

// ── Execution row ─────────────────────────────────────────────────────────────

function renderExecStats(d) {
  const act  = d.activity          || {};
  const risk = d.risk              || {};
  const s    = d._summary          || {};
  const exec = s.execution_summary || {};

  // Submitted: prefer execution_summary; derive from filled+rejected if absent.
  const submitted = exec.orders_submitted
    ?? ((act.orders_filled ?? 0) + (act.orders_rejected ?? 0));
  const accepted  = exec.orders_accepted ?? act.orders_filled;
  const rejected  = act.orders_rejected;
  const dupes     = exec.duplicate_orders;
  const status    = exec.status || (d.run_meta || {}).run_status || (d.kpis || {}).run_status || '—';

  const rows = [
    { label: 'Status',     val: status,     cls: statusColor(status) },
    { label: 'Submitted',  val: submitted != null ? String(submitted) : '—', cls: '' },
    { label: 'Accepted',   val: accepted  != null ? String(accepted)  : '—', cls: accepted  > 0 ? 'pos' : '' },
    { label: 'Rejected',   val: rejected  != null ? String(rejected)  : '—', cls: rejected  > 0 ? 'neg' : '' },
    { label: 'Duplicates', val: dupes     != null ? String(dupes)     : '—', cls: '' },
    { label: 'Turnover',   val: fmtPct(risk.turnover_pct, { sign: false }), cls: '' },
    { label: 'Breaker',    val: risk.breaker_status || '—', cls: breakerCls(risk.breaker_status) },
  ];

  const el = document.getElementById('exec-stats');
  if (!el) return;
  el.innerHTML = rows.map(r =>
    `<div class="stat-row">
      <span class="stat-label">${esc(r.label)}</span>
      <span class="stat-val ${r.cls}">${esc(r.val)}</span>
    </div>`
  ).join('');
}

function breakerCls(s) {
  switch ((s || '').toUpperCase()) {
    case 'OFF':
    case 'CLEAR': return 'pos';
    case 'LOCK':  return 'neg';
    case 'PARTIAL': return 'warn';
    default: return '';
  }
}

function renderOrderFlow(d) {
  const act  = d.activity          || {};
  const s    = d._summary          || {};
  const exec = s.execution_summary || {};

  const buys   = exec.buy_orders  ?? act.buys;
  const sells  = exec.sell_orders ?? act.sells;
  const newPos = act.new_positions;
  const exits  = act.full_exits;
  const dupes  = exec.duplicate_orders;

  const cards = [
    { cls: 'buy',  num: buys  ?? '—', lbl: 'Buy Orders' },
    { cls: 'sell', num: sells ?? '—', lbl: 'Sell Orders' },
    { cls: '',     num: newPos != null ? String(newPos) : '—', lbl: 'New Positions' },
    { cls: '',     num: exits  != null ? String(exits)  : '—', lbl: 'Full Exits' },
  ];
  if (dupes) cards.push({ cls: 'dup', num: String(dupes), lbl: 'Replays' });

  const el = document.getElementById('order-flow');
  if (!el) return;
  el.innerHTML = cards.map(c =>
    `<div class="of-card ${c.cls}">
      <div class="of-num">${esc(String(c.num))}</div>
      <div class="of-lbl">${esc(c.lbl)}</div>
    </div>`
  ).join('');
}

function renderBrokerStats(d) {
  const bk  = d.broker_snapshot  || {};
  const gov = d.governed_snapshot || {};
  const df  = d.data_freshness   || {};

  const el = document.getElementById('broker-stats');
  if (!el) return;

  let html = '';

  // Suspicious broker value — show as a prominent yellow alert before the stat rows.
  if (bk.suspicious && bk.confidence_note) {
    html += `<div class="broker-alert">${esc(bk.confidence_note)}</div>`;
  }

  // Broker freshness note — surfaces alignment_detail when non-trivial.
  if (df.alignment_detail) {
    const isBad = (df.broker_vs_run_alignment || '').toLowerCase() !== 'aligned';
    if (isBad) {
      html += `<div class="broker-alert broker-alert--soft">${esc(df.alignment_detail)}</div>`;
    }
  }

  const govVal  = gov.portfolio_value;
  const bkVal   = bk.suspicious ? null : (bk.display_equity ?? bk.equity);

  const rows = [
    { label: 'Governed NAV',  val: fmt$(govVal),  cls: '' },
    { label: 'Broker Equity', val: bk.suspicious ? 'suppressed — see above' : fmt$(bkVal), cls: bk.suspicious ? 'warn' : '' },
    { label: 'Broker Cash',   val: bk.suspicious ? '—' : fmt$(bk.cash), cls: '' },
    { label: 'Mkt Value',     val: bk.suspicious ? '—' : fmt$(bk.market_value), cls: '' },
    { label: 'Broker As Of',  val: bk.as_of ? fmtTimestamp(bk.as_of) : '—', cls: '' },
    { label: 'Alignment',     val: df.broker_vs_run_alignment || '—', cls: alignCls(df.broker_vs_run_alignment) },
  ];

  html += rows.map(r =>
    `<div class="stat-row">
      <span class="stat-label">${esc(r.label)}</span>
      <span class="stat-val ${r.cls}">${esc(r.val)}</span>
    </div>`
  ).join('');

  el.innerHTML = html;
}

function alignCls(s) {
  switch ((s || '').toLowerCase()) {
    case 'aligned': return 'pos';
    case 'stale':
    case 'mismatch': return 'warn';
    default: return '';
  }
}

// ── Portfolio row ─────────────────────────────────────────────────────────────

function renderTopChanges(d) {
  const changes = d.top_changes || [];
  const tbody   = document.getElementById('changes-body');
  if (!tbody) return;
  if (!changes.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty-state">No changes recorded</td></tr>';
    return;
  }
  tbody.innerHTML = changes.map(c => {
    const cls = c.action === 'BUY' ? 'pos' : c.action === 'SELL' ? 'neg' : '';
    const wt  = c.change_weight != null
      ? fmtPctRaw(c.change_weight * 100, { sign: true })
      : '—';
    return `<tr>
      <td style="font-family:var(--font-mono);font-weight:600">${esc(c.ticker)}</td>
      <td class="${cls}" style="font-family:var(--font-mono)">${esc(c.action)}</td>
      <td class="${cls}" style="font-family:var(--font-mono)">${wt}</td>
      <td style="color:var(--c-text-2);font-size:0.78rem">${esc((c.reason || '').replace(/_/g, ' '))}</td>
    </tr>`;
  }).join('');
}

// Allocation bars show today's executed position weight deltas, not holdings.
// buy change_weight values are the fraction of NAV traded INTO that position.
function renderAllocBars(d) {
  const risk       = d.risk        || {};
  const topChanges = d.top_changes || [];
  const gov        = d.governed_snapshot || {};

  const el = document.getElementById('alloc-bars');
  if (!el) return;

  const items = [];
  topChanges
    .filter(c => c.action === 'BUY' && c.change_weight != null)
    .forEach(c => items.push({
      label: c.ticker,
      pct:   Math.abs(c.change_weight) * 100,
      isCash: false,
    }));

  // Cash: prefer governed snapshot over risk ratio for dollar sense.
  const govCash  = gov.cash;
  const govTotal = gov.portfolio_value || gov.equity;
  if (govCash != null && govTotal != null && govTotal > 0) {
    items.push({ label: 'CASH', pct: (govCash / govTotal) * 100, isCash: true });
  } else if (risk.cash_position != null) {
    items.push({ label: 'CASH', pct: risk.cash_position * 100, isCash: true });
  }

  if (!items.length) {
    el.innerHTML = '<div class="empty-state">No allocation data</div>';
    return;
  }

  // Sort: cash last, equities descending by weight.
  items.sort((a, b) => {
    if (a.isCash) return 1;
    if (b.isCash) return -1;
    return b.pct - a.pct;
  });

  const maxPct = Math.max(...items.map(i => i.pct), 1);
  el.innerHTML = items.map(item => {
    const barWidth = Math.min(100, (item.pct / maxPct) * 100);
    return `<div class="alloc-item">
      <div class="alloc-meta">
        <span class="alloc-ticker">${esc(item.label)}</span>
        <span class="alloc-pct">${item.pct.toFixed(1)}%</span>
      </div>
      <div class="alloc-bar-bg">
        <div class="alloc-bar-fill ${item.isCash ? 'cash' : ''}" style="width:${barWidth}%"></div>
      </div>
    </div>`;
  }).join('');
}

// ── Health row ────────────────────────────────────────────────────────────────

function renderChecks(d) {
  const checks = d.operating_checks || [];
  const el     = document.getElementById('checks-list');
  if (!el) return;
  if (!checks.length) {
    el.innerHTML = '<li class="empty-state">No checks available</li>';
    return;
  }
  el.innerHTML = checks.map(c => {
    const s      = (c.status || '').toLowerCase();
    const isWarn = s === 'warn' || s === 'warning';
    const icon   = s === 'pass' ? 'PASS' : s === 'fail' ? 'FAIL' : isWarn ? 'WARN' : 'SKIP';
    const cls    = s === 'pass' ? 'pass' : s === 'fail' ? 'fail' : isWarn ? 'warn' : 'skip';
    return `<li class="check-item">
      <span class="check-icon ${cls}">${icon}</span>
      <div class="check-body">
        <div class="check-label">${esc(c.label || '')}</div>
        ${c.detail ? `<div class="check-detail">${esc(c.detail)}</div>` : ''}
      </div>
    </li>`;
  }).join('');
}

function renderExceptions(d) {
  const excs = d.exceptions || [];
  const el   = document.getElementById('exceptions-list');
  if (!el) return;
  if (!excs.length) {
    el.innerHTML = '<div class="empty-state">No exceptions recorded</div>';
    return;
  }
  // Sort: fail first, then warning, then pass — most actionable at top.
  const order = { fail: 0, warning: 1, warn: 1, pass: 2 };
  const sorted = [...excs].sort((a, b) =>
    (order[(a.status || '').toLowerCase()] ?? 9) -
    (order[(b.status || '').toLowerCase()] ?? 9)
  );
  el.innerHTML = sorted.map(e => {
    const s   = (e.status || '').toLowerCase();
    const cls = s === 'pass' ? 'pass' : s === 'fail' ? 'fail' : 'warning';
    return `<div class="exc-item ${cls}">
      <span class="exc-cat">${esc(e.category || '')}</span>
      <span class="exc-msg">${esc(e.message  || '')}</span>
    </div>`;
  }).join('');
}

// ── Footer ────────────────────────────────────────────────────────────────────

function renderFooter(d) {
  const meta    = d.run_meta || {};
  const sources = d.sources  || [];

  setText('footer-run',     meta.run_id       ? 'Run: ' + meta.run_id : '');
  setText('footer-updated', meta.last_updated ? 'Updated: ' + fmtTimestamp(meta.last_updated) : '');

  const list = document.getElementById('sources-list');
  if (list && sources.length) {
    // Show missing artifacts first so they're not buried.
    const sorted = [...sources].sort((a, b) => {
      const rank = s => s.status === 'missing' ? 0 : 1;
      return rank(a) - rank(b);
    });
    list.innerHTML = sorted.map(s => {
      const cls   = s.status === 'missing' ? 'missing' : 'used';
      const short = String(s.path || '')
        .replace('outputs/runs/', 'runs/')
        .replace('outputs/', '')
        .replace(/\*/g, '…');
      return `<li class="${cls}" title="${esc(s.path)}">${esc(short)}</li>`;
    }).join('');
  }
}

// ── Utility ───────────────────────────────────────────────────────────────────

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

// ── In-page diagnostics ───────────────────────────────────────────────────────
// Shows which data files loaded/failed, their resolved paths, and any boot
// exception. Always visible so developers don't need the browser console.

function showDiag(entries, bootError) {
  const panel   = document.getElementById('diag-panel');
  const content = document.getElementById('diag-content');
  if (!panel || !content) return;
  panel.classList.remove('hidden');
  let html = entries.map(e =>
    `<div class="diag-row">
      <span class="${e.ok ? 'diag-ok' : 'diag-fail'}">${e.ok ? 'OK  ' : 'FAIL'}</span>
      <span class="diag-label">${esc(e.label)}</span>
      <span class="diag-path">${esc(e.path)}</span>
    </div>`
  ).join('');
  if (bootError) {
    html += `<div class="diag-error">Boot exception: ${esc(String(bootError))}\n${esc((bootError && bootError.stack) ? bootError.stack : '')}</div>`;
  }
  content.innerHTML = html;
}

// ── Boot ──────────────────────────────────────────────────────────────────────

async function boot() {
  // Resolve paths first — synchronous, never throws in practice.
  const paths = resolveDataPaths();

  // Fetch both sources in parallel; null on any error or missing file.
  let dash, summary;
  try {
    [dash, summary] = await Promise.all([
      fetchJSON(paths.dashData),
      fetchJSON(paths.summary),
    ]);
  } catch (err) {
    showDiag([], err);
    const pill = document.getElementById('status-pill');
    if (pill) { pill.className = 'status-pill degraded'; pill.textContent = 'ERROR'; }
    setText('status-detail', 'Fetch failed — see diagnostics panel');
    return;
  }

  // Always show fetch results so developer can see what loaded.
  showDiag([
    { ok: !!dash,    label: 'dashboard_data.json',      path: paths.dashData },
    { ok: !!summary, label: 'trading_day_summary.json', path: paths.summary },
  ], null);

  if (!dash && !summary) {
    const pill = document.getElementById('status-pill');
    if (pill) { pill.className = 'status-pill degraded'; pill.textContent = 'NO DATA'; }
    setText('status-detail', 'No data loaded — check diagnostics panel for resolved paths');
    return;
  }

  // Render — wrapped so any unexpected exception surfaces in-page rather than
  // leaving the dashboard silently frozen at "LOADING".
  try {
    const d = mergeData(dash, summary);

    renderHeader(d);
    renderKPIs(d);
    renderPerfStats(d);

    // Double rAF ensures a full layout pass completes before we read canvas
    // parent dimensions — single rAF can fire before flex/grid layout settles.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      drawNavChart(d);
      drawDailyChart(d);
      drawDrawdownChart(d);
    }));

    renderExecStats(d);
    renderOrderFlow(d);
    renderBrokerStats(d);
    renderTopChanges(d);
    renderAllocBars(d);
    renderChecks(d);
    renderExceptions(d);
    renderFooter(d);
  } catch (err) {
    // Update diag panel with the exception; page is partially rendered.
    showDiag([
      { ok: !!dash,    label: 'dashboard_data.json',      path: paths.dashData },
      { ok: !!summary, label: 'trading_day_summary.json', path: paths.summary },
    ], err);
    const pill = document.getElementById('status-pill');
    if (pill) { pill.className = 'status-pill degraded'; pill.textContent = 'ERROR'; }
    setText('status-detail', 'Render error — see diagnostics panel');
  }
}

document.addEventListener('DOMContentLoaded', boot);
