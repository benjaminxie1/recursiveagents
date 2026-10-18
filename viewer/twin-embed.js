// GridTwin embeddable twin: assets/splats/plant.splat with scene.json's asset anchors as clickable hotspots, orbit and
// first-person walk modes, and the run's faulted / thermal assets highlighted from runs/<id>/events.jsonl (contract C2,
// demo.* events included). Built to sit in an iframe next to the drone feeds: it posts the clicked asset to the parent
// page and takes focus / mode / thermal / status messages back. Like the other views it never reads truth.jsonl.
//
//   <iframe src="twin-embed.html?run=<id>&mode=orbit|walk&asset=INV-3&thermal=1"></iframe>
//   child -> parent  {source: 'gridtwin-twin', type: 'ready', assets: [...]}
//                    {source: 'gridtwin-twin', type: 'select', asset, status, note}   (asset null when deselected)
//                    {source: 'gridtwin-twin', type: 'mode', mode}
//                    {source: 'gridtwin-twin', type: 'cta', action: 'work_order', asset}  (the data panel's Dispatch button)
//   parent -> child  {type: 'focus', asset}  {type: 'mode', mode: 'orbit'|'walk'}  {type: 'thermal', on}  {type: 'home'}
//                    {type: 'status', assets: {ID: 'bad'|'hot'|'work'|'iso'|'ok'|'idle'}}  (overrides the run log)
//                    {type: 'run', run}  {type: 'inside', asset?}  (walk into the equipment room)
// The equipment room is Will's engine-room scan (twin.html?scene=tugboat.splat) with the same asset IDs (INV-1..4,
// XFMR-1, CB-1.5): 'Walk inside' swaps this frame to it, with the run and a Site chip to come back out here. Inside, it
// posts {source: 'gridtwin-twin', scene: 'interior', type: 'select', asset} the same way.
import * as THREE from 'three';
import { PlantView, loadScene } from './splat.js';
import { loadCase, panelHTML, hasHotSpot } from './twin-panel.js';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const V3 = THREE.Vector3;
const params = new URLSearchParams(location.search);
let runId = params.get('run');
const controlled = params.get('controlled') === '1';  // the parent owns the replay cursor; never read future events

const KIND = [[/^INV-/, 'Inverter'], [/^CB-/, 'Combiner box'], [/^B\d$/, 'PV array block'], [/^52-/, 'Breaker'],
  [/^XFMR-/, 'Main transformer'], [/^BESS-/, 'Battery enclosure'], [/^POI-/, 'Revenue meter'], [/^MET-/, 'Met station']];
const kindOf = (id) => (KIND.find(([re]) => re.test(id)) || [null, 'Equipment'])[1];
const LABEL = { bad: 'Fault', hot: 'Thermal anomaly', work: 'Being worked on', iso: 'Isolated', ok: 'Verified', seen: 'Inspected', idle: 'Healthy' };
const send = (msg) => { try { if (parent !== window) parent.postMessage({ source: 'gridtwin-twin', ...msg }, '*'); } catch { /* cross-origin parent gone */ } };

let view, scene, orbit, selected = null, mode = 'orbit', thermal = params.get('thermal') === '1';
let fromRun = {}, override = {};
// frontloaded per-asset evidence (SCADA, camera, infrared), ?case= or Will's s06 case; null until loaded or if missing
const normalizeCase = (root) => !root || root === 'none' ? null : root;
let caseRoot = normalizeCase(params.get('case') || 'assets/demo/s06_case');
let caseData = null;
let caseRevision = 0;
async function setCase(root) {
  caseRoot = normalizeCase(root);
  const revision = ++caseRevision;
  caseData = null;
  if (scene) refresh();
  const data = caseRoot ? await loadCase(caseRoot).catch(() => null) : null;
  if (revision !== caseRevision) return;
  caseData = data;
  if (scene) refresh();
}
const INSIDE = ['INV-1', 'INV-2', 'INV-3', 'INV-4', 'XFMR-1', 'CB-1.5'];  // labelled in tugboat.registration.json
function goInside(asset) {
  const here = new URL(location.href);
  here.searchParams.set('case', caseRoot || 'none');
  if (asset) here.searchParams.set('asset', asset); else here.searchParams.delete('asset');
  if (mode === 'walk') here.searchParams.set('mode', 'walk');
  const q = new URLSearchParams({ scene: 'tugboat.splat', tour: '1', run: controlled ? 'none' : runId || 'none', case: caseRoot || '' });
  if (controlled) q.set('controlled', '1');
  if (asset) q.set('open', asset);
  q.set('back', `twin-embed.html${here.search}`);
  send({ type: 'scene', scene: 'interior', asset: asset || null });
  location.href = `twin.html?${q}`;
}
const statusOf = (id) => {
  const s = { ...(fromRun[id] || {}), ...(override[id] ? { cls: override[id], hot: override[id] === 'hot' } : {}) };
  if (hasHotSpot(caseData?.packs?.[id]) && s.cls !== 'ok') s.hot = true;  // an infrared hot spot is flagged in 3D whatever the run says
  return s;
};

