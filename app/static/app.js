let syncDetailsVisible = false;

const ICONS = {
  bell: '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon icon-bell"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path><path d="M13.73 21a2 2 0 0 1-3.46 0"></path></svg>',
  zap: '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon icon-zap"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>',
  loader: '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon icon-loader icon-spin"><line x1="12" y1="2" x2="12" y2="6"></line><line x1="12" y1="18" x2="12" y2="22"></line><line x1="4.93" y1="4.93" x2="7.76" y2="7.76"></line><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"></line><line x1="2" y1="12" x2="6" y2="12"></line><line x1="18" y1="12" x2="22" y2="12"></line><line x1="4.93" y1="19.07" x2="7.76" y2="16.24"></line><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"></line></svg>',
  chevronDown: '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon icon-chevron-down"><polyline points="6 9 12 15 18 9"></polyline></svg>',
  chevronUp: '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="icon icon-chevron-up"><polyline points="18 15 12 9 6 15"></polyline></svg>',
};

async function refreshSyncStatus() {
  const el = document.getElementById("sync-status");
  if (!el) return;

  const todayStr = el.getAttribute("data-today") || new Date().toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });

  try {
    const res = await fetch("/api/sync-status");
    if (!res.ok) return;
    const data = await res.json();

    const parts = [];
    parts.push("<div class='sync-status-pill'>");

    // Pulse dot + Live Status + Formatted Date (stacked: label on top, date/time below)
    if (!data.connected) {
      parts.push("<span class='pulse-dot error'></span>");
      parts.push(`<div class='sync-pill-text'><strong>Garmin Disconnected</strong><span class='sync-pill-time'>${ICONS.bell}${escapeHtml(todayStr)}</span></div>`);
      parts.push("<a href='/onboarding/connect' class='btn-toggle-sync' style='text-decoration:none; color:var(--accent-blue); font-weight:700;'>Connect</a>");
    } else if (data.auth_error) {
      parts.push("<span class='pulse-dot error'></span>");
      parts.push(`<div class='sync-pill-text' style='color:var(--danger);'><strong>Garmin Auth Issue</strong><span class='sync-pill-time'>${ICONS.bell}${escapeHtml(todayStr)}</span></div>`);
      parts.push("<a href='/onboarding/connect' class='btn-toggle-sync' style='text-decoration:none; color:var(--danger); font-weight:700;'>Reconnect</a>");
    } else {
      parts.push("<span class='pulse-dot'></span>");
      const lastRun = data.last_run_at ? new Date(data.last_run_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : "Live";
      parts.push(`<div class='sync-pill-text'><strong>Garmin Connect Live</strong><span class='sync-pill-time'>${ICONS.bell}${escapeHtml(todayStr)} &middot; ${escapeHtml(lastRun)}</span></div>`);

      if (data.metrics && data.metrics.length > 0) {
        const chevron = syncDetailsVisible ? ICONS.chevronUp : ICONS.chevronDown;
        parts.push(`<button type='button' class='btn-toggle-sync' onclick='toggleSyncDetails(event)' id='btn-sync-toggle'>Details ${chevron}</button>`);
      }
      parts.push("<button type='button' class='btn-nav-sync' onclick='triggerManualSync(event)' id='btn-sync-trigger' style='font-size: 0.76rem; padding: 0.35rem 0.85rem;'>");
      parts.push(`${ICONS.zap} Sync`);
      parts.push("</button>");
    }

    parts.push("</div>");

    // Collapsible drawer (dropdown below chip)
    if (data.metrics && data.metrics.length > 0) {
      const displayStyle = syncDetailsVisible ? "display: flex;" : "display: none;";
      parts.push(`<div id='sync-pills-drawer' class='sync-header-drawer' style='${displayStyle}'>`);
      for (const m of data.metrics) {
        const isComplete = m.status === 'complete' || m.status === 'up_to_date';
        const bg = isComplete ? 'var(--success-soft)' : 'var(--warning-soft)';
        const fg = isComplete ? 'var(--success)' : 'var(--warning)';
        parts.push(`<span style='background: ${bg}; color: ${fg}; font-size: 0.72rem; font-weight: 700; padding: 0.2rem 0.55rem; border-radius: var(--radius-pill);'>${escapeHtml(m.metric_type)}: ${escapeHtml(m.status)}</span>`);
      }
      parts.push("</div>");
    }

    el.innerHTML = parts.join("\n");
  } catch (err) {
    console.error("Failed to refresh sync status:", err);
  }
}

function toggleSyncDetails(evt) {
  if (evt) {
    evt.preventDefault();
    evt.stopPropagation();
  }
  syncDetailsVisible = !syncDetailsVisible;
  const drawer = document.getElementById("sync-pills-drawer");
  const toggleBtn = document.getElementById("btn-sync-toggle");
  if (drawer) {
    drawer.style.display = syncDetailsVisible ? "flex" : "none";
  }
  if (toggleBtn) {
    toggleBtn.innerHTML = `Details ${syncDetailsVisible ? ICONS.chevronUp : ICONS.chevronDown}`;
  }
}

