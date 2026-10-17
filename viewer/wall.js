'use strict';
// GridTwin inspection wall: every site camera as a station tile, driven by the agents' get_camera_view calls
// in runs/<id>/events.jsonl (contract C2). Like the evidence view it never reads truth.jsonl.
// A station only turns green or red on a verifier verdict or a page; a photo on its own is "seen, no check".

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const short = (v, n = 140) => {
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return s && s.length > n ? s.slice(0, n - 1) + '…' : s ?? '';
};
// The planner's dod groups checks by outcome ({DONE: [...], ...}); older scripted logs carry one flat list.
const dodChecks = (dod) => {
  const c = dod?.data?.checks;
  return !c ? [] : Array.isArray(c) ? c : Object.values(c).flat();
};

const params = new URLSearchParams(location.search);
const replay = params.get('replay') === '1';
let runId = params.get('run');
let stations = [];  // from /api/viewpoints, ordered and numbered S01…
let events = [];
let cursor = 0;
let playing = true, speed = 1, timer = null;

// ---------- where each camera sits on the top-down map (viewBox 320×250, heading 0° = north/up) ----------
// viewpoints.yaml can override with map_xy: [x, y] and heading_deg; poses are splat coordinates, so they aren't used here.
const ROW_Y = [30, 80, 130, 180];
const BLOCK_X = [16, 126], INV_X = 150, BRK_X = 186, BUS_X = 206;
const XFMR = [250, 105], POI = [296, 105], BESS_Y = 222;
function placement(asset) {
  let m;
  if ((m = asset.match(/^B(\d)$/))) return { xy: [(BLOCK_X[0] + BLOCK_X[1]) / 2, ROW_Y[m[1] - 1] + 20], heading: 0 };
  if ((m = asset.match(/^CB-(\d)\.(\d+)$/))) return { xy: [BLOCK_X[0] + (m[2] - 0.5) * (BLOCK_X[1] - BLOCK_X[0]) / 6, ROW_Y[m[1] - 1] - 20], heading: 180 };
  if ((m = asset.match(/^INV-(\d)$/))) return { xy: [INV_X, ROW_Y[m[1] - 1] + 20], heading: 0 };
  if ((m = asset.match(/^52-I(\d)$/))) return { xy: [BRK_X, ROW_Y[m[1] - 1] - 18], heading: 180 };
  if (asset === 'XFMR-1' || asset === '52-M') return { xy: [XFMR[0], XFMR[1] + 30], heading: 0 };
  if (asset === 'BESS-1' || asset === '52-B1') return { xy: [INV_X + 20, BESS_Y], heading: 270 };
  if (asset === 'POI-1') return { xy: [POI[0], POI[1] + 30], heading: 0 };
  return { xy: [300, 230], heading: 315 };
}
const KIND = [[/^INV-/, 'inverter cabinet'], [/^CB-/, 'combiner box'], [/^B\d$/, 'PV array'], [/^52-/, 'breaker'],
  [/^XFMR-/, 'transformer yard'], [/^BESS-/, 'battery enclosure'], [/^POI-/, 'revenue meter']];
const kindOf = (asset) => (KIND.find(([re]) => re.test(asset)) || [null, 'camera'])[1];

function setStations(vps) {
  stations = vps.map((v) => {
    const p = placement(v.asset);
    return { ...v, xy: v.map_xy || p.xy, heading: v.heading_deg ?? p.heading, label: v.label || `${v.asset} ${kindOf(v.asset)}` };
  });
  stations.sort((a, b) => a.xy[1] - b.xy[1] || a.xy[0] - b.xy[0]);
  stations.forEach((s, i) => { s.code = `S${String(i + 1).padStart(2, '0')}`; });
}

// ---------- fold the log into per-station state ----------
const ASSET_RE = /\b(INV-\d|XFMR-1|BESS-1|POI-1|52-(?:I\d|M|B1)|CB-\d\.\d+|B\d)\b/g;
function mentions(obj) {
  const s = typeof obj === 'string' ? obj : JSON.stringify(obj ?? '');
  const out = new Set([...s.matchAll(ASSET_RE)].map((m) => m[1]));
  for (const st of stations) if (s.includes(st.id)) out.add(st.id);
  return out;
}

