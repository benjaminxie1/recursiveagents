// GridTwin digital twin: the plant's Gaussian splat with its camera stations and asset hotspots, coloured by the agents'
// run log in runs/<id>/events.jsonl (contract C2). Navigation and chrome follow the XGRIDS LCC viewer: first person (drag
// to look around 360 degrees, W A S D to walk, E Q up and down, wheel forward and back, click a spot to go there) and
// pivot mode, behind an intro splash. Like the evidence view and the wall it never reads truth.jsonl.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { SparkRenderer, SplatMesh } from '@sparkjsdev/spark';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const short = (v, n = 140) => {
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return s && s.length > n ? s.slice(0, n - 1) + '…' : s ?? '';
};
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const V3 = THREE.Vector3;
const UP = new V3(0, 1, 0);

const params = new URLSearchParams(location.search);
const SCENE = params.get('scene') || 'scene';
const SPLAT_URL = `/assets/splats/${/\.(ply|spz|splat|ksplat|sog)$/i.test(SCENE) ? SCENE : `${SCENE}.ply`}`;
const REG_URL = `/assets/splats/${SCENE.replace(/\.[a-z]+$/i, '')}.registration.json`;
const replay = params.get('replay') === '1';
const TOUR = params.get('tour') === '1';  // skip the intro splash (demos, tests)
let runId = params.get('run');

// ---------- renderer ----------
// "scene" coordinates are what the camera moves in: metres with Y up and the registration board's centre at the origin
// when there is a registration. splatFrame maps the splat file's own coordinates (and the registered cameras) into them.
// The canvas is transparent: the page's dark radial gradient shows wherever the scan has no splats.
const canvas = $('#view');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));  // splat cost grows with pixels: 1.5 keeps a 1M-splat scan near 50 fps
renderer.setClearColor(0x000000, 0);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(65, 1, 0.02, 2000);
camera.rotation.order = 'YXZ';
const spark = new SparkRenderer({ renderer });
scene.add(spark);
const splatFrame = new THREE.Group();
splatFrame.matrixAutoUpdate = false;
scene.add(splatFrame);
const stationGroup = new THREE.Group();
splatFrame.add(stationGroup);

let mesh = null, reg = null;
let metric = false;      // true when the registration gives metres
let M = 1;               // one metre (or a metre-ish guess) in scene units
let focus = new V3();    // robust scene centre, for the overview and orbit fallbacks
let extent = 10;

function placeFrame() {
  if (!reg?.board_to_splat) { splatFrame.matrix.makeRotationX(Math.PI); return; }  // the usual PLY flip (y down, z forward)
  const { scale: s, R, t } = reg.board_to_splat;  // splat = s * R @ board + t, board in metres
  // world up is the board normal when the board lies flat, or its printed "up" when it hangs on a wall: whichever the
  // registered photo cameras' up vectors agree with
  const upB = new V3();
  for (const v of Object.values(reg.views || {})) {
    const u = v.up;  // splat direction -> board direction is R^T u
    upB.add(new V3(R[0][0] * u[0] + R[1][0] * u[1] + R[2][0] * u[2], R[0][1] * u[0] + R[1][1] * u[1] + R[2][1] * u[2],
      R[0][2] * u[0] + R[1][2] * u[1] + R[2][2] * u[2]));
  }
  const flat = !upB.lengthSq() || Math.abs(upB.z) >= Math.abs(upB.y);
  const Y = flat ? new V3(0, 0, upB.z < 0 ? -1 : 1) : new V3(0, upB.y < 0 ? -1 : 1, 0);
  const X = new V3(1, 0, 0), Z = X.clone().cross(Y);
  const A = new THREE.Matrix4().set(X.x, X.y, X.z, 0, Y.x, Y.y, Y.z, 0, Z.x, Z.y, Z.z, 0, 0, 0, 0, 1);
  const RT = new THREE.Matrix4().set(R[0][0], R[1][0], R[2][0], 0, R[0][1], R[1][1], R[2][1], 0, R[0][2], R[1][2], R[2][2], 0, 0, 0, 0, 1);
  splatFrame.matrix.copy(A).multiply(new THREE.Matrix4().makeScale(1 / s, 1 / s, 1 / s)).multiply(RT)
    .multiply(new THREE.Matrix4().makeTranslation(-t[0], -t[1], -t[2]));
  metric = true;
}

// percentiles of a subsample of splat centres, so a few far floaters don't set the scale
function measureScene() {
  splatFrame.updateMatrixWorld(true);
  const n = mesh.packedSplats?.numSplats ?? mesh.numSplats ?? 0;
  const every = Math.max(1, Math.floor(n / 40000));
  const xs = [], ys = [], zs = [], v = new V3();
  (mesh.splats ?? mesh.packedSplats).forEachSplat((i, c) => {
    if (i % every) return;
    v.copy(c).applyMatrix4(splatFrame.matrixWorld);
    xs.push(v.x); ys.push(v.y); zs.push(v.z);
  });
  const pct = (a, p) => a[Math.min(a.length - 1, Math.floor(p * a.length))];
  [xs, ys, zs].forEach((a) => a.sort((p, q) => p - q));
  if (!xs.length) return;
  focus.set(pct(xs, 0.5), pct(ys, 0.5), pct(zs, 0.5));
  extent = Math.max(pct(xs, 0.95) - pct(xs, 0.05), pct(ys, 0.95) - pct(ys, 0.05), pct(zs, 0.95) - pct(zs, 0.05)) || 10;
  M = metric ? 1 : extent / 12;
  camera.near = 0.02 * M;
  camera.far = Math.max(200 * M, extent * 30);
  camera.updateProjectionMatrix();
}

// ---------- navigation ----------
const WALK = 1.4;  // m/s, Shift for three times that
const nav = {
  mode: 'walk', locked: false, yaw: 0, pitch: 0, tyaw: 0, tpitch: 0,
  vel: new V3(), push: new V3(), keys: new Set(), fly: null, pinch: null, panDepth: 3,
};
const orbit = new OrbitControls(camera, canvas);
orbit.enableDamping = true;
orbit.dampingFactor = 0.09;
orbit.screenSpacePanning = true;
orbit.zoomToCursor = true;
orbit.enabled = false;

function syncFromCamera() {
  const e = new THREE.Euler().setFromQuaternion(camera.quaternion, 'YXZ');
  nav.yaw = nav.tyaw = e.y;
  nav.pitch = nav.tpitch = clamp(e.x, -1.55, 1.55);
}
const lookQuat = (eye, target) => new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().lookAt(eye, target, UP));

function flyTo(position, target, { quat = null, dur = null } = {}) {
  const q1 = quat ?? (target ? lookQuat(position, target) : camera.quaternion.clone());
  const dist = camera.position.distanceTo(position);
  nav.fly = {
    p0: camera.position.clone(), p1: position.clone(), q0: camera.quaternion.clone(), q1, t: 0,
    dur: dur ?? clamp(0.45 + dist / (4 * M), 0.6, 1.8),
    o0: orbit.target.clone(), o1: target ? target.clone() : orbit.target.clone(),
  };
  nav.vel.set(0, 0, 0);
  nav.push.set(0, 0, 0);
}
function cancelFly() { if (nav.fly) { nav.fly = null; syncFromCamera(); } }

function updateNav(dt) {
  if (nav.fly) {
    const f = nav.fly;
    f.t = Math.min(1, f.t + dt / f.dur);
    const e = f.t < 0.5 ? 4 * f.t ** 3 : 1 - (-2 * f.t + 2) ** 3 / 2;
    camera.position.lerpVectors(f.p0, f.p1, e);
    camera.quaternion.slerpQuaternions(f.q0, f.q1, e);
    orbit.target.lerpVectors(f.o0, f.o1, e);
    if (f.t >= 1) { nav.fly = null; syncFromCamera(); }
    return;
  }
  if (nav.mode === 'orbit') { orbit.update(); return; }
  const a = 1 - Math.exp(-dt * 16);
  nav.yaw += (nav.tyaw - nav.yaw) * a;
  nav.pitch += (nav.tpitch - nav.pitch) * a;
  camera.rotation.set(nav.pitch, nav.yaw, 0, 'YXZ');
  const k = (...codes) => (codes.some((c) => nav.keys.has(c)) ? 1 : 0);
  const f = k('KeyW', 'ArrowUp') - k('KeyS', 'ArrowDown');
  const r = k('KeyD', 'ArrowRight') - k('KeyA', 'ArrowLeft');
  const u = k('KeyE') - k('KeyQ');
  const want = new V3(-Math.sin(nav.yaw), 0, -Math.cos(nav.yaw)).multiplyScalar(f)
    .addScaledVector(new V3(Math.cos(nav.yaw), 0, -Math.sin(nav.yaw)), r).addScaledVector(UP, u);
  if (nav.locked) want.set(0, 0, 0);
  if (want.lengthSq()) want.normalize().multiplyScalar(WALK * M * (k('ShiftLeft', 'ShiftRight') ? 3 : 1));
  nav.vel.lerp(want, 1 - Math.exp(-dt * 9));
  camera.position.addScaledVector(nav.vel, dt);
  const b = 1 - Math.exp(-dt * 10);
  camera.position.addScaledVector(nav.push, b);
  nav.push.multiplyScalar(1 - b);
}

