'use strict';
/* Caerus performance narrative module.
   Renders the "how am I performing" story into #perf-root, ABOVE the operator
   cockpit. Self-loads dashboard_data.json (so it picks up every VM refresh) with
   a window.DASHBOARD_V1 fallback for file:// / offline viewing. Fully namespaced;
   shares no globals or element IDs with quant_daily_executive.js. */
(function () {
  function resolveDataPath() {
    const base = new URL('.', window.location.href);
    const params = new URL(window.location.href).searchParams;
    const dataFile = String(params.get('data') || 'dashboard_data.json').trim() || 'dashboard_data.json';
    return new URL(dataFile, base).href;
  }
  async function loadPayload() {
    try {
      const target = new URL(resolveDataPath(), window.location.href);
      target.searchParams.set('_ts', String(Date.now()));
      const r = await fetch(target.href, { cache: 'no-store' });
      if (r.ok) return await r.json();
    } catch (e) { /* fall through to inline global */ }
    if (window.DASHBOARD_V1) return window.DASHBOARD_V1;
    throw new Error('performance module: no dashboard payload available');
  }

  const pct = (v, d = 2) => v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%';
  const pctp = (v, d = 2) => v == null ? '—' : (v * 100).toFixed(d) + '%';
  const money = v => v == null ? '—' : v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
  const money2 = v => v == null ? '—' : v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sign = v => v >= 0 ? 'pf-pos' : 'pf-neg';
  const el = id => document.getElementById(id);

  function lineChart(id, series, opts = {}) {
    const cv = el(id); if (!cv) return;
    const dpr = window.devicePixelRatio || 1;
    const W = cv.clientWidth || (cv.parentElement.clientWidth - 36);
    const H = opts.height || cv.height;
    cv.width = W * dpr; cv.height = H * dpr; cv.style.height = H + 'px';
    const ctx = cv.getContext('2d'); ctx.scale(dpr, dpr);
    const padL = 46, padR = 12, padT = 12, padB = 22;
    const all = series.flatMap(s => s.data.map(p => p.value));
    let min = Math.min(...all), max = Math.max(...all);
    if (opts.zero) { min = Math.min(min, 0); max = Math.max(max, 0); }
    const rng = (max - min) || 1; min -= rng * 0.08; max += rng * 0.08;
    const n = series[0].data.length;
    const X = i => padL + (W - padL - padR) * (i / (n - 1));
    const Y = v => padT + (H - padT - padB) * (1 - (v - min) / (max - min));
    ctx.font = '10px monospace'; ctx.fillStyle = '#566073'; ctx.strokeStyle = '#161d26'; ctx.lineWidth = 1;
    const ticks = 4;
    for (let t = 0; t <= ticks; t++) {
      const val = min + (max - min) * t / ticks, y = Y(val);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
      ctx.textAlign = 'right'; ctx.fillText(opts.yfmt ? opts.yfmt(val) : val.toFixed(0), padL - 6, y + 3);
    }
    if (opts.zero && min < 0 && max > 0) {
      const y = Y(0); ctx.strokeStyle = '#39465a'; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke(); ctx.setLineDash([]);
    }
    ctx.fillStyle = '#566073'; ctx.textAlign = 'center';
    [0, Math.floor((n - 1) / 2), n - 1].forEach(i => {
      ctx.fillText(series[0].data[i].date.slice(5), X(i), H - 6);
    });
    series.forEach(s => {
      if (s.fill) {
        const grad = ctx.createLinearGradient(0, padT, 0, H - padB);
        grad.addColorStop(0, s.fill); grad.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.beginPath(); ctx.moveTo(X(0), Y(s.data[0].value));
        s.data.forEach((p, i) => ctx.lineTo(X(i), Y(p.value)));
        ctx.lineTo(X(n - 1), Y(opts.zero ? 0 : min)); ctx.lineTo(X(0), Y(opts.zero ? 0 : min));
        ctx.closePath(); ctx.fillStyle = grad; ctx.fill();
      }
      ctx.beginPath(); ctx.lineWidth = s.w || 2; ctx.strokeStyle = s.color; ctx.lineJoin = 'round';
      s.data.forEach((p, i) => { i ? ctx.lineTo(X(i), Y(p.value)) : ctx.moveTo(X(i), Y(p.value)); });
      ctx.stroke();
    });
  }

  function render(D) {
    const root = el('perf-root'); if (!root) return;
    const S = D.sections, T = D.terminal, PH = S.performance_history, B = T.benchmark, sum = PH.summary;
    const fresh = !(PH.is_stale);
    const si = sum.since_inception_return, spy = sum.spy_since_inception_return, exc = si - spy;
    const day = S.nav.day_return, dayPnl = S.nav.day_pnl;
    const reg = S.regime_market_state, P = T.positioning;

    root.className = 'perf-narrative';
    root.innerHTML = `
      <div class="pf-head">
        <div>
          <p class="pf-tag">Caerus · Paper Track Record</p>
          <h2>How Am I Performing?</h2>
          <p class="pf-sub">Since-inception return vs benchmark, how it was earned, and what the book looks like now.</p>
        </div>
        <div class="pf-meta">
          <div class="pf-chip"><div class="k">Report Date</div><div class="v">${D.report_date}</div></div>
          <div class="pf-chip"><div class="k">Inception</div><div class="v">${sum.inception_date}</div></div>
          <div class="pf-chip ${fresh ? 'good' : 'warn'}"><div class="k">Data</div><div class="v">${fresh ? 'FRESH' : 'STALE'}</div></div>
        </div>
      </div>

      <div class="pf-hero">
        <div class="pf-hcell">
          <div class="lbl">Since Inception · Caerus Paper Book</div>
          <div class="big ${sign(si)}">${pct(si)}</div>
          <div class="ctx">NAV ${money(sum.latest_nav)} · first recorded NAV ${money(sum.inception_nav)} on ${sum.inception_date} · ${B.up_days} up / ${B.down_days} down days</div>
        </div>
        <div class="pf-hcell div">
          <div class="lbl">Excess vs SPY</div>
          <div class="num ${sign(exc)}">${pct(exc)}</div>
          <div class="foot">SPY ${pct(spy)} over same window</div>
        </div>
        <div class="pf-hcell div">
          <div class="lbl">Today</div>
          <div class="num ${sign(day)}">${pct(day)}</div>
          <div class="foot ${sign(dayPnl)}">${dayPnl >= 0 ? '+' : ''}${money2(dayPnl)} day P&L</div>
        </div>
      </div>

      <div class="pf-seclabel"><span class="n">01 — THE CURVE</span><span class="d">growth of the book vs the market</span></div>
      <div class="pf-grid">
        <div class="pf-panel">
          <div class="pf-phead"><h3>Relative Performance</h3><span class="kick">indexed to 100 · ${PH.series.nav_indexed.length} sessions</span></div>
          <canvas id="pf-navChart" height="300"></canvas>
          <div class="pf-cnote">Both indexed to 100 at inception. Blue = Caerus paper book · Gray = SPY.</div>
        </div>
      </div>

      <div class="pf-seclabel"><span class="n">02 — RISK</span><span class="d">how the return was earned</span></div>
      <div class="pf-grid pf-g2">
        <div class="pf-panel">
          <div class="pf-phead"><h3>Drawdown</h3><span class="kick">loss from peak</span></div>
          <canvas id="pf-ddChart" height="220"></canvas>
          <div class="pf-cnote">Zero line = new equity high. Worst trough is the max drawdown.</div>
        </div>
        <div class="pf-panel">
          <div class="pf-phead"><h3>Risk &amp; Regime</h3><span class="kick">regime ${reg.current_regime}</span></div>
          <div class="pf-tiles" id="pf-riskTiles"></div>
        </div>
      </div>

      <div class="pf-seclabel"><span class="n">03 — WHY</span><span class="d">where the edge came from</span></div>
      <div class="pf-grid pf-g2">
        <div class="pf-panel">
          <div class="pf-phead"><h3>Cumulative Excess vs SPY</h3><span class="kick">edge over the market</span></div>
          <canvas id="pf-excessChart" height="220"></canvas>
          <div class="pf-cnote">Above zero = beating SPY on a cumulative basis since inception.</div>
        </div>
        <div class="pf-panel">
          <div class="pf-phead"><h3>Top Contributors</h3><span class="kick">unrealized P&amp;L</span></div>
          <div id="pf-contrib"></div>
        </div>
      </div>
      <div class="pf-panel" style="margin-top:14px">
        <div class="pf-phead"><h3>Edge Attribution</h3><span class="kick" id="pf-edgeState">diagnostic</span></div>
        <div class="pf-tiles" id="pf-edgeTiles"></div>
        <div class="pf-cnote" id="pf-edgeNote"></div>
      </div>

      <div class="pf-seclabel"><span class="n">04 — POSITIONING</span><span class="d">what the money is in right now</span></div>
      <div class="pf-grid pf-g2">
        <div class="pf-panel">
          <div class="pf-phead"><h3>Book Construction</h3><span class="kick">${S.positions.summary.positions_count} positions</span></div>
          <div class="pf-tiles" id="pf-posTiles"></div>
        </div>
        <div class="pf-panel">
          <div class="pf-phead"><h3>Largest Holdings</h3><span class="kick">top 8 by weight</span></div>
          <table>
            <thead><tr><th>Ticker</th><th>Weight</th><th>Value</th><th>Unreal P&amp;L</th><th>%</th></tr></thead>
            <tbody id="pf-holdBody"></tbody>
          </table>
        </div>
      </div>

      <div class="pf-divider"><span>Operator &amp; Governance Detail</span></div>`;

    // charts
    const navS = PH.series.nav_indexed, spyS = PH.series.spy_indexed;
    const drawAll = () => {
      lineChart('pf-navChart', [
        { data: spyS, color: '#5b6678', w: 1.6 },
        { data: navS, color: '#5ec8ff', w: 2.4, fill: 'rgba(94,200,255,0.14)' },
      ], { height: 300, yfmt: v => v.toFixed(0) });
      lineChart('pf-ddChart', [{ data: PH.series.drawdown, color: '#ff5d6c', w: 2, fill: 'rgba(255,93,108,0.16)' }],
        { height: 220, zero: true, yfmt: v => (v * 100).toFixed(0) + '%' });
      lineChart('pf-excessChart', [{ data: PH.series.excess_return_cumulative, color: '#8b7dff', w: 2, fill: 'rgba(139,125,255,0.16)' }],
        { height: 220, zero: true, yfmt: v => (v * 100).toFixed(1) + '%' });
    };
    drawAll();

    // risk tiles
    el('pf-riskTiles').innerHTML = [
      ['Max Drawdown', pct(sum.max_drawdown), 'worst peak-to-trough', sign(sum.max_drawdown)],
      ['Rolling 20D', pct(B.rolling_20d_return), 'SPY ' + pct(B.rolling_20d_spy_return), sign(B.rolling_20d_return)],
      ['Rolling 5D', pct(B.rolling_5d_return), 'SPY ' + pct(B.rolling_5d_spy_return), sign(B.rolling_5d_return)],
      ['VIX', reg.vix != null ? reg.vix.toFixed(1) : '—', 'regime ' + reg.current_regime, (reg.vix > 25 ? 'pf-neg' : 'pf-accentc')],
    ].map(t => `<div class="pf-tile"><div class="k">${t[0]}</div><div class="v ${t[3]}">${t[1]}</div><div class="f">${t[2]}</div></div>`).join('');

    // contributors
    const win = T.leaders.winners || [], lag = T.leaders.laggards || [];
    const contrib = [...win.slice(0, 5), ...lag.slice(0, 3)].sort((a, b) => b.unrealized_pnl - a.unrealized_pnl);
    const maxAbs = Math.max(...contrib.map(c => Math.abs(c.unrealized_pnl))) || 1;
    el('pf-contrib').innerHTML = contrib.map(c => {
      const w = Math.abs(c.unrealized_pnl) / maxAbs * 100, p = c.unrealized_pnl >= 0;
      return `<div class="pf-bar-row"><span class="tk">${c.ticker}</span>
        <span class="pf-bar-track"><span class="pf-bar-fill" style="width:${w}%;background:${p ? 'var(--pf-pos)' : 'var(--pf-neg)'};margin-left:${p ? '0' : 'auto'}"></span></span>
        <span class="amt ${p ? 'pf-pos' : 'pf-neg'}">${p ? '+' : ''}${money2(c.unrealized_pnl)}</span></div>`;
    }).join('');

    // Model-vs-actual diagnostics: separates recent signal weakness from a
    // broker book that does not yet match the current target portfolio.
    const edge = S.edge_attribution || {}, fidelity = edge.target_fidelity || {};
    const edgePerf = edge.performance || {}, edgeExec = edge.execution || {};
    el('pf-edgeState').textContent = String(edge.classification || 'unavailable').replaceAll('_', ' ');
    el('pf-edgeTiles').innerHTML = [
      ['20D Excess', pct(edgePerf.rolling_20d_excess_return), 'portfolio minus SPY', sign(edgePerf.rolling_20d_excess_return || 0)],
      ['Target Attainment', pctp(fidelity.target_attainment_ratio), `${fidelity.target_name_count ?? '—'} target names`, (fidelity.target_attainment_ratio || 0) >= .9 ? 'pf-pos' : 'pf-warnc'],
      ['Off-Target Weight', pctp(fidelity.off_target_weight), 'legacy/non-target holdings', (fidelity.off_target_weight || 0) <= .05 ? 'pf-pos' : 'pf-neg'],
      ['Absolute Weight Gap', pctp(fidelity.total_absolute_weight_gap), 'targets + cash', (fidelity.total_absolute_weight_gap || 0) <= .1 ? 'pf-pos' : 'pf-neg'],
    ].map(t => `<div class="pf-tile"><div class="k">${t[0]}</div><div class="v ${t[3]}">${t[1]}</div><div class="f">${t[2]}</div></div>`).join('');
    const missing = fidelity.missing_target_symbols || [], fractional = fidelity.off_target_fractional_symbols || [];
    el('pf-edgeNote').textContent = `Missing targets: ${missing.join(', ') || 'none'} · off-target fractional exits: ${fractional.length} · submitted/filled: ${edgeExec.submitted_count ?? '—'}/${edgeExec.filled_count ?? '—'}`;

    // positioning tiles
    el('pf-posTiles').innerHTML = [
      ['Invested', pctp(P.invested_ratio), 'market value / equity', 'pf-accentc'],
      ['Cash', pctp(P.cash_ratio), 'dry powder', 'pf-accentc'],
      ['Top-10 Conc.', pctp(P.top10_concentration), 'sum of 10 largest', P.top10_concentration > 0.8 ? 'pf-warnc' : 'pf-accentc'],
      ['Largest Name', pctp(P.largest_position_weight), 'single-name max', P.largest_position_weight > 0.15 ? 'pf-warnc' : 'pf-accentc'],
    ].map(t => `<div class="pf-tile"><div class="k">${t[0]}</div><div class="v ${t[3]}">${t[1]}</div><div class="f">${t[2]}</div></div>`).join('');

    // holdings
    const rows = [...S.positions.rows].sort((a, b) => b.weight - a.weight).slice(0, 8);
    el('pf-holdBody').innerHTML = rows.map(r =>
      `<tr><td>${r.ticker}</td><td style="text-align:right">${pctp(r.weight)}</td>
       <td style="text-align:right">${money(r.market_value)}</td>
       <td style="text-align:right" class="${sign(r.unrealized_pnl)}">${r.unrealized_pnl >= 0 ? '+' : ''}${money2(r.unrealized_pnl)}</td>
       <td style="text-align:right" class="${sign(r.unrealized_pnl_pct)}">${pct(r.unrealized_pnl_pct)}</td></tr>`).join('');

    let rt;
    window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(drawAll, 120); });
  }

  function boot() {
    loadPayload().then(render).catch(err => {
      const root = el('perf-root');
      if (root) root.innerHTML = '<p style="color:#ff5d6c;font-family:monospace;padding:16px">Performance module: ' + err.message + '</p>';
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