// ---------- fold the run log into per-asset status ----------
let mentionRe = null;
const mentions = (v) => {
  if (!mentionRe || v == null) return [];
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return [...new Set([...s.matchAll(mentionRe)].map((m) => m[1]))];
};
const THERMAL = /thermal|hot ?spot|overheat|over-temp|temperature|°C|\bdegC\b/i;

function derive(evs) {
  const A = {}, wo = {};
  const set = (ids, cls, note, hot) => [ids].flat().filter((id) => id && scene.anchors[id]).forEach((id) => {
    const a = (A[id] ??= {});
    a.cls = cls;
    if (note) a.note = note;
    if (hot != null) a.hot = hot;
    if (cls === 'ok') a.hot = false;
  });
  for (const e of evs) {
    const d = e.data || {}, t = e.type, name = d.name;
    const asset = d.asset || d.args?.asset || d.result?.asset;
    if (d.wo_id && asset) wo[d.wo_id] = asset;
    if (t === 'demo.diagnosis' || t === 'finding') {
      set(asset || mentions(d.summary || d.text), 'bad', d.summary || d.text, THERMAL.test(`${d.fault} ${d.summary}`) || undefined);
    } else if (t === 'demo.workorder' || (t === 'tool_call' && name === 'dispatch_tech')) {
      set(asset, 'work', d.work_order?.replace?.(/_/g, ' '));
    } else if (t === 'tool_call' && name === 'operate') {
      set(asset, d.args?.action === 'open' ? 'iso' : 'work', `${d.args?.action || 'operate'} ${asset}`.replace(/_/g, ' '));
    } else if (t === 'demo.check' || t === 'demo.verdict' || t === 'verdict') {
      const ids = asset || wo[d.wo_id] || mentions([d.evidence, d.claim, d.check_id]);
      if (!d.pass) set(ids, 'bad', d.detail || (d.failed ? `failed ${d.failed.join(', ')}` : `check ${d.check_id || ''} failed`), THERMAL.test(`${d.name} ${d.detail}`) || undefined);
      else if (t !== 'demo.check') set(ids, 'ok', 'verified');
    } else if (t === 'demo.final' || t === 'final') {
      const ok = /VERIFIED|DONE/i.test(d.status || '');
      set(Object.values(wo).length ? Object.values(wo) : mentions(d.summary), ok ? 'ok' : 'bad', d.summary);
    } else if (t === 'page') {
      set(mentions(d.brief), 'bad', 'escalated to a human');
    } else if (t === 'demo.camera' || (t === 'tool_result' && name === 'get_camera_view')) {
      const r = d.result || d, a = A[asset];
      if (asset && scene.anchors[asset] && !a) A[asset] = { cls: 'seen', note: r.description };
      if (asset && r.mode === 'thermal' && A[asset]?.cls === 'bad') A[asset].hot = true;
      if (asset && A[asset] && r.description) A[asset].note = A[asset].note || r.description;
    }
  }
  return A;
}

