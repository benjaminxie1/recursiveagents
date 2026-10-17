// Asset data panel for twin-embed.html: the frontloaded evidence for one asset (SCADA series, camera frame, infrared
// frame with its spot readings) and what in it stands out against the asset's peers. Everything is static files under
// a case pack, fetched once at start and the images preloaded, so a click shows it at once (the demo is 3 minutes).
// Pack today: Will's case layout (<case>/agent/objects.json + sensors/scada_1min.csv), the one twin.html reads.
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const hhmm = (t) => (typeof t === 'string' && t.length >= 16 ? t.slice(11, 16) : String(t ?? ''));
const num = (v, d = 1) => (typeof v === 'number' ? v.toLocaleString('en-US', { maximumFractionDigits: d }) : esc(v ?? '–'));
const mins = (a, b) => Math.round((Date.parse(a) - Date.parse(b)) / 60000);
const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : NaN);

export async function loadCase(root) {
  root = `/${String(root).replace(/^\/+|\/+$/g, '')}`;
  const get = async (p, as = 'json') => { try { const r = await fetch(`${root}/${p}`); return r.ok ? await r[as]() : null; } catch { return null; } };
  const objects = await get('agent/objects.json');
  if (!objects?.objects) return null;
  const files = [...new Set(Object.values(objects.objects).map((o) => o.sensors?.file).filter(Boolean))];
  const tables = Object.fromEntries(await Promise.all(files.map(async (f) => [f, parseCSV(await get(f, 'text'))])));
  const packs = {};
  for (const [id, o] of Object.entries(objects.objects)) {
    const tab = tables[o.sensors?.file] || { t: [], cols: {} };
    const series = {};
    for (const c of o.sensors?.columns || []) {
      const key = c.startsWith(`${id}_`) ? c.slice(id.length + 1) : c;
      if (tab.cols[c]?.every((v) => typeof v === 'number')) series[key] = tab.cols[c];
    }
    packs[id] = { id, kind: o.kind, label: o.label, block: o.block, breaker: o.breaker, rating_kw: o.rating_kw, latest: o.scada_latest || {},
      t: tab.t, series, camera: o.camera?.file && { src: `${root}/${o.camera.file}`, id: o.camera.camera_id, at: o.camera.captured },
      ir: o.infrared?.file && { src: `${root}/${o.infrared.file}`, id: o.infrared.camera_id, at: o.infrared.captured, span: o.infrared.span_c,
        readings: o.infrared.readings || {}, grid: [320, 240] } };
  }
  const meta = { as_of: objects.as_of, meter: objects.meter, plant: objects.plant, ambient: tables[files[0]]?.cols?.ambient_c };
  for (const p of Object.values(packs)) p.flags = analyse(p, Object.values(packs).filter((q) => q !== p && q.kind === p.kind), meta);
  // preload every image now, so opening an asset never waits on the network
  for (const p of Object.values(packs)) for (const src of [p.camera?.src, p.ir?.src]) if (src) { const im = new Image(); im.src = src; }
  return { root, meta, packs };
}

function parseCSV(text) {
  if (!text) return { t: [], cols: {} };
  const [head, ...rows] = text.trim().split('\n').map((l) => l.split(','));
  const cols = Object.fromEntries(head.map((h) => [h, []]));
  for (const r of rows) head.forEach((h, i) => { const v = r[i]; cols[h].push(v !== '' && !isNaN(+v) ? +v : v); });
  return { t: cols.t || cols[head[0]], cols };
}

const irMax = (p) => Math.max(...Object.values(p.ir?.readings || {}).map((r) => r.max_c ?? r.temp_c).filter((x) => x != null));

