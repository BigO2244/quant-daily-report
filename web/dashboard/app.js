(function () {
  function formatNumber(value, digits) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "N/A";
    }
    return new Intl.NumberFormat("en-US", {
      minimumFractionDigits: digits || 0,
      maximumFractionDigits: digits || 0,
    }).format(Number(value));
  }

  function formatCurrency(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "N/A";
    }
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 2,
    }).format(Number(value));
  }

  function renderList(items) {
    if (!items || !items.length) {
      return '<span class="muted">None</span>';
    }
    return items
      .map(function (item) {
        return '<span class="pill">' + String(item) + "</span>";
      })
      .join("");
  }

  function metricCard(label, value, tone) {
    return (
      '<section class="metric-card' +
      (tone ? " " + tone : "") +
      '">' +
      '<p class="metric-label">' +
      label +
      "</p>" +
      '<p class="metric-value">' +
      value +
      "</p>" +
      "</section>"
    );
  }

  var root = document.getElementById("app");
  var data = window.DASHBOARD_DATA || {};
  var broker = data.broker || {};
  var pretrade = broker.pretrade || {};
  var posttrade = broker.posttrade || {};
  var delta = broker.delta || {};
  var trustClass = String((broker.trustLevel || "LOW")).toLowerCase();

  root.innerHTML =
    '<section class="status-band">' +
    '<div class="status-copy">' +
    '<p class="status-kicker">Trade Date</p>' +
    "<h2>" +
    (data.tradeDate || "Unknown") +
    "</h2>" +
    '<p class="status-note">' +
    (broker.authoritativeMessage || "No broker state summary available.") +
    "</p>" +
    "</div>" +
    '<div class="trust-badge trust-' +
    trustClass +
    '">' +
    '<span class="trust-label">Broker Trust</span>' +
    '<span class="trust-value">' +
    (broker.trustLevel || "LOW") +
    "</span>" +
    "</div>" +
    "</section>" +
    '<section class="metric-grid">' +
    metricCard("Run ID", data.runId || "Unavailable") +
    metricCard("Pretrade Status", pretrade.status || "UNKNOWN") +
    metricCard("Posttrade Recon", posttrade.reconStatus || "UNKNOWN") +
    metricCard(
      "Authoritative State",
      broker.authoritativeState ? "Confirmed" : "Not Confirmed",
      broker.authoritativeState ? "good" : "warn"
    ) +
    "</section>" +
    '<section class="panel-grid">' +
    '<article class="panel">' +
    "<h3>Pretrade Snapshot</h3>" +
    '<div class="panel-metrics">' +
    metricCard("Positions", formatNumber(pretrade.positionsCount, 0)) +
    metricCard("Cash", formatCurrency(pretrade.cash)) +
    metricCard("Equity", formatCurrency(pretrade.equity)) +
    metricCard("Buying Power", formatCurrency(pretrade.buyingPower)) +
    "</div>" +
    '<div class="tag-row"><span class="tag-heading">Restrictions</span>' +
    renderList(pretrade.restrictionFlags) +
    "</div>" +
    '<div class="tag-row"><span class="tag-heading">Warnings</span>' +
    renderList(pretrade.warningFlags) +
    "</div>" +
    "</article>" +
    '<article class="panel">' +
    "<h3>Posttrade Snapshot</h3>" +
    '<div class="panel-metrics">' +
    metricCard("Positions", formatNumber(posttrade.positionsCount, 0)) +
    metricCard("Cash", formatCurrency(posttrade.cash)) +
    metricCard("Equity", formatCurrency(posttrade.equity)) +
    metricCard("Recon Status", posttrade.reconStatus || "UNKNOWN") +
    "</div>" +
    '<div class="tag-row"><span class="tag-heading">Affected Symbols</span>' +
    renderList(posttrade.affectedSymbols) +
    "</div>" +
    '<div class="tag-row"><span class="tag-heading">Repair Suggestions</span>' +
    renderList(posttrade.repairSuggestions) +
    "</div>" +
    "</article>" +
    "</section>" +
    '<section class="panel">' +
    "<h3>Delta Surface</h3>" +
    '<div class="panel-metrics">' +
    metricCard("Position Delta", formatNumber(delta.positionsCount, 0)) +
    metricCard("Cash Delta", formatCurrency(delta.cash), delta.cash >= 0 ? "good" : "warn") +
    metricCard("Equity Delta", formatCurrency(delta.equity), delta.equity >= 0 ? "good" : "warn") +
    metricCard(
      "Pretrade Recon",
      broker.pretradeReconDecision || "UNKNOWN"
    ) +
    "</div>" +
    "</section>" +
    '<section class="panel">' +
    "<h3>Artifact Paths</h3>" +
    '<div class="path-grid">' +
    Object.entries(broker.paths || {})
      .map(function (entry) {
        return (
          '<div class="path-card">' +
          '<p class="path-label">' +
          entry[0] +
          "</p>" +
          '<p class="path-value">' +
          (entry[1] || "Unavailable") +
          "</p>" +
          "</div>"
        );
      })
      .join("") +
    "</div>" +
    "</section>";
})();