const runPath = (id) => id.split('/').map(encodeURIComponent).join('/');
let nEvents = -1, pollTimer = null, autoSeq = null;
async function poll() {
  clearTimeout(pollTimer);
  if (controlled || !runId || runId === 'none') return;
  try {
    const res = await fetch(`/runs/${runPath(runId)}/events.jsonl`, { cache: 'no-store' });
    if (res.ok) {
      const evs = [];
      for (const line of (await res.text()).split('\n')) { if (!line.trim()) continue; try { evs.push(JSON.parse(line)); } catch { break; } }
      if (evs.length !== nEvents) {
        nEvents = evs.length; fromRun = derive(evs); refresh();
        // the agent names the broken object (its diagnosis, or opening it in the twin): jump there and open its data
        const named = evs.filter((e) => (e.type === 'demo.diagnosis' || e.type === 'demo.twin' || e.type === 'finding') && (e.data?.asset && scene.anchors[e.data.asset])).at(-1);
        if (named && named.seq !== autoSeq) { autoSeq = named.seq; select(named.data.asset, true); }
      }
      if (evs.some((e) => e.type === 'demo.final' || e.type === 'final')) return;  // closed: stop polling
    }
  } catch { /* run not started yet */ }
  pollTimer = setTimeout(poll, 1500);
}

// ---------- 3D highlights: a pulsing ground ring and a light column on every faulted or thermal asset ----------
const beacons = new THREE.Group();
const COL = { bad: 0xd9480f, hot: 0xff6a00, work: 0xb7791f, iso: 0x5f6469, ok: 0x1f8a55 };
function buildBeacons() {
  beacons.clear();
  for (const [id, p] of Object.entries(scene.anchors)) {
    const s = statusOf(id), cls = s.hot ? 'hot' : s.cls;
    if (!['bad', 'hot', 'work'].includes(cls) && id !== selected) continue;
    const color = id === selected && !['bad', 'hot', 'work'].includes(cls) ? 0x111315 : COL[cls];
    const big = /^(B\d|XFMR|BESS)/.test(id) ? 2.2 : 1;
    const g = new THREE.Group();
    g.position.set(p[0], 0.15, p[2]);
    const ring = new THREE.Mesh(new THREE.RingGeometry(2.6 * big, 3.2 * big, 64),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.9, side: THREE.DoubleSide, depthWrite: false }));
    ring.rotation.x = -Math.PI / 2;
    const disc = new THREE.Mesh(new THREE.CircleGeometry(2.6 * big, 64),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false }));
    disc.rotation.x = -Math.PI / 2;
    const column = new THREE.Mesh(new THREE.CylinderGeometry(0.35 * big, 0.35 * big, 14, 16, 1, true),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: cls === 'work' ? 0.25 : 0.4, depthWrite: false, blending: THREE.AdditiveBlending }));
    column.position.y = 7;
    g.add(ring, disc, column);
    g.userData = { ring, pulse: cls === 'bad' || cls === 'hot' };
    beacons.add(g);
  }
}
function pulseBeacons(now) {
  let any = false;
  for (const g of beacons.children) {
    if (!g.userData.pulse) continue;
    const f = (now / 1400) % 1;
    g.userData.ring.scale.setScalar(1 + f * 0.9);
    g.userData.ring.material.opacity = 0.9 * (1 - f);
    any = true;
  }
  return any;
}

// ---------- hotspots ----------
let pins = [];
function buildPins() {
  const el = $('#pins');
  el.innerHTML = '';
  pins = Object.entries(scene.anchors).map(([id, p]) => {
    const b = document.createElement('button');
    b.className = 'pin';
    b.dataset.asset = id;
    b.innerHTML = `<span class="tag">${esc(id)}</span><i class="dot"></i>`;
    b.onclick = (e) => { e.stopPropagation(); select(selected === id ? null : id, true); };
    el.append(b);
    return { id, at: new V3(p[0], p[1] + (/^B\d$/.test(id) ? 1.6 : 1.4), p[2]), el: b };
  });
}
function refresh() {
  for (const p of pins) {
    const s = statusOf(p.id), cls = s.cls || 'idle';
    p.el.className = `pin ${cls}${s.hot ? ' hot' : ''}${['bad', 'work', 'iso'].includes(cls) || s.hot ? ' lbl' : ''}${p.id === selected ? ' sel' : ''}${cls === 'idle' && /^CB-/.test(p.id) ? ' dim' : ''}`;
    p.el.setAttribute('aria-label', `${p.id} ${kindOf(p.id)}: ${LABEL[s.hot ? 'hot' : cls]}`);
  }
  buildBeacons();
  if (view) {
    const hot = Object.keys(scene.anchors).filter((id) => { const s = statusOf(id); return s.hot || s.cls === 'bad'; }).map((id) => scene.anchors[id]);
    view.setThermal(thermal, hot);
    loadOverlays();
  }
  if (selected) showCard(selected);
  view?.touch(4);
}
const tmp = new V3();
function placePins() {
  const c = view.renderer.domElement, w = c.clientWidth, h = c.clientHeight;
  for (const p of pins) {
    tmp.copy(p.at).project(view.camera);
    const dist = view.camera.position.distanceTo(p.at);
    if (tmp.z > 1 || tmp.z < -1 || Math.abs(tmp.x) > 1.05 || Math.abs(tmp.y) > 1.05 || dist > 900) { p.el.style.display = 'none'; continue; }
    p.el.style.display = '';
    p.el.style.zIndex = String(100000 - Math.round(dist * 10) + (p.el.classList.contains('lbl') ? 200000 : 0));
    p.el.style.transform = `translate3d(${(((tmp.x + 1) / 2) * w).toFixed(1)}px, ${(((1 - tmp.y) / 2) * h).toFixed(1)}px, 0)`;
  }
}

