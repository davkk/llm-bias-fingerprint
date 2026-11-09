(function () {
  'use strict';

  var MAX_SAMPLES = 24;
  var MAX_PROMPT = 90;

  function makeEl(tag, className) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  function makeNote(text, isErr) {
    var p = makeEl('p', isErr ? 'detail-note err' : 'detail-note');
    p.textContent = text;
    return p;
  }

  function shorten(text) {
    if (text.length <= MAX_PROMPT) return text;
    return text.slice(0, MAX_PROMPT) + '\u2026';
  }

  function groupByPrompt(samples) {
    var groups = [];
    var index = new Map();
    for (var i = 0; i < samples.length; i++) {
      var sample = samples[i];
      var prompt = sample && sample.prompt != null ? String(sample.prompt) : '';
      if (!index.has(prompt)) {
        index.set(prompt, groups.length);
        groups.push({ prompt: prompt, samples: [] });
      }
      groups[index.get(prompt)].samples.push(sample);
    }
    return groups;
  }

  function buildGroup(group) {
    var wrap = makeEl('div', 'samples-group');

    var promptEl = makeEl('span', 'samples-prompt');
    promptEl.textContent = shorten(group.prompt);
    wrap.appendChild(promptEl);

    var list = makeEl('div', 'samples');
    var shown = Math.min(group.samples.length, MAX_SAMPLES);
    for (var i = 0; i < shown; i++) {
      var sample = group.samples[i] || {};

      var item = makeEl('span', 'sample');

      var idx = makeEl('span', 'sample-index');
      idx.textContent = String(i);
      item.appendChild(idx);

      var raw = makeEl('span', 'sample-raw');
      raw.textContent = sample.raw == null ? '' : String(sample.raw);
      item.appendChild(raw);

      list.appendChild(item);
    }
    wrap.appendChild(list);

    var hidden = group.samples.length - shown;
    if (hidden > 0) {
      wrap.appendChild(makeNote('+' + hidden + ' more', false));
    }

    return wrap;
  }

  window.SamplesPanel = {
    loading: function (el) {
      el.replaceChildren(makeNote('loading samples...', false));
    },

    error: function (el, msg) {
      el.replaceChildren(makeNote('could not load samples: ' + (msg == null ? '' : String(msg)), true));
    },

    render: function (el, samples) {
      el.replaceChildren();

      if (!Array.isArray(samples) || samples.length === 0) {
        el.appendChild(makeNote('no samples yet', false));
        return;
      }

      var groups = groupByPrompt(samples);
      var frag = document.createDocumentFragment();
      for (var i = 0; i < groups.length; i++) {
        frag.appendChild(buildGroup(groups[i]));
      }
      el.appendChild(frag);
    }
  };
})();

"use strict";

// ---------- config ----------
const POLL_MS = 1500;
const FETCH_TIMEOUT_MS = 5000;
const DISCOVERY_MS = 10000;

// Try the page's own origin first (the dashboard is served by the API at "/"),
// then fall back to the default port. Override with ?api=http://host:port
const params = new URLSearchParams(location.search);
const override = params.get("api");
const candidates = override
  ? [override.replace(/\/+$/, "")]
  : (location.protocol.startsWith("http") ? ["", "http://localhost:8000"] : ["http://localhost:8000"]);
let apiIndex = 0;
let API = candidates[0];

function apiLabel() {
  return API || location.origin;
}

// ---------- dom ----------
const $ = (id) => document.getElementById(id);
const tbody = $("runs-body");
const emptyEl = $("empty");
const connDot = $("conn-dot");
const connText = $("conn-text");
const updatedEl = $("updated");
const pauseBtn = $("pause-btn");
const distRowsEl = $("distribution-rows");
const distNEl = $("distribution-n");
const distNoteEl = $("distribution-note");
const distExpEl = $("distribution-exp");
const experimentChipsEl = $("experiment-chips");
const planesExpEl = $("planes-exp");
const planesSourceEl = $("planes-source");
const planesPrecisionEl = $("planes-precision");
const planesHeatmapEl = $("planes-heatmap");
const planesPairwiseEl = $("planes-pairwise");
const planesRankEl = $("planes-rank");

const counters = {
  total: $("n-total"),
  running: $("n-running"),
  waiting: $("n-waiting"),
  done: $("n-done"),
};

$("api-host").textContent = apiLabel();

// ---------- state ----------
let paused = false;
let timer = null;
const rows = new Map();   // run id -> { tr, els }
const expanded = new Set(); // run ids with an open detail row
let lastPayload = "";
let lastAnalysisKey = "";       // dedup key for the selected experiment's render
let analysisUnscoped = null;    // last good unscoped /analysis (chip discovery)
let analysisUnscopedRaw = "";
let lastDiscoveryAt = 0;
let experimentNames = [];
let selectedExperiment = null;
let chipsSignature = "";
let analysisBusy = false;
let analysisQueued = false;
const analysisCache = new Map(); // experiment -> { reg, regRaw, val, valRaw }

// ---------- helpers ----------
const nowSec = () => Math.floor(Date.now() / 1000);

