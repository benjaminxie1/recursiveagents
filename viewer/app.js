'use strict';
// GridTwin evidence view. Reads runs/<id>/events.jsonl (contract C2) and nothing else from the run:
// never truth.jsonl. Live mode polls every second; ?replay=1 steps through a finished run.

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const short = (v, n = 140) => {
  const s = typeof v === 'string' ? v : JSON.stringify(v);
  return s && s.length > n ? s.slice(0, n - 1) + '…' : s ?? '';
};
const seqLink = (seq) => `<a class="seq" href="#ev-${seq}" data-seq="${seq}">#${seq}</a>`;

const params = new URLSearchParams(location.search);
const replay = params.get('replay') === '1';
let runId = params.get('run');
let events = [];   // everything fetched for this run
let cursor = 0;    // events applied; replay advances it, live keeps it at events.length
let rendered = 0;  // timeline rows already in the DOM
let playing = true, speed = 1, selected = null, timer = null;

// ---------- plant model: pins roll every sub-asset up to the equipment an operator would point at ----------
const ROWS = [45, 115, 185, 255];
const PINS = {};
ROWS.forEach((cy, i) => { PINS[`INV-${i + 1}`] = { x: 204, y: cy - 17 }; });
PINS['BESS-1'] = { x: 204, y: 308 };
PINS['XFMR-1'] = { x: 446, y: 166 };
PINS['POI-1'] = { x: 560, y: 168 };

const ASSET_RE = /\b(INV-\d|XFMR-1|BESS-1|POI-1|52-(?:I\d|M|B1)|CB-\d\.\d+|B\d)\b/g;
function rollup(a) {
  let m;
  if (!a) return null;
  if (PINS[a]) return a;
  if ((m = a.match(/^(?:B|52-I|CB-)(\d)/))) return `INV-${m[1]}`;
  if (a === '52-M') return 'XFMR-1';
  if (a === '52-B1') return 'BESS-1';
  return null;
}
function assetsIn(obj) {
  const s = typeof obj === 'string' ? obj : JSON.stringify(obj ?? '');
  return [...new Set([...s.matchAll(ASSET_RE)].map((m) => m[1]))];
}