// the side panel takes the right of the frame: shift the 3D image left by half of it, so the asset stays in view
function applyOffset() {
  if (!view) return;
  const c = view.renderer.domElement, w = c.clientWidth, h = c.clientHeight, card = $('#card');
  const pw = !card.hidden && card.classList.contains('panel') && w > 560 ? card.offsetWidth + 12 : 0;
  if (pw) view.camera.setViewOffset(w, h, pw / 2, 0, w, h); else view.camera.clearViewOffset();
  view.camera.updateProjectionMatrix();
  view.touch();
}
addEventListener('resize', () => setTimeout(applyOffset, 50));
function showCard(id) {
  const card = $('#card');
  if (!id) { card.hidden = true; card.className = 'card'; applyOffset(); return; }
  const pack = caseData?.packs?.[id];
  if (pack) {
    card.className = 'card panel';
    card.innerHTML = panelHTML(pack, caseData, { status: statusOf(id), inside: INSIDE.includes(id) });
    card.querySelector('.x').onclick = () => select(null, true);
    card.querySelectorAll('[data-cta]').forEach((b) => { b.onclick = () => {
      if (b.dataset.cta === 'inside') { goInside(id); return; }
      send({ type: 'cta', action: b.dataset.cta, asset: id });
      b.textContent = 'Work order sent ✓'; b.disabled = true;
    }; });
    card.hidden = false;
    applyOffset();
    return;
  }
  card.className = 'card';
  const s = statusOf(id), cls = s.hot ? 'hot' : s.cls || 'idle';
  const st = Object.entries(scene.stations || {}).filter(([, v]) => v.asset === id).map(([k]) => k);
  card.innerHTML = `<button class="x" aria-label="Close">×</button><h3>${esc(id)}<small>${esc(kindOf(id))}</small></h3>
    <div class="st ${cls === 'seen' ? 'idle' : cls}">${esc(LABEL[cls])}</div>
    ${s.note ? `<p>${esc(s.note)}</p>` : ''}
    ${st.length ? `<p class="mono" style="font-size:11px;color:var(--muted)">Cameras: ${esc(st.join(', '))}</p>` : ''}
    ${INSIDE.includes(id) ? '<button class="inside">Walk inside the equipment room →</button>' : ''}`;
  card.querySelector('.inside')?.addEventListener('click', () => goInside(id));
  card.querySelector('.x').onclick = () => select(null, true);
  card.hidden = false;
  applyOffset();
}

function select(id, fromUser) {
  selected = id && scene.anchors[id] ? id : null;
  refresh();
  showCard(selected);
  $('#where').textContent = selected ? `${selected} · ${kindOf(selected)}` : mode === 'walk' ? 'Walking the plant' : 'Plant overview';
  if (selected) focusAsset(selected);
  if (fromUser) { const s = statusOf(selected); send({ type: 'select', asset: selected, status: selected ? (s.hot ? 'hot' : s.cls || 'idle') : null, note: s.note || null }); }
}