// ---------- picking ----------
const raycaster = new THREE.Raycaster();
function rayAt(x, y) {
  const r = canvas.getBoundingClientRect();
  raycaster.setFromCamera(new THREE.Vector2(((x - r.left) / r.width) * 2 - 1, -((y - r.top) / r.height) * 2 + 1), camera);
  return raycaster.ray;
}
function pick(x, y) {
  if (!mesh?.isInitialized) return null;
  rayAt(x, y);
  try { return raycaster.intersectObject(mesh, false)[0] ?? null; } catch { return null; }
}
let lastPick = { t: 0, x: -1e9, y: 0, hit: null };
function pickCached(x, y) {
  const now = performance.now();
  if (now - lastPick.t > 140 || Math.hypot(x - lastPick.x, y - lastPick.y) > 12) lastPick = { t: now, x, y, hit: pick(x, y) };
  return lastPick.hit;
}

// the LCC-style cursor: a ring on whatever the pointer is over (flat on floors, facing you on walls)
const ring = new THREE.Group();
ring.add(new THREE.Mesh(new THREE.RingGeometry(0.8, 1, 56), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.9, depthTest: false, depthWrite: false, side: THREE.DoubleSide })));
ring.add(new THREE.Mesh(new THREE.CircleGeometry(0.8, 56), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.16, depthTest: false, depthWrite: false, side: THREE.DoubleSide })));
ring.children.forEach((m) => { m.renderOrder = 30; });
ring.visible = false;
scene.add(ring);
const isFloor = (hit) => {
  const d = hit.point.clone().sub(camera.position);
  return -d.y > 0.35 * Math.hypot(d.x, d.z) && -d.y > 0.3 * M;
};
function hover(x, y) {
  const hit = nav.mode === 'walk' || measuring ? pickCached(x, y) : null;
  ring.visible = !!hit && !nav.locked;
  if (!ring.visible) return;
  ring.position.copy(hit.point);
  ring.scale.setScalar(clamp(hit.distance * 0.03, 0.06 * M, 1.2 * M));
  if (isFloor(hit)) ring.quaternion.setFromEuler(new THREE.Euler(-Math.PI / 2, 0, 0));
  else ring.quaternion.copy(camera.quaternion);
}

function walkTo(hit) {
  const C = camera.position, P = hit.point;
  const d = P.clone().sub(C);
  const horiz = new V3(d.x, 0, d.z);
  const hl = horiz.length();
  if (isFloor(hit)) { flyTo(new V3(P.x, C.y, P.z), null); return; }  // stand on the spot at the same eye height
  const stop = Math.max(1.2 * M, 0.25 * hl);  // walk up to the equipment and face it
  const dest = hl > stop ? C.clone().addScaledVector(horiz.normalize(), hl - stop) : C.clone();
  flyTo(dest, P);
}

// ---------- input ----------
const pointers = new Map();
let press = null;
canvas.addEventListener('contextmenu', (e) => e.preventDefault());
canvas.addEventListener('pointerdown', (e) => {
  press = { x: e.clientX, y: e.clientY, t: performance.now(), button: e.button, moved: 0 };
  ring.visible = false;
  if (nav.mode !== 'walk' || nav.locked) return;
  cancelFly();
  try { canvas.setPointerCapture(e.pointerId); } catch { /* a synthetic pointer has nothing to capture */ }
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY, button: e.button });
  nav.pinch = null;
  if (e.button !== 0) nav.panDepth = pick(e.clientX, e.clientY)?.distance ?? 3 * M;
});
canvas.addEventListener('pointermove', (e) => {
  if (press) press.moved = Math.max(press.moved, Math.hypot(e.clientX - press.x, e.clientY - press.y));
  const p = pointers.get(e.pointerId);
  if (!p) { if (!e.buttons) hover(e.clientX, e.clientY); return; }
  const dx = e.clientX - p.x, dy = e.clientY - p.y;
  p.x = e.clientX; p.y = e.clientY;
  if (pointers.size === 1) {
    if (p.button === 0 && !e.shiftKey) look(dx, dy);
    else pan(dx, dy, nav.panDepth);
    return;
  }
  const [a, b] = [...pointers.values()];
  const d = Math.hypot(a.x - b.x, a.y - b.y), mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
  if (nav.pinch) {
    nav.push.addScaledVector(camera.getWorldDirection(new V3()), (d - nav.pinch.d) * 0.012 * M);
    pan(mx - nav.pinch.mx, my - nav.pinch.my, 3 * M);
  }
  nav.pinch = { d, mx, my };
});
const release = (e) => {
  pointers.delete(e.pointerId);
  nav.pinch = null;
  if (!press || e.type === 'pointercancel') { press = null; return; }
  const click = press.button === 0 && press.moved < 5 && performance.now() - press.t < 450;
  press = null;
  if (click) onClick(e.clientX, e.clientY);
};
canvas.addEventListener('pointerup', release);
canvas.addEventListener('pointercancel', release);
canvas.addEventListener('pointerleave', () => { if (!pointers.size) ring.visible = false; });

function look(dx, dy) {
  const k = ((camera.fov * Math.PI) / 180 / canvas.clientHeight) * 1.15;  // drag the view, Street View style
  nav.tyaw += dx * k;
  nav.tpitch = clamp(nav.tpitch + dy * k, -1.55, 1.55);
}
function pan(dx, dy, depth) {
  const k = (2 * Math.tan((camera.fov * Math.PI) / 360) * depth) / canvas.clientHeight;
  camera.position.addScaledVector(new V3(1, 0, 0).applyQuaternion(camera.quaternion), -dx * k)
    .addScaledVector(new V3(0, 1, 0).applyQuaternion(camera.quaternion), dy * k);
}

canvas.addEventListener('wheel', (e) => {
  if (nav.mode !== 'walk' || nav.locked) return;
  e.preventDefault();
  cancelFly();
  const notches = clamp(-e.deltaY / (e.deltaMode === 1 ? 3 : 100), -3, 3);  // wheel up = forward
  const hit = pickCached(e.clientX, e.clientY);
  const dir = rayAt(e.clientX, e.clientY).direction.clone();
  let step = clamp((hit?.distance ?? 4 * M) * 0.16, 0.08 * M, 2.5 * M) * notches;
  if (hit && step > 0) step = Math.min(step, Math.max(0, hit.distance - nav.push.length() - 0.35 * M));  // stop short of the surface
  nav.push.addScaledVector(dir, step);
}, { passive: false });

const typing = (e) => /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName);
addEventListener('keydown', (e) => {
  if (!entered || typing(e) || e.metaKey || e.ctrlKey) return;  // the splash takes Enter on its own button
  if (e.code === 'Escape') { escape(); return; }
  if (/^(Key[WASDEQ]|Arrow\w+|Shift\w+)$/.test(e.code)) {
    nav.keys.add(e.code);
    if (/^(Key|Arrow)/.test(e.code)) { cancelFly(); e.preventDefault(); }
  }
});
addEventListener('keyup', (e) => nav.keys.delete(e.code));
addEventListener('blur', () => nav.keys.clear());

// ---------- intro splash and entry notice, as on the LCC tours ----------
// The splat loads behind the splash. Enter fades it onto the loader while the load still runs, else straight into the
// tour; the one-line notice on moving shows once the scan is in view and goes on the first interaction.
let entered = TOUR, loaded = false, noticeShown = false, noticeTimer = null;
function enterTour() {
  if (entered) return;
  entered = true;
  $('#splash').classList.add('out');
  setTimeout(() => { $('#splash').hidden = true; }, 700);
  if (loaded) setTimeout(showNotice, 300);
}
$('#btnEnter').onclick = enterTour;
if (TOUR) $('#splash').hidden = true; else $('#btnEnter').focus();
function revealed() { loaded = true; if (entered) setTimeout(showNotice, 450); }  // the loader has started to fade
function showNotice() {
  if (noticeShown) return;
  noticeShown = true;
  $('#notice').hidden = false;
  noticeTimer = setTimeout(hideNotice, 12000);  // an unattended demo screen clears itself
}
function hideNotice() {
  const n = $('#notice');
  if (n.hidden || n.classList.contains('out')) return;
  clearTimeout(noticeTimer);
  n.classList.add('out');
  setTimeout(() => { n.hidden = true; }, 420);
}
$('#notice .x').onclick = hideNotice;
for (const type of ['pointerdown', 'wheel', 'keydown']) {
  addEventListener(type, (e) => { if (entered && !e.target.closest?.('#notice')) hideNotice(); }, { capture: true, passive: true });
}