// ---------- fold the event log into what each panel shows ----------
function derive(evs) {
  const S = { goal: null, dod: null, verdicts: {}, nVerdicts: 0, pages: [], final: null, judge: null,
    findings: [], lessons: [], usage: { prompt: 0, cached: 0, completion: 0, usd: 0, n: 0 }, assets: {}, photos: [] };
  const touch = (raw, e, status, note) => {
    const id = rollup(raw);
    if (!id) return null;
    const a = (S.assets[id] ??= { id, status: 'idle', note: '', parts: new Set(), seqs: [], verdicts: [], failedCheck: false });
    a.parts.add(raw);
    if (!a.seqs.includes(e.seq)) a.seqs.push(e.seq);
    if (status && (status !== 'inspected' || a.status === 'idle')) a.status = status;
    if (note) a.note = note;
    return a;
  };
  const checkById = (id) => (S.dod?.data?.checks || []).find((c) => (c.id ?? c.check_id) === id);
  for (const e of evs) {
    const d = e.data || {};
    switch (e.type) {
      case 'goal': S.goal = e; break;
      case 'dod': S.dod = e; break;
      case 'tool_call':
        assetsIn(d.args).forEach((a) => touch(a, e, 'inspected', `${d.name} ${a}`));
        break;
      case 'tool_result': {
        const r = d.result || {};
        if (r.error) break;
        if (d.name === 'get_camera_view') {
          S.photos.push({ seq: e.seq, ...r });
          touch(r.asset, e, 'inspected', `photo ${r.viewpoint} (${r.mode})`);
        } else if (d.name === 'dispatch_tech') {
          touch(r.asset, e, 'work', `${r.work_order} · ${r.ticket}, eta ${r.eta}`);
        } else if (d.name === 'run_diagnostic') {
          touch(r.asset, e, r.reachable === false ? 'fault' : null, `${r.test}: ${r.reachable === false ? 'unreachable' : 'completed'}`);
        } else if (d.name === 'operate' && r.asset) {
          const off = r.state === 'open' || r.isolated === true;
          const what = Object.entries(r).filter(([k]) => k !== 'asset' && k !== 'now').map(([k, v]) => `${k} ${v}`).join(', ');
          touch(r.asset, e, off ? 'work' : null, `${r.asset}: ${what}`);
        }
        break;
      }
      case 'gate':
        if (d.route !== 'proceed') assetsIn(d.proposed).forEach((a) => touch(a, e, null, `safety gate ${d.route}: ${d.reason}`));
        break;
      case 'page':
        S.pages.push(e);
        assetsIn(d.brief).forEach((a) => touch(a, e, 'fault', `human paged (${d.severity})`));
        break;
      case 'verdict':
        S.verdicts[d.check_id] = e;
        S.nVerdicts++;
        assetsIn([checkById(d.check_id), d.evidence]).forEach((raw) => {
          const a = touch(raw, e, d.pass ? 'verified' : 'fault', `${d.check_id} ${d.pass ? 'passed' : 'failed'}`);
          if (a) { a.failedCheck = !d.pass; a.verdicts.push(e); }
        });
        break;
      case 'final':
        S.final = e;
        if (d.status === 'DONE') {
          for (const a of Object.values(S.assets)) {
            if (a.status === 'work' || (a.status === 'fault' && !a.failedCheck)) { a.status = 'done'; a.note = 'closed out by final DONE'; }
          }
        }
        break;
      case 'judge': S.judge = e; break;
      case 'finding': S.findings.push(e); break;
      case 'lesson': S.lessons.push(e); break;
      case 'usage': {
        const u = S.usage;
        u.prompt += d.prompt || 0; u.cached += d.cached || 0; u.completion += d.completion || 0;
        u.usd += d.usd ?? d.cost_usd ?? 0; u.n++;
        break;
      }
    }
  }
  return S;
}

// ---------- panels ----------
const FINAL_COPY = {
  DONE: 'DONE',
  ESCALATED: 'ESCALATED: a human was paged and the fault is not fixed',
  NO_ACTION: 'NO ACTION: nothing was wrong',
};

function renderBanners(S) {
  const out = [];
  const u = S.usage;
  const usage = u.n ? `<span class="usage">${(u.prompt + u.completion).toLocaleString()} tokens (${u.cached.toLocaleString()} cached)${u.usd ? ` · $${u.usd.toFixed(4)}` : ''}</span>` : '';
  if (S.final) {
    const d = S.final.data;
    const vs = Object.values(S.verdicts);
    const proof = vs.length
      ? `Verifier: ${vs.filter((v) => v.data.pass).length}/${vs.length} checks passing on their latest verdict`
      : 'No verifier verdicts in this log';
    out.push(`<div class="banner ${esc(d.status)}">${usage}${esc(FINAL_COPY[d.status] || d.status)}
      <small>${esc(d.summary)} · ${proof} · ${seqLink(S.final.seq)}</small></div>`);
  } else if (usage) {
    out.push(`<div class="banner NO_ACTION">${usage}Run in progress</div>`);
  }
  for (const p of S.pages) {
    out.push(`<div class="banner page">Human paged (${esc(p.data.severity)})<small>${esc(p.data.brief)} · ${esc(p.t_sim)} · ${seqLink(p.seq)}</small></div>`);
  }
  $('#banners').innerHTML = out.join('');
}

function renderGoal(S) {
  $('#goal').innerHTML = S.goal
    ? `Operator goal: <q>${esc(S.goal.data.text)}</q> <span class="muted">${esc(S.goal.data.scenario_id)} · ${esc(S.goal.t_sim)}</span>`
    : '<span class="muted">Waiting for the operator goal…</span>';
}