function toggleUserDropdown(evt) {
  if (evt) {
    evt.preventDefault();
    evt.stopPropagation();
  }
  const menu = document.getElementById("user-dropdown-menu");
  const btn = document.getElementById("user-chip-btn");
  if (!menu) return;

  const isOpen = menu.classList.contains("show");
  if (isOpen) {
    menu.classList.remove("show");
    if (btn) btn.setAttribute("aria-expanded", "false");
  } else {
    menu.classList.add("show");
    if (btn) btn.setAttribute("aria-expanded", "true");
  }
}

// Close dropdowns when clicking outside
document.addEventListener("click", (evt) => {
  const userMenu = document.getElementById("user-dropdown-menu");
  const userBtn = document.getElementById("user-chip-btn");
  if (userMenu && userMenu.classList.contains("show")) {
    if (!userMenu.contains(evt.target) && (!userBtn || !userBtn.contains(evt.target))) {
      userMenu.classList.remove("show");
      if (userBtn) userBtn.setAttribute("aria-expanded", "false");
    }
  }

  const syncDrawer = document.getElementById("sync-pills-drawer");
  const syncBtn = document.getElementById("btn-sync-toggle");
  if (syncDrawer && syncDetailsVisible) {
    if (!syncDrawer.contains(evt.target) && (!syncBtn || !syncBtn.contains(evt.target))) {
      syncDetailsVisible = false;
      syncDrawer.style.display = "none";
      if (syncBtn) syncBtn.innerHTML = `Details ${ICONS.chevronDown}`;
    }
  }
});

async function triggerManualSync(evt) {
  if (evt) {
    evt.preventDefault();
  }
  const btn = document.getElementById("btn-sync-trigger") || (evt ? evt.currentTarget : null);
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `${ICONS.loader} Syncing…`;
  }

  try {
    const res = await fetch("/api/sync/trigger", { method: "POST" });
    if (res.ok) {
      setTimeout(refreshSyncStatus, 1500);
      setTimeout(refreshSyncStatus, 5000);
    }
  } catch (err) {
    console.error("Failed to trigger sync:", err);
  } finally {
    if (btn) {
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = `${ICONS.zap} Sync`;
      }, 3000);
    }
  }
}

// Settings page: one-off, manually-triggered resync of a metric's entire
// history (ignores checkpoints -- see /api/sync/full-history), distinct
// from the routine "Sync Now" action which only ever fetches new data.
// The server runs it in a background thread and responds immediately, so
// this just confirms it started; actual progress shows up in the
// Dashboard's sync status over the next several minutes.
async function triggerFullHistorySync(evt) {
  if (evt) evt.preventDefault();
  const btn = document.getElementById("btn-full-history-sync");
  const statusEl = document.getElementById("full-history-sync-status");

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `${ICONS.loader} Starting…`;
  }

  try {
    const res = await fetch("/api/sync/full-history", { method: "POST" });
    if (statusEl) {
      statusEl.style.display = "block";
      statusEl.textContent = res.ok
        ? "Full history sync started -- this can take several minutes. Check the Dashboard's sync status for progress."
        : "Couldn't start full history sync. Try again in a moment.";
    }
  } catch (err) {
    console.error("Failed to trigger full history sync:", err);
    if (statusEl) {
      statusEl.style.display = "block";
      statusEl.textContent = "Couldn't start full history sync. Try again in a moment.";
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Full History Sync";
    }
  }
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value;
  return div.innerHTML;
}

// Metric cards are always small; clicking one promotes its data into the
// fixed detail panel (top-left, 2 cols x 2 rows -- see .metric-detail-panel),
// rather than the card itself changing size/position. Only one card is
// "active" (shown in the panel) at a time.
let activeMetricCard = null;
let selectedRangeDays = 7;
const metricDetailTooltip = document.createElement("div");
metricDetailTooltip.className = "chart-tooltip";

function rangeLabel(days) {
  return days === 365 ? "1-year average" : `${days}-day average`;
}

function selectMetricCard(card) {
  if (activeMetricCard === card) return;
  if (activeMetricCard) {
    activeMetricCard.classList.remove("metric-card--active");
    activeMetricCard.setAttribute("aria-pressed", "false");
  }
  card.classList.add("metric-card--active");
  card.setAttribute("aria-pressed", "true");
  activeMetricCard = card;

  loadMetricDetail(card.dataset.metricType, card.dataset.metricLabel, card.querySelector(".metric-pill").innerHTML);
}