function derive(evs) {
  const S = { st: {}, final: null, goal: null, captured: new Set(), dod: null };
  for (const s of stations) S.st[s.id] = { shots: [], inflight: false, error: null, verdict: null, page: null };
  const hit = (set) => stations.filter((s) => set.has(s.asset) || set.has(s.id)).map((s) => S.st[s.id]);
  let lastCall = null;
  for (const e of evs) {
    const d = e.data || {};
    switch (e.type) {
      case 'goal': S.goal = e; break;
      case 'dod': S.dod = e; break;
      case 'tool_call':
        if (d.name === 'get_camera_view') {
          lastCall = d.args?.viewpoint;
          if (S.st[lastCall]) { S.st[lastCall].inflight = true; S.st[lastCall].error = null; }
        }
        break;
      case 'tool_result': {
        if (d.name !== 'get_camera_view') break;
        const r = d.result || {};
        const st = S.st[r.viewpoint ?? lastCall];
        if (!st) break;
        st.inflight = false;
        if (r.error) { st.error = { seq: e.seq, text: r.error }; break; }
        st.shots.push({ seq: e.seq, ...r });
        S.captured.add(`${r.viewpoint}|${r.mode}`);
        break;
      }
      case 'verdict': {
        const check = dodChecks(S.dod).find((c) => (c.id ?? c.check_id) === d.check_id);
        hit(mentions([check, d.evidence, d.requirement, d.requirements])).forEach((st) => { st.verdict = e; });
        break;
      }
      case 'page': hit(mentions(d.brief)).forEach((st) => { st.page = e; }); break;
      case 'final': S.final = e; break;
    }
  }
  return S;
}

function statusOf(st, S) {
  if (st.error) return ['failed', 'Failed', `camera error: ${st.error.text}`];
  if (st.inflight) return ['analyzing', 'Capturing', 'photo requested'];
  if (st.page) return ['failed', 'Paged', `human paged (${st.page.data.severity})`];
  if (st.verdict) {
    const v = st.verdict.data;
    return v.pass ? ['verified', 'Verified', `${v.check_id} passed`] : ['failed', 'Failed', `${v.check_id} failed`];
  }
  if (st.shots.length) return S.final ? ['seen', 'Seen', 'photo taken, no verifier check'] : ['analyzing', 'Analyzing', 'waiting on a check'];
  return ['idle', 'Not visited', ''];
}

// ---------- render ----------
const totalViews = () => stations.reduce((n, s) => n + (s.modes?.length || 1), 0);

function renderTiles(S) {
  $('#tiles').innerHTML = stations.map((s) => {
    const st = S.st[s.id];
    const [cls, badge, why] = statusOf(st, S);
    const last = st.shots[st.shots.length - 1];
    // With a splat render, the tile shows the twin's view of the station and the camera photo as an inset.
    const img = !last ? '<div class="noimg">no photo yet</div>' : last.render_path
      ? `<a href="/${esc(last.render_path)}" target="_blank"><img src="/${esc(last.render_path)}" alt="${esc(s.id)} splat view"></a>
         <a href="/${esc(last.image_path)}" target="_blank"><img class="inset" src="/${esc(last.image_path)}" alt="${esc(s.id)} ${esc(last.mode)} photo"></a>`
      : `<a href="/${esc(last.image_path)}" target="_blank"><img src="/${esc(last.image_path)}" alt="${esc(s.id)} ${esc(last.mode)}"></a>`;
    const dots = (s.modes || ['rgb']).map((m) => {
      const n = st.shots.filter((x) => x.mode === m).length;
      return `<i class="dot ${n ? cls : 'empty'}" title="${esc(m)}: ${n} photo${n === 1 ? '' : 's'}"></i>`;
    }).join('');
    const ev = st.verdict ? `<div class="why">${esc(short(st.verdict.data.evidence, 110))}</div>` : why ? `<div class="why">${esc(why)}</div>` : '';
    return `<article class="tile ${cls}" data-vp="${esc(s.id)}">
      <div class="shot">${img}<span class="badge ${cls}">${['analyzing'].includes(cls) ? '<i class="spin"></i>' : ''}${esc(badge)}</span>
        ${last ? `<span class="mode">${esc(last.mode)} · ${esc((last.captured_at || '').slice(11))}</span>` : ''}</div>
      <div class="cap"><div><b>${esc(s.code)}</b> · ${esc(s.label)}<div class="sub mono">${esc(s.id)} · ${esc(s.heading)}°${st.shots.length ? ` · ${st.shots.length} shot${st.shots.length === 1 ? '' : 's'}` : ''}</div></div>
        <div class="dots">${dots}</div></div>${ev}</article>`;
  }).join('');
}