// ---------- modes and tools ----------
function setMode(mode) {
  if (mode === nav.mode) return;
  cancelFly();
  nav.mode = mode;
  if (mode === 'orbit') {
    const r = canvas.getBoundingClientRect();
    const hit = pick(r.left + r.width / 2, r.top + r.height / 2);
    orbit.target.copy(hit ? hit.point : camera.position.clone().addScaledVector(camera.getWorldDirection(new V3()), 3 * M));
    orbit.update();
  } else syncFromCamera();
  orbit.enabled = mode === 'orbit' && !nav.locked;
  ring.visible = false;
  $('#tWalk').classList.toggle('on', mode === 'walk');
  $('#tOrbit').classList.toggle('on', mode === 'orbit');
}
$('#tWalk').onclick = () => setMode('walk');
$('#tOrbit').onclick = () => setMode('orbit');

let follow = false;
$('#tTour').onclick = () => {
  follow = !follow;
  $('#tTour').classList.toggle('on', follow);
  if (follow) {
    const last = lastState?.lastCamera;
    const st = stations.find((s) => s.id === last && s.view) ?? stations.find((s) => s.view);
    if (st) viewFrom(st);
  }
};
// stations are quiet by default: the camera tool shows all of them, and a station the agents look through shows its
// frustum and photo for a few seconds on its own (stationAlpha, run every frame)
let stationsOn = false;
$('#tStations').onclick = () => {
  stationsOn = !stationsOn;
  $('#tStations').classList.toggle('on', stationsOn);
  $('#tStations').dataset.tip = stationsOn ? 'Hide camera stations' : 'Show camera stations';
  document.body.classList.toggle('nostations', !stationsOn);
};
const FLASH_IN = 0.25, FLASH_HOLD = 4.5, FLASH_OUT = 1.4;  // seconds
function flashLevel(st, now) {
  if (!st.flashAt) return 0;
  const t = (now - st.flashAt) / 1000;
  return t < FLASH_IN ? t / FLASH_IN : t < FLASH_IN + FLASH_HOLD ? 1 : clamp(1 - (t - FLASH_IN - FLASH_HOLD) / FLASH_OUT, 0, 1);
}
function flashStation(st) {  // (re)start the hold from wherever the flash is now, so a photo arriving doesn't blink it
  if (!st?.gfx) return;
  const now = performance.now();
  st.flashAt = now - flashLevel(st, now) * FLASH_IN * 1000;
}
const stationAlpha = (st, now) => (stationsOn ? 1 : flashLevel(st, now));
function updateStations(now) {
  for (const st of stations) {
    const g = st.gfx;
    if (!g) continue;
    const a = stationAlpha(st, now);
    g.lines.visible = a > 0.002;
    g.lines.material.opacity = (g.busy ? 1 : 0.55) * a;
    g.photo.visible = a > 0.002 && g.photoReady;
    g.photo.material.opacity = 0.92 * a;
  }
}
$('#tPins').onclick = () => {
  const on = !$('#tPins').classList.contains('on');
  $('#tPins').classList.toggle('on', on);
  document.body.classList.toggle('nopins', !on);
};

// measure: two clicks on the scene, distance in metres when the registration fixes the scale
let measuring = false;
const measure = { pts: [], group: new THREE.Group() };
measure.group.renderOrder = 31;
scene.add(measure.group);
const dotGeo = new THREE.SphereGeometry(1, 16, 12);
const measureMat = new THREE.MeshBasicMaterial({ color: 0xffd23f, depthTest: false, depthWrite: false, transparent: true });
const measureLine = new THREE.LineBasicMaterial({ color: 0xffd23f, depthTest: false, depthWrite: false, transparent: true });
function setMeasuring(on) {
  measuring = on;
  $('#tMeasure').classList.toggle('on', on);
  document.body.classList.toggle('measuring', on);
  if (!on) { measure.pts = []; measure.group.clear(); $('#measureLabel').hidden = true; }
}
$('#tMeasure').onclick = () => setMeasuring(!measuring);
function addMeasurePoint(p) {
  if (measure.pts.length === 2) { measure.pts = []; measure.group.clear(); }
  measure.pts.push(p.clone());
  const dot = new THREE.Mesh(dotGeo, measureMat);
  dot.position.copy(p);
  dot.scale.setScalar(Math.max(0.012 * M, camera.position.distanceTo(p) * 0.006));
  dot.renderOrder = 31;
  measure.group.add(dot);
  if (measure.pts.length === 2) {
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(measure.pts), measureLine);
    line.renderOrder = 31;
    measure.group.add(line);
  }
}
const fmtDist = (d) => (!metric ? `${d.toFixed(2)} units` : d < 1 ? `${(d * 100).toFixed(1)} cm` : `${d.toFixed(2)} m`);

function onClick(x, y) {
  const hit = pick(x, y);
  if (measuring) { if (hit) addMeasurePoint(hit.point); return; }
  if (!hit || nav.locked) return;
  if (nav.mode === 'walk') walkTo(hit);
  else flyTo(camera.position.clone().add(hit.point.clone().sub(orbit.target)), hit.point, { quat: camera.quaternion.clone(), dur: 0.7 });
}

function setLocked(on) {
  nav.locked = on;
  orbit.enabled = nav.mode === 'orbit' && !on;
  $('#btnLock').classList.toggle('on', on);
  $('#btnLock').dataset.tip = on ? 'Unlock the view' : 'Lock the view';
  $('#lockShackle').setAttribute('d', on ? 'M8.5 10.5V7.8a3.5 3.5 0 0 1 7 0v2.7' : 'M8.5 10.5V7.8a3.5 3.5 0 0 1 6.9-.8');
  ring.visible = false;
}
$('#btnLock').onclick = () => setLocked(!nav.locked);
$('#btnHome').onclick = () => home(true);

function escape() {
  if (measuring) setMeasuring(false);
  else if (!$('#drawer').hidden) closeDrawer();
  else if (!$('#banner').hidden) setBanner(false);
  else if (actOpen) setActivity(false);
  else { $('#settings').hidden = true; $('#help').hidden = true; syncChips(); }
}
const syncChips = () => { $('#btnSettings').classList.toggle('on', !$('#settings').hidden); $('#btnHelp').classList.toggle('on', !$('#help').hidden); };
$('#btnSettings').onclick = () => { $('#settings').hidden = !$('#settings').hidden; $('#help').hidden = true; syncChips(); };
$('#btnHelp').onclick = () => { $('#help').hidden = !$('#help').hidden; $('#settings').hidden = true; syncChips(); };

// tooltips for every [data-tip] button, kept inside the window
const tip = $('#tip');
document.addEventListener('pointerover', (e) => {
  const el = e.target.closest?.('[data-tip]');
  if (!el || e.pointerType === 'touch') { tip.hidden = true; return; }
  tip.textContent = el.dataset.tip;
  tip.hidden = false;
  const r = el.getBoundingClientRect(), w = tip.offsetWidth;
  const below = r.top < 80;
  tip.style.left = `${clamp(r.left + r.width / 2, 8 + w / 2, innerWidth - 8 - w / 2)}px`;
  tip.style.top = `${below ? r.bottom + 8 : r.top - 8}px`;
  tip.style.transform = below ? 'translate(-50%, 0)' : 'translate(-50%, -100%)';
});
document.addEventListener('pointerdown', () => { tip.hidden = true; });