// What stands out: stale SCADA, output below peers, a cabinet hotter (hot spot) or colder (not converting) than peers.
function analyse(p, peers, meta) {
  const f = [];
  const L = p.latest;
  const age = L.last_update && meta.as_of ? mins(meta.as_of, L.last_update) : 0;
  if (age > 5) f.push({ sev: 'bad', key: 'stale', title: `SCADA frozen for ${age} min`, text: `Last update ${hhmm(L.last_update)}; the dashboard is still showing ${num(L.ac_kw)} kW from then.` });
  const pk = mean(peers.map((q) => q.latest.ac_kw).filter((x) => typeof x === 'number'));
  if (typeof L.ac_kw === 'number' && pk > 0 && L.ac_kw < 0.92 * pk) f.push({ sev: 'warn', key: 'output', title: `Output ${Math.round(100 * (1 - L.ac_kw / pk))}% below peers`, text: `${num(L.ac_kw)} kW against a peer average of ${num(pk)} kW.` });
  const mine = irMax(p), theirs = mean(peers.map(irMax).filter(isFinite));
  if (isFinite(mine) && isFinite(theirs)) {
    const d = mine - theirs;
    if (d >= 8) f.push({ sev: 'bad', key: 'hot', title: `Hot spot: ${num(mine)} °C`, text: `${num(d)} °C above the peer cabinets (${num(theirs)} °C). Check the connections and cooling before it trips.` });
    else if (d <= -3.5) f.push({ sev: 'warn', key: 'cold', title: `Cabinet ${num(-d)} °C cooler than peers`, text: `Infrared reads ${num(mine)} °C against ${num(theirs)} °C: a cool inverter is not converting power, whatever SCADA says.` });
  }
  return f;
}
export const hasHotSpot = (pack) => !!pack?.flags?.some((f) => f.key === 'hot');