function statusOf(run) {
  if (run.status === "failed") return "failed";
  if (run.status === "done") return "done";
  if (run.lease_until == null) return "pending";
  return run.lease_until > nowSec() ? "running" : "stalled";
}

function setConn(kind, text) {
  connDot.className = "dot dot-" + kind;
  connText.textContent = text;
}

function setPaused(value) {
  paused = value;
  pauseBtn.textContent = paused ? "resume" : "pause";
  pauseBtn.classList.toggle("active", paused);
  if (paused) setConn("pause", "paused");
  else refresh();
}

// ---------- rendering ----------
function makeRow(run) {
  const tr = document.createElement("tr");
  tr.className = "run-row";
  tr.dataset.runId = String(run.id);

  const cell = (cls) => {
    const td = document.createElement("td");
    if (cls) td.className = cls;
    tr.append(td);
    return td;
  };

  const els = {
    id: cell("col-id mono"),
    experiment: cell("experiment"),
    model: cell("model mono"),
  };

  const statusTd = cell();
  const chip = document.createElement("span");
  statusTd.append(chip);

  const progressTd = cell("col-progress");
  const bar = document.createElement("div");
  bar.className = "progress";
  const fill = document.createElement("div");
  fill.className = "progress-bar";
  bar.append(fill);
  const pct = document.createElement("span");
  pct.className = "progress-label";
  progressTd.append(bar, pct);

  els.samples = cell("col-samples mono samples-count");
  els.lease = cell("col-lease mono lease");

  els.chip = chip;
  els.fill = fill;
  els.pct = pct;

  tr.addEventListener("click", () => toggleDetail(run.id));
  tbody.append(tr);
  rows.set(run.id, { tr, els });
  return rows.get(run.id);
}

function updateRow(entry, run, status) {
  const { els } = entry;
  els.id.textContent = String(run.id);
  els.experiment.textContent = run.experiment;
  els.model.textContent = run.model;

  els.chip.className = "chip chip-" + status;
  els.chip.textContent = status;

  const total = run.n_prompts * run.samples_per_prompt;
  const done = run.n_samples;
  const ratio = total > 0 ? Math.min(1, done / total) : 0;

  els.fill.style.width = (ratio * 100).toFixed(1) + "%";
  els.fill.classList.toggle("done", status === "done");
  els.pct.textContent = total > 0 ? Math.round(ratio * 100) + "%" : "\u2014";
  els.samples.textContent = total > 0 ? done + " / " + total : done + " / \u2014";
  els.samples.title = run.n_prompts + " prompt(s) x " + run.samples_per_prompt + " samples";

  els.lease.className = "col-lease mono lease";
  els.lease.dataset.until = String(run.lease_until == null ? 0 : run.lease_until);
  if (status === "running") {
    const left = Math.max(0, run.lease_until - nowSec());
    els.lease.textContent = left + "s";
    els.lease.classList.add("live");
  } else if (status === "stalled") {
    els.lease.textContent = "stalled";
    els.lease.classList.add("stalled");
  } else {
    els.lease.textContent = "\u2014";
  }
}

function setEmpty(text) {
  if (!text) {
    emptyEl.hidden = true;
    return;
  }
  emptyEl.textContent = text;
  emptyEl.hidden = false;
}

function render(runs) {
  const seen = new Set();

  for (const run of runs) {
    seen.add(run.id);
    const status = statusOf(run);
    const entry = rows.get(run.id) || makeRow(run);
    updateRow(entry, run, status);
  }

  for (const id of [...rows.keys()]) {
    if (seen.has(id)) continue;
    const entry = rows.get(id);
    entry.tr.remove();
    rows.delete(id);
    expanded.delete(id);
  }

  const counts = { total: runs.length, running: 0, waiting: 0, done: 0 };
  for (const run of runs) {
    const status = statusOf(run);
    if (status === "running") counts.running++;
    else if (status === "done") counts.done++;
    else counts.waiting++;
  }
  for (const key of Object.keys(counters)) counters[key].textContent = String(counts[key]);

  setEmpty(runs.length ? null : "no runs yet \u2014 POST /runs");
}

function renderPrompts(el, prompts) {
  el.replaceChildren();
  if (!Array.isArray(prompts) || prompts.length === 0) {
    const note = document.createElement("span");
    note.className = "detail-note";
    note.textContent = "no prompts";
    el.append(note);
    return;
  }
  for (const p of prompts) {
    const chip = document.createElement("span");
    chip.className = "detail-prompt-chip";
    chip.textContent = p == null ? "" : String(p);
    chip.title = chip.textContent;
    el.append(chip);
  }
}

