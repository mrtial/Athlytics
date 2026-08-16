let syncDetailsVisible = false;

async function refreshSyncStatus() {
  const el = document.getElementById("sync-status");
  if (!el) return;

  try {
    const res = await fetch("/api/sync-status");
    if (!res.ok) return;
    const data = await res.json();

    const parts = [];
    parts.push("<div class='sync-status-top-bar'>");
    
    // Left side: Pulse dot + Live connection info
    parts.push("<div class='sync-info'>");
    if (!data.connected) {
      parts.push("<span class='pulse-dot error'></span>");
      parts.push("<div><h3 class='sync-title' style='margin:0; font-size: 0.95rem;'>Data Source Disconnected</h3>");
      parts.push("<p class='sync-subtitle'>No Garmin account connected yet. <a href='/onboarding/connect' style='color: var(--accent-blue); font-weight: 600;'>Connect account</a></p></div>");
    } else if (data.auth_error) {
      parts.push("<span class='pulse-dot error'></span>");
      parts.push("<div><h3 class='sync-title' style='margin:0; font-size: 0.95rem; color: var(--danger);'>Garmin Connection Issue</h3>");
      parts.push(`<p class='sync-subtitle error'>${escapeHtml(data.auth_error)}. <a href='/onboarding/connect' style='color: var(--danger); font-weight: 700; text-decoration: underline;'>Reconnect</a></p></div>`);
    } else {
      parts.push("<span class='pulse-dot'></span>");
      parts.push("<div><h3 class='sync-title' style='margin:0; font-size: 0.95rem;'>Garmin Connect Live</h3>");
      const lastRun = data.last_run_at ? new Date(data.last_run_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : "Recently";
      parts.push(`<p class='sync-subtitle'>Active &bull; Last synchronized: ${escapeHtml(lastRun)}</p></div>`);
    }
    parts.push("</div>");

    // Right side: Toggle Details + Manual Sync button
    parts.push("<div class='sync-actions'>");
    if (data.metrics && data.metrics.length > 0) {
      const btnLabel = syncDetailsVisible ? "Hide Details ▴" : "Details ▾";
      parts.push(`<button type='button' class='btn-toggle-sync' onclick='toggleSyncDetails(event)' id='btn-sync-toggle'>${btnLabel}</button>`);
    }
    parts.push("<button type='button' class='btn-nav-sync' onclick='triggerManualSync(event)' id='btn-sync-trigger' style='font-size: 0.8rem; padding: 0.45rem 1rem;'>");
    parts.push("<span>⚡</span> Sync Now");
    parts.push("</button>");
    parts.push("</div>");

    parts.push("</div>");

    // Collapsible drawer (hidden by default)
    if (data.metrics && data.metrics.length > 0) {
      const displayStyle = syncDetailsVisible ? "display: flex;" : "display: none;";
      parts.push(`<div id='sync-pills-drawer' class='sync-pills-drawer' style='${displayStyle}'>`);
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
  if (evt) evt.preventDefault();
  syncDetailsVisible = !syncDetailsVisible;
  const drawer = document.getElementById("sync-pills-drawer");
  const toggleBtn = document.getElementById("btn-sync-toggle");
  if (drawer) {
    drawer.style.display = syncDetailsVisible ? "flex" : "none";
  }
  if (toggleBtn) {
    toggleBtn.textContent = syncDetailsVisible ? "Hide Details ▴" : "Details ▾";
  }
}

async function triggerManualSync(evt) {
  if (evt) {
    evt.preventDefault();
  }
  const btn = document.getElementById("btn-sync-trigger") || (evt ? evt.currentTarget : null);
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "<span>⏳</span> Syncing…";
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
        btn.innerHTML = "<span>⚡</span> Sync Now";
      }, 3000);
    }
  }
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value;
  return div.innerHTML;
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("sync-status")) {
    refreshSyncStatus();
    setInterval(refreshSyncStatus, 10000);
  }
});
