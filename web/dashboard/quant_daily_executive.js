(() => {
  const DEFAULT_DATA_PATH = "sample_dashboard_data.json";
  let currentModel = null;
  const EMBEDDED_SAMPLE_DATA = {
    run_meta: {
      report_date: "2026-03-06",
      run_id: "sample-run",
      mode: "ALPACA_PAPER",
      overall_status: "PASS",
      benchmark: "SPY",
      last_updated: "2026-03-06T17:10:22Z",
      status_banner:
        "Run completed successfully on March 6, 2026. Three trades executed. Portfolio outperformed benchmark by 7 bps. No material exceptions."
    },
    kpis: {
      portfolio_value: 100482.33,
      daily_pl: 128.45,
      daily_return: 0.0013,
      benchmark_return: 0.0006,
      excess_return: 0.0007,
      holdings: 12,
      turnover: 0.082,
      run_status: "PASS"
    },
    perf_summary: {
      mtd_return: 0.0112,
      qtd_return: 0.0187,
      since_inception_return: 0.0048,
      since_inception_alpha: 0.0026,
      current_drawdown: -0.0061,
      best_day: 0.0054,
      worst_day: -0.0047
    },
    series: {
      nav: [
        { date: "2026-02-27", value: 100190.0 },
        { date: "2026-03-02", value: 100320.0 },
        { date: "2026-03-03", value: 100410.0 },
        { date: "2026-03-04", value: 100354.0 },
        { date: "2026-03-06", value: 100482.33 }
      ],
      benchmark: [
        { date: "2026-02-27", value: 685.99 },
        { date: "2026-03-02", value: 688.12 },
        { date: "2026-03-03", value: 690.33 },
        { date: "2026-03-04", value: 691.22 },
        { date: "2026-03-06", value: 692.47 }
      ],
      daily_returns: [
        { date: "2026-03-02", value: 0.0013 },
        { date: "2026-03-03", value: 0.0009 },
        { date: "2026-03-04", value: -0.0006 },
        { date: "2026-03-06", value: 0.0013 }
      ],
      excess_returns: [
        { date: "2026-03-02", value: 0.0012 },
        { date: "2026-03-03", value: -0.0006 },
        { date: "2026-03-04", value: -0.0019 },
        { date: "2026-03-06", value: 0.0007 }
      ],
      drawdown: [
        { date: "2026-03-02", value: 0.0 },
        { date: "2026-03-03", value: 0.0 },
        { date: "2026-03-04", value: -0.0006 },
        { date: "2026-03-06", value: 0.0 }
      ]
    },
    risk: {
      drawdown: -0.0061,
      cash_position: 0.182,
      gross_exposure: 0.818,
      largest_position_weight: 0.089,
      turnover_pct: 0.082,
      turnover_limit_pct: 0.35,
      breaker_status: "PARTIAL"
    },
    activity: {
      buys: 2,
      sells: 1,
      new_positions: 1,
      full_exits: 1,
      orders_filled: 3,
      orders_rejected: 0
    },
    governed_snapshot: {
      portfolio_value: 100482.33,
      equity: 100482.33,
      cash: 18287.88,
      market_value: 82194.45,
      as_of: "2026-03-06",
      source: "governed:canonical_performance",
      status: "fresh"
    },
    broker_snapshot: {
      portfolio_value: 100615.72,
      cash: 18410.11,
      buying_power: 73640.44,
      equity: 100615.72,
      market_value: 82205.61,
      as_of: "2026-03-07T13:31:00Z",
      source: "artifact:outputs/broker/broker_snapshot_latest.json",
      source_detail: "artifact snapshot outputs/broker/broker_snapshot_latest.json",
      trust_level: "authoritative",
      status: "fresh",
      suspicious: false,
      confidence_note: "",
      display_equity: 100615.72
    },
    data_freshness: {
      run_report_date: "2026-03-06",
      run_last_updated: "2026-03-06T17:10:22Z",
      broker_as_of: "2026-03-07T13:31:00Z",
      broker_vs_run_alignment: "mismatch",
      alignment_detail: "Broker snapshot is newer than governed run date.",
      stale_threshold_hours: 36,
      broker_trust_level: "authoritative",
      broker_source_detail: "artifact snapshot outputs/broker/broker_snapshot_latest.json",
      suspicious_broker_value: false
    },
    top_changes: [
      { ticker: "ADI", action: "BUY", change_weight: 0.0114, reason: "rebalance_to_target" },
      { ticker: "JNJ", action: "SELL", change_weight: -0.0097, reason: "removed_from_targets" },
      { ticker: "MO", action: "BUY", change_weight: 0.0061, reason: "rebalance_to_target" }
    ],
    exceptions: [
      { category: "Reconciliation", status: "pass", message: "No issues detected." },
      { category: "Execution", status: "pass", message: "All planned orders filled." },
      { category: "Risk", status: "warning", message: "Breaker in PARTIAL mode from volatility guard." },
      { category: "Data / artifacts", status: "pass", message: "All critical artifacts present." }
    ],
    operating_checks: [
      { label: "Run completed", status: "pass", detail: "Execution finished with PASS status." },
      { label: "Trades executed", status: "pass", detail: "3 trades executed." },
      { label: "Reconciliation passed", status: "pass", detail: "No model/broker drift found." },
      { label: "Canonical positions present", status: "pass", detail: "Canonical snapshot found." },
      { label: "Ledger updated", status: "pass", detail: "Trades ledger includes latest run." },
      { label: "Daily report generated", status: "pass", detail: "Run report artifacts available." }
    ],
    sources: [
      { path: "web/dashboard/sample_dashboard_data.json", status: "embedded-fallback" }
    ]
  };

  function queryDataPath() {
    const params = new URLSearchParams(window.location.search);
    return params.get("data") || DEFAULT_DATA_PATH;
  }

  function resolveDataUrl() {
    const path = queryDataPath();
    try {
      return new URL(path, window.location.href).toString();
    } catch (_err) {
      return path;
    }
  }

  function toNumber(value) {
    if (value === null || value === undefined || value === "") {
      return null;
    }
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function formatCurrency(value) {
    const n = toNumber(value);
    if (n === null) return "Data unavailable";
    return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(n);
  }

  function formatPercent(value, digits = 2) {
    const n = toNumber(value);
    if (n === null) return "Data unavailable";
    return `${(n * 100).toFixed(digits)}%`;
  }

  function formatSignedPercent(value, digits = 2) {
    const n = toNumber(value);
    if (n === null) return "Data unavailable";
    const sign = n > 0 ? "+" : "";
    return `${sign}${(n * 100).toFixed(digits)}%`;
  }

  function formatSignedBps(value) {
    const n = toNumber(value);
    if (n === null) return "Data unavailable";
    const bps = n * 10000;
    const sign = bps > 0 ? "+" : "";
    return `${sign}${bps.toFixed(0)} bps`;
  }

  function statusClass(status) {
    const normalized = String(status || "").toLowerCase();
    if (!normalized) return "info";
    if (normalized.includes("fail") || normalized.includes("error") || normalized.includes("halt") || normalized.includes("lock")) {
      return "fail";
    }
    if (normalized.includes("align")) return "pass";
    if (normalized.includes("warn") || normalized.includes("partial") || normalized.includes("degraded") || normalized.includes("elevated") || normalized.includes("mismatch") || normalized.includes("stale") || normalized.includes("missing")) {
      return "warning";
    }
    if (["pass", "ok", "ready", "success", "completed"].includes(normalized)) return "pass";
    if (["warning", "warn", "partial"].includes(normalized)) return "warning";
    if (["fail", "failed", "halted", "error", "action_required"].includes(normalized)) return "fail";
    return "info";
  }

  function trendClass(value) {
    const n = toNumber(value);
    if (n === null) return "info";
    if (n < 0) return "fail";
    if (n > 0) return "pass";
    return "info";
  }

  function statusLengthClass(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    if (text.length > 22 || text.includes("_")) return "status-wrap status-very-long";
    if (text.length > 12) return "status-wrap status-long";
    return "";
  }

  function drawCanvasEmptyState(ctx, w, h, message) {
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#f4f8fc";
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = "#d7e1ec";
    ctx.lineWidth = 1 * window.devicePixelRatio;
    ctx.strokeRect(0.5 * window.devicePixelRatio, 0.5 * window.devicePixelRatio, w - 1 * window.devicePixelRatio, h - 1 * window.devicePixelRatio);
    ctx.fillStyle = "#5c6f82";
    ctx.font = `${13 * window.devicePixelRatio}px Avenir Next, Segoe UI, sans-serif`;
    const text = message || "Data unavailable";
    const textWidth = ctx.measureText(text).width;
    const x = Math.max((w - textWidth) / 2, 10 * window.devicePixelRatio);
    const y = h / 2;
    ctx.fillText(text, x, y);
  }

  function clear(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function renderMeta(meta) {
    const metaGrid = document.getElementById("meta-grid");
    clear(metaGrid);

    const items = [
      ["Report Date", meta.report_date],
      ["Run ID", meta.run_id],
      ["Mode", meta.mode],
      ["Overall Status", meta.overall_status],
      ["Benchmark", meta.benchmark],
      ["Last Updated", meta.last_updated]
    ];

    items.forEach(([label, value]) => {
      const card = document.createElement("div");
      card.className = "meta-item";
      card.innerHTML = `<div class="label">${label}</div><div class="value">${value || "Not generated"}</div>`;
      metaGrid.appendChild(card);
    });

    const banner = document.getElementById("status-banner");
    banner.textContent = meta.status_banner || "Run status unavailable. Source artifacts may be incomplete.";
    banner.className = `status-banner ${statusClass(meta.overall_status)}`;

    const footer = document.getElementById("footer-meta");
    clear(footer);
    [
      `Report date: ${meta.report_date || "Not generated"}`,
      `Run ID: ${meta.run_id || "Not generated"}`,
      `Mode: ${meta.mode || "Not generated"}`,
      `Benchmark: ${meta.benchmark || "SPY"}`,
      `Last updated: ${meta.last_updated || "Not generated"}`
    ].forEach((text) => {
      const span = document.createElement("span");
      span.textContent = text;
      footer.appendChild(span);
    });
  }

  function renderKpis(kpis) {
    const strip = document.getElementById("kpi-strip");
    clear(strip);

    const runStatusClass = statusClass(kpis.run_status);
    const cards = [
      { key: "Portfolio Value", value: formatCurrency(kpis.portfolio_value), prominent: true },
      { key: "Daily P/L", value: formatCurrency(kpis.daily_pl), prominent: true, className: trendClass(kpis.daily_pl) },
      { key: "Daily Return", value: formatSignedPercent(kpis.daily_return), className: trendClass(kpis.daily_return) },
      { key: "Benchmark Return", value: formatSignedPercent(kpis.benchmark_return), className: trendClass(kpis.benchmark_return) },
      { key: "Excess Return", value: formatSignedBps(kpis.excess_return), className: trendClass(kpis.excess_return), excess: true },
      { key: "Holdings", value: toNumber(kpis.holdings) === null ? "Data unavailable" : String(kpis.holdings) },
      { key: "Turnover", value: formatPercent(kpis.turnover), className: "info" },
      {
        key: "Run Status",
        value: kpis.run_status || "Unknown",
        className: runStatusClass,
        valueClass: statusLengthClass(kpis.run_status || "Unknown")
      }
    ];

    cards.forEach((card) => {
      const wrapper = document.createElement("article");
      wrapper.className = `kpi-card ${card.prominent ? "prominent" : ""} ${card.excess ? "excess" : ""}`.trim();
      wrapper.innerHTML = `<div class="kpi-label">${card.key}</div><div class="kpi-value ${card.prominent ? "large" : ""} ${card.className || ""} ${card.valueClass || ""}">${card.value}</div>`;
      strip.appendChild(wrapper);
    });
  }

  function renderPerfSummary(summary) {
    const grid = document.getElementById("perf-summary-grid");
    clear(grid);
    const items = [
      ["MTD Return", formatSignedPercent(summary.mtd_return)],
      ["QTD Return", formatSignedPercent(summary.qtd_return)],
      ["Since Inception Return", formatSignedPercent(summary.since_inception_return)],
      ["Since Inception Alpha", formatSignedPercent(summary.since_inception_alpha)],
      ["Current Drawdown", formatSignedPercent(summary.current_drawdown)],
      ["Best Day / Worst Day", `${formatSignedPercent(summary.best_day)} / ${formatSignedPercent(summary.worst_day)}`]
    ];

    items.forEach(([label, value]) => {
      const el = document.createElement("div");
      el.className = "summary-item";
      el.innerHTML = `<div class="label">${label}</div><div class="value">${value}</div>`;
      grid.appendChild(el);
    });
  }

  function renderRisk(risk) {
    const row = document.getElementById("risk-row");
    clear(row);
    const turnoverLimit = toNumber(risk.turnover_limit_pct);
    const turnover = toNumber(risk.turnover_pct);
    let turnoverText = "Data unavailable";
    if (turnover !== null) {
      turnoverText = formatPercent(turnover);
      if (turnoverLimit !== null) {
        turnoverText = `${formatPercent(turnover)} / ${formatPercent(turnoverLimit)} limit`;
      }
    }

    const cards = [
      ["Drawdown", formatSignedPercent(risk.drawdown), toNumber(risk.drawdown) === null ? "info" : (toNumber(risk.drawdown) < -0.03 ? "warning" : "pass")],
      ["Cash Position", formatPercent(risk.cash_position), "info"],
      ["Gross Exposure", formatPercent(risk.gross_exposure), "info"],
      ["Largest Position Weight", formatPercent(risk.largest_position_weight), "info"],
      ["Turnover vs Limit", turnoverText, turnover === null ? "info" : (turnover !== null && turnoverLimit !== null && turnover > turnoverLimit ? "warning" : "pass")],
      ["Breaker Status", risk.breaker_status || "Not generated", statusClass(risk.breaker_status || "info")]
    ];

    cards.forEach(([label, value, cls]) => {
      const el = document.createElement("article");
      el.className = "risk-card";
      el.innerHTML = `<div class="label">${label}</div><div class="value ${cls}">${value}</div>`;
      row.appendChild(el);
    });
  }

  function renderActivity(activity) {
    const grid = document.getElementById("activity-grid");
    clear(grid);
    const items = [
      ["Buys", activity.buys],
      ["Sells", activity.sells],
      ["New Positions", activity.new_positions],
      ["Full Exits", activity.full_exits],
      ["Orders Filled", activity.orders_filled],
      ["Orders Rejected", activity.orders_rejected]
    ];

    items.forEach(([label, value]) => {
      const v = toNumber(value);
      let cls = "info";
      if (label === "Orders Rejected") cls = v === null ? "info" : (v > 0 ? "fail" : "pass");
      if (label === "Orders Filled") cls = v === null ? "info" : (v > 0 ? "pass" : "warning");
      const el = document.createElement("div");
      el.className = "mini-card";
      el.innerHTML = `<div class="label">${label}</div><div class="value ${cls}">${v === null ? "Data unavailable" : v}</div>`;
      grid.appendChild(el);
    });
  }

  function renderSnapshots(governed, broker, freshness) {
    const governedGrid = document.getElementById("governed-snapshot-grid");
    const brokerGrid = document.getElementById("broker-snapshot-grid");
    const brokerTitle = document.getElementById("broker-snapshot-title");
    const governedAsOf = document.getElementById("governed-asof-label");
    const brokerAsOf = document.getElementById("broker-asof-label");
    const alignmentNote = document.getElementById("snapshot-alignment-note");

    clear(governedGrid);
    clear(brokerGrid);

    const governedItems = [
      ["Portfolio Value", formatCurrency(governed.portfolio_value)],
      ["Cash", formatCurrency(governed.cash)],
      ["Market Value", formatCurrency(governed.market_value)]
    ];
    const trust = String(broker.trust_level || "missing").toLowerCase();
    const lowTrust = trust === "derived" || trust === "missing";
    const suspicious = !!broker.suspicious;

    if (brokerTitle) {
      brokerTitle.textContent = lowTrust ? "Derived Broker Estimate" : "Latest Broker Snapshot";
    }

    const brokerHeadlineValue = suspicious
      ? "Data unavailable"
      : formatCurrency(
        broker.display_equity !== undefined && broker.display_equity !== null
          ? broker.display_equity
          : (broker.portfolio_value || broker.equity)
      );

    const brokerItems = [
      ["Portfolio / Equity", brokerHeadlineValue],
      ["Cash", formatCurrency(broker.cash)],
      ["Buying Power", formatCurrency(broker.buying_power)]
    ];

    governedItems.forEach(([label, value]) => {
      const el = document.createElement("div");
      el.className = "snapshot-item";
      el.innerHTML = `<div class="label">${label}</div><div class="value">${value}</div>`;
      governedGrid.appendChild(el);
    });

    brokerItems.forEach(([label, value]) => {
      const el = document.createElement("div");
      el.className = "snapshot-item";
      el.innerHTML = `<div class="label">${label}</div><div class="value">${value}</div>`;
      brokerGrid.appendChild(el);
    });

    governedAsOf.textContent = `As of: ${governed.as_of || "not available"}`;
    brokerAsOf.textContent = `As of: ${broker.as_of || "not available"} (${trust || "missing"})`;

    const alignment = String((freshness && freshness.broker_vs_run_alignment) || "missing");
    const detail = (freshness && freshness.alignment_detail) || "Broker snapshot alignment unavailable.";
    const sourceDetail = String(broker.source_detail || broker.source || "source unknown");
    const confidenceDetail = suspicious
      ? (broker.confidence_note || "Derived estimate flagged as suspicious.")
      : (lowTrust ? "Derived estimate; use governed snapshot as primary source." : "Authoritative/reconciled broker source.");
    alignmentNote.textContent = `${detail} Source: ${sourceDetail}. ${confidenceDetail}`;
    alignmentNote.className = `snapshot-note ${suspicious ? "warning" : statusClass(alignment)}`;
  }

  function renderTopChanges(rows) {
    const tbody = document.querySelector("#top-changes-table tbody");
    clear(tbody);
    if (!rows || !rows.length) {
      const tr = document.createElement("tr");
      tr.innerHTML = '<td colspan="4" class="data-unavailable">No trade change data available.</td>';
      tbody.appendChild(tr);
      return;
    }

    rows.slice(0, 5).forEach((row) => {
      const tr = document.createElement("tr");
      const action = String(row.action || "").toUpperCase();
      const className = action === "BUY" ? "pass" : action === "SELL" ? "fail" : "info";
      tr.innerHTML = `
        <td>${row.ticker || "-"}</td>
        <td><span class="badge ${className}">${action || "N/A"}</span></td>
        <td>${formatSignedPercent(row.change_weight, 2)}</td>
        <td>${row.reason || "Not provided"}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  function renderExceptions(items) {
    const container = document.getElementById("exceptions-list");
    clear(container);
    if (!items || !items.length) {
      container.innerHTML = '<div class="data-unavailable">No exception diagnostics available.</div>';
      return;
    }

    items.forEach((item) => {
      const row = document.createElement("div");
      row.className = "exception-row";
      row.innerHTML = `
        <div><strong>${item.category || "Unknown"}</strong></div>
        <div><span class="badge ${statusClass(item.status)}">${item.status || "unknown"}</span></div>
        <div>${item.message || "No details available."}</div>
      `;
      container.appendChild(row);
    });
  }

  function renderChecks(items) {
    const list = document.getElementById("operating-checks");
    clear(list);
    if (!items || !items.length) {
      list.innerHTML = '<li><span>Operating checks not available.</span></li>';
      return;
    }

    items.forEach((item) => {
      const li = document.createElement("li");
      li.innerHTML = `
        <span>${item.label || "Unnamed check"}</span>
        <span class="badge ${statusClass(item.status)}">${item.status || "unknown"}</span>
        <span class="detail">${item.detail || "No detail provided."}</span>
      `;
      list.appendChild(li);
    });
  }

  function renderSources(items) {
    const list = document.getElementById("sources-list");
    clear(list);
    if (!items || !items.length) {
      list.innerHTML = '<li>No source file records were provided.</li>';
      return;
    }

    items.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = `${item.path || "unknown"} (${item.status || "unknown"})`;
      list.appendChild(li);
    });
  }

  function drawLineChart(canvas, seriesA, seriesB) {
    const ctx = canvas.getContext("2d");
    const w = canvas.width = canvas.clientWidth * window.devicePixelRatio;
    const h = canvas.height = canvas.clientHeight * window.devicePixelRatio;
    const pad = { left: 44, right: 12, top: 16, bottom: 28 };

    const valuesA = (seriesA || []).map((x) => toNumber(x.value)).filter((x) => x !== null);
    const valuesB = (seriesB || []).map((x) => toNumber(x.value)).filter((x) => x !== null);

    if (!valuesA.length || !valuesB.length) {
      drawCanvasEmptyState(ctx, w, h, "Data unavailable for NAV vs Benchmark");
      return;
    }

    const normalize = (arr) => {
      const base = arr[0];
      if (!base) return arr;
      return arr.map((v) => (v / base) * 100);
    };

    const aNorm = normalize(valuesA);
    const bNorm = normalize(valuesB);
    const all = aNorm.concat(bNorm);

    const yMin = Math.min(...all) * 0.998;
    const yMax = Math.max(...all) * 1.002;
    const plotW = w - pad.left - pad.right;
    const plotH = h - pad.top - pad.bottom;

    const x = (i, n) => pad.left + (i / Math.max(n - 1, 1)) * plotW;
    const y = (v) => pad.top + ((yMax - v) / Math.max(yMax - yMin, 0.00001)) * plotH;

    ctx.clearRect(0, 0, w, h);

    ctx.strokeStyle = "#e1e8f0";
    ctx.lineWidth = 1 * window.devicePixelRatio;
    for (let i = 0; i <= 4; i += 1) {
      const gy = pad.top + (plotH * i) / 4;
      ctx.beginPath();
      ctx.moveTo(pad.left, gy);
      ctx.lineTo(w - pad.right, gy);
      ctx.stroke();
    }

    const drawSeries = (arr, color) => {
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.2 * window.devicePixelRatio;
      ctx.beginPath();
      arr.forEach((v, i) => {
        const px = x(i, arr.length);
        const py = y(v);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();
    };

    drawSeries(aNorm, "#1f4f85");
    drawSeries(bNorm, "#8799ad");

    ctx.fillStyle = "#5c6f82";
    ctx.font = `${11 * window.devicePixelRatio}px Avenir Next, Segoe UI, sans-serif`;
    ctx.fillText(`Min ${yMin.toFixed(1)}`, 6 * window.devicePixelRatio, (h - 10 * window.devicePixelRatio));
    ctx.fillText(`Max ${yMax.toFixed(1)}`, 6 * window.devicePixelRatio, (14 * window.devicePixelRatio));
  }

  function drawBarChart(canvas, series, positiveColor, negativeColor) {
    const ctx = canvas.getContext("2d");
    const w = canvas.width = canvas.clientWidth * window.devicePixelRatio;
    const h = canvas.height = canvas.clientHeight * window.devicePixelRatio;
    const pad = { left: 36, right: 10, top: 14, bottom: 22 };

    const values = (series || []).map((x) => toNumber(x.value)).filter((x) => x !== null);
    if (!values.length) {
      drawCanvasEmptyState(ctx, w, h, "Data unavailable");
      return;
    }

    const minVal = Math.min(...values, 0);
    const maxVal = Math.max(...values, 0);
    const plotW = w - pad.left - pad.right;
    const plotH = h - pad.top - pad.bottom;
    const baseline = pad.top + ((maxVal - 0) / Math.max(maxVal - minVal, 0.00001)) * plotH;

    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = "#d8e1ea";
    ctx.lineWidth = 1 * window.devicePixelRatio;
    ctx.beginPath();
    ctx.moveTo(pad.left, baseline);
    ctx.lineTo(w - pad.right, baseline);
    ctx.stroke();

    const bw = plotW / values.length;
    values.forEach((v, i) => {
      const barH = Math.abs(v / Math.max(maxVal - minVal, 0.00001)) * plotH;
      const x = pad.left + i * bw + bw * 0.12;
      const y = v >= 0 ? baseline - barH : baseline;
      const width = bw * 0.76;
      ctx.fillStyle = v >= 0 ? positiveColor : negativeColor;
      ctx.fillRect(x, y, width, Math.max(barH, 1));
    });
  }

  function renderCharts(series) {
    const navSeries = (series.nav || []).slice(-30);
    const benchSeries = (series.benchmark || []).slice(-30);
    const dailyReturns = (series.daily_returns || []).slice(-25);
    const excessReturns = (series.excess_returns || []).slice(-25);

    drawLineChart(document.getElementById("hero-nav-chart"), navSeries, benchSeries);
    drawBarChart(document.getElementById("daily-returns-chart"), dailyReturns, "#1b6f4a", "#a6343c");
    drawBarChart(document.getElementById("excess-returns-chart"), excessReturns, "#1f4f85", "#a6343c");
  }

  async function loadData() {
    const path = queryDataPath();
    const resolvedUrl = resolveDataUrl();
    try {
      const res = await fetch(resolvedUrl, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      throw new Error(`Failed to load dashboard JSON (${path} -> ${resolvedUrl}): ${err.message}`);
    }
  }

  function fallbackModel(errorMessage) {
    return {
      run_meta: {
        report_date: "Not generated",
        run_id: "Not generated",
        mode: "Not generated",
        overall_status: "warning",
        benchmark: "SPY",
        last_updated: new Date().toISOString(),
        status_banner: `Dashboard data unavailable. ${errorMessage}`
      },
      kpis: {},
      perf_summary: {},
      series: { nav: [], benchmark: [], daily_returns: [], excess_returns: [], drawdown: [] },
      risk: {},
      activity: {},
      governed_snapshot: { portfolio_value: null, cash: null, market_value: null, as_of: null, source: "missing" },
      broker_snapshot: {
        portfolio_value: null,
        cash: null,
        buying_power: null,
        equity: null,
        market_value: null,
        as_of: null,
        source: "missing",
        status: "missing",
        trust_level: "missing",
        source_detail: "fallback:model",
        suspicious: false,
        confidence_note: "Broker snapshot unavailable in fallback model.",
        display_equity: null,
      },
      data_freshness: { broker_vs_run_alignment: "missing", alignment_detail: "Broker snapshot unavailable." },
      top_changes: [],
      exceptions: [
        { category: "Data / artifacts", status: "warning", message: errorMessage }
      ],
      operating_checks: [
        { label: "Run completed", status: "warning", detail: "No dashboard data file was loaded." }
      ],
      sources: []
    };
  }

  function render(model) {
    currentModel = model;
    renderMeta(model.run_meta || {});
    renderKpis(model.kpis || {});
    renderSnapshots(model.governed_snapshot || {}, model.broker_snapshot || {}, model.data_freshness || {});
    renderPerfSummary(model.perf_summary || {});
    renderRisk(model.risk || {});
    renderActivity(model.activity || {});
    renderTopChanges(model.top_changes || []);
    renderExceptions(model.exceptions || []);
    renderChecks(model.operating_checks || []);
    renderSources(model.sources || []);
    renderCharts(model.series || {});
  }

  function init() {
    loadData()
      .then((model) => render(model))
      .catch((err) => {
        if (queryDataPath() === DEFAULT_DATA_PATH) {
          render(EMBEDDED_SAMPLE_DATA);
          return;
        }
        const model = fallbackModel(err.message);
        model.exceptions.unshift({
          category: "Data / artifacts",
          status: "warning",
          message: "Real-data mode unavailable. Start a local static server and verify the ?data path."
        });
        render(model);
      });

    window.addEventListener("resize", () => {
      renderCharts((currentModel && currentModel.series) || {});
    });
  }

  init();
})();