// ---------- gizmo: X red, Y green, Z blue with Z up (world Y), like a survey frame ----------
// drawn at 2x into a 160 px canvas shown at 80 px: a dark disc, small lettered bubbles, grey bubbles for the negative axes
const gizmo = $('#gizmo'), g2 = gizmo.getContext('2d');
const AXES = [{ v: new V3(1, 0, 0), c: '#f0524f', l: 'X' }, { v: new V3(0, 0, -1), c: '#4fd25a', l: 'Y' }, { v: new V3(0, 1, 0), c: '#4a95ff', l: 'Z' }];
const GZ = 160, GC = 80, GR = 56, GB = 17, GN = 14;  // canvas size, centre, axis length, bubble radii
let gizmoItems = [], gizmoKey = '';
function drawGizmo() {
  const q = camera.quaternion;
  const key = `${q.x.toFixed(4)},${q.y.toFixed(4)},${q.z.toFixed(4)},${q.w.toFixed(4)}`;
  if (key === gizmoKey) return;  // only redraw when the view turns
  gizmoKey = key;
  const qi = q.clone().invert();
  g2.clearRect(0, 0, GZ, GZ);
  g2.fillStyle = 'rgba(14, 14, 16, 0.62)';
  g2.beginPath(); g2.arc(GC, GC, GC, 0, Math.PI * 2); g2.fill();
  gizmoItems = [];
  for (const a of AXES) for (const s of [1, -1]) {
    const p = a.v.clone().multiplyScalar(s).applyQuaternion(qi);
    gizmoItems.push({ x: GC + p.x * GR, y: GC - p.y * GR, z: p.z, a, s });
  }
  gizmoItems.sort((i, j) => i.z - j.z);
  g2.lineCap = 'round';
  for (const it of gizmoItems) {  // back to front, each axis line drawn with its own bubble
    g2.strokeStyle = it.s > 0 ? it.a.c : 'rgba(150, 150, 156, 0.4)';
    g2.lineWidth = it.s > 0 ? 3 : 2.5;
    g2.beginPath(); g2.moveTo(GC, GC); g2.lineTo(it.x, it.y); g2.stroke();
    if (it.s > 0) {
      g2.fillStyle = it.a.c;
      g2.beginPath(); g2.arc(it.x, it.y, GB, 0, Math.PI * 2); g2.fill();
      g2.fillStyle = 'rgba(8, 10, 14, 0.78)'; g2.font = '700 19px -apple-system, system-ui, sans-serif';
      g2.textAlign = 'center'; g2.textBaseline = 'middle';
      g2.fillText(it.a.l, it.x, it.y + 1);
    } else {
      g2.fillStyle = 'rgba(112, 112, 116, 0.78)';
      g2.beginPath(); g2.arc(it.x, it.y, GN, 0, Math.PI * 2); g2.fill();
    }
  }
}
const gizmoHit = (e) => {
  const r = gizmo.getBoundingClientRect(), k = GZ / r.width;
  const x = (e.clientX - r.left) * k, y = (e.clientY - r.top) * k;
  return [...gizmoItems].reverse().find((it) => Math.hypot(it.x - x, it.y - y) < GB + 3);
};
gizmo.addEventListener('pointermove', (e) => { gizmo.style.cursor = gizmoHit(e) ? 'pointer' : 'default'; });
gizmo.addEventListener('click', (e) => {
  const it = gizmoHit(e);
  if (!it || nav.locked) return;
  const dir = it.a.v.clone().multiplyScalar(it.s);  // look from that side towards the focus
  const pivot = nav.mode === 'orbit' ? orbit.target.clone() : camera.position.clone().addScaledVector(camera.getWorldDirection(new V3()), 3 * M);
  const dist = camera.position.distanceTo(pivot);
  const eye = pivot.clone().addScaledVector(dir, dist);
  if (Math.abs(dir.y) > 0.5) eye.add(new V3(0, 0, 1e-3 * dist));  // straight down or up: keep lookAt well defined
  if (nav.mode === 'orbit') flyTo(eye, pivot, { dur: 0.8 });
  else flyTo(camera.position.clone(), null, { quat: lookQuat(eye, pivot), dur: 0.6 });
});

// ---------- stations and pins ----------
const KIND = [[/^INV-/, 'central inverter'], [/^CB-/, 'combiner box'], [/^B\d$/, 'PV array'], [/^52-/, 'breaker'],
  [/^XFMR-/, 'transformer yard'], [/^BESS-/, 'battery enclosure'], [/^POI-/, 'revenue meter']];
const kindOf = (asset) => (KIND.find(([re]) => re.test(asset)) || [null, 'equipment'])[1];
let stations = [];  // /api/viewpoints, each with .view when the registration has its camera
let pins = [];      // one per asset with at least one registered station

function setStations(vps) {
  stations = (vps || []).map((v) => ({ ...v, label: v.label || `${v.asset} ${kindOf(v.asset)}`, view: reg?.views?.[v.id] ?? null }));
  // registered stations the sim has no viewpoint for (a scene's extra equipment) name their asset in the registration
  const known = new Set(stations.map((s) => s.id));
  for (const [id, view] of Object.entries(reg?.views || {})) {
    if (!known.has(id) && view.asset) stations.push({ id, asset: view.asset, modes: ['rgb'], label: `${view.asset} ${kindOf(view.asset)}`, view });
  }
}

const splatPoint = (p) => new V3(...p).applyMatrix4(splatFrame.matrixWorld);
const lineMat = () => new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.55, depthTest: false, depthWrite: false });

function buildStations() {
  stationGroup.clear();
  const s = reg?.board_to_splat?.scale ?? 1;
  for (const st of stations.filter((x) => x.view)) {
    const { K, R, t, size: [w, h], position, target } = st.view;
    const fx = K[0][0], fy = K[1][1], cx = K[0][2], cy = K[1][2];
    const depth = Math.min(0.4 * Math.hypot(...target.map((x, i) => x - position[i])), 0.45 * s);
    const toSplat = (xc, yc, zc) => {  // camera -> splat: R^T (x - t)
      const a = [xc - t[0], yc - t[1], zc - t[2]];
      return new V3(R[0][0] * a[0] + R[1][0] * a[1] + R[2][0] * a[2], R[0][1] * a[0] + R[1][1] * a[1] + R[2][1] * a[2],
        R[0][2] * a[0] + R[1][2] * a[1] + R[2][2] * a[2]);
    };
    const apex = toSplat(0, 0, 0);
    const c = [[0, 0], [w, 0], [w, h], [0, h]].map(([u, v]) => toSplat(((u - cx) / fx) * depth, ((v - cy) / fy) * depth, depth));
    const lines = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(
      [apex, c[0], apex, c[1], apex, c[2], apex, c[3], c[0], c[1], c[1], c[2], c[2], c[3], c[3], c[0]]), lineMat());
    lines.renderOrder = 21;
    const quad = new THREE.BufferGeometry().setFromPoints([c[0], c[1], c[2], c[0], c[2], c[3]]);
    quad.setAttribute('uv', new THREE.Float32BufferAttribute([0, 1, 1, 1, 1, 0, 0, 1, 1, 0, 0, 0], 2));
    const photo = new THREE.Mesh(quad, new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.0, depthTest: false, depthWrite: false, side: THREE.DoubleSide }));
    photo.renderOrder = 20;
    lines.visible = photo.visible = false;  // updateStations shows them
    stationGroup.add(lines, photo);
    st.gfx = { lines, photo, photoPath: null, photoReady: false, busy: false };
  }
  splatFrame.updateMatrixWorld(true);
  for (const st of stations.filter((x) => x.view)) {
    st.eye = splatPoint(st.view.position);
    st.target = splatPoint(st.view.target);
  }
}

function buildMarkers() {
  const el = $('#pins');
  el.innerHTML = '';
  const byAsset = new Map();
  for (const st of stations.filter((x) => x.view)) {
    if (!byAsset.has(st.asset)) byAsset.set(st.asset, []);
    byAsset.get(st.asset).push(st);
    const b = document.createElement('button');
    b.className = 'station';
    b.dataset.tip = `View from ${st.id}`;
    b.setAttribute('aria-label', `View from camera ${st.id}`);
    b.innerHTML = '<svg viewBox="0 0 24 24"><path d="M3.5 8.5h11v8h-11z"/><path d="M14.5 11l6-3v9l-6-3"/></svg>';
    b.onclick = () => viewFrom(st);
    el.append(b);
    st.el = b;
  }
  pins = [...byAsset].map(([asset, sts]) => {
    const at = sts.reduce((a, s) => a.add(s.target), new V3()).divideScalar(sts.length);
    const b = document.createElement('button');
    b.className = 'pin st-idle';
    b.setAttribute('aria-label', `${asset} ${kindOf(asset)}: not visited`);
    b.innerHTML = `<span class="tag"><b>${esc(asset)}</b><span class="more"><em>${esc(kindOf(asset))}</em><span class="state">Not visited</span></span></span><span class="stem"></span><i class="hs"></i>`;
    b.onclick = () => openDrawer(asset);
    el.append(b);
    return { asset, at, el: b, tag: b.querySelector('.tag'), stem: b.querySelector('.stem'), stations: sts };
  });
  if (!pins.length) toast(stations.length ? 'No registered camera stations yet, so the pins have nowhere to go. Run scripts/register_viewpoints.py.' : "Can't load the camera stations.");
}

