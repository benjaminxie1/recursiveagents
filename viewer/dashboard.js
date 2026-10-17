// GridTwin operations dashboard: the plant twin on the left (drone passes, then the unified splat), asset health,
// the current action's pipeline and the agent team on the right. Reads runs/<id>/events.jsonl (contract C2) only.
// ?run=<id> picks a run (default: newest), &replay=1 plays it back, &speed=2 speeds playback up.
import { PlantView, loadScene } from './splat.js';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const clip = (s, n = 90) => { s = typeof s === 'string' ? s : JSON.stringify(s ?? ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
const params = new URLSearchParams(location.search);
const replay = params.get('replay') === '1';
const speed = +(params.get('speed') || 1);
let runId = params.get('run');
let events = [], cursor = 0;

const ASSET_RE = /\b(INV-\d|XFMR-1|BESS-1|POI-1|52-(?:I\d|M|B1)|CB-\d\.\d+|B\d)\b/g;
const mentioned = (o) => [...new Set([...(typeof o === 'string' ? o : JSON.stringify(o ?? '')).matchAll(ASSET_RE)].map((m) => m[1]))];
const TILES = ['INV-1', 'INV-2', 'INV-3', 'INV-4', 'B1', 'B2', 'B3', 'B4', '52-I1', '52-I2', '52-I3', '52-I4', 'XFMR-1', '52-M', 'BESS-1', 'POI-1'];
const tileOf = (a) => (TILES.includes(a) ? a : (a.match(/^CB-(\d)/) ? `B${a[3]}` : null));
const LANES = ['planner', 'inspector', 'diagnoser', 'researcher', 'operator', 'gate', 'verifier', 'supervisor'];
const laneOf = (actor) => (actor.startsWith('worker:') ? actor.slice(7) : actor);
const STEPS = ['Detect', 'Plan', 'Act', 'Verify', 'Close'];
const dodChecks = (d) => { const c = d?.data?.checks; return !c ? [] : Array.isArray(c) ? c : Object.values(c).flat(); };

let REQS = {}, STDS = {};

// ---------- fold the log ----------
function derive(evs) {
  const S = { goal: null, dod: null, final: null, assets: {}, lanes: {}, verdicts: {}, findings: [], acts: [], usage: 0, last: null,
    shots: {}, lastActor: null };
  const set = (a, cls, st) => { const t = a && tileOf(a); if (t) S.assets[t] = { cls, st: a === t ? st : `${a} ${st}`, raw: a }; };
  let actor = 'planner';
  evs.forEach((e, i) => {
    const d = e.data || {};
    S.last = e;
    const who = e.actor === 'sim' ? (d.by || actor) : e.actor;
    if (!['sim', 'judge', 'operator'].includes(e.actor) || e.type === 'claim') actor = e.actor === 'operator' ? actor : e.actor;
    if (who && !['judge', 'operator'].includes(who)) {
      const L = (S.lanes[laneOf(who)] ??= { ticks: [], what: '' });
      L.ticks.push({ i, type: e.type, pass: d.pass });
      L.what = describe(e) || L.what;
      L.at = i;
    }
    switch (e.type) {
      case 'goal': S.goal = d.text; break;
      case 'dod': S.dod = e; break;
      case 'finding': S.findings.push(e); break;
      case 'tool_call':
        if (d.name === 'operate') { S.acts.push(e); set(d.args?.asset, 'work', d.args?.action === 'open' ? 'isolated' : d.args?.action); }
        if (d.name === 'dispatch_tech') { S.acts.push(e); set(d.args?.asset, 'work', d.args?.work_order?.replace(/_/g, ' ')); }
        break;
      case 'tool_result':
        if (d.name === 'get_camera_view' && d.result?.asset) (S.shots[d.result.asset] ??= []).push(d.result);
        break;
      case 'verdict': {
        S.verdicts[d.check_id] = d;
        const check = dodChecks(S.dod).find((c) => (c.id ?? c.check_id) === d.check_id);
        mentioned([check?.asset, check?.viewpoint, check?.claim, d.evidence]).forEach((a) => set(a, d.pass ? 'verified' : 'bad', d.pass ? 'verified' : 'check failed'));
        break;
      }
      case 'page': mentioned(d.brief).forEach((a) => set(a, 'bad', 'escalated')); break;
      case 'usage': S.usage += (d.prompt || 0) + (d.completion || 0); break;
      case 'final': S.final = d; break;
    }
  });
  S.focus = focusAsset(S);
  if (S.focus && tileOf(S.focus) && !S.assets[tileOf(S.focus)]) set(S.focus, S.final ? 'verified' : 'warn', 'investigating');
  return S;
}

function focusAsset(S) {
  const dispatch = S.acts.find((e) => e.data.name === 'dispatch_tech');
  if (dispatch) return dispatch.data.args.asset;
  const count = {};
  S.findings.forEach((f) => mentioned(f.data.text).forEach((a) => { count[a] = (count[a] || 0) + 1; }));
  return Object.entries(count).sort((a, b) => b[1] - a[1])[0]?.[0] || (S.goal && mentioned(S.goal)[0]) || null;
}

function describe(e) {
  const d = e.data || {};
  switch (e.type) {
    case 'dod': return `Definition of done · ${dodChecks(e).length} checks`;
    case 'finding': return d.text;
    case 'tool_call': return `${d.name.replace(/_/g, ' ')} ${d.args?.asset || d.args?.viewpoint || ''}`.trim();
    case 'gate': return `${d.route === 'approve' ? 'Approved' : 'Held'} ${d.proposed?.args?.asset || ''} ${String(d.proposed?.name || '').replace(/_/g, ' ')}`;
    case 'claim': return `Claims ${d.status}`;
    case 'verdict': return `${d.pass ? 'Pass' : 'Fail'} · ${d.check_id}`;
    case 'supervisor': return d.reason || d.decision;
    case 'page': return `Paged · ${d.brief}`;
    case 'final': return d.summary;
    case 'lesson': return `Lesson ${d.op}`;
    default: return null;
  }
}

// ---------- right column ----------
function renderAssets(S) {
  const bad = Object.values(S.assets).filter((a) => a.cls === 'bad').length;
  $('#health').textContent = `${TILES.length - bad}/${TILES.length}`;
  $('#assets').innerHTML = TILES.map((t) => {
    const a = S.assets[t];
    return `<div class="asset ${a?.cls || ''}" data-asset="${t}"><div class="id">${t}</div><div class="st">${esc(a?.st || 'nominal')}</div></div>`;
  }).join('');
}

function renderIncident(S) {
  const show = !!(S.focus || S.dod);
  $('#incident').hidden = !show;
  if (!show) return;
  const checks = dodChecks(S.dod);
  const vs = Object.values(S.verdicts);
  const passed = vs.filter((v) => v.pass).length;
  const work = S.acts.find((e) => e.data.name === 'dispatch_tech') || S.acts[S.acts.length - 1];
  const done = [S.findings.length > 0 || !!S.focus, !!S.dod, S.acts.length > 0, checks.length > 0 && passed === checks.length, !!S.final];
  const failed = [false, false, false, vs.some((v) => !v.pass) && !S.final, S.final && S.final.status !== 'DONE'];
  const now = S.final ? -1 : done.indexOf(false);
  const sub = [`${S.findings.length} finding${S.findings.length === 1 ? '' : 's'}`, checks.length ? `${checks.length} checks` : '',
    work ? (work.data.args.work_order || `${work.data.args.action} ${work.data.args.asset}`).replace(/_/g, ' ') : '',
    vs.length ? `${passed}/${checks.length || vs.length} passed` : '', S.final?.status || ''];
  $('#incAsset').textContent = S.focus || '—';
  $('#incText').textContent = clip(S.findings.filter((f) => !S.focus || f.data.text.includes(S.focus)).pop()?.data.text || S.goal || '', 70);
  const st = $('#incState');
  st.className = `pill ${S.final ? (S.final.status === 'DONE' ? 'ok' : 'warn') : 'work'}`;
  st.textContent = S.final ? S.final.status : STEPS[Math.max(0, now)];
  $('#steps').innerHTML = STEPS.map((s, i) => `<li class="${failed[i] ? 'fail' : i === now ? 'now' : done[i] ? 'done' : ''}">
    <span class="n">0${i + 1}</span>${s}<span class="sub">${esc(sub[i])}${i === now ? '<span class="dots"><i></i><i></i><i></i></span>' : ''}</span></li>`).join('');
  const reqs = [];
  checks.forEach((c) => [c.requirement, ...(c.requirements || [])].filter(Boolean).forEach((r) => {
    if (!reqs.some((x) => x.r === r)) reqs.push({ r, c });
  }));
  $('#reqs').innerHTML = reqs.map(({ r, c }) => {
    const v = S.verdicts[c.id ?? c.check_id];
    const cls = v ? (v.pass ? 'pass' : 'fail') : 'pending';
    const req = REQS[r];
    const stds = (req?.standards || []).join(' · ') || req?.title || '';
    return `<div class="req ${cls}" title="${esc(req?.title || '')}"><i class="m"></i><span class="k">${esc(r)}</span><span class="t">${esc(stds)}</span></div>`;
  }).join('');
}

function renderLanes(S) {
  const n = Math.max(1, cursor);
  const names = [...LANES, ...Object.keys(S.lanes).filter((l) => !LANES.includes(l) && l !== 'sim')];
  const lastLane = [...events.slice(0, cursor)].reverse().map((e) => ({ actor: e.actor === 'sim' ? e.data?.by || '' : e.actor })).find((e) => e.actor && !['sim', 'judge', 'operator'].includes(e.actor));
  $('#lanes').innerHTML = names.map((name) => {
    const L = S.lanes[name];
    const active = !S.final && L && (laneOf(lastLane?.actor || '') === name || L.at > cursor - 4);
    const ticks = (L?.ticks || []).map((t) => {
      const x = (100 * (t.i + 0.5)) / n;
      const col = t.type === 'verdict' ? (t.pass ? 'var(--ok)' : 'var(--bad)') : t.type === 'final' ? 'var(--ink)' : t.type === 'tool_call' || t.type === 'tool_result' ? 'var(--line2)' : 'var(--mid)';
      const h = t.type === 'tool_call' || t.type === 'tool_result' ? 6 : 12;
      return `<rect x="${x.toFixed(2)}%" y="${(14 - h) / 2}" width="2" height="${h}" fill="${col}"/>`;
    }).join('');
    const span = L ? `<line x1="${(100 * L.ticks[0].i) / n}%" x2="${(100 * (L.at + 1)) / n}%" y1="7" y2="7" stroke="var(--line)" stroke-width="1"/>` : '';
    const what = active ? `${esc(clip(L.what, 60))}<span class="dots"><i></i><i></i><i></i></span>` : esc(clip(L?.what || '', 64));
    return `<div class="lane ${active ? 'active' : ''}"><div class="who">${esc(name)}</div><svg preserveAspectRatio="none">${span}${ticks}</svg><div class="what">${what}</div></div>`;
  }).join('');
  $('#tokens').textContent = S.usage ? `${(S.usage / 1000).toFixed(1)}k tok` : `${cursor} events`;
}

// ---------- the twin ----------
let view, scene, S0 = null, thermal = false, flying = params.get('fly') !== '0', flyT0 = performance.now();
const DRONE_MS = 5200;
const PASSES = [30, 80, 130, 180].map((z, i) => ({ name: `Drone ${i + 1} · B${i + 1}`, from: [6, 20, z + 26], to: [136, 20, z + 26], look: z }));

function flyFrame(now) {
  if (!flying || !view) return false;
  const t = now - flyT0;
  const k = Math.floor(t / DRONE_MS);
  if (k < PASSES.length) {
    const p = PASSES[k], f = (t % DRONE_MS) / DRONE_MS, e = f * f * (3 - 2 * f);
    const x = p.from[0] + (p.to[0] - p.from[0]) * e;
    view.look([x, p.from[1], p.from[2]], [x + 18, 0, p.look], 55);
    $('#pass').textContent = p.name;
    $('#drones').innerHTML = PASSES.map((_, j) => `<i class="${j < k ? 'done' : ''}"><b style="width:${j === k ? 100 * f : j < k ? 100 : 0}%"></b></i>`).join('');
  } else {
    const a = (t - PASSES.length * DRONE_MS) / 26000 * Math.PI * 2;
    view.look([160 + 230 * Math.sin(a), 120, 125 + 230 * Math.cos(a)], [160, 0, 125], 45);
    $('#pass').textContent = 'Unified model';
    $('#drones').innerHTML = PASSES.map(() => '<i class="done"><b></b></i>').join('');
  }
  return true;
}

function flyTo(asset) {
  if (!view || !scene) return;
  flying = false;
  $('#mFly').classList.remove('on');
  const st = Object.values(scene.stations).find((s) => s.asset === asset);
  const a = scene.anchors[asset] || scene.anchors[`INV-${asset.match(/\d/)?.[0]}`];
  if (!a) return;
  const from = st ? st.position : [a[0] - 10, 6, a[2] + 10];
  view.look([from[0] + (from[0] - a[0]) * 0.4, from[1] + 2.5, from[2] + (from[2] - a[2]) * 0.4], a, 50);
  $('#pass').textContent = asset;
  showCam(asset);
}

function showCam(asset) {
  const shot = (S0?.shots[asset] || []).filter((s) => (thermal ? s.mode === 'thermal' : true)).pop() || (S0?.shots[asset] || []).pop();
  const fig = $('#cam');
  fig.hidden = !shot;
  if (!shot) return;
  fig.querySelector('img').src = `/${shot.render_path || shot.image_path}`;
  fig.querySelector('figcaption').innerHTML = `<span>${esc(shot.viewpoint)}</span><span>${esc(shot.mode)} · ${esc((shot.captured_at || '').slice(11))}</span>`;
}

function placePins() {
  if (!view || !scene || !S0) return;
  const W = view.renderer.domElement.clientWidth / view.renderer.domElement.width;
  const H = view.renderer.domElement.clientHeight / view.renderer.domElement.height;
  const show = Object.entries(S0.assets).filter(([, a]) => a.cls !== 'verified' || S0.final).map(([id, a]) => [id, a.cls]);
  if (S0.focus && !show.some(([id]) => id === S0.focus)) show.push([S0.focus, 'work']);
  $('#pins').innerHTML = show.map(([id, cls]) => {
    const p = scene.anchors[id];
    if (!p) return '';
    const [x, y, z] = view.project([p[0], p[1] + 2.4, p[2]]);
    if (z > 1 || x < 0 || y < 0 || x > view.renderer.domElement.width || y > view.renderer.domElement.height) return '';
    return `<div class="pin ${cls === 'warn' ? 'work' : cls}" data-asset="${id}" style="left:${x * W}px;top:${y * H}px">${id}</div>`;
  }).join('');
}

async function initTwin() {
  scene = await loadScene();
  view = new PlantView($('#view'));
  await view.load(undefined, scene.sky);
  view.animate = () => flyFrame(performance.now());
  const draw = view.draw.bind(view);
  view.draw = () => { draw(); placePins(); };
  view.controls.addEventListener('start', () => { if (flying) { flying = false; $('#mFly').classList.remove('on'); } });
  view.overview();
  view.start();
  $('#mFly').classList.toggle('on', flying);
}

function setMode(t) {
  thermal = t;
  $('#mRgb').classList.toggle('on', !t);
  $('#mThermal').classList.toggle('on', t);
  $('#scale').hidden = !t;
  $('#cam').hidden = true;
  syncTwin();
}

function syncTwin() {
  if (!view || !S0) return;
  const hot = Object.entries(S0.assets).filter(([, a]) => a.cls === 'bad').map(([id, a]) => scene.anchors[a.raw] || scene.anchors[id]).filter(Boolean);
  view.setThermal(thermal, hot);
}

// ---------- data ----------
function render() {
  const S = derive(events.slice(0, cursor));
  S0 = S;
  $('#goal').textContent = S.goal || '';
  const st = $('#state');
  st.className = `pill ${S.final ? (S.final.status === 'DONE' ? 'ok' : 'warn') : events.length ? 'work' : ''}`;
  st.textContent = S.final ? S.final.status : events.length ? 'Running' : 'Idle';
  $('#clock').textContent = (S.last?.t_sim || '').replace('T', ' ');
  renderAssets(S); renderIncident(S); renderLanes(S); syncTwin();
  view?.touch(2);
}

const runPath = (id) => id.split('/').map(encodeURIComponent).join('/');
async function fetchEvents() {
  const res = await fetch(`/runs/${runPath(runId)}/events.jsonl`, { cache: 'no-store' });
  if (!res.ok) throw new Error(res.status);
  const out = [];
  for (const line of (await res.text()).split('\n')) { if (!line.trim()) continue; try { out.push(JSON.parse(line)); } catch { break; } }
  return out;
}

async function poll() {
  try { const evs = await fetchEvents(); if (evs.length !== events.length) { events = evs; cursor = evs.length; render(); } } catch { /* run not started */ }
  setTimeout(poll, 1000);
}

function tick() {
  if (cursor < events.length) { cursor++; render(); }
  const e = events[cursor - 1];
  const slow = e && (e.type === 'tool_call' ? 450 : e.type === 'tool_result' ? 250 : 1100);
  if (cursor < events.length) setTimeout(tick, (slow || 600) / speed);
}

async function main() {
  try { const r = await (await fetch('/api/requirements')).json(); r.requirements.forEach((q) => { REQS[q.id] = q; }); STDS = r.standards; } catch { /* optional */ }
  let runs = [];
  try { runs = await (await fetch('/api/runs')).json(); } catch { /* no api */ }
  if (!runId && runs.length) runId = runs[0].id;
  const sel = $('#runPick');
  sel.innerHTML = runs.map((r) => `<option value="${esc(r.id)}"${r.id === runId ? ' selected' : ''}>${esc(r.id)}</option>`).join('');
  sel.onchange = () => { params.set('run', sel.value); location.search = params.toString(); };
  const pick = (e) => { const el = e.target.closest('[data-asset]'); if (el) flyTo(el.dataset.asset); };
  $('#assets').onclick = pick;
  $('#pins').onclick = pick;
  $('#incAsset').onclick = () => S0?.focus && flyTo(S0.focus);
  $('#incident').ondblclick = () => runId && window.open(`/?run=${encodeURIComponent(runId).replace(/%2F/g, '/')}`, '_blank');  // operator view
  $('#mRgb').onclick = () => setMode(false);
  $('#mThermal').onclick = () => setMode(true);
  $('#mFly').onclick = () => { flying = true; flyT0 = performance.now(); $('#mFly').classList.add('on'); $('#cam').hidden = true; view?.touch(); };
  initTwin().catch((err) => { console.warn('twin unavailable', err); $('#view').innerHTML = ''; });
  render();
  if (!runId) return;
  if (!replay) { poll(); return; }
  events = await fetchEvents();
  cursor = 0;
  render();
  setTimeout(tick, 800);
}

main();