// ---------- camera moves ----------
let tween = null;
function flyTo(pos, target, dur = 1.1) {
  const fwd = new V3();
  view.camera.getWorldDirection(fwd);
  const t0 = mode === 'orbit' ? orbit.target.clone() : view.camera.position.clone().addScaledVector(fwd, target.distanceTo(view.camera.position));
  tween = { p0: view.camera.position.clone(), p1: pos.clone(), t0, t1: target.clone(), k: 0, dur };
  walk.vel.set(0, 0, 0);
  view.touch();
}
function stepTween(dt) {
  const T = tween;
  T.k = Math.min(1, T.k + dt / T.dur);
  const e = T.k < 0.5 ? 4 * T.k ** 3 : 1 - (-2 * T.k + 2) ** 3 / 2;
  view.camera.position.lerpVectors(T.p0, T.p1, e);
  const look = new V3().lerpVectors(T.t0, T.t1, e);
  view.camera.lookAt(look);
  orbit.target.copy(look);
  if (T.k >= 1) { tween = null; syncWalk(); }
}

function focusAsset(id) {
  const a = new V3(...scene.anchors[id]);
  if (mode === 'walk') {  // stand a few metres off it at eye height, looking at it
    const st = Object.values(scene.stations || {}).find((s) => s.asset === id);
    const dir = st ? new V3(st.position[0] - a.x, 0, st.position[2] - a.z).normalize() : view.camera.position.clone().sub(a).setY(0).normalize();
    const back = /^B\d$/.test(id) ? 16 : /^(XFMR|BESS)/.test(id) ? 14 : 6;
    flyTo(a.clone().addScaledVector(dir, back).setY(EYE), a.clone().setY(1.2));
    return;
  }
  const st = Object.values(scene.stations || {}).find((s) => s.asset === id);
  const from = st ? new V3(...st.position) : new V3(a.x - 10, 6, a.z + 10);
  const pos = from.clone().add(from.clone().sub(a).multiplyScalar(0.5)).add(new V3(0, 3, 0));
  flyTo(pos, a);
}

const OVERVIEW = { pos: new V3(150, 85, 290), at: new V3(140, 0, 105) };
const WALK_START = { pos: new V3(120, 1.7, 246), at: new V3(150, 1.4, 205) };
function home() {
  if (mode === 'walk') flyTo(WALK_START.pos, WALK_START.at);
  else flyTo(OVERVIEW.pos, OVERVIEW.at);
}

// ---------- walk: drag to look, W A S D / arrows to move, E Q up and down, wheel forward, click the ground to go there ----------
const EYE = 1.7, SPEED = 7;  // metres, m/s (Shift for three times that)
const walk = { yaw: 0, pitch: 0, keys: new Set(), vel: new V3(), drag: null };
function syncWalk() {
  const e = new THREE.Euler().setFromQuaternion(view.camera.quaternion, 'YXZ');
  walk.yaw = e.y; walk.pitch = clamp(e.x, -1.45, 1.45);
}
function stepWalk(dt) {
  const k = (...c) => (c.some((x) => walk.keys.has(x)) ? 1 : 0);
  const f = k('KeyW', 'ArrowUp') - k('KeyS', 'ArrowDown'), r = k('KeyD', 'ArrowRight') - k('KeyA', 'ArrowLeft'), u = k('KeyE') - k('KeyQ');
  const want = new V3(-Math.sin(walk.yaw), 0, -Math.cos(walk.yaw)).multiplyScalar(f)
    .addScaledVector(new V3(Math.cos(walk.yaw), 0, -Math.sin(walk.yaw)), r).addScaledVector(new V3(0, 1, 0), u);
  if (want.lengthSq()) want.normalize().multiplyScalar(SPEED * (k('ShiftLeft', 'ShiftRight') ? 3 : 1));
  walk.vel.lerp(want, 1 - Math.exp(-dt * 9));
  const cam = view.camera;
  cam.position.addScaledVector(walk.vel, dt);
  cam.position.y = Math.max(0.6, cam.position.y);
  cam.rotation.set(walk.pitch, walk.yaw, 0, 'YXZ');
  return walk.vel.lengthSq() > 1e-4 || walk.drag;
}
function groundAt(x, y) {
  const r = view.renderer.domElement.getBoundingClientRect();
  const ray = new THREE.Raycaster();
  ray.setFromCamera(new THREE.Vector2(((x - r.left) / r.width) * 2 - 1, -((y - r.top) / r.height) * 2 + 1), view.camera);
  const hit = new V3();
  return ray.ray.intersectPlane(new THREE.Plane(new V3(0, 1, 0), 0), hit) ? hit : null;
}