async function loadMetricDetail(metricType, label, iconHtml) {
  const panel = document.getElementById("metric-detail-panel");
  const iconEl = document.getElementById("metric-detail-panel-icon");
  const labelEl = document.getElementById("metric-detail-panel-label");
  const bodyEl = document.getElementById("metric-detail-panel-body");
  if (!panel || !bodyEl) return;

  iconEl.innerHTML = iconHtml;
  labelEl.textContent = label;
  bodyEl.classList.add("is-loading");

  try {
    const res = await fetch(`/api/metric-detail/${encodeURIComponent(metricType)}?days=${selectedRangeDays}`);
    if (!res.ok) throw new Error(`status ${res.status}`);
    const data = await res.json();

    const isTimeMetric = metricType.startsWith("race_predictor");
    const formatValue = isTimeMetric ? AthlyticsCharts.formatSeconds : AthlyticsCharts.formatValueDefault;
    const realValues = data.points.map((p) => p.value).filter((v) => v !== null);
    const average = realValues.length ? realValues.reduce((a, b) => a + b, 0) / realValues.length : null;

    const header = document.createElement("div");
    header.className = "metric-detail-header";
    const valueLabel = average !== null ? formatValue(average) : "—";
    // formatSeconds already encodes the unit as mm:ss -- don't also append
    // the raw stored unit ("s"), which would read as "22:56 s".
    const unitLabel = data.unit && !isTimeMetric ? ` <span class="metric-unit">${escapeHtml(data.unit)}</span>` : "";
    header.innerHTML = `<span class="metric-detail-value">${valueLabel}${unitLabel}</span><span class="metric-detail-range">${escapeHtml(label)} &middot; ${rangeLabel(selectedRangeDays)}</span>`;

    const chartContainer = document.createElement("div");
    chartContainer.className = "metric-detail-chart";

    bodyEl.innerHTML = "";
    bodyEl.appendChild(header);
    bodyEl.appendChild(chartContainer);
    bodyEl.classList.remove("is-loading");

    AthlyticsCharts.render(chartContainer, metricType, data.points, data.unit, metricDetailTooltip);
  } catch (err) {
    console.error("Failed to load metric detail:", err);
    bodyEl.innerHTML = "<div class='chart-empty-state'>Couldn't load chart data.</div>";
    bodyEl.classList.remove("is-loading");
  }
}

function selectRange(days, toggleEl) {
  if (days === selectedRangeDays) return;
  selectedRangeDays = days;
  toggleEl.querySelectorAll(".range-btn").forEach((btn) => {
    btn.classList.toggle("is-active", Number(btn.dataset.days) === days);
  });
  if (activeMetricCard) {
    loadMetricDetail(activeMetricCard.dataset.metricType, activeMetricCard.dataset.metricLabel, activeMetricCard.querySelector(".metric-pill").innerHTML);
  }
}

function initMetricCards() {
  const grid = document.getElementById("metrics-grid");
  if (!grid) return;

  document.body.appendChild(metricDetailTooltip);

  grid.addEventListener("click", (evt) => {
    const card = evt.target.closest(".metric-card");
    if (card) selectMetricCard(card);
  });

  grid.addEventListener("keydown", (evt) => {
    if (evt.key !== "Enter" && evt.key !== " ") return;
    const card = evt.target.closest(".metric-card");
    if (!card) return;
    evt.preventDefault();
    selectMetricCard(card);
  });

  const rangeToggle = document.getElementById("metric-detail-range-toggle");
  if (rangeToggle) {
    rangeToggle.addEventListener("click", (evt) => {
      const btn = evt.target.closest(".range-btn");
      if (btn) selectRange(Number(btn.dataset.days), rangeToggle);
    });
  }

  // Default to Resting HR (the primary daily recovery signal) if present,
  // otherwise the first metric card, so the panel isn't empty on load.
  const cards = Array.from(grid.querySelectorAll(".metric-card"));
  const defaultCard = cards.find((c) => c.dataset.metricType === "resting_hr") || cards[0];
  if (defaultCard) selectMetricCard(defaultCard);
}

// Theme toggle in the user dropdown: applies instantly (matching base.html's
// own bootstrap logic) and persists server-side via the existing
// /settings/theme route, fire-and-forget, so it stays in sync next login.
function initThemeToggle() {
  const group = document.getElementById("theme-toggle-group");
  if (!group) return;

  group.addEventListener("click", (evt) => {
    const btn = evt.target.closest(".theme-toggle-btn");
    if (!btn) return;
    const value = btn.dataset.themeValue;
    if (value === document.documentElement.getAttribute("data-theme")) return;

    document.documentElement.setAttribute("data-theme", value);
    localStorage.setItem("athlytics_theme", value);
    group.querySelectorAll(".theme-toggle-btn").forEach((b) => {
      b.classList.toggle("is-active", b === btn);
    });

    fetch("/settings/theme", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: `theme=${encodeURIComponent(value)}`,
    }).catch((err) => console.error("Failed to persist theme:", err));
  });
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("sync-status")) {
    refreshSyncStatus();
    setInterval(refreshSyncStatus, 10000);
  }
  initMetricCards();
  initThemeToggle();
});