function renderPlant(S) {
  const g = [];
  ROWS.forEach((cy, i) => {
    const n = i + 1;
    g.push(`<rect class="sym" x="16" y="${cy - 17}" width="80" height="34" rx="4"/><text x="24" y="${cy - 1}">B${n}</text><text class="lbl" x="24" y="${cy + 11}">6 combiners</text>`);
    g.push(`<path class="wire" d="M96 ${cy}H140M204 ${cy}H240M254 ${cy}H300"/>`);
    g.push(`<rect class="sym" x="140" y="${cy - 17}" width="64" height="34" rx="4"/><text x="148" y="${cy + 4}">INV-${n}</text>`);
    g.push(`<rect class="sym" x="240" y="${cy - 7}" width="14" height="14"/><text class="lbl" x="232" y="${cy - 12}">52-I${n}</text>`);
  });
  g.push(`<path class="wire" d="M300 45V325M204 325H240M254 325H300M300 185H333M347 185H394M446 185H500M560 185H612"/>`);
  g.push(`<rect class="sym" x="140" y="308" width="64" height="34" rx="4"/><text x="146" y="329">BESS-1</text>`);
  g.push(`<rect class="sym" x="240" y="318" width="14" height="14"/><text class="lbl" x="232" y="313">52-B1</text>`);
  g.push(`<text class="lbl" x="306" y="40">MV bus</text>`);
  g.push(`<rect class="sym" x="333" y="178" width="14" height="14"/><text class="lbl" x="328" y="172">52-M</text>`);
  g.push(`<circle class="sym" cx="410" cy="185" r="16"/><circle class="sym" cx="430" cy="185" r="16" fill-opacity="0.6"/><text x="396" y="222">XFMR-1</text>`);
  g.push(`<rect class="sym" x="500" y="168" width="60" height="34" rx="4"/><text x="510" y="189">POI-1</text><text class="lbl" x="590" y="178">grid</text>`);
  for (const [id, p] of Object.entries(PINS)) {
    const st = S.assets[id]?.status || 'idle';
    g.push(`<g class="pin ${id === selected ? 'sel' : ''}" data-asset="${id}">
      <circle class="halo" cx="${p.x}" cy="${p.y}" r="12"/>
      <circle cx="${p.x}" cy="${p.y}" r="8" style="fill:var(--${st})"><title>${id}: ${st}</title></circle></g>`);
  }
  $('#plantSvg').innerHTML = `<svg viewBox="0 0 640 360" role="img" aria-label="Plant one-line diagram with status pins">${g.join('')}</svg>`;
}

function describe(e) {
  const d = e.data || {};
  switch (e.type) {
    case 'goal': return `Goal: “${esc(d.text)}”`;
    case 'dod': return `Definition of done: ${(d.checks || []).length} checks (${[...new Set((d.checks || []).map((c) => c.kind))].map(esc).join(', ')})`;
    case 'tool_call': return `${esc(d.name)}(${esc(short(d.args, 90))})`;
    case 'tool_result': {
      const r = d.result || {};
      if (r.error) return `← ${esc(d.name)}: <span class="err">${esc(short(r.error, 120))}</span>`;
      const img = r.image_path ? `<img class="thumb" src="/${esc(r.image_path)}" alt="${esc(r.viewpoint)}">` : '';
      return `← ${esc(d.name)}: ${esc(short(r, 150))}${img}`;
    }
    case 'gate': return `Safety gate on ${esc(d.proposed?.name ?? short(d.proposed, 60))}: <b>${esc(d.route)}</b> (unsafe ${esc(d.unsafe)}) ${esc(d.reason)}`;
    case 'claim': return `Claim: <b>${esc(d.status)}</b>, ${esc(d.summary)}`;
    case 'verdict': return `Verifier ${d.pass ? '<b>PASS</b>' : '<b>FAIL</b>'} ${esc(d.check_id)} <span class="kind">${esc(d.kind)}</span>${esc(short(d.evidence, 160))}`;
    case 'supervisor': return `Supervisor: <b>${esc(d.decision)}</b>, ${esc(d.reason)}`;
    case 'page': return `Human paged (${esc(d.severity)}): ${esc(d.brief)}`;
    case 'finding': return `Board post: ${esc(d.text)} ${(d.evidence || []).map(seqLink).join('')}`;
    case 'lesson': return `Lesson ${esc(d.op)}: ${esc(short(d.text, 160))}`;
    case 'final': return `Final: <b>${esc(d.status)}</b>, ${esc(d.summary)}`;
    case 'judge': return 'Bench judge scored the run (see footer)';
    case 'usage': return `${esc(d.role)}: ${d.prompt} prompt (${d.cached} cached), ${d.completion} out`;
    default: return esc(short(d));
  }
}