const proj = new V3();
function screenOf(at) {
  proj.copy(at).project(camera);
  const dist = camera.position.distanceTo(at);
  const fade = clamp((45 * M - dist) / (15 * M), 0, 1);
  const on = proj.z < 1 && proj.z > -1 && Math.abs(proj.x) < 1.3 && Math.abs(proj.y) < 1.3 && fade > 0;
  return { on, x: ((proj.x + 1) / 2) * innerWidth, y: ((1 - proj.y) / 2) * innerHeight, dist, fade };
}
function place(el, at, anchor) {
  const p = screenOf(at);
  if (!p.on) { el.style.display = 'none'; return; }
  el.style.display = '';
  el.style.opacity = p.fade.toFixed(2);
  el.style.zIndex = String(Math.max(1, 100000 - Math.round((p.dist / M) * 100)));
  el.style.transform = `translate3d(${p.x}px, ${p.y}px, 0) ${anchor}`;
}
// hotspots: the dot sits on the asset, its id tag fades out with distance (gone past TAG_FAR), and where two tags would
// overlap the farther one is lifted on a longer leader
const STEM = 20, TAG_H = 20, TAG_NEAR = 10, TAG_FAR = 12.5;  // px, px, metres
let tagsOn = true;
function placePins() {
  const placed = [];
  const order = pins.map((p) => ({ p, s: screenOf(p.at) })).sort((a, b) => a.s.dist - b.s.dist);
  for (const { p, s } of order) {
    if (!s.on) { p.el.style.display = 'none'; continue; }
    p.el.style.display = '';
    const tf = tagsOn ? clamp((TAG_FAR * M - s.dist) / ((TAG_FAR - TAG_NEAR) * M), 0, 1) : 0;
    let lift = 0;
    if (tf > 0) {
      if (!p.w) p.w = p.tag.offsetWidth;
      const w = p.w || 60;
      for (let k = 0; k < 6; k++) {
        const top = s.y - 7 - STEM - lift - TAG_H, left = s.x - w / 2;
        const hit = placed.find((r) => left < r.right + 6 && left + w > r.left - 6 && top < r.bottom + 4 && top + TAG_H > r.top - 4);
        if (!hit) { placed.push({ left, right: left + w, top, bottom: top + TAG_H }); break; }
        lift += top + TAG_H - hit.top + 4;
      }
    }
    p.el.style.setProperty('--stem', `${STEM + lift}px`);
    // keep the tag on screen, the wider open one too (it is open while pointed at, focused or selected)
    const w = p.el.matches(':hover, :focus-visible, .sel') ? p.tag.offsetWidth : p.w || 60;
    p.tag.style.setProperty('--dx', `${(clamp(s.x - w / 2, 8, innerWidth - 8 - w) - (s.x - w / 2)).toFixed(1)}px`);
    p.tag.style.opacity = p.stem.style.opacity = tf.toFixed(2);
    p.tag.style.visibility = p.stem.style.visibility = tf > 0 ? '' : 'hidden';
    p.el.style.opacity = s.fade.toFixed(2);
    p.el.style.zIndex = String(Math.max(1, 100000 - Math.round((s.dist / M) * 100)));
    p.el.style.transform = `translate3d(${s.x.toFixed(1)}px, ${s.y.toFixed(1)}px, 0)`;
  }
}
function placeMarkers() {
  placePins();
  for (const st of stations) {
    if (!st.el || !stationsOn) continue;
    if (camera.position.distanceTo(st.eye) < 1.2 * M) st.el.style.display = 'none';
    else place(st.el, st.eye, 'translate(-50%, -50%)');
  }
  const ml = $('#measureLabel');
  if (measure.pts.length === 2) {
    const [a, b] = measure.pts;
    ml.textContent = fmtDist(a.distanceTo(b));
    ml.hidden = false;
    place(ml, a.clone().add(b).multiplyScalar(0.5), 'translate(-50%, -140%)');
  } else ml.hidden = true;
}

function viewFrom(st) {
  if (!st?.eye) return;
  const dir = st.target.clone().sub(st.eye).normalize();
  const eye = st.eye.clone().addScaledVector(dir, -0.7 * M);  // a step behind the camera, so its frustum is in view
  flyTo(eye, st.target);
}

// the start view: standing behind the registered stations, looking over the equipment they watch, like walking in
function startPose() {
  // a scene can name its own start (registration "start": {position, target} in splat coordinates), e.g. standing
  // in the aisle of a scan so a full turn shows the room
  const st = reg?.start;
  if (st?.position && st?.target) { splatFrame.updateMatrixWorld(true); return { eye: splatPoint(st.position), at: splatPoint(st.target) }; }
  const placed = stations.filter((s) => s.eye);
  if (!placed.length) return { eye: focus.clone().add(new V3(0, 0.35 * extent, 0.8 * extent)), at: focus.clone() };
  const at = placed.reduce((a, s) => a.add(s.target), new V3()).divideScalar(placed.length);
  const back = placed.reduce((a, s) => a.add(s.eye.clone().sub(s.target).setY(0).normalize()), new V3()).setY(0);
  if (back.lengthSq() < 0.04) back.set(0, 0, 1);
  back.normalize();
  const spread = Math.max(...placed.map((s) => Math.hypot(s.target.x - at.x, s.target.z - at.z)), 2 * M);
  const heights = placed.map((s) => s.eye.y).sort((a, b) => a - b);
  // back far enough for the spread to fill the width (a portrait phone counts as 1.2:1), near enough for the tags
  const hTan = Math.tan((camera.fov * Math.PI) / 360) * Math.max(camera.aspect, 1.2);
  const eye = at.clone().addScaledVector(back, (spread / hTan) * 1.25 + M).setY(heights[heights.length >> 1] + 0.4 * M);
  return { eye, at };
}
function home(animate) {
  const { eye, at } = startPose();
  if (animate) { flyTo(eye, at); return; }
  camera.position.copy(eye);
  camera.quaternion.copy(lookQuat(eye, at));
  orbit.target.copy(at);
  syncFromCamera();
}

let toastTimer = null;
function toast(text) {
  let el = $('#toast');
  if (!el) { el = document.createElement('div'); el.id = 'toast'; el.className = 'toast'; document.body.append(el); }
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 7000);
}

// ---------- the run log, folded the way the wall folds it ----------
const ASSET_RE = /\b(INV-\d|XFMR-1|BESS-1|POI-1|52-(?:I\d|M|B1)|CB-\d\.\d+|B\d)\b/g;
function mentions(obj) {
  const s = typeof obj === 'string' ? obj : JSON.stringify(obj ?? '');
  const out = new Set([...s.matchAll(ASSET_RE)].map((m) => m[1]));
  for (const st of stations) if (s.includes(st.id)) out.add(st.id);
  return out;
}

// the planner's dod groups checks by outcome ({DONE: [...], ...}); older scripted logs carry one flat list
const dodChecks = (dod) => {
  const c = dod?.data?.checks;
  return !c ? [] : Array.isArray(c) ? c : Object.values(c).flat();
};

function derive(evs) {
  const S = { st: {}, final: null, goal: null, dod: null, verdicts: [], pages: [], assets: null, lastCamera: null };
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
          lastCall = S.lastCamera = d.args?.viewpoint;
          if (S.st[lastCall]) { S.st[lastCall].inflight = true; S.st[lastCall].error = null; }
        }
        break;
      case 'tool_result': {
        if (d.name === 'list_assets' && d.result?.assets) { S.assets = d.result; break; }
        if (d.name !== 'get_camera_view') break;
        const r = d.result || {};
        const st = S.st[r.viewpoint ?? lastCall];
        if (!st) break;
        st.inflight = false;
        if (r.error) { st.error = { seq: e.seq, text: r.error }; break; }
        st.shots.push({ seq: e.seq, ...r });
        break;
      }
      case 'verdict': {
        const check = dodChecks(S.dod).find((c) => (c.id ?? c.check_id) === d.check_id);
        const set = mentions([check, d.evidence, d.requirement, d.requirements]);
        S.verdicts.push({ e, set });
        hit(set).forEach((st) => { st.verdict = e; });
        break;
      }
      case 'page': {
        const set = mentions(d.brief);
        S.pages.push({ e, set });
        hit(set).forEach((st) => { st.page = e; });
        break;
      }
      case 'final': case 'demo.final': S.final = e; break;
      // the end-to-end demo names the failed object in its diagnosis; it stays marked until a verified fix
      case 'demo.diagnosis': if (d.asset) S.failed = { asset: d.asset, text: d.summary || d.fault || '' }; break;
      case 'demo.verdict': if (d.pass && S.failed) S.failed = { ...S.failed, fixed: true }; break;
      case 'demo.stage': if (d.status === 'start') S.stage = d; break;
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
const RANK = { failed: 4, analyzing: 3, verified: 2, seen: 1, idle: 0 };
function assetStatus(asset, S) {
  const all = stations.filter((s) => s.asset === asset).map((s) => statusOf(S.st[s.id], S));
  return all.sort((a, b) => RANK[b[0]] - RANK[a[0]])[0] ?? ['idle', 'Not visited', ''];
}