function cone(x, y, deg, len = 20, half = 26) {
  const r = (a) => ((a - 90) * Math.PI) / 180;  // 0° = up
  const p = (a) => `${(x + len * Math.cos(r(a))).toFixed(1)},${(y + len * Math.sin(r(a))).toFixed(1)}`;
  return `M${x},${y}L${p(deg - half)}L${p(deg + half)}Z`;
}

function renderMap(S) {
  const g = [];
  ROW_Y.forEach((y, i) => {
    g.push(`<rect class="blk" x="${BLOCK_X[0]}" y="${y - 12}" width="${BLOCK_X[1] - BLOCK_X[0]}" height="24" rx="2"/><text x="${BLOCK_X[0] + 4}" y="${y + 4}">B${i + 1}</text>`);
    g.push(`<path class="wire" d="M${BLOCK_X[1]} ${y}H${INV_X - 10}M${INV_X + 10} ${y}H${BUS_X}"/><rect class="eq" x="${INV_X - 10}" y="${y - 8}" width="20" height="16"/>`);
    g.push(`<text class="lbl" x="${INV_X - 10}" y="${y - 11}">INV-${i + 1}</text><rect class="eq" x="${BRK_X - 4}" y="${y - 4}" width="8" height="8"/>`);
  });
  g.push(`<path class="wire" d="M${BUS_X} ${ROW_Y[0]}V${BESS_Y}M${INV_X + 10} ${BESS_Y}H${BUS_X}M${BUS_X} ${XFMR[1]}H${POI[0] - 12}"/>`);
  g.push(`<rect class="eq" x="${INV_X - 10}" y="${BESS_Y - 8}" width="20" height="16"/><text class="lbl" x="${INV_X - 44}" y="${BESS_Y + 4}">BESS-1</text>`);
  g.push(`<circle class="eq" cx="${XFMR[0] - 6}" cy="${XFMR[1]}" r="9"/><circle class="eq" cx="${XFMR[0] + 6}" cy="${XFMR[1]}" r="9"/><text class="lbl" x="${XFMR[0] - 18}" y="${XFMR[1] - 14}">XFMR-1</text>`);
  g.push(`<rect class="eq" x="${POI[0] - 12}" y="${XFMR[1] - 8}" width="20" height="16"/><text class="lbl" x="${POI[0] - 14}" y="${XFMR[1] - 12}">POI-1</text>`);
  for (const s of stations) {
    const [cls] = statusOf(S.st[s.id], S);
    const [x, y] = s.xy;
    g.push(`<g class="stn ${cls}" data-vp="${esc(s.id)}"><title>${esc(s.code)} ${esc(s.label)}</title><path class="cone" d="${cone(x, y, s.heading)}"/>
      <circle cx="${x}" cy="${y}" r="3.5"/><text class="code" x="${x + 5}" y="${y + 9}">${esc(s.code)}</text></g>`);
  }
  $('#map').innerHTML = `<svg viewBox="0 0 320 250" role="img" aria-label="Top-down map of camera stations">${g.join('')}</svg>`;
}

function render() {
  const S = derive(events.slice(0, cursor));
  renderTiles(S); renderMap(S);
  window.wallTwin = { stations, S, status: (s) => statusOf(S.st[s.id], S)[0], flash: (vp) => window.wallFlash?.(vp) };
  window.dispatchEvent(new Event('wall:render'));
  const n = S.captured.size, total = totalViews();
  $('#views').textContent = `Run ${runId || '—'} · ${n} of ${total} views`;
  $('#progressBar').style.width = `${total ? (100 * n) / total : 0}%`;
  const count = {};
  stations.forEach((s) => { const c = statusOf(S.st[s.id], S)[0]; count[c] = (count[c] || 0) + 1; });
  const goal = S.goal ? `<q>${esc(S.goal.data.text)}</q>` : '<span class="muted">Waiting for the operator goal…</span>';
  $('#summary').innerHTML = `${goal}<span class="counts">${[['verified', 'verified'], ['failed', 'failed'], ['analyzing', 'analyzing'], ['seen', 'seen, no check'], ['idle', 'not visited']]
    .filter(([k]) => count[k]).map(([k, l]) => `<span><i class="dot ${k}"></i>${count[k]} ${l}</span>`).join('')}</span>`;
  $('#final').innerHTML = S.final ? `<b>${esc(S.final.data.status)}</b> ${esc(S.final.data.summary)}` : '';
}