function renderTimeline() {
  const ol = $('#timeline');
  if (cursor < rendered) { ol.innerHTML = ''; rendered = 0; }
  if (cursor === rendered) return;
  const frag = document.createDocumentFragment();
  for (const e of events.slice(rendered, cursor)) {
    const li = document.createElement('li');
    li.id = `ev-${e.seq}`;
    const minor = ['tool_call', 'tool_result', 'usage'].includes(e.type) || (e.type === 'gate' && e.data.route === 'proceed');
    const ok = e.type === 'verdict' ? (e.data.pass ? 'ok' : 'bad') : e.type === 'gate' && e.data.route !== 'proceed' ? 'bad' : '';
    li.className = `t-${e.type} ${ok} ${minor ? 'minor tool' : ''}`;
    li.innerHTML = `<span class="meta">#${e.seq} ${esc((e.t_sim || '').slice(11))} ${esc(e.actor)}</span>${describe(e)}`;
    frag.appendChild(li);
  }
  ol.appendChild(frag);
  rendered = cursor;
  ol.scrollTop = ol.scrollHeight;
}

function renderDod(S) {
  const checks = S.dod?.data?.checks;
  if (!checks) {
    $('#dod').innerHTML = '<p class="empty">No definition of done in this log. In a full-loop run the planner emits it as a <code>dod</code> event before any worker acts, and the verifier fills in each check here.</p>';
    return;
  }
  let pass = 0;
  const rows = checks.map((c) => {
    const id = c.id ?? c.check_id;
    const v = S.verdicts[id];
    const st = !v ? 'pending' : v.data.pass ? 'pass' : 'fail';
    if (st === 'pass') pass++;
    const text = c.description ?? c.desc ?? c.text ?? c.claim ?? c.what ?? short(Object.fromEntries(Object.entries(c).filter(([k]) => !['id', 'check_id', 'kind'].includes(k))), 140);
    const ev = v ? `<div class="ev">${seqLink(v.seq)}${esc(short(v.data.evidence, 180))}</div>` : '';
    return `<li class="${st}"><span class="mark">${st === 'pass' ? '✓' : st === 'fail' ? '✗' : '·'}</span><div><span class="kind">${esc(c.kind)}</span><b>${esc(id)}</b> ${esc(text)}${ev}</div></li>`;
  });
  const pct = checks.length ? Math.round((100 * pass) / checks.length) : 0;
  $('#dod').innerHTML = `<div class="muted">Requirements verified: ${pass}/${checks.length} (${pct}%) · ${seqLink(S.dod.seq)}</div>
    <div class="bar"><i style="width:${pct}%"></i></div><ul class="checks">${rows.join('')}</ul>`;
}