function tickerLine(e) {
  const d = e.data || {}, who = `<b>${esc(e.actor || 'agent')}</b>`;
  switch (e.type) {
    case 'goal': return `<b>Operator</b> ${esc(short(d.text, 110))}`;
    case 'dod': return `${who} wrote the definition of done: ${dodChecks(e).length} checks`;
    case 'tool_call': {
      const by = e.actor && e.actor !== 'sim' ? `${who} ` : '';
      if (d.name === 'get_camera_view') return `${by}<b>looks through ${esc(d.args?.viewpoint)}</b> (${esc(d.args?.mode || 'rgb')})`;
      const args = Object.entries(d.args || {}).map(([k, v]) => (k === 'asset' ? v : `${k} ${Array.isArray(v) ? v.join(', ') : typeof v === 'object' ? short(v, 30) : v}`));
      return `${by}<b>${esc(d.name)}</b>${args.length ? ` · ${esc(short(args.join(' · '), 70))}` : ''}`;
    }
    case 'finding': return `${who} ${esc(short(d.text ?? d.summary ?? d, 110))}`;
    case 'tool_result': return d.result?.error ? `<b>${esc(d.name)}</b> failed: ${esc(short(d.result.error, 90))}` : null;
    case 'claim': return `${who} claims ${esc(short(d.status ?? d.outcome ?? d, 90))}`;
    case 'verdict': return `<b>Verifier</b> ${esc(d.check_id)} ${d.pass ? 'passed' : 'failed'}`;
    case 'supervisor': return `<b>Supervisor</b> ${esc(short(d.action ?? d.reason ?? d, 100))}`;
    case 'page': return `<b>Paged a human</b> (${esc(d.severity ?? '')}) ${esc(short(d.brief, 90))}`;
    case 'final': case 'demo.final': return `<b>${esc(d.status)}</b> ${esc(short(d.summary, 110))}`;
    case 'demo.stage': return d.status === 'start' ? `<b>${esc(d.stage)}</b>${d.round > 1 ? ` round ${esc(d.round)}` : ''}` : null;
    case 'demo.diagnosis': return `<b>Diagnosis</b> ${esc(d.asset)}: ${esc(short(d.summary ?? d.fault, 100))}`;
    case 'demo.twin': return `${who} opens <b>${esc(d.asset)}</b> in the twin`;
    case 'demo.sensor': return d.stale ? `${who} <b>${esc(d.asset)}</b> data is ${esc(d.data_age_min)} min old` : null;
    case 'demo.camera': return `${who} <b>looks through ${esc(d.viewpoint)}</b> (${esc(d.mode || 'rgb')})`;
    case 'demo.verdict': return `<b>Verifier</b> ${d.pass ? 'verified the fix' : `not verified: ${esc((d.failed || []).join(', '))}`}`;
    default: return null;
  }
}

let events = [], cursor = 0, shown = 0, lastState = null;
let playing = true, speed = 1, timer = null;
const texLoader = new THREE.TextureLoader();

function render() {
  const S = derive(events.slice(0, cursor));
  lastState = S;
  const F = failedNow(S);
  for (const p of pins) {
    const [cls, label, why] = F?.asset === p.asset ? failedStatus(F) : assetStatus(p.asset, S);
    p.el.className = `pin st-${cls}${F?.asset === p.asset && !F.fixed ? ' failed' : ''}${['sel', 'flash'].map((c) => (p.el.classList.contains(c) ? ` ${c}` : '')).join('')}`;
    p.el.querySelector('.state').textContent = label;
    p.el.title = why || label;
    p.el.setAttribute('aria-label', `${p.asset} ${kindOf(p.asset)}: ${label}`);
  }
  for (const st of stations) {
    if (!st.gfx) continue;
    const s = S.st[st.id], g = st.gfx;
    g.busy = s.inflight;
    g.lines.material.color.set(s.inflight ? 0x5aa9ff : 0xffffff);
    st.el?.classList.toggle('busy', s.inflight);
    const last = s.shots[s.shots.length - 1];
    if (last?.image_path && last.image_path !== g.photoPath) {
      const path = g.photoPath = last.image_path;
      texLoader.load(`/${path}`, (tex) => {
        if (g.photoPath !== path) { tex.dispose(); return; }  // a replay restart got there first
        tex.colorSpace = THREE.SRGBColorSpace;
        g.photo.material.map?.dispose();
        g.photo.material.map = tex;
        g.photo.material.needsUpdate = true;
        g.photoReady = true;
      });
    } else if (!last && g.photoPath) {
      g.photoPath = null;
      g.photoReady = false;
    }
  }
  // agents looking through a camera: light its station up for a few seconds, flash its hotspot, and fly there when
  // following. A page load of a long log is not news, so only a few new events at a time count.
  const fresh = cursor - shown < 8;
  for (const e of events.slice(shown, cursor)) {
    // the demo's agent opening an object in the twin (or naming it in its diagnosis): fly there and show its data
    if ((e.type === 'demo.twin' || e.type === 'demo.diagnosis') && e.data?.asset) { if (fresh) openObject(e.data.asset); continue; }
    if (e.data?.name !== 'get_camera_view') continue;
    if (e.type === 'tool_result') { if (fresh) flashStation(stations.find((s) => s.id === (e.data.result?.viewpoint ?? S.lastCamera))); continue; }
    if (e.type !== 'tool_call') continue;
    const st = stations.find((s) => s.id === e.data.args?.viewpoint);
    const pin = pins.find((p) => p.asset === st?.asset);
    if (pin) { pin.el.classList.add('flash'); clearTimeout(pin.flashT); pin.flashT = setTimeout(() => pin.el.classList.remove('flash'), 1400); }
    if (fresh) flashStation(st);
    if (follow && st?.eye && fresh) viewFrom(st);
  }
  shown = cursor;
  renderChrome(S);
  if (!$('#drawer').hidden) openDrawer($('#drawer').dataset.asset, true);
}

// the run chip: mode, run, progress and, once the run ends, a status badge that opens the summary
let bannerOpen = false, actOpen = false;
function renderChrome(S) {
  const chip = $('#runChip');
  chip.hidden = false;
  const mode = $('#runMode');
  mode.textContent = !runId ? 'no run' : replay ? 'replay' : 'live';
  mode.className = `mode${runId && !replay && !S.final ? ' live' : ''}`;
  $('#runName').textContent = runId ? runId.split('/').pop() : 'start one with python -m gridtwin.bench';
  $('#runName').title = runId || '';
  const checks = dodChecks(S.dod);
  const latest = new Map(S.verdicts.map(({ e }) => [e.data.check_id, e.data.pass]));
  const ok = checks.filter((c) => latest.get(c.id ?? c.check_id) === true).length;
  const seen = stations.filter((s) => S.st[s.id].shots.length).length;
  $('#runChecks').innerHTML = checks.length ? `<b>${ok}</b> of ${checks.length} checks`
    : S.goal ? `<b>${seen}</b> of ${stations.length} cameras`
    : S.stage ? `<b>${esc(S.stage.stage)}</b> stage${S.stage.round > 1 ? `, round ${esc(S.stage.round)}` : ''}` : 'waiting for a goal';
  const fin = S.final?.data;
  chip.classList.toggle('final', !!fin);
  $('#runStatus').hidden = !fin;
  if (fin) {
    const status = String(fin.status || 'final'), cls = status.replace(/\W/g, ''), words = status.replace(/_/g, ' ');
    $('#runStatus').className = `badge ${cls}`;
    $('#runStatus').textContent = words;
    $('#banner').className = `banner ${cls}`;
    $('#banner').innerHTML = `<h2>${esc(words)}</h2><p>${esc(fin.summary)}</p>`;
  }
  setBanner(bannerOpen);  // closes itself when a replay restart takes the final away
  // agent activity: the latest step on one line; open, the five before it as well
  const lines = events.slice(0, cursor).map(tickerLine).filter(Boolean);
  $('#activity').hidden = false;
  $('#activity').classList.toggle('running', !!runId && !!lines.length && !fin);
  $('#actText').innerHTML = lines.at(-1) ?? (runId ? 'Waiting for the agents' : 'No run yet');
  $('#actList').innerHTML = lines.slice(-6, -1).map((l) => `<li>${l}</li>`).join('');
  setActivity(actOpen);
}
function setBanner(on) {
  bannerOpen = on && !!lastState?.final;
  $('#banner').hidden = !bannerOpen;
  $('#runStatus').setAttribute('aria-expanded', String(bannerOpen));
  $('#runStatus').dataset.tip = bannerOpen ? 'Hide the summary' : 'Show the summary';
}
function setActivity(on) {
  actOpen = on;
  $('#activity').classList.toggle('open', on);
  $('#actList').hidden = !on || !$('#actList').children.length;
  $('#actLine').setAttribute('aria-expanded', String(on));
}
$('#runChip').onclick = (e) => { if (!e.target.closest('#replayCtl') && lastState?.final) setBanner(!bannerOpen); };
$('#activity').onclick = () => setActivity(!actOpen);

