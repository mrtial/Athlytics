async function refreshSyncStatus() {
  const el = document.getElementById("sync-status");
  if (!el) return;

  const res = await fetch("/api/sync-status");
  if (!res.ok) return;
  const data = await res.json();

  const parts = [];
  parts.push("<h2>Sync status</h2>");

  if (!data.connected) {
    parts.push("<p>No data source connected yet.</p>");
  } else {
    if (data.auth_error) {
      parts.push(`<p class="error">Garmin connection issue: ${escapeHtml(data.auth_error)}. ` +
        `<a href="/onboarding/connect">Reconnect</a>.</p>`);
    }
    parts.push(`<p>Last sync attempt: ${data.last_run_at ? escapeHtml(data.last_run_at) : "never"}</p>`);
    if (data.metrics.length > 0) {
      parts.push("<ul>");
      for (const m of data.metrics) {
        parts.push(`<li>${escapeHtml(m.metric_type)}: ${escapeHtml(m.status)}</li>`);
      }
      parts.push("</ul>");
    }
  }

  el.innerHTML = parts.join("\n");
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
