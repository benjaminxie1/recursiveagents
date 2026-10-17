// The inspection wall's 3D plant twin: the Gaussian splat with every camera station drawn in its status colour.
// wall.js publishes window.wallTwin and fires 'wall:render' on each redraw. If the splat or WebGL fails, the section
// stays hidden and the wall keeps its 2D map, so the demo never depends on it.
import { PlantView, loadScene } from './splat.js';

const COLORS = { verified: 0x3ccf7a, failed: 0xef5a4f, analyzing: 0xe0b54a, seen: 0x6f9ee8, idle: 0x9aa59f };
const $ = (s) => document.querySelector(s);

async function main() {
  if (new URLSearchParams(location.search).get('splat') === '0') return;
  const scene = await loadScene();
  $('#twin').hidden = false;
  const view = new PlantView($('#twinView'));
  await view.load(undefined, scene.sky);
  view.overview();
  view.start();
  const poseOf = (s) => scene.stations[s.id] && { position: scene.stations[s.id].position, target: scene.stations[s.id].target };
  let thermal = false;
  // Heat comes only from the log: an asset glows once a check on it failed or it was paged, never from hidden truth.
  const hotAssets = (w) => [...new Set(w.stations.filter((s) => w.status(s) === 'failed').map((s) => s.asset))]
    .map((a) => scene.anchors[a]).filter(Boolean);
  let sig = '';
  const draw = () => {
    const w = window.wallTwin;
    if (!w) return;
    const next = `${thermal}|${w.stations.map((s) => w.status(s)).join(',')}`;
    if (next === sig) return;  // the wall redraws every poll; the twin only when a colour changes
    sig = next;
    view.setStations(w.stations.map((s) => ({ ...s, pose: poseOf(s) })), (s) => COLORS[w.status(s)] ?? COLORS.idle);
    view.setThermal(thermal, hotAssets(w));
  };
  $('#twinThermal').onclick = () => {
    thermal = !thermal;
    $('#twinThermal').classList.toggle('on', thermal);
    draw();
  };
  window.addEventListener('wall:render', draw);
  draw();
  const focus = (vp) => {
    const st = scene.stations[vp];
    if (!st) return;
    const [px, py, pz] = st.position, [tx, ty, tz] = st.target;
    view.look([px + (px - tx) * 0.6, py + 3, pz + (pz - tz) * 0.6], st.target, 50);
    const w = window.wallTwin;
    const shots = w?.S?.st?.[vp]?.shots || [];
    const last = shots[shots.length - 1];
    const box = $('#twinShot');
    box.hidden = !last;
    if (last) {
      const src = last.render_path || last.image_path;
      box.innerHTML = `<img src="/${src}" alt="${vp}"><div>${vp} · ${last.mode} · ${(last.captured_at || '').slice(11)}</div>`;
    }
  };
  window.twinFocus = focus;
  view.renderer.domElement.addEventListener('click', (e) => {
    const vp = view.pick(e.clientX, e.clientY);
    if (vp) { focus(vp); window.wallTwin?.flash(vp); }
  });
  $('#twinHome').onclick = () => { view.overview(); $('#twinShot').hidden = true; };
  $('#twinHide').onclick = () => { $('#twin').hidden = true; };
}

main().catch((err) => { console.warn('plant twin unavailable, keeping the 2D map:', err); $('#twin').hidden = true; });