function renderAsset(S) {
  const el = $('#assetPanel');
  if (!selected) { el.innerHTML = '<p class="empty">Click a pin to see the photos the agents took of that equipment and the checks that touch it.</p>'; return; }
  const a = S.assets[selected];
  const photos = S.photos.filter((p) => rollup(p.asset) === selected);
  const ph = photos.length
    ? `<div class="photos">${photos.map((p) => `<figure><a href="/${esc(p.image_path)}" target="_blank"><img src="/${esc(p.image_path)}" alt="${esc(p.viewpoint)} ${esc(p.mode)}"></a>
        <figcaption>${seqLink(p.seq)}${esc(p.viewpoint)} · ${esc(p.mode)} · ${esc(p.captured_at)}</figcaption></figure>`).join('')}</div>`
    : '<p class="empty">The agents have not looked at this equipment through a camera in this run.</p>';
  const vs = a?.verdicts.length
    ? `<ul class="acts">${a.verdicts.map((v) => `<li>${seqLink(v.seq)}${v.data.pass ? 'PASS' : 'FAIL'} ${esc(v.data.check_id)}: ${esc(short(v.data.evidence, 140))}</li>`).join('')}</ul>`
    : '<p class="empty">No verifier verdicts touch it yet.</p>';
  const acts = a?.seqs.length
    ? `<ul class="acts">${a.seqs.map((s) => { const e = events.find((x) => x.seq === s); return `<li>${seqLink(s)}${describe(e)}</li>`; }).join('')}</ul>`
    : '';
  el.innerHTML = `<h3>${esc(selected)} <span class="muted"><i class="dot ${a?.status || 'idle'}"></i>${esc(a?.status || 'untouched')}${a?.note ? ` · ${esc(a.note)}` : ''}</span></h3>
    ${a && a.parts.size > 1 ? `<div class="muted">includes ${[...a.parts].map(esc).join(', ')}</div>` : ''}
    <h2>Photos</h2>${ph}<h2>Verifier</h2>${vs}${acts ? `<h2>Actions</h2>${acts}` : ''}`;
}

function renderBoard(S) {
  $('#board').innerHTML = S.findings.length
    ? S.findings.map((f) => `<div class="post"><span class="who">${esc(f.data.worker || f.actor)} · ${seqLink(f.seq)}</span><div>${esc(f.data.text)}</div>
        <div class="ev">evidence: ${(f.data.evidence || []).map(seqLink).join('') || 'none cited'}</div></div>`).join('')
    : '<p class="empty">No findings posted. Workers share what they learn here during a full-loop run; the verifier never reads this board.</p>';
}

function renderMemory(S) {
  if (!S.lessons.length) {
    $('#memory').innerHTML = '<p class="empty">No lessons read or written in this run. Only verified outcomes become memory.</p>';
    return;
  }
  const superseded = new Set(S.lessons.filter((l) => l.data.op === 'supersede').map((l) => l.data.supersedes ?? l.data.id));
  $('#memory').innerHTML = S.lessons.map((l) => {
    const d = l.data;
    const passing = events.slice(0, cursor).filter((e) => e.type === 'verdict' && e.data.pass && e.seq < l.seq).length;
    const from = d.op === 'write'
      ? `written by this run (${esc(runId)}), backed by ${passing} passing verdict${passing === 1 ? '' : 's'}`
      : d.run || d.source_run ? `from run ${esc(d.run || d.source_run)}` : '';
    const vs = d.verdicts ? ` · verdicts ${esc(short(d.verdicts, 80))}` : '';
    return `<div class="post lesson ${superseded.has(d.id) && d.op !== 'supersede' ? 'superseded' : ''}">
      <span class="op">${esc(d.op)}</span><span class="who">${esc(d.id)} · ${seqLink(l.seq)}</span>
      <div class="txt">${esc(d.text)}</div><div class="ev">${from}${vs}</div></div>`;
  }).join('');
}