function setMode(m, quiet) {
  if (m === mode) return;
  mode = m;
  document.body.classList.toggle('walk', m === 'walk');
  $('#mOrbit').classList.toggle('on', m === 'orbit');
  $('#mWalk').classList.toggle('on', m === 'walk');
  $('#hint').textContent = m === 'walk' ? 'Drag to look · W A S D to walk · click the ground to go there' : 'Drag to orbit · scroll to zoom · click an asset';
  view.controls = m === 'orbit' ? orbit : null;
  orbit.enabled = m === 'orbit';
  if (m === 'walk') {
    // drop to eye height where you are looking: the orbit target, or the selected asset
    const at = selected ? new V3(...scene.anchors[selected]) : orbit.target.clone();
    const dir = view.camera.position.clone().sub(at).setY(0);
    if (dir.lengthSq() < 1) dir.set(0, 0, 1);
    dir.normalize();
    flyTo(at.clone().addScaledVector(dir, selected ? 8 : 24).setY(EYE), at.clone().setY(1.3), 1.4);
  } else {
    const fwd = new V3();
    view.camera.getWorldDirection(fwd);
    const at = view.camera.position.clone().addScaledVector(fwd, 30).setY(0);
    flyTo(at.clone().sub(fwd.clone().setY(0).normalize().multiplyScalar(55)).setY(38), at, 1.2);
  }
  $('#where').textContent = selected ? `${selected} · ${kindOf(selected)}` : m === 'walk' ? 'Walking the plant' : 'Plant overview';
  if (!quiet) send({ type: 'mode', mode: m });
}
function setThermalMode(on) {
  thermal = !!on;
  $('#mThermal').classList.toggle('on', thermal);
  refresh();
}

function bindInput() {
  const c = view.renderer.domElement;
  c.addEventListener('pointerdown', (e) => {
    tween = null;
    if (mode !== 'walk') return;
    walk.drag = { x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY, moved: false };
    c.setPointerCapture(e.pointerId);
  });
  c.addEventListener('pointermove', (e) => {
    if (mode !== 'walk' || !walk.drag) return;
    const dx = e.clientX - walk.drag.x, dy = e.clientY - walk.drag.y;
    walk.drag.x = e.clientX; walk.drag.y = e.clientY;
    if (Math.hypot(e.clientX - walk.drag.x0, e.clientY - walk.drag.y0) > 4) walk.drag.moved = true;
    walk.yaw += dx * 0.0042; walk.pitch = clamp(walk.pitch + dy * 0.0042, -1.45, 1.45);
    view.touch();
  });
  c.addEventListener('pointerup', (e) => {
    if (mode !== 'walk' || !walk.drag) return;
    const moved = walk.drag.moved;
    walk.drag = null;
    if (moved) return;
    const p = groundAt(e.clientX, e.clientY);
    if (!p || p.distanceTo(view.camera.position) > 220) return;
    const d = p.clone().sub(view.camera.position).setY(0);
    const len = d.length();
    d.normalize();
    const to = view.camera.position.clone().addScaledVector(d, Math.max(0, len - 2)).setY(EYE);
    flyTo(to, to.clone().addScaledVector(d, 10).setY(EYE - 0.15), clamp(len / 25, 0.6, 2.2));
  });
  c.addEventListener('wheel', (e) => {
    if (mode !== 'walk') return;
    e.preventDefault();
    const fwd = new V3();
    view.camera.getWorldDirection(fwd);
    view.camera.position.addScaledVector(fwd, -e.deltaY * 0.04);
    view.camera.position.y = Math.max(0.6, view.camera.position.y);
    view.touch();
  }, { passive: false });
  addEventListener('keydown', (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === 'Escape') { select(null, true); return; }
    if (/^(Key[WASDEQ]|Arrow|Shift)/.test(e.code)) {
      if (mode !== 'walk' && /^Key[WASD]$/.test(e.code)) setMode('walk');
      walk.keys.add(e.code); view.touch();
      if (e.code.startsWith('Arrow')) e.preventDefault();
    }
  });
  addEventListener('keyup', (e) => walk.keys.delete(e.code));
  addEventListener('blur', () => walk.keys.clear());
  orbit.addEventListener('start', () => { tween = null; });
  $('#mOrbit').onclick = () => setMode('orbit');
  $('#mWalk').onclick = () => setMode('walk');
  $('#mThermal').onclick = () => setThermalMode(!thermal);
  $('#mInside').onclick = () => goInside(selected && INSIDE.includes(selected) ? selected : null);
  $('#mHome').onclick = () => { select(null, true); home(); };
}