// ---------- asset drawer ----------
function openDrawer(asset, refresh = false) {
  const S = lastState ?? derive([]);
  const d = $('#drawer');
  pins.forEach((p) => p.el.classList.toggle('sel', p.asset === asset));
  if (!refresh) toParent({ type: 'select', asset });
  d.dataset.asset = asset;
  const info = S.assets?.assets?.find((a) => a.asset === asset);
  const F = failedNow(S);
  const [cls, label, why] = F?.asset === asset ? failedStatus(F) : assetStatus(asset, S);
  const obj = caseData?.objects?.[asset];
  const sts = stations.filter((s) => s.asset === asset);
  const shots = sts.flatMap((s) => S.st[s.id].shots.map((x) => ({ ...x, vid: s.id }))).sort((a, b) => b.seq - a.seq);
  const verdicts = S.verdicts.filter(({ set }) => set.has(asset) || sts.some((s) => set.has(s.id))).map(({ e }) => e).reverse();
  const pages = S.pages.filter(({ set }) => set.has(asset)).map(({ e }) => e).reverse();
  const path = info ? [info.system, info.subsystem, info.component].filter(Boolean).map(esc).join(' › ') + (info.component_code ? ` <b>(${esc(info.component_code)})</b>` : '') : '';
  const standards = (info?.standards || []).map((s) => `<span>${esc(typeof s === 'string' ? s : s.id ?? s.name ?? short(s, 40))}</span>`).join('');
  const shot = shots[0];
  d.innerHTML = `<button class="x" aria-label="Close"><svg viewBox="0 0 24 24"><path d="M7 7l10 10M17 7L7 17"/></svg></button>
    <h2>${esc(asset)}</h2><div class="kind">${esc(info?.type || kindOf(asset))}</div>
    ${path ? `<div class="path">${path}</div>` : obj ? `<div class="path">${[obj.block && `Block ${obj.block}`, obj.breaker && `breaker ${obj.breaker}`, obj.rating_kw && `${obj.rating_kw} kW`].filter(Boolean).map(esc).join(' · ')}</div>` : ''}
    ${F?.asset === asset && !F.fixed ? failNote(F) : `<span class="status st-${cls}"><i class="dot st-${cls}"></i>${esc(label)}</span>${why ? ` <span class="muted">${esc(why)}</span>` : ''}`}
    ${obj ? objectData(asset, obj) : ''}
    ${shot || !obj ? `<h3>Latest photo</h3>${shot ? `<a href="/${esc(shot.image_path)}" target="_blank"><img src="/${esc(shot.image_path)}" alt="${esc(shot.vid)} ${esc(shot.mode)}"></a>
      <div class="cap">${esc(shot.vid)} · ${esc(shot.mode)} · ${esc(shot.captured_at || '')}</div>` : '<div class="muted">No photo taken yet in this run.</div>'}` : ''}
    ${verdicts.length || !obj ? `<h3>Verifier</h3>${verdicts.length ? `<ul>${verdicts.map((v) => `<li><span class="${v.data.pass ? 'pass' : 'fail'}">${v.data.pass ? 'PASS' : 'FAIL'}</span> ${esc(v.data.check_id)}<div class="muted">${esc(short(v.data.evidence, 180))}</div></li>`).join('')}</ul>` : '<div class="muted">No verdict on this asset yet.</div>'}` : ''}
    ${pages.length ? `<h3>Pages</h3><ul>${pages.map((p) => `<li><span class="fail">${esc(p.data.severity || 'page')}</span> ${esc(short(p.data.brief, 180))}</li>`).join('')}</ul>` : ''}
    ${standards ? `<h3>Standards</h3><div class="tags">${standards}</div>` : ''}
    <h3>Camera stations</h3><ul>${sts.map((s) => `<li class="vrow"><span><b>${esc(s.id)}</b> <span class="muted">${esc((s.modes || ['rgb']).join(', '))}</span></span>${s.eye ? `<button data-vp="${esc(s.id)}">View from here</button>` : '<span class="muted">not registered</span>'}</li>`).join('')}</ul>`;
  d.hidden = false;
  d.querySelector('.x').onclick = closeDrawer;
  d.querySelectorAll('button[data-vp]').forEach((b) => { b.onclick = () => viewFrom(stations.find((s) => s.id === b.dataset.vp)); });
  if (!refresh) { d.scrollTop = 0; const p = pins.find((x) => x.asset === asset); if (p?.stations[0]) viewFrom(p.stations[0]); }
}
function closeDrawer() { toParent({ type: 'select', asset: null }); $('#drawer').hidden = true; pins.forEach((p) => p.el.classList.remove('sel')); }

// ---------- case data: one scenario's evidence per object (SCADA, camera, infrared) ----------
// The registration's "viewer": {"case": "assets/demo/s06_case"} (or ?case=) names the pack; its agent/objects.json holds
// each object's data and no verdict. The failed object comes from the run's diagnosis, else ?failed=<asset>, else the
// pack's answer key with ?reveal=1. An agent or the demo page opens an object with window.twin.openObject(asset) or
// postMessage({twin: 'open', asset}), and marks one with postMessage({twin: 'fail', asset, text}).
let caseRoot = null, caseData = null, failedSet = null;
async function loadCase() {
  const root = params.get('case') ?? reg?.viewer?.case;
  if (!root) return;
  caseRoot = `/${root.replace(/^\/+|\/+$/g, '')}`;
  caseData = await getJSON(`${caseRoot}/agent/objects.json`);
  if (params.get('failed')) failedSet = { asset: params.get('failed'), text: params.get('why') || '' };
  else if (params.get('reveal') === '1') {
    const k = await getJSON(`${caseRoot}/answer_key/finding.json`);
    if (k?.failed_asset) failedSet = { asset: k.failed_asset, text: k.failure || '', shouldBe: k.failed_to_be || '' };
  }
}
const failedNow = (S) => S?.failed ?? failedSet;
const failedStatus = (F) => (F.fixed ? ['verified', 'Fixed', 'the fix was verified'] : ['failed', 'Failed', F.text]);
function setFailed(asset, text = '', shouldBe = '') {
  failedSet = asset ? { asset, text, shouldBe } : null;
  render();
}
function openObject(asset) {
  if (!pins.some((p) => p.asset === asset)) return false;
  openDrawer(asset);
  return true;
}
// opened from the outdoor site (twin-embed.html?...): a chip back out, and the embedding page hears which object is open
const BACK = params.get('back');
if (BACK && /^twin-embed\.html(\?|$)/.test(BACK)) {
  $('#btnBack').hidden = false;
  $('#btnBack').onclick = () => { location.href = BACK; };
}
const toParent = (msg) => { if (parent !== window) parent.postMessage({ source: 'gridtwin-twin', scene: 'interior', ...msg }, '*'); };
const hhmm = (t) => (typeof t === 'string' && t.length >= 16 ? t.slice(11, 16) : String(t ?? ''));
function failNote(F) {
  return `<div class="failnote"><b>Failed</b>${esc(F.text)}${F.shouldBe ? `<div>Should be ${esc(F.shouldBe)}</div>` : ''}</div>`;
}
function objectData(asset, o) {
  const at = (p) => esc(`${caseRoot}/${p}`);
  const sc = o.scada_latest || {};
  const fmt = (v, unit) => (typeof v === 'number' ? `${v.toLocaleString('en-US', { maximumFractionDigits: 1 })}${unit ? `&nbsp;${unit}` : ''}` : esc(v));
  const age = sc.last_update && caseData.as_of ? Math.round((Date.parse(caseData.as_of) - Date.parse(sc.last_update)) / 60000) : 0;
  const rows = [['AC power', 'ac_kw', 'kW'], ['DC power', 'dc_kw', 'kW'], ['DC voltage', 'dc_v', 'V'], ['Heatsink', 'heatsink_c', '°C'],
    ['Fan', 'fan_rpm', 'rpm'], ['Status', 'status', '']].filter(([, k]) => sc[k] != null)
    .map(([name, k, unit]) => `<tr><td>${name}</td><td>${fmt(sc[k], unit)}</td></tr>`);
  if (sc.last_update) rows.push(`<tr><td>Last update</td><td>${esc(hhmm(sc.last_update))}${age > 2 ? ` <span class="stale">${age} min old</span>` : ''}</td></tr>`);
  const meter = caseData.meter;
  const cam = o.camera, ir = o.infrared;
  const spots = Object.entries(ir?.readings || {}).map(([id, r]) => `<li><span><b>${esc(id)}</b> ${esc(r.label)}</span><span>${r.temp_c != null
    ? fmt(r.temp_c, '°C') : `max ${fmt(r.max_c, '°C')} · mean ${fmt(r.mean_c, '°C')}`}</span></li>`).join('');
  const files = [o.sensors?.file && [o.sensors.file, 'SCADA, one row a minute'], ir?.radiometric && [ir.radiometric, 'Infrared, radiometric °C']].filter(Boolean);
  return `<h3>SCADA at ${esc(hhmm(sc.t || caseData.as_of))}</h3><table class="kv">${rows.join('')}</table>
    ${meter ? `<div class="cap">Plant revenue meter ${esc(meter.id)}: ${fmt(meter.export_kw, 'kW')}</div>` : ''}
    ${cam?.file ? `<h3>Camera</h3><a href="${at(cam.file)}" target="_blank"><img src="${at(cam.file)}" alt="${esc(asset)} display camera"></a>
      <div class="cap">${esc(cam.camera_id || '')} · ${esc(hhmm(cam.captured))}</div>` : ''}
    ${ir?.file ? `<h3>Infrared</h3><a href="${at(ir.file)}" target="_blank"><img src="${at(ir.file)}" alt="${esc(asset)} infrared"></a>
      <div class="cap">${esc(ir.camera_id || '')} · ${esc(hhmm(ir.captured))}${ir.span_c ? ` · scale ${esc(ir.span_c[0])} to ${esc(ir.span_c[1])} °C` : ''}</div>
      ${spots ? `<ul class="spots">${spots}</ul>` : ''}` : ''}
    ${files.length ? `<h3>Data</h3><div class="files">${files.map(([p, name]) => `<a href="${at(p)}" target="_blank">${esc(name)} <span class="muted">${esc(p.split('/').pop())}</span></a>`).join('')}</div>` : ''}`;
}
addEventListener('message', (e) => {
  if (e.origin !== location.origin && !/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(e.origin)) return;
  const m = e.data || {};
  if ((m.twin === 'open' || m.type === 'focus') && m.asset) openObject(m.asset);
  else if (m.twin === 'fail') setFailed(m.asset, m.text, m.should_be);
});