// ---------- data + modes (same conventions as the evidence view) ----------
const runPath = (id) => id.split('/').map(encodeURIComponent).join('/');
const runUrl = (id, rep) => `?run=${encodeURIComponent(id).replace(/%2F/g, '/')}${rep ? '&replay=1' : ''}`;

async function fetchEvents() {
  const res = await fetch(`/runs/${runPath(runId)}/events.jsonl`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${runId}`);
  const out = [];
  for (const line of (await res.text()).split('\n')) {
    if (!line.trim()) continue;
    try { out.push(JSON.parse(line)); } catch { break; }
  }
  return out;
}

async function poll() {
  try {
    const evs = await fetchEvents();
    if (evs.length !== events.length) { events = evs; cursor = events.length; render(); }
  } catch (err) { $('#views').textContent = `can't read run: ${err.message}`; }
  setTimeout(poll, 1000);
}

const CAMERA = (e) => e?.data?.name === 'get_camera_view' || ['verdict', 'page', 'final', 'goal'].includes(e?.type);
function tick() {
  clearTimeout(timer);
  if (playing && cursor < events.length) { cursor++; render(); }
  if (cursor >= events.length) { playing = false; $('#btnPlay').textContent = '▶'; return; }
  if (playing) timer = setTimeout(tick, (CAMERA(events[cursor - 1]) ? 1200 : 120) / speed);
}

async function loadRuns() {
  let runs = [];
  try { runs = await (await fetch('/api/runs')).json(); } catch { /* served without /api: keep ?run= */ }
  if (!runId && runs.length) { runId = runs[0].id; history.replaceState(null, '', runUrl(runId, replay)); }
  const sel = $('#runPick');
  if (runId && !runs.some((r) => r.id === runId)) runs.unshift({ id: runId, status: null });
  sel.innerHTML = runs.map((r) => `<option value="${esc(r.id)}"${r.id === runId ? ' selected' : ''}>${esc(r.id)} · ${esc(r.status || 'running')}</option>`).join('');
  sel.onchange = () => { location.search = runUrl(sel.value, replay); };
}

async function main() {
  try { setStations(await (await fetch('/api/viewpoints')).json()); } catch { $('#views').textContent = "can't load camera stations"; }
  await loadRuns();
  if (runId) $('#backLink').href = `./${runUrl(runId, replay)}`;
  const tag = $('#modeTag');
  tag.textContent = replay ? 'replay · switch to live' : '● live · switch to replay';
  tag.className = `tag ${replay ? '' : 'live'}`;
  tag.onclick = () => { if (runId) location.search = runUrl(runId, !replay); };
  const flash = (vp) => {
    const el = document.querySelector(`.tile[data-vp="${CSS.escape(vp)}"]`);
    if (!el) return;
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    el.classList.add('flash');
    setTimeout(() => el.classList.remove('flash'), 1200);
  };
  window.wallFlash = flash;
  $('#map').onclick = (e) => { const g = e.target.closest('.stn'); if (g) { flash(g.dataset.vp); window.twinFocus?.(g.dataset.vp); } };
  render();
  if (!runId) {
    $('#summary').innerHTML = '<span class="muted">No runs yet. Start one, for example <code>python -m gridtwin.bench --agent scripted_correct --scenario s03_fan_relapse</code>, then reload.</span>';
    return;
  }
  if (!replay) { poll(); return; }
  $('#replayCtl').hidden = false;
  try { events = await fetchEvents(); } catch (err) { $('#views').textContent = `can't read run: ${err.message}`; return; }
  cursor = 0;
  render();
  $('#btnPlay').onclick = () => { playing = !playing; $('#btnPlay').textContent = playing ? '⏸' : '▶'; if (playing && cursor >= events.length) cursor = 0; tick(); };
  $('#btnStep').onclick = () => { playing = false; $('#btnPlay').textContent = '▶'; if (cursor < events.length) { cursor++; render(); } };
  $('#btnRestart').onclick = () => { cursor = 0; render(); playing = true; $('#btnPlay').textContent = '⏸'; tick(); };
  $('#speed').oninput = (e) => { speed = +e.target.value; $('#speedVal').textContent = `${speed}×`; if (playing) tick(); };
  tick();
}

main();
