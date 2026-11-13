'use strict';

const POLL = 2000;
const TIMEOUT = 5000;
const candidates = location.protocol.startsWith('http') ? ['', 'http://localhost:8000'] : ['http://localhost:8000'];
let api = candidates[0];
let apiIndex = 0;
let paused = false;
const leaseCells = new Map();


const $ = (id) => document.getElementById(id);
const body = $('body');
const dot = $('dot');
const statusEl = $('status');
const updatedEl = $('updated');
const pauseBtn = $('pause');
const emptyEl = $('empty');

function nowSec() { return Math.floor(Date.now() / 1000); }

function statusOf(run) {
  if (run.status === 'failed') return 'failed';
  if (run.status === 'done') return 'done';
  if (run.lease_until == null) return 'pending';
  return run.lease_until > nowSec() ? 'running' : 'stalled';
}

function setConn(kind, text) {
  dot.className = 'dot' + (kind === 'ok' ? ' ok' : kind === 'err' ? ' err' : '');
  statusEl.textContent = text;
}

async function fetchJson(url) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const res = await fetch(url, { signal: ctrl.signal, cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  } finally {
    clearTimeout(t);
  }
}

function render(runs) {
  body.replaceChildren();
  leaseCells.clear();
  if (!runs.length) {
    emptyEl.style.display = 'block';
    return;
  }
  emptyEl.style.display = 'none';

  const groups = new Map();
  for (const run of runs) {
    const key = run.experiment || '(none)';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(run);
  }

  for (const [experiment, groupRuns] of groups) {
    const header = document.createElement('tr');
    const hd = document.createElement('td');
    hd.colSpan = 8;
    hd.style.padding = '10px 8px 4px';
    hd.style.fontWeight = '600';
    hd.style.color = '#8b93a7';
    hd.style.borderBottom = '1px solid #222';
    hd.textContent = experiment;
    header.append(hd);
    body.append(header);

    for (const run of groupRuns) {
      const tr = document.createElement('tr');
      const status = statusOf(run);
      const total = run.n_prompts * run.samples_per_prompt;
      const done = run.n_samples;
      const ratio = total > 0 ? Math.min(1, done / total) : 0;
      tr.innerHTML =
        '<td>' + run.id + '</td>' +
        '<td>' + (run.experiment || '') + '</td>' +
        '<td>' + (run.model || '') + '</td>' +
        '<td><span class="chip">' + status + '</span></td>' +
        '<td><span class="bar"><span class="fill" style="width:' + Math.round(ratio * 100) + 'px;display:block"></span></span></td>' +
        '<td>' + (total > 0 ? done + '/' + total : done + '/—') + '</td>' +
        '<td class="lease">—</td>' +
        '<td class="vals" style="white-space:nowrap"></td>';
      body.append(tr);
      const valsTd = tr.querySelector('td.vals');
      const vals = (run.all_values || '').split(', ').filter(Boolean);
      for (const v of vals) {
        const code = document.createElement('code');
        code.textContent = v;
        valsTd.append(code);
      }
      const leaseTd = tr.querySelector('td.lease');
      if (status === 'running' && run.lease_until != null) {
        leaseTd.textContent = Math.max(0, run.lease_until - nowSec()) + 's';
        leaseTd.style.color = '#3fb950';
        leaseCells.set(run.id, { el: leaseTd, until: run.lease_until });
      } else if (status === 'stalled') {
        leaseTd.textContent = 'stalled';
        leaseTd.style.color = '#d29922';
      } else {
        leaseTd.textContent = '—';
      }
    }
  }
}

async function refresh() {
  try {
    const runs = await fetchJson(api + '/progress');
    if (!Array.isArray(runs)) throw new Error('bad response');
    render(runs);
    setConn('ok', 'live');
    updatedEl.textContent = new Date().toLocaleTimeString();
  } catch (err) {
    if (apiIndex + 1 < candidates.length) {
      apiIndex++;
      api = candidates[apiIndex];
      setConn('pause', 'looking for api');
      return;
    }
    setConn('err', err && err.name === 'AbortError' ? 'timeout' : 'disconnected');
    if (!body.children.length) emptyEl.textContent = 'cannot reach ' + (api || location.origin);
  }
}

function tickLeases() {
  const now = nowSec();
  for (const { el, until } of leaseCells.values()) {
    const left = Math.max(0, until - now);
    el.textContent = left + 's';
  }
}

async function loop() {
  if (!paused && !document.hidden) await refresh();
  setTimeout(loop, paused ? 1000 : POLL);
}

pauseBtn.addEventListener('click', () => {
  paused = !paused;
  pauseBtn.textContent = paused ? 'resume' : 'pause';
  if (!paused) refresh();
});

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && !paused) refresh();
});

setInterval(() => { if (!paused) tickLeases(); }, 250);

setConn('pause', 'connecting');
loop();