// ---------- detail row ----------
async function toggleDetail(id) {
  const entry = rows.get(id);
  if (!entry) return;

  if (expanded.has(id)) {
    expanded.delete(id);
    entry.tr.classList.remove("expanded");
    const next = entry.tr.nextElementSibling;
    if (next && next.classList.contains("detail-row")) next.remove();
    return;
  }

  expanded.add(id);
  entry.tr.classList.add("expanded");

  const tr = document.createElement("tr");
  tr.className = "detail-row";
  const td = document.createElement("td");
  td.colSpan = 7;
  const div = document.createElement("div");
  div.className = "detail";
  td.append(div);
  tr.append(td);
  entry.tr.after(tr);

  const promptsEl = document.createElement("div");
  promptsEl.className = "detail-prompts";
  const promptsLabel = document.createElement("span");
  promptsLabel.className = "detail-prompts-label";
  promptsLabel.textContent = "prompts";
  const promptsList = document.createElement("div");
  promptsList.className = "detail-prompts-list";
  promptsEl.append(promptsLabel, promptsList);

  const samplesEl = document.createElement("div");
  samplesEl.className = "detail-samples";

  div.append(promptsEl, samplesEl);

  // prompts first (small, fast), then samples
  try {
    const pres = await fetch(API + "/prompts?run_id=" + id, { cache: "no-store" });
    if (pres.ok) {
      const prompts = await pres.json();
      renderPrompts(promptsList, prompts);
    } else {
      promptsList.textContent = "could not load prompts";
    }
  } catch (e) {
    promptsList.textContent = "could not load prompts";
  }

  SamplesPanel.loading(samplesEl);
  try {
    const res = await fetch(API + "/samples?run_id=" + id, { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    SamplesPanel.render(samplesEl, await res.json());
  } catch (err) {
    SamplesPanel.error(samplesEl, err && err.message ? err.message : String(err));
  }
}

// ---------- distribution ----------
const DIST_COLORS = {
  blue: "#4c8dff",
  red: "#f85149",
  purple: "#bc8cff",
  black: "#8b949e",
};
const DIST_OTHER_COLOR = "#3fb950";
const DIST_MIN_SEG_LABEL = 0.04;   // segment label only above 4%
const DIST_MIN_SUMMARY_SHARE = 0.005; // rest is bucketed as "other"
const DIST_BYTE = "\u00b7";

function distEntries(dist) {
  const entries = [];
  if (!dist || typeof dist !== "object") return entries;
  for (const label of Object.keys(dist)) {
    const count = Number(dist[label]);
    if (!Number.isFinite(count) || count <= 0) continue;
    entries.push([String(label), count]);
  }
  return entries;
}

function distSum(entries) {
  let total = 0;
  for (const entry of entries) total += entry[1];
  return total;
}

function buildDistSegment(label, count, total) {
  const share = total > 0 ? count / total : 0;
  const seg = document.createElement("div");
  seg.className = "distribution-seg";
  seg.style.width = (share * 100).toFixed(3) + "%";
  seg.style.background = DIST_COLORS[label] || DIST_OTHER_COLOR;
  seg.title = label + ": " + count + "/" + total + " (" + (share * 100).toFixed(1) + "%)";
  if (share >= DIST_MIN_SEG_LABEL) {
    const text = document.createElement("span");
    text.className = "distribution-seg-label";
    text.textContent = label;
    seg.append(text);
  }
  return seg;
}

function buildDistBar(entries, total) {
  const bar = document.createElement("div");
  bar.className = "distribution-bar";
  for (const [label, count] of entries) {
    bar.append(buildDistSegment(label, count, total));
  }
  return bar;
}

function buildDistSummary(entries, total) {
  const shareEl = document.createElement("span");
  shareEl.className = "distribution-share";
  const parts = [];
  let other = 0;
  for (const [label, count] of entries) {
    const share = total > 0 ? count / total : 0;
    if (share >= DIST_MIN_SUMMARY_SHARE) parts.push(label + " " + Math.round(share * 100));
    else other += share;
  }
  const otherPct = Math.round(other * 100);
  if (otherPct > 0) parts.push("other " + otherPct);
  shareEl.textContent = parts.join(" " + DIST_BYTE + " ");
  return shareEl;
}

function buildDistRow(model, dist, rowClass) {
  const entries = distEntries(dist);
  const total = distSum(entries);

  const row = document.createElement("div");
  row.className = rowClass ? "distribution-row " + rowClass : "distribution-row";

  const name = document.createElement("span");
  name.className = "distribution-model mono";
  name.textContent = model;
  name.title = model;

  row.append(name, buildDistBar(entries, total), buildDistSummary(entries, total));
  return row;
}

function renderDistribution(data) {
  const frag = document.createDocumentFragment();

  // quant size descending: Q8 -> Q3
  const byModel = (Array.isArray(data.by_model) ? data.by_model.slice() : [])
    .sort((a, b) => compareStrings((b && b.model != null ? String(b.model) : ""), (a && a.model != null ? String(a.model) : "")));
  for (const entry of byModel) {
    if (!entry || typeof entry !== "object") continue;
    frag.append(buildDistRow(entry.model == null ? "" : String(entry.model), entry.dist, ""));
  }

  const overall = data.overall && typeof data.overall === "object" ? data.overall : {};
  frag.append(buildDistRow("all models", overall.dist, "distribution-all"));
  distRowsEl.replaceChildren(frag);

  const overallN = Number(overall.n);
  const fallbackN = distSum(distEntries(overall.dist));
  distNEl.textContent = "n = " + (Number.isFinite(overallN) ? overallN : fallbackN);
  distNoteEl.hidden = true;
  if (distExpEl) distExpEl.textContent = selectedExperiment || "";
}

// ---------- planes: shared helpers ----------
const PLANE_PALETTE = ["#d29922", "#4c8dff", "#bc8cff", "#3fb950", "#f85149"];
const PLANE_BASE = "#171a21";
const PLANE_BLUE = "#4c8dff";
const PLANE_PURPLE = "#bc8cff";
const SVG_NS = "http://www.w3.org/2000/svg";

function planesEl(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function planesNote(text) {
  const p = planesEl("p", "planes-state");
  p.textContent = text;
  return p;
}

function compareStrings(a, b) {
  return a < b ? -1 : a > b ? 1 : 0;
}

function compareLabelsNumeric(a, b) {
  const na = Number(a);
  const nb = Number(b);
  if (Number.isFinite(na) && Number.isFinite(nb) && na !== nb) return na - nb;
  return compareStrings(a, b);
}

function paletteColor(index) {
  return PLANE_PALETTE[index % PLANE_PALETTE.length];
}

function clampNum(value, lo, hi) {
  return value < lo ? lo : value > hi ? hi : value;
}

function hexToRgb(hex) {
  const value = hex.replace("#", "");
  return [
    parseInt(value.slice(0, 2), 16),
    parseInt(value.slice(2, 4), 16),
    parseInt(value.slice(4, 6), 16),
  ];
}

function mixHex(from, to, ratio) {
  const a = hexToRgb(from);
  const b = hexToRgb(to);
  const t = clampNum(ratio, 0, 1);
  return "rgb(" + Math.round(a[0] + (b[0] - a[0]) * t) + ", " +
    Math.round(a[1] + (b[1] - a[1]) * t) + ", " +
    Math.round(a[2] + (b[2] - a[2]) * t) + ")";
}

function svgNode(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  if (attrs) {
    for (const key of Object.keys(attrs)) node.setAttribute(key, String(attrs[key]));
  }
  return node;
}

function svgText(x, y, text, anchor) {
  const node = svgNode("text", {
    x: x,
    y: y,
    "font-size": 8,
    fill: "#8b93a7",
    "text-anchor": anchor || "start",
  });
  node.textContent = text;
  return node;
}

function formatSharePct(value) {
  const pct = value * 100;
  return (pct >= 10 || pct === 0 ? pct.toFixed(0) : pct.toFixed(1)) + "%";
}

function planeModels(data) {
  const models = [];
  const rows = Array.isArray(data && data.by_model) ? data.by_model : [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const entries = distEntries(row.dist);
    if (!entries.length) continue;
    const sum = distSum(entries);
    let n = Number(row.n);
    if (!Number.isFinite(n) || n <= 0) n = sum;
    models.push({
      model: row.model == null ? "" : String(row.model),
      entries: entries,
      n: n,
      sum: sum,
    });
  }
  // quant size descending: Q8 -> Q3
  models.sort((a, b) => compareStrings(b.model, a.model));
  return models;
}

function shareOf(count, model) {
  if (model.n > 0) return count / model.n;
  if (model.sum > 0) return count / model.sum;
  return 0;
}

function topShareOf(model) {
  let max = 0;
  for (const entry of model.entries) if (entry[1] > max) max = entry[1];
  return shareOf(max, model);
}

function entropyOf(model) {
  let h = 0;
  for (const entry of model.entries) {
    const p = shareOf(entry[1], model);
    if (p > 0) h -= p * Math.log2(p);
  }
  return h;
}

function normalizedEntropyOf(model) {
  const h = entropyOf(model);
  const k = Math.max(1, model.entries.length);
  const denom = Math.log2(k);
  return denom > 0 ? h / denom : 0;
}

function jsDistance(left, right) {
  const leftDist = new Map(left.entries);
  const rightDist = new Map(right.entries);
  const labels = new Set();
  for (const label of leftDist.keys()) labels.add(label);
  for (const label of rightDist.keys()) labels.add(label);

  let klLeft = 0;
  let klRight = 0;
  for (const label of labels) {
    const p = shareOf(leftDist.get(label) || 0, left);
    const q = shareOf(rightDist.get(label) || 0, right);
    const mean = (p + q) / 2;
    // 0 * log2(0) is taken as 0 by construction
    if (p > 0 && mean > 0) klLeft += p * Math.log2(p / mean);
    if (q > 0 && mean > 0) klRight += q * Math.log2(q / mean);
  }
  const js = 0.5 * klLeft + 0.5 * klRight;
  return Math.sqrt(Math.max(0, js));
}

// ---------- planes: panel renderers (each returns a node or note) ----------
function buildMiniChart(models, metric) {
  const W = 230;
  const H = 120;
  const padL = 36;
  const padR = 8;
  const padT = 10;
  const padB = 14;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  let top = 0;
  for (const model of models) {
    const value = metric.value(model);
    if (Number.isFinite(value) && value > top) top = value;
  }
  if (!(top > 0)) top = 1;
  top *= 1.12;

  const x = (index) => models.length <= 1
    ? padL + innerW / 2
    : padL + ((index + 0.5) * innerW) / models.length;
  const y = (value) => padT + (1 - clampNum(value, 0, top) / top) * innerH;

  const svg = svgNode("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": metric.title });
  svg.classList.add("planes-svg");

  for (const t of [0, 0.5, 1]) {
    const gy = y(top * t);
    svg.append(svgNode("line", {
      x1: padL, y1: gy, x2: W - padR, y2: gy,
      stroke: "#262b36", "stroke-width": 1,
    }));
  }
  svg.append(svgText(padL - 5, padT + 4, metric.format(top), "end"));
  svg.append(svgText(padL - 5, padT + innerH, metric.format(0), "end"));
  svg.append(svgNode("line", {
    x1: padL, y1: padT + innerH, x2: W - padR, y2: padT + innerH,
    stroke: "#262b36", "stroke-width": 1,
  }));

  const points = models.map((model, index) => x(index) + "," + y(metric.value(model)));
  if (models.length > 1) {
    svg.append(svgNode("polyline", {
      points: points.join(" "), fill: "none", stroke: "#8b93a7",
      "stroke-width": 1.2, "stroke-opacity": 0.55, "stroke-linejoin": "round",
    }));
  }

  models.forEach((model, index) => {
    const value = metric.value(model);
    const dot = svgNode("circle", {
      cx: x(index), cy: y(value), r: 2.6, fill: paletteColor(index),
    });
    const title = svgNode("title");
    title.textContent = model.model + " \u00b7 " + metric.title + " " + metric.format(value);
    dot.append(title);
    svg.append(dot);
  });

  return svg;
}

function buildPlaneLegend(models) {
  const legend = planesEl("div", "planes-legend");
  models.forEach((model, index) => {
    const item = planesEl("span", "planes-legend-item");
    const swatch = planesEl("span", "planes-legend-swatch");
    swatch.style.background = paletteColor(index);
    const name = planesEl("span", "planes-legend-name");
    name.textContent = model.model;
    name.title = model.model;
    item.append(swatch, name);
    legend.append(item);
  });
  return legend;
}

function buildPrecisionPanel(models) {
  if (!models.length) return planesNote("no model distributions");
  const metrics = [
    { title: "top-1 %", value: topShareOf, format: formatSharePct },
    { title: "H (bits)", value: entropyOf, format: (value) => Number(value).toFixed(2) },
    { title: "H / log2 k", value: normalizedEntropyOf, format: (value) => Number(value).toFixed(2) },
  ];

  const row = planesEl("div", "planes-mini-row");
  for (const metric of metrics) {
    const cell = planesEl("div", "planes-mini");
    const title = planesEl("div", "planes-mini-title");
    title.textContent = metric.title;
    cell.append(title, buildMiniChart(models, metric));
    row.append(cell);
  }

  const frag = document.createDocumentFragment();
  frag.append(row, buildPlaneLegend(models));
  return frag;
}

function buildLabelCounts(data, models) {
  const counts = new Map();
  const overall = data && data.overall && typeof data.overall === "object" ? data.overall : null;
  if (overall) {
    for (const entry of distEntries(overall.dist)) counts.set(entry[0], entry[1]);
  }
  for (const model of models) {
    for (const entry of model.entries) {
      if (!counts.has(entry[0])) counts.set(entry[0], entry[1]);
    }
  }
  return counts;
}

function buildHeatmapPanel(data, models, byValue) {
  if (!models.length) return planesNote("no model distributions");
  const counts = buildLabelCounts(data, models);
  if (!counts.size) return planesNote("no labels");

  const ranked = Array.from(counts.entries());
  ranked.sort((a, b) => {
    if (b[1] !== a[1]) return b[1] - a[1];
    return byValue ? compareLabelsNumeric(a[0], b[0]) : compareStrings(a[0], b[0]);
  });
  const labels = ranked.slice(0, 12).map((entry) => entry[0]);
  if (byValue) labels.sort(compareLabelsNumeric);

  const maps = models.map((model) => new Map(model.entries));
  const shares = models.map((model, modelIndex) =>
    labels.map((label) => shareOf(maps[modelIndex].get(label) || 0, model))
  );
  let maxShare = 0;
  for (const row of shares) {
    for (const share of row) if (share > maxShare) maxShare = share;
  }

  const grid = planesEl("div", "planes-heat");
  grid.style.gridTemplateColumns = "190px repeat(" + labels.length + ", minmax(34px, 1fr))";
  grid.append(planesEl("div", "planes-heat-corner"));

  for (const label of labels) {
    const head = planesEl("div", "planes-heat-head");
    head.textContent = label;
    head.title = label;
    grid.append(head);
  }

  models.forEach((model, modelIndex) => {
    const name = planesEl("div", "planes-heat-label");
    name.textContent = model.model;
    name.title = model.model;
    grid.append(name);

    labels.forEach((label, labelIndex) => {
      const share = shares[modelIndex][labelIndex];
      const count = maps[modelIndex].get(label) || 0;
      // shifted log scale: lifts low values so faint cells stay visible
      const c = maxShare * 0.01 + 1e-6;
      const ratio = maxShare > 0
        ? clampNum((Math.log(share + c) - Math.log(c)) / (Math.log(maxShare + c) - Math.log(c)), 0, 1)
        : 0;
      const cell = planesEl("div", "planes-heat-cell");
      cell.style.background = mixHex(PLANE_BASE, PLANE_BLUE, ratio);
      cell.title = label + " " + count + " (" + (share * 100).toFixed(1) + "%)";
      grid.append(cell);
    });
  });

  const scroll = planesEl("div", "planes-scroll");
  scroll.append(grid);
  const frag = document.createDocumentFragment();
  frag.append(scroll);
  return frag;
}

function buildPairwisePanel(models) {
  if (models.length < 2) return planesNote("needs at least two models");

  const matrix = models.map((left) => models.map((right) => jsDistance(left, right)));
  const grid = planesEl("div", "planes-matrix");
  grid.style.gridTemplateColumns = "190px repeat(" + models.length + ", minmax(44px, 1fr))";
  grid.append(planesEl("div", "planes-matrix-corner"));

  for (const model of models) {
    const head = planesEl("div", "planes-matrix-head");
    head.textContent = model.model;
    head.title = model.model;
    grid.append(head);
  }

  models.forEach((left, i) => {
    const label = planesEl("div", "planes-matrix-label");
    label.textContent = left.model;
    label.title = left.model;
    grid.append(label);

    models.forEach((right, j) => {
      const cell = planesEl("div", "planes-matrix-cell");
      if (i === j) {
        cell.classList.add("planes-diag");
        cell.textContent = "\u2014";
        cell.title = left.model + " diagonal";
      } else {
        const distance = matrix[i][j];
        cell.textContent = distance.toFixed(3);
        cell.style.background = mixHex(PLANE_BASE, PLANE_PURPLE, clampNum(distance, 0, 1));
        cell.style.color = distance > 0.55 ? "#0f1115" : "var(--text)";
        cell.title = left.model + ", " + right.model + " JSdist=" + distance.toFixed(3);
      }
      grid.append(cell);
    });
  });

  const scroll = planesEl("div", "planes-scroll");
  scroll.append(grid);
  const caption = planesEl("div", "planes-caption");
  caption.textContent = "JS distance (0\u20131), absolute fill";
  const frag = document.createDocumentFragment();
  frag.append(scroll, caption);
  return frag;
}

function buildRankPanel(models) {
  const series = [];
  let maxShare = 0;

  models.forEach((model, modelIndex) => {
    const entries = model.entries.slice();
    entries.sort((a, b) => {
      if (b[1] !== a[1]) return b[1] - a[1];
      return compareLabelsNumeric(a[0], b[0]);
    });
    if (!entries.length) return;

    const points = entries.slice(0, 20).map((entry, index) => {
      const share = shareOf(entry[1], model);
      if (share > maxShare) maxShare = share;
      return { rank: index + 1, share: share, label: entry[0] };
    });
    series.push({ model: model.model, points: points, colorIndex: modelIndex });
  });

  if (!series.length) return planesNote("no model distributions");

  const W = 760;
  const H = 200;
  const padL = 48;
  const padR = 12;
  const padT = 12;
  const padB = 28;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const top = maxShare > 0 ? maxShare * 1.12 : 1;
  const x = (rank) => padL + ((rank - 1) / 19) * innerW;
  const y = (share) => padT + (1 - clampNum(share, 0, top) / top) * innerH;

  const svg = svgNode("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "rank curve" });
  svg.classList.add("planes-svg");

  for (const t of [0, 0.5, 1]) {
    const gy = y(top * t);
    svg.append(svgNode("line", {
      x1: padL, y1: gy, x2: W - padR, y2: gy,
      stroke: "#262b36", "stroke-width": 1,
    }));
  }
  svg.append(svgText(padL - 6, padT + 4, formatSharePct(top), "end"));
  svg.append(svgText(padL - 6, padT + innerH, "0%", "end"));
  svg.append(svgNode("line", {
    x1: padL, y1: padT + innerH, x2: W - padR, y2: padT + innerH,
    stroke: "#262b36", "stroke-width": 1,
  }));

  for (const rank of [1, 5, 10, 15, 20]) {
    const gx = x(rank);
    svg.append(svgNode("line", {
      x1: gx, y1: padT, x2: gx, y2: padT + innerH,
      stroke: "#262b36", "stroke-width": 1, "stroke-opacity": 0.5,
    }));
    svg.append(svgText(gx, padT + innerH + 12, String(rank), "middle"));
  }

  for (const line of series) {
    const points = line.points.map((point) => x(point.rank) + "," + y(point.share)).join(" ");
    if (line.points.length > 1) {
      svg.append(svgNode("polyline", {
        points: points, fill: "none", stroke: paletteColor(line.colorIndex),
        "stroke-width": 1.6, "stroke-linejoin": "round", "stroke-linecap": "round",
      }));
    }
    for (const point of line.points) {
      const dot = svgNode("circle", {
        cx: x(point.rank), cy: y(point.share), r: 2.1, fill: paletteColor(line.colorIndex),
      });
      const title = svgNode("title");
      title.textContent = line.model + " rank " + point.rank + " \u00b7 " +
        point.label + " " + (point.share * 100).toFixed(1) + "%";
      dot.append(title);
      svg.append(dot);
    }
  }

  const caption = planesEl("div", "planes-caption");
  caption.textContent = "share by rank (top 20) \u00b7 colors as precision axis";
  const frag = document.createDocumentFragment();
  frag.append(svg, caption, buildPlaneLegend(models));
  return frag;
}

function renderPanelSafe(el, label, build) {
  try {
    el.replaceChildren(build());
  } catch (err) {
    el.replaceChildren(planesNote(label + " unavailable"));
  }
}

function renderPlanes(data, byValue) {
  const models = planeModels(data);
  if (planesExpEl) planesExpEl.textContent = selectedExperiment ? "experiment " + selectedExperiment : "";
  if (planesSourceEl) planesSourceEl.textContent = byValue ? "by value" : "labels";

  renderPanelSafe(planesPrecisionEl, "precision axis", () => buildPrecisionPanel(models));
  renderPanelSafe(planesHeatmapEl, "heatmap", () => buildHeatmapPanel(data, models, byValue));
  renderPanelSafe(planesPairwiseEl, "pairwise", () => buildPairwisePanel(models));
  renderPanelSafe(planesRankEl, "rank curve", () => buildRankPanel(models));
}

function setPlanesState(text) {
  for (const el of [planesPrecisionEl, planesHeatmapEl, planesPairwiseEl, planesRankEl]) {
    el.replaceChildren(planesNote(text));
  }
}

function renderPlanesLoading() {
  setPlanesState("loading...");
}

function renderPlanesUnavailable() {
  setPlanesState("analysis unavailable");
  if (planesExpEl) planesExpEl.textContent = "";
  if (planesSourceEl) planesSourceEl.textContent = "";
}

function renderDistributionLoading() {
  distRowsEl.replaceChildren();
  distNEl.textContent = "n = \u2014";
  distNoteEl.textContent = "loading...";
  distNoteEl.hidden = false;
  if (distExpEl) distExpEl.textContent = selectedExperiment || "";
}

// ---------- analysis: chips, cache, fetch ----------
function experimentsFromPayload(data) {
  const names = new Set();
  const runs = Array.isArray(data && data.runs) ? data.runs : [];
  for (const run of runs) {
    if (!run || run.experiment == null) continue;
    const name = String(run.experiment);
    if (name !== "") names.add(name);
  }
  return Array.from(names).sort(compareStrings);
}

function looksByValue(data) {
  if (!data || typeof data !== "object") return false;
  const buckets = [];
  if (data.overall && typeof data.overall === "object") buckets.push(data.overall);
  if (Array.isArray(data.by_model)) {
    for (const bucket of data.by_model) if (bucket && typeof bucket === "object") buckets.push(bucket);
  }
  if (Array.isArray(data.runs)) {
    for (const bucket of data.runs) if (bucket && typeof bucket === "object") buckets.push(bucket);
  }
  let hasSkipped = false;
  let hasNonEmpty = false;
  for (const bucket of buckets) {
    if (Object.prototype.hasOwnProperty.call(bucket, "skipped")) hasSkipped = true;
    const dist = bucket && bucket.dist;
    if (dist && typeof dist === "object" && Object.keys(dist).length > 0) hasNonEmpty = true;
  }
  return hasSkipped && hasNonEmpty;
}

function renderExperimentChips() {
  const signature = experimentNames.join("\u0000") + "|" + (selectedExperiment || "");
  if (signature === chipsSignature) return;
  chipsSignature = signature;

  const frag = document.createDocumentFragment();
  for (const name of experimentNames) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn planes-chip" + (name === selectedExperiment ? " active" : "");
    button.textContent = name;
    button.title = name;
    button.setAttribute("aria-pressed", name === selectedExperiment ? "true" : "false");
    button.addEventListener("click", () => selectExperiment(name));
    frag.append(button);
  }
  experimentChipsEl.replaceChildren(frag);
}

function analysisCacheKey(name, cache, chosen) {
  const regRaw = cache && cache.regRaw ? cache.regRaw : "";
  const valRaw = cache && cache.valRaw ? cache.valRaw : "";
  const byFlag = cache && cache.val && looksByValue(cache.val) ? "1" : "0";
  const fallback = chosen === analysisUnscoped ? "u" + analysisUnscopedRaw : "";
  return name + "|" + regRaw + "|" + valRaw + "|" + byFlag + "|" + fallback;
}

function paintAnalysisFromCache(name) {
  const cache = analysisCache.get(name);
  const regular = (cache && cache.reg) || analysisUnscoped;
  if (!regular) {
    lastAnalysisKey = "";
    renderDistributionLoading();
    renderPlanesLoading();
    return;
  }
  const byValueData = cache && cache.val && looksByValue(cache.val) ? cache.val : null;
  lastAnalysisKey = analysisCacheKey(name, cache, regular);
  renderDistribution(regular);
  renderPlanes(byValueData || regular, Boolean(byValueData));
}

function selectExperiment(name) {
  if (name === selectedExperiment) return;
  selectedExperiment = name;
  renderExperimentChips();
  paintAnalysisFromCache(name);
  refreshAnalysis();
}

async function fetchAnalysisJson(query) {
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  try {
    const url = API + "/analysis" + (query ? "?" + query : "");
    const res = await fetch(url, { signal: ctrl.signal, cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("unexpected response");
    return data;
  } finally {
    clearTimeout(timeout);
  }
}

async function refreshAnalysis() {
  if (analysisBusy) {
    analysisQueued = true;
    return;
  }
  analysisBusy = true;

  try {
    const now = Date.now();
    if (!analysisUnscoped || now - lastDiscoveryAt >= DISCOVERY_MS) {
      try {
        const data = await fetchAnalysisJson("");
        analysisUnscoped = data;
        analysisUnscopedRaw = JSON.stringify(data);
        lastDiscoveryAt = now;
      } catch (err) {
        // keep the last known experiment list; retry on a later poll
      }
    }

    const names = analysisUnscoped ? experimentsFromPayload(analysisUnscoped) : [];
    if (names.length) {
      experimentNames = names;
      if (!selectedExperiment || names.indexOf(selectedExperiment) === -1) {
        selectedExperiment = names[0];
      }
    }
    renderExperimentChips();

    if (!selectedExperiment) {
      if (!analysisUnscoped) {
        distNEl.textContent = "n = 0";
        distNoteEl.textContent = "analysis unavailable";
        distNoteEl.hidden = false;
        if (distExpEl) distExpEl.textContent = "";
        renderPlanesUnavailable();
      } else {
        const key = analysisCacheKey("", null, analysisUnscoped);
        if (key !== lastAnalysisKey) {
          lastAnalysisKey = key;
          renderDistribution(analysisUnscoped);
          renderPlanes(analysisUnscoped, false);
        }
      }
      return;
    }

    const name = selectedExperiment;
    let cache = analysisCache.get(name);
    if (!cache) {
      cache = { reg: null, regRaw: "", val: null, valRaw: "" };
      analysisCache.set(name, cache);
    }

    const encoded = encodeURIComponent(name);
    const results = await Promise.allSettled([
      fetchAnalysisJson("experiment=" + encoded),
      fetchAnalysisJson("experiment=" + encoded + "&by=value"),
    ]);
    if (selectedExperiment !== name) return;

    if (results[0].status === "fulfilled") {
      cache.reg = results[0].value;
      cache.regRaw = JSON.stringify(cache.reg);
    }
    if (results[1].status === "fulfilled") {
      cache.val = results[1].value;
      cache.valRaw = JSON.stringify(cache.val);
    }

    const chosen = cache.reg || analysisUnscoped;
    if (!chosen) {
      distNEl.textContent = "n = 0";
      distNoteEl.textContent = "analysis unavailable";
      distNoteEl.hidden = false;
      if (distExpEl) distExpEl.textContent = name;
      renderPlanesUnavailable();
      return;
    }

    const byValueData = cache.val && looksByValue(cache.val) ? cache.val : null;
    const key = analysisCacheKey(name, cache, chosen);
    if (key !== lastAnalysisKey) {
      lastAnalysisKey = key;
      renderDistribution(chosen);
      renderPlanes(byValueData || chosen, Boolean(byValueData));
    }
  } catch (err) {
    // additive panel: a missing /analysis must never disturb the runs view
    if (!analysisUnscoped) {
      distNEl.textContent = "n = 0";
      distNoteEl.textContent = "analysis unavailable";
      distNoteEl.hidden = false;
      renderPlanesUnavailable();
    }
  } finally {
    analysisBusy = false;
    if (analysisQueued) {
      analysisQueued = false;
      refreshAnalysis();
    }
  }
}

// ---------- polling ----------
async function refresh() {
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(API + "/progress", { signal: ctrl.signal, cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const runs = await res.json();
    if (!Array.isArray(runs)) throw new Error("unexpected response");

    const payload = JSON.stringify(runs);
    if (payload !== lastPayload) {
      lastPayload = payload;
      render(runs);
    }

    document.body.classList.remove("offline");
    if (!paused) setConn("ok", "live");
    updatedEl.textContent = "updated " + new Date().toLocaleTimeString();

    // optional, guarded: /analysis never affects the runs outcome above
    await refreshAnalysis();
  } catch (err) {
    // a wrong guess about where the API lives is not a broken connection
    if (apiIndex + 1 < candidates.length) {
      apiIndex++;
      API = candidates[apiIndex];
      $("api-host").textContent = apiLabel();
      if (!paused) setConn("pause", "looking for api");
      return;
    }
    document.body.classList.add("offline");
    if (paused) return;
    setConn("err", err && err.name === "AbortError" ? "timeout" : "disconnected");
    if (rows.size === 0) setEmpty("cannot reach " + apiLabel() + " \u2014 is the backend running?");
  } finally {
    clearTimeout(timeout);
  }
}

// lease countdown ticks even when the payload is unchanged
function tickLeases() {
  const now = nowSec();
  for (const entry of rows.values()) {
    if (!entry.els.lease.classList.contains("live")) continue;
    const left = Math.max(0, Number(entry.els.lease.dataset.until || 0) - now);
    entry.els.lease.textContent = left + "s";
  }
}

async function loop() {
  if (!paused && !document.hidden) await refresh();
  timer = setTimeout(loop, paused ? 1000 : POLL_MS);
}

pauseBtn.addEventListener("click", () => setPaused(!paused));

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !paused) refresh();
});

// keep the countdown honest between polls
setInterval(() => {
  if (!paused) tickLeases();
}, 250);

renderPlanesLoading();
loop();
