const DAY_LETTERS = ["S", "M", "T", "W", "T", "F", "S"];

const CHART_CONFIG = {
  resting_hr: { kind: "line", area: true },
  hrv: { kind: "bar", avgLine: true },
  training_load: { kind: "bar", avgLine: true },
};

function chartConfigFor(metricType) {
  if (metricType.startsWith("race_predictor")) {
    return { kind: "dots", invert: true, formatValue: formatSeconds };
  }
  return CHART_CONFIG[metricType] || { kind: "dots" };
}

function formatSeconds(totalSeconds) {
  const total = Math.round(totalSeconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

function formatValueDefault(value) {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function dayLetter(dateStr) {
  const d = new Date(dateStr + "T00:00:00");
  return DAY_LETTERS[d.getDay()];
}

function axisDateLabel(dateStr) {
  const d = new Date(dateStr + "T00:00:00");
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

// Which point indices get an axis label, and how. A 7-day window labels
// every day (single weekday letter); longer windows would be unreadable
// with one label per point, so they get ~6 evenly-spaced "Aug 3"-style
// labels instead (dataviz rule: never a label on every point past a
// handful).
function axisLabelPlan(points) {
  const n = points.length;
  if (n <= 7) {
    return { indices: new Set(points.map((_, i) => i)), format: (p) => dayLetter(p.date) };
  }
  const indices = new Set();
  const step = Math.ceil(n / 6);
  for (let i = 0; i < n; i += step) indices.add(i);
  indices.add(n - 1);
  return { indices, format: (p) => axisDateLabel(p.date) };
}

function svgEl(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

/**
 * Renders a metric's 7-day detail chart into `container` (a DOM node).
 * `points` is [{date: "YYYY-MM-DD", value: number|null}, ...] from
 * /api/metric-detail. Chart kind (line/bar/dots) and value formatting are
 * picked per metric_type -- see chartConfigFor.
 */
function renderMetricChart(container, metricType, points, unit, tooltipEl) {
  const cfg = chartConfigFor(metricType);
  const formatValue = cfg.formatValue || formatValueDefault;
  const realPoints = points.filter((p) => p.value !== null);
  const n = points.length;
  // Bars stop being legible much past ~30 of them (they'd overlap or shrink
  // to slivers); a line reads better for "trend over time" at that density.
  const effectiveKind = cfg.kind === "bar" && n > 31 ? "line" : cfg.kind;

  if (realPoints.length === 0) {
    const rangeDesc = n === 365 ? "year" : `${n} days`;
    container.innerHTML = `<div class='chart-empty-state'>No data in the last ${rangeDesc}.</div>`;
    return;
  }

  const width = 640;
  const height = 220;
  const padL = 12;
  const padR = 12;
  const padT = 28;
  const padB = 28;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;

  const values = realPoints.map((p) => p.value);
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (lo === hi) {
    lo -= Math.abs(lo) * 0.1 || 1;
    hi += Math.abs(hi) * 0.1 || 1;
  } else {
    const pad = (hi - lo) * 0.15;
    lo -= pad;
    hi += pad;
  }

  const x = (i) => padL + (n === 1 ? innerW / 2 : (i * innerW) / (n - 1));
  const yOf = (v) => {
    const t = (v - lo) / (hi - lo);
    return cfg.invert ? padT + t * innerH : padT + (1 - t) * innerH;
  };

  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "none" });
  const latestRealIndex = points.findIndex((p) => p.date === realPoints[realPoints.length - 1].date);

  // Axis labels: every day for a 7-day window, ~6 evenly-spaced date labels
  // for longer ones (one-per-point would be unreadable past a handful).
  const { indices: labelIndices, format: formatAxisLabel } = axisLabelPlan(points);
  points.forEach((p, i) => {
    if (!labelIndices.has(i)) return;
    const label = svgEl("text", { x: x(i), y: height - 6, class: "chart-axis-label" });
    label.textContent = formatAxisLabel(p);
    svg.appendChild(label);
  });

  if (effectiveKind === "bar") {
    const barW = Math.max(10, (innerW / n) * 0.5);
    const baseline = padT + innerH;
    points.forEach((p, i) => {
      if (p.value === null) return;
      const cx = x(i);
      const top = yOf(p.value);
      const r = Math.min(4, baseline - top);
      const path = `M${cx - barW / 2},${baseline} L${cx - barW / 2},${top + r} Q${cx - barW / 2},${top} ${cx - barW / 2 + r},${top} L${cx + barW / 2 - r},${top} Q${cx + barW / 2},${top} ${cx + barW / 2},${top + r} L${cx + barW / 2},${baseline} Z`;
      const bar = svgEl("path", { d: path, class: `chart-bar${i === latestRealIndex ? " is-latest" : ""}` });
      svg.appendChild(bar);
      addHoverTarget(svg, cx, top, barW, baseline - top, p, unit, formatValue, tooltipEl, false, bar);
    });
  } else if (effectiveKind === "line") {
    const segments = [];
    let current = [];
    points.forEach((p, i) => {
      if (p.value === null) {
        if (current.length) segments.push(current);
        current = [];
      } else {
        current.push([x(i), yOf(p.value), p]);
      }
    });
    if (current.length) segments.push(current);

    // Past ~31 points, per-day dots turn into visual noise on a line --
    // keep the line clean and only mark the latest point (still hoverable
    // everywhere via the invisible hit target).
    const showAllDots = n <= 31;

    segments.forEach((seg) => {
      const d = seg.map(([px, py], i) => `${i === 0 ? "M" : "L"}${px},${py}`).join(" ");
      svg.appendChild(svgEl("path", { d, class: "chart-series" }));
      if (cfg.area) {
        const baseline = padT + innerH;
        const areaD = `${d} L${seg[seg.length - 1][0]},${baseline} L${seg[0][0]},${baseline} Z`;
        svg.appendChild(svgEl("path", { d: areaD, class: "chart-area" }));
      }
      seg.forEach(([px, py, p], i) => {
        const isLatest = seg === segments[segments.length - 1] && i === seg.length - 1;
        if (showAllDots || isLatest) {
          svg.appendChild(svgEl("circle", { cx: px, cy: py, r: isLatest ? 5 : 3.5, class: `chart-dot${isLatest ? " is-latest" : ""}` }));
        }
        addHoverTarget(svg, px, py, 16, 16, p, unit, formatValue, tooltipEl, true);
      });
    });
  } else {
    // dots: sparse metrics (vo2max, race predictors) -- connect only real
    // readings, in order, so gaps between them are never interpolated.
    const real = points
      .map((p, i) => ({ ...p, i }))
      .filter((p) => p.value !== null);

    if (real.length > 1) {
      const d = real.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.i)},${yOf(p.value)}`).join(" ");
      svg.appendChild(svgEl("path", { d, class: "chart-series" }));
    }
    real.forEach((p, i) => {
      const isLatest = i === real.length - 1;
      const px = x(p.i);
      const py = yOf(p.value);
      svg.appendChild(svgEl("circle", { cx: px, cy: py, r: isLatest ? 5.5 : 4, class: `chart-dot${isLatest ? " is-latest" : ""}` }));
      addHoverTarget(svg, px, py, 18, 18, p, unit, formatValue, tooltipEl, true);
    });
  }

  // Average reference line -- independent of whether bars got downgraded to
  // a line at high point density, since it reflects the metric's own job
  // (HRV/Training Load), not the render kind.
  if (cfg.avgLine) {
    const avg = values.reduce((a, b) => a + b, 0) / values.length;
    const avgY = yOf(avg);
    svg.appendChild(svgEl("line", { x1: padL, x2: width - padR, y1: avgY, y2: avgY, class: "chart-avg-line" }));
  }

  // Direct label on the most recent real reading (never label every point)
  const latest = realPoints[realPoints.length - 1];
  const labelX = Math.min(width - padR - 4, Math.max(padL + 24, x(latestRealIndex)));
  const labelY = Math.max(14, yOf(latest.value) - 12);
  const labelText = svgEl("text", { x: labelX, y: labelY, class: "chart-direct-label", "text-anchor": "middle" });
  labelText.textContent = formatValue(latest.value);
  svg.appendChild(labelText);

  container.innerHTML = "";
  container.appendChild(svg);
}

function addHoverTarget(svg, cx, cy, w, h, point, unit, formatValue, tooltipEl, round, markEl) {
  const attrs = round
    ? { cx, cy, r: Math.max(w, h) / 2 + 4, class: "chart-hit-target" }
    : { x: cx - w / 2, y: cy, width: w, height: h, class: "chart-hit-target" };
  const target = svgEl(round ? "circle" : "rect", attrs);
  target.addEventListener("mouseenter", (evt) => {
    showTooltip(evt, point, unit, formatValue, tooltipEl);
    if (markEl) markEl.classList.add("is-hovered");
  });
  target.addEventListener("mousemove", (evt) => positionTooltip(evt, tooltipEl));
  target.addEventListener("mouseleave", () => {
    hideTooltip(tooltipEl);
    if (markEl) markEl.classList.remove("is-hovered");
  });
  svg.appendChild(target);
}

function showTooltip(evt, point, unit, formatValue, tooltipEl) {
  if (!tooltipEl) return;
  const dateLabel = new Date(point.date + "T00:00:00").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  // A custom formatValue (e.g. formatSeconds) already encodes the unit as
  // mm:ss -- appending the raw stored unit ("s") on top would read as "22:56 s".
  const showUnit = unit && formatValue === formatValueDefault;
  const valueLabel = point.value === null ? "No data" : `${formatValue(point.value)}${showUnit ? " " + unit : ""}`;
  tooltipEl.innerHTML = `<span class="chart-tooltip-date">${dateLabel}</span>${valueLabel}`;
  tooltipEl.classList.add("show");
  positionTooltip(evt, tooltipEl);
}

function positionTooltip(evt, tooltipEl) {
  if (!tooltipEl) return;
  tooltipEl.style.left = `${evt.clientX + 14}px`;
  tooltipEl.style.top = `${evt.clientY - 10}px`;
}

function hideTooltip(tooltipEl) {
  if (!tooltipEl) return;
  tooltipEl.classList.remove("show");
}

window.AthlyticsCharts = { render: renderMetricChart, formatSeconds, formatValueDefault };