function renderJudge(S) {
  if (!S.judge) { $('#judge').innerHTML = ''; return; }
  const m = S.judge.data.metrics || {};
  const rows = Object.entries(m).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(short(v, 160))}</td></tr>`).join('');
  $('#judge').innerHTML = `<details><summary>Bench judge: scored after the run from hidden sim truth.
    No agent, no verifier and none of the panels above ever see it.</summary><table>${rows}</table></details>`;
}

function render() {
  const S = derive(events.slice(0, cursor));
  renderBanners(S); renderGoal(S); renderPlant(S); renderAsset(S); renderDod(S);
  renderTimeline(); renderBoard(S); renderMemory(S); renderJudge(S);
  const last = events[cursor - 1];
  $('#cursor').textContent = `event ${cursor}/${events.length}${last?.t_sim ? ` · sim ${last.t_sim}` : ''}`;
}

// ---------- data + modes ----------
const runPath = (id) => id.split('/').map(encodeURIComponent).join('/');
const runUrl = (id, rep) => `?run=${encodeURIComponent(id).replace(/%2F/g, '/')}${rep ? '&replay=1' : ''}`;

async function fetchEvents() {
  const res = await fetch(`/runs/${runPath(runId)}/events.jsonl`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${runId}`);
  const out = [];
  for (const line of (await res.text()).split('\n')) {
    if (!line.trim()) continue;
    try { out.push(JSON.parse(line)); } catch { break; } // a half-written last line waits for the next poll
  }
  return out;
}

async function poll() {
  try {
    const evs = await fetchEvents();
    if (evs.length !== events.length) { events = evs; cursor = events.length; render(); }
  } catch (err) { $('#cursor').textContent = `can't read run: ${err.message}`; }
  setTimeout(poll, 1000);
}

const MAJOR = new Set(['goal', 'dod', 'claim', 'verdict', 'supervisor', 'page', 'final', 'finding', 'lesson']);
function tick() {
  clearTimeout(timer);
  if (playing && cursor < events.length) { cursor++; render(); }
  if (cursor >= events.length) { playing = false; $('#btnPlay').textContent = '▶'; return; }
  if (playing) timer = setTimeout(tick, (MAJOR.has(events[cursor - 1]?.type) ? 1500 : 450) / speed);
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
  await loadRuns();
  if (runId) $('#wallLink').href = `wall.html${runUrl(runId, replay)}`;
  const tag = $('#modeTag');
  tag.textContent = replay ? 'replay · switch to live' : '● live · switch to replay';
  tag.className = `tag ${replay ? '' : 'live'}`;
  tag.style.cursor = 'pointer';
  tag.onclick = () => { if (runId) location.search = runUrl(runId, !replay); };
  $('#showTools').onchange = (e) => document.body.classList.toggle('hide-tools', !e.target.checked);
  $('#plantSvg').onclick = (e) => {
    const pin = e.target.closest('.pin');
    if (pin) { selected = pin.dataset.asset === selected ? null : pin.dataset.asset; render(); }
  };
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a.seq');
    if (!a) return;
    e.preventDefault();
    const li = document.getElementById(`ev-${a.dataset.seq}`);
    if (!li) return;
    if (li.classList.contains('tool')) { $('#showTools').checked = true; document.body.classList.remove('hide-tools'); }
    li.scrollIntoView({ block: 'center', behavior: 'smooth' });
    li.classList.add('flash');
    setTimeout(() => li.classList.remove('flash'), 1500);
  });
  if (!runId) {
    $('#goal').innerHTML = '<span class="muted">No runs yet. Start one, for example <code>python -m gridtwin.bench --agent scripted_correct --scenario s06_stale_scada</code>, then reload.</span>';
    return;
  }
  if (!replay) { poll(); return; }
  $('#replayCtl').hidden = false;
  try { events = await fetchEvents(); } catch (err) { $('#cursor').textContent = `can't read run: ${err.message}`; return; }
  render();
  $('#btnPlay').onclick = () => { playing = !playing; $('#btnPlay').textContent = playing ? '⏸' : '▶'; if (playing && cursor >= events.length) cursor = 0; tick(); };
  $('#btnStep').onclick = () => { playing = false; $('#btnPlay').textContent = '▶'; if (cursor < events.length) { cursor++; render(); } };
  $('#btnRestart').onclick = () => { cursor = 0; render(); playing = true; $('#btnPlay').textContent = '⏸'; tick(); };
  $('#speed').oninput = (e) => { speed = +e.target.value; $('#speedVal').textContent = `${speed}×`; if (playing) tick(); };
  tick();
}

main();