// ---------- fault overlays: the splat edit for an asset's broken state, when the run names it ----------
const overlayList = {};
async function loadOverlays() {
  for (const [id, s] of Object.entries(fromRun)) {
    if (s.cls !== 'bad' && !s.hot) continue;
    if (!(id in overlayList)) {
      overlayList[id] = null;
      try {
        const html = await (await fetch(`/assets/splats/overlays/${encodeURIComponent(id)}/`)).text();
        overlayList[id] = [...html.matchAll(/href="([^"/]+\.splat)"/g)].map((m) => decodeURIComponent(m[1]));
      } catch { overlayList[id] = []; }
    }
    const files = overlayList[id] || [];
    const text = `${s.note || ''}`.toLowerCase().replace(/[\s-]+/g, '_');
    const f = files.find((x) => text.includes(x.replace(/\.splat$/, ''))) || (files.length === 1 ? files[0] : null);
    if (f) view.setOverlay(`${id}/${f}`, `/assets/splats/overlays/${encodeURIComponent(id)}/${encodeURIComponent(f)}`).then(() => view.touch(20)).catch(() => {});
  }
}

// ---------- parent page messages ----------
addEventListener('message', (e) => {
  const m = e.data;
  if (!m || typeof m !== 'object' || m.source === 'gridtwin-twin' || !scene) return;
  if (m.type === 'focus' || m.type === 'select') select(m.asset || null, false);
  else if (m.type === 'mode' && (m.mode === 'walk' || m.mode === 'orbit')) setMode(m.mode, true);
  else if (m.type === 'thermal') setThermalMode(m.on);
  else if (m.type === 'home') { select(null, false); home(); }
  else if (m.type === 'status' && m.assets) { override = { ...m.assets }; refresh(); }
  else if (m.type === 'case') setCase(m.case);
  else if (m.type === 'inside') goInside(m.asset || null);
  else if (m.type === 'run') { runId = m.run || null; nEvents = -1; fromRun = {}; override = {}; refresh(); poll(); }
});

async function main() {
  const casing = setCase(caseRoot);
  scene = await loadScene();
  const ids = Object.keys(scene.anchors).sort((a, b) => b.length - a.length).map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  mentionRe = new RegExp(`(?<![\\w.-])(${ids.join('|')})(?![\\w-]|\\.\\d)`, 'g');
  view = new PlantView($('#view'));
  orbit = view.controls;
  orbit.maxDistance = 600;
  orbit.minDistance = 3;
  view.markers.add(beacons);  // drawn on top of the thermal pass too
  buildPins();
  bindInput();
  // the load bar: Spark reports no progress through SplatMesh, so follow the fetch ourselves
  let pct = 0;
  const bump = setInterval(() => { pct = Math.min(92, pct + (92 - pct) * 0.08); $('#bar').style.width = `${pct}%`; $('#pct').textContent = `Loading twin ${Math.round(pct)}%`; }, 120);
  await view.load(undefined, scene.sky);
  await casing;
  clearInterval(bump);
  $('#bar').style.width = '100%';
  view.camera.position.copy(OVERVIEW.pos);
  view.look(OVERVIEW.pos.toArray(), OVERVIEW.at.toArray(), 50);
  let last = performance.now();
  view.animate = () => {
    const now = performance.now(), dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    let busy = false;
    if (tween) { stepTween(dt); busy = true; } else if (mode === 'walk') busy = stepWalk(dt);
    return pulseBeacons(now) || busy;
  };
  const draw = view.draw.bind(view);
  view.draw = () => { draw(); placePins(); };
  view.start();
  setTimeout(() => { $('#loader').classList.add('done'); }, 200);
  if (thermal) $('#mThermal').classList.add('on');
  refresh();
  if (params.get('mode') === 'walk') setMode('walk', true);
  await poll();
  if (params.get('asset')) select(params.get('asset'), false);
  send({ type: 'ready', assets: Object.keys(scene.anchors) });
}

window.gridtwinTwin = { view: () => view, settle: () => { if (tween) stepTween(1e9); }, select: (a) => select(a, false), setMode: (m) => setMode(m, true), setThermal: setThermalMode, home, status: () => ({ ...fromRun }) };
main().catch((err) => { $('#pct').textContent = `Twin unavailable: ${err?.message || err}`; });