function spark(values, { peer, w = 148, h = 34, stale } = {}) {
  const all = [...values, ...(peer || [])].filter((x) => typeof x === 'number');
  if (!all.length) return '';
  let lo = Math.min(...all), hi = Math.max(...all);
  if (hi - lo < 1e-6) { lo -= 1; hi += 1; }
  const xy = (v, i, n) => `${((i / Math.max(1, n - 1)) * w).toFixed(1)},${(h - 2 - ((v - lo) / (hi - lo)) * (h - 4)).toFixed(1)}`;
  const line = (vs) => vs.map((v, i) => xy(v, i, vs.length)).join(' ');
  const sx = stale != null ? (stale / Math.max(1, values.length - 1)) * w : null;
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    ${sx != null ? `<rect x="${sx.toFixed(1)}" y="0" width="${(w - sx).toFixed(1)}" height="${h}" class="stalebg"/>` : ''}
    ${peer ? `<polyline points="${line(peer)}" class="peer"/>` : ''}<polyline points="${line(values)}" class="me"/></svg>`;
}

const SERIES = [['ac_kw', 'AC power', 'kW', 0], ['dc_kw', 'DC power', 'kW', 0], ['heatsink_c', 'Heatsink', '°C', 1], ['fan_rpm', 'Fan', 'rpm', 0], ['dc_v', 'DC voltage', 'V', 0]];

// The panel's HTML. status: the run's view of the asset ({cls, note, hot}); peers come from the same case.
export function panelHTML(pack, data, { status = {}, inside = false } = {}) {
  const L = pack.latest, flags = pack.flags || [];
  const peers = Object.values(data.packs).filter((q) => q !== pack && q.kind === pack.kind);
  const staleAt = L.last_update ? pack.t.findIndex((t) => t > L.last_update) : -1;
  const runBad = status.cls === 'bad' || status.hot, fixed = status.cls === 'ok';
  const alert = fixed ? null : flags.find((f) => f.sev === 'bad') || flags[0] || (runBad ? { sev: 'bad', title: 'Fault', text: status.note } : null);
  const rows = SERIES.filter(([k]) => pack.series[k]).map(([k, name, unit, d]) => {
    const peerAvg = peers.length ? pack.series[k].map((_, i) => mean(peers.map((q) => q.series[k]?.[i]).filter((x) => typeof x === 'number'))) : null;
    const v = L[k] ?? pack.series[k].at(-1);
    return `<div class="sr"><div class="sk"><span>${name}</span><b class="mono">${num(v, d)}<small> ${unit}</small></b></div>${spark(pack.series[k], { peer: peerAvg, stale: staleAt > 0 ? staleAt : null })}</div>`;
  }).join('');
  const age = L.last_update && data.meta.as_of ? mins(data.meta.as_of, L.last_update) : 0;
  const ir = pack.ir;
  const hottest = ir ? Object.entries(ir.readings).filter(([, r]) => r.xy).sort((a, b) => (b[1].temp_c ?? 0) - (a[1].temp_c ?? 0))[0]?.[0] : null;
  const marks = ir ? Object.entries(ir.readings).map(([id, r]) => {
    const [gw, gh] = ir.grid;
    if (r.box) {
      const [x0, y0, x1, y1] = r.box;
      return `<i class="bx" style="left:${(100 * x0) / gw}%;top:${(100 * y0) / gh}%;width:${(100 * (x1 - x0)) / gw}%;height:${(100 * (y1 - y0)) / gh}%"><em>${esc(id)} max ${num(r.max_c)}°</em></i>`;
    }
    if (!r.xy) return '';
    const hot = id === hottest && hasHotSpot(pack);
    return `<i class="sp${hot ? ' hot' : ''}" style="left:${(100 * r.xy[0]) / gw}%;top:${(100 * r.xy[1]) / gh}%"><em>${esc(id)} ${num(r.temp_c)}°${hot ? ' HOT' : ''}</em></i>`;
  }).join('') : '';
  const cls = fixed ? 'ok' : alert ? alert.sev : 'ok';
  const label = fixed ? 'Fix verified' : runBad ? 'Fault' : alert ? 'Needs a look' : 'Normal';
  return `<header><div><h3>${esc(pack.id)}</h3><div class="kind">${esc((pack.label || '').replace(pack.id, '').trim() || pack.kind)}</div>
      <div class="facts mono">${[pack.block && `Block ${pack.block}`, pack.breaker && `breaker ${pack.breaker}`, pack.rating_kw && `${pack.rating_kw} kW`].filter(Boolean).map(esc).join(' · ')}</div></div>
      <span class="chip ${cls}">${label}</span><button class="x" aria-label="Close">×</button></header>
    ${alert ? `<section class="alert ${alert.sev}"><b>${esc(alert.title)}</b><p>${esc(alert.text)}</p>
      ${flags.filter((f) => f !== alert).map((f) => `<p class="also">${esc(f.title)}</p>`).join('')}
      <div class="ctas"><button class="cta" data-cta="work_order">Dispatch work order</button>${inside ? '<button class="cta ghost" data-cta="inside">Walk inside →</button>' : ''}</div></section>`
      : fixed ? `<section class="alert ok"><b>Fix verified</b><p>${esc(status.note || '')}</p></section>` : ''}
    <section><h4>SCADA <span class="mono">${hhmm(pack.t[0])}–${hhmm(L.t || data.meta.as_of)}${age > 5 ? ` · <span class="stale">stale ${age} min</span>` : ''}</span></h4>
      <div class="legend mono"><span><i class="me"></i>${esc(pack.id)}</span>${peers.length ? '<span><i class="peer"></i>peer avg</span>' : ''}${staleAt > 0 ? '<span><i class="stalebg"></i>frozen</span>' : ''}</div>
      ${rows}</section>
    ${pack.camera ? `<section><h4>Camera <span class="mono">${esc(pack.camera.id || '')} · ${hhmm(pack.camera.at)}</span></h4>
      <a href="${esc(pack.camera.src)}" target="_blank" class="shot"><img src="${esc(pack.camera.src)}" alt="${esc(pack.id)} camera"></a></section>` : ''}
    ${ir ? `<section><h4>Infrared <span class="mono">${esc(ir.id || '')} · ${hhmm(ir.at)}</span></h4>
      <div class="ir"><img src="${esc(ir.src)}" alt="${esc(pack.id)} infrared">${marks}</div>
      ${ir.span ? `<div class="irscale mono"><span>${num(ir.span[0])} °C</span><i></i><span>${num(ir.span[1])} °C</span></div>` : ''}</section>` : ''}`;
}