// ---------- runs: live polling or replay, same conventions as the wall ----------
const runPath = (id) => id.split('/').map(encodeURIComponent).join('/');
function urlFor(id, rep) {
  const q = new URLSearchParams(location.search);
  if (id) q.set('run', id); else q.delete('run');
  if (rep) q.set('replay', '1'); else q.delete('replay');
  return `?${q.toString().replace(/%2F/g, '/')}`;
}
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
  } catch (err) { $('#runChecks').textContent = `can't read run: ${err.message}`; }
  setTimeout(poll, 1000);
}
const CAMERA = (e) => e?.data?.name === 'get_camera_view' || ['verdict', 'page', 'final', 'goal'].includes(e?.type);
const PLAY = '<svg viewBox="0 0 24 24"><path d="M8 5.5v13l10.5-6.5z" class="fill"/></svg>';
const PAUSE = '<svg viewBox="0 0 24 24"><path d="M8.5 5.5v13M15.5 5.5v13"/></svg>';
function setPlaying(on) { playing = on; $('#btnPlay').innerHTML = on ? PAUSE : PLAY; }
function tick() {
  clearTimeout(timer);
  if (playing && cursor < events.length) { cursor++; render(); }
  if (cursor >= events.length) { setPlaying(false); return; }
  const e = events[cursor - 1];
  if (playing) timer = setTimeout(tick, (CAMERA(e) ? 1400 : tickerLine(e) ? 260 : 25) / speed);  // bookkeeping flies by
}

async function startRun() {
  let runs = [];
  try { runs = await (await fetch('/api/runs')).json(); } catch { /* served without /api: keep ?run= */ }
  if (!runId && runs.length) { runId = runs[0].id; history.replaceState(null, '', urlFor(runId, replay)); }
  const sel = $('#runPick');
  if (runId && !runs.some((r) => r.id === runId)) runs.unshift({ id: runId, status: null });
  sel.innerHTML = runs.length ? runs.map((r) => `<option value="${esc(r.id)}"${r.id === runId ? ' selected' : ''}>${esc(r.id)} · ${esc(r.status || 'running')}</option>`).join('') : '<option>No runs yet</option>';
  sel.onchange = () => { location.search = urlFor(sel.value, replay); };
  $('#optReplay').checked = replay;
  $('#optReplay').onchange = (e) => { location.search = urlFor(runId, e.target.checked); };
  $('#optSpeed').oninput = (e) => { speed = +e.target.value; $('#optSpeedVal').textContent = `${speed}x`; if (playing) tick(); };
  $('#optLabels').onchange = (e) => { tagsOn = e.target.checked; $('#pins').classList.toggle('nolabels', !tagsOn); };
  render();
  if (!runId) return;
  if (runId === 'none') {  // ?run=none: the scan on its own, no run chip or activity
    $('#runChip').hidden = true; $('#activity').hidden = true; return;
  }
  if (!replay) { poll(); return; }
  $('#replayCtl').hidden = false;
  try { events = await fetchEvents(); } catch (err) { $('#runChecks').textContent = `can't read run: ${err.message}`; return; }
  cursor = shown = 0;
  render();
  $('#btnPlay').onclick = () => { if (!playing && cursor >= events.length) cursor = shown = 0; setPlaying(!playing); tick(); };
  $('#btnRestart').onclick = () => { cursor = shown = 0; render(); setPlaying(true); tick(); };
  setPlaying(true);
  tick();
}

// ---------- loading ----------
const getJSON = async (url) => { try { const r = await fetch(url, { cache: 'no-store' }); return r.ok ? await r.json() : null; } catch { return null; } };
function setPct(x) { $('#pct').textContent = `${(Math.floor(x * 1000) / 10).toFixed(1)}%`; }
function loaderMessage(html) {
  const m = $('#loaderMsg');
  m.innerHTML = html;
  m.hidden = false;
  $('#pct').hidden = true;
}

function resize() {
  renderer.setSize(innerWidth, innerHeight, false);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize);

let prev = performance.now();
renderer.setAnimationLoop((now) => {
  const dt = Math.min(0.25, (now - prev) / 1000);  // slow frames still walk at 1.4 m/s; a tab switch does not jump
  prev = now;
  updateNav(dt);
  updateStations(now);
  placeMarkers();
  drawGizmo();
  renderer.render(scene, camera);
});

// the splash title: <scene>.credits.json "title" when the scene has one, else the scene's name
function sceneTitle(credits) {
  if (credits?.title) return credits.title;
  const name = SCENE.replace(/\.[a-z]+$/i, '');
  return name === 'scene' || name.startsWith('_') ? 'Plant digital twin' : name.replace(/[_-]+/g, ' ');
}
function showCredits(credits) {
  $('#splashTitle').textContent = `GridTwin - ${sceneTitle(credits)}`;
  const by = credits?.attribution || [credits?.author && `Scan by ${credits.author}`, credits?.license].filter(Boolean).join(', ');
  if (by) { $('#splashCredit').textContent = by; $('#splashCredit').hidden = false; $('#sceneCredit').textContent = ` (${by})`; }
}

async function main() {
  resize();
  $('#sceneName').textContent = SPLAT_URL.replace('/assets/splats/', '');
  const [r, vps] = await Promise.all([getJSON(REG_URL), getJSON('/api/viewpoints')]);
  // the credits file is optional: look for it in the folder listing first, so a scene without one logs no 404
  const CREDITS_URL = REG_URL.replace(/\.registration\.json$/, '.credits.json');
  const listing = await fetch('/assets/splats/', { cache: 'no-store' }).then((x) => (x.ok ? x.text() : '')).catch(() => '');
  showCredits(listing.includes(`"${CREDITS_URL.split('/').pop()}"`) ? await getJSON(CREDITS_URL) : null);
  reg = r;
  setStations(vps);
  const head = await fetch(SPLAT_URL, { method: 'HEAD', cache: 'no-store' }).catch(() => null);
  if (!head?.ok) {
    loaderMessage(`<b>No plant scene yet.</b> Capture the equipment area with Scaniverse with the printed board in view, export a PLY to
      <code>${esc(SPLAT_URL.slice(1))}</code>, then register the station photos:<br><code>python -m scripts.register_viewpoints register --splat ${esc(SPLAT_URL.slice(1))}</code>`);
    startRun();
    return;
  }
  placeFrame();
  try {
    // a full-detail scan renders through Spark's level of detail: a fixed splat budget, fine splats near the camera
    // and merged ones far away (registration "viewer": {"lod": true, "lod_splats": N}; ?lod=0 or ?lodsplats=N override)
    const lod = params.has('lod') ? params.get('lod') !== '0' : !!reg?.viewer?.lod;
    const budget = Number(params.get('lodsplats')) || reg?.viewer?.lod_splats;
    if (lod && budget) spark.lodSplatCount = budget;
    mesh = new SplatMesh({ url: SPLAT_URL, lod: lod || undefined, onProgress: (e) => { if (e.lengthComputable && e.total) setPct(Math.min(0.999, e.loaded / e.total)); } });
    splatFrame.add(mesh);
    await mesh.initialized;
  } catch (err) {
    loaderMessage(`<b>Couldn't load ${esc(SPLAT_URL.slice(1))}.</b> ${esc(err?.message || err)}`);
    return;
  }
  measureScene();
  buildStations();
  buildMarkers();
  await loadCase();
  home(false);
  if (params.get('open')) openObject(params.get('open'));
  setPct(1);
  setTimeout(() => { $('#loader').classList.add('done'); revealed(); }, 250);
  setTimeout(() => { $('#loader').hidden = true; }, 1200);  // after the fade, even where transitions are paused
  startRun();
}

window.twin = { camera, nav, orbit, scene, stations: () => stations, pins: () => pins, setMode, home, viewFrom, openObject, setFailed,
  caseData: () => caseData };
main();
