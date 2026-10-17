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
// The planner's dod groups checks by outcome ({DONE: [...], ESCALATED: [...], NO_ACTION: [...]});
// older scripted logs carry one flat list, read here as a single branch.
const dodBranches = (dod) => {
  const c = dod?.data?.checks;
  return !c ? null : Array.isArray(c) ? { checks: c } : c;
};
const dodChecks = (dod) => Object.values(dodBranches(dod) || {}).flat();
// The branch the team is held to: the final status, else the latest claim, else DONE.
function activeBranch(S) {
  const branches = dodBranches(S.dod);
  if (!branches) return null;
  const names = Object.keys(branches);
  const want = S.final?.data?.status ?? S.claim?.data?.status;
  const name = names.includes(want) ? want : names.includes('DONE') ? 'DONE' : names[0];
  const checks = branches[name] || [];
  const pass = checks.filter((c) => S.verdicts[c.id ?? c.check_id]?.data.pass).length;
  return { name, names, branches, checks, pass, want };
}

const PLANT_KW = 5000; // gridtwin/sim/plant.yaml rating_kwac
// What an operator reads in place of the loop's terminal states.
const STATUS_WORD = { DONE: 'Fixed and verified', ESCALATED: 'Needs a human', NO_ACTION: 'Nothing wrong' };
function incidentState(S) {
  if (S.final) return { cls: S.final.data.status, word: STATUS_WORD[S.final.data.status] || S.final.data.status };
  if (S.pages.length) return { cls: 'ESCALATED', word: 'Human paged' };
  if (S.claim) return { cls: 'working', word: 'Fix claimed, verifier checking' };
  return { cls: 'working', word: S.goal ? 'Agents working' : 'Waiting for a goal' };
}
// Camera frames live in the run's own frames/ folder; resolve them against the run being viewed,
// so a run folder still shows its photos after it is moved (e.g. into runs/demo/).
const frameUrl = (path) => `/runs/${runPath(runId)}/frames/${encodeURIComponent(String(path).split('/').pop())}`;
const hhmm = (t) => (t ? String(t).slice(11, 16) : '');
const kw = (v) => `${Math.round(v).toLocaleString()} kW`;

const params = new URLSearchParams(location.search);
const replay = params.get('replay') === '1';
let view = params.get('view') === 'contractor' ? 'contractor' : 'operator';
let runId = params.get('run');
let events = [];   // everything fetched for this run
let cursor = 0;    // events applied; replay advances it, live keeps it at events.length
let rendered = 0;  // timeline rows already in the DOM
let playing = true, speed = 1, selected = null, timer = null;

// The breaker a technician locks out before working on each piece of equipment.
function isolator(a) {
  const m = String(a || '').match(/^(?:INV-|B|CB-|52-I)(\d)/);
  if (m) return `52-I${m[1]}`;
  return { 'BESS-1': '52-B1', 'XFMR-1': '52-M', '52-B1': '52-B1', '52-M': '52-M' }[a] || null;
}
const humanize = (s) => { const t = String(s || '').replace(/_/g, ' '); return t.charAt(0).toUpperCase() + t.slice(1); };
const minsBetween = (a, b) => Math.round((new Date(b) - new Date(a)) / 60000);

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
  const S = { goal: null, dod: null, claim: null, verdicts: {}, nVerdicts: 0, pages: [], final: null, judge: null,
    findings: [], lessons: [], usage: { prompt: 0, cached: 0, completion: 0, usd: 0, n: 0 }, assets: {}, photos: [],
    poi: { first: null, last: null }, actions: [], holds: [], now: null };
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
  const checkById = (id) => dodChecks(S.dod).find((c) => (c.id ?? c.check_id) === id);
  for (const e of evs) {
    const d = e.data || {};
    if (e.t_sim) S.now = e.t_sim;
    switch (e.type) {
      case 'goal': S.goal = e; break;
      case 'dod': S.dod = e; break;
      case 'claim': S.claim = e; break;
      case 'tool_call':
        assetsIn(d.args).forEach((a) => touch(a, e, 'inspected', `${d.name} ${a}`));
        break;
      case 'tool_result': {
        const r = d.result || {};
        if (r.error) break;
        if (d.name === 'read_telemetry' && r.asset === 'POI-1' && r.points?.export_kw?.length) {
          const pts = r.points.export_kw;
          S.poi.first ??= pts[0];
          if (!S.poi.last || pts[pts.length - 1][0] >= S.poi.last[0]) S.poi.last = pts[pts.length - 1];
        }
        if (d.name === 'operate' || d.name === 'dispatch_tech') S.actions.push({ seq: e.seq, t: e.t_sim, name: d.name, r });
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
        if (d.route !== 'proceed') S.holds.push(e);
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

// ---------- operator view: plant status, the incident, and the photo evidence ----------
function renderStatus(S) {
  const tile = (label, value, sub, cls = '') => `<div class="tile ${cls}"><span class="label">${label}</span><b>${value}</b><span class="sub">${sub}</span></div>`;
  const st = incidentState(S);
  const out = [];
  const p = S.poi.last;
  out.push(p
    ? tile('Plant output', kw(p[1]), `of ${kw(PLANT_KW)} at the grid meter · ${hhmm(p[0])}${S.poi.first && Math.abs(S.poi.first[1] - p[1]) > 25 ? ` · was ${kw(S.poi.first[1])} at ${hhmm(S.poi.first[0])}` : ''}`)
    : tile('Plant output', '–', 'no grid-meter reading yet'));
  out.push(tile('Incident', esc(st.word), S.goal ? `opened ${hhmm(S.goal.t_sim)}${S.final ? ` · closed ${hhmm(S.final.t_sim)}` : ''}` : '', `state ${esc(st.cls)}`));
  const b = activeBranch(S);
  out.push(b
    ? tile('Independently verified', `${b.pass} of ${b.checks.length} checks`, `for ${esc(STATUS_WORD[b.name] || b.name).toLowerCase()}, re-run by a separate verifier model`)
    : tile('Independently verified', '–', 'no definition of done yet'));
  const m = S.judge?.data?.metrics;
  out.push(m && m.energy_lost_mwh != null
    ? tile('Lost to this fault', `${m.energy_lost_mwh.toFixed(2)} MWh · $${Math.round(m.revenue_lost_usd).toLocaleString()}`, 'scored after the run from the simulator’s hidden truth, at $45/MWh')
    : tile('Lost to this fault', '–', 'scored once the run ends'));
  const u = S.usage;
  out.push(tile('Agent cost', u.usd ? `$${u.usd.toFixed(2)}` : '–', u.n ? `${(u.prompt + u.completion).toLocaleString()} tokens on Crusoe` : 'no model calls yet'));
  $('#status').innerHTML = out.join('');
}

function actionText(a) {
  const r = a.r;
  if (a.name === 'dispatch_tech') return `Technician dispatched to ${esc(r.asset)}: ${esc(r.work_order || '')}${r.ticket ? ` (${esc(r.ticket)}${r.eta ? `, ETA ${esc(hhmm(r.eta) || r.eta)}` : ''})` : ''}`;
  if (r.state) return `${r.state === 'open' ? 'Opened' : r.state === 'closed' ? 'Closed' : 'Set'} ${esc(r.asset)}${['open', 'closed'].includes(r.state) ? '' : ` to ${esc(r.state)}`}`;
  return `${esc(r.asset)}: ${esc(Object.entries(r).filter(([k]) => !['asset', 'now'].includes(k)).map(([k, v]) => `${k} ${v}`).join(', '))}`;
}

function renderIncident(S) {
  if (!S.goal) { $('#incident').innerHTML = '<p class="empty">Waiting for the operator’s goal…</p>'; return; }
  const st = incidentState(S);
  const src = S.final || S.claim;
  const b = activeBranch(S);
  const sec = (h, body) => `<h3>${h}</h3>${body}`;
  const parts = [`<div class="badge ${esc(st.cls)}">${esc(st.word)}</div>`,
    `<p class="ask">You asked: <q>${esc(S.goal.data.text)}</q></p>`];
  parts.push(sec('What the agents found and did', src
    ? `<p>${esc(src.data.summary)} ${seqLink(src.seq)}</p>`
    : '<p class="empty">Still investigating. Findings appear on the team board below as workers post them.</p>'));
  parts.push(sec('Actions on the plant', S.actions.length
    ? `<ul class="acts">${S.actions.map((a) => `<li>${seqLink(a.seq)}<span class="t">${esc(hhmm(a.t))}</span> ${actionText(a)}</li>`).join('')}</ul>`
    : '<p class="empty">None. No breaker, setpoint or work order was changed.</p>'));
  if (S.holds.length) {
    parts.push(sec(`Safety gate stopped ${S.holds.length} action${S.holds.length === 1 ? '' : 's'}`,
      `<ul class="acts">${S.holds.map((g) => `<li>${seqLink(g.seq)}<b>${esc(g.data.proposed?.tool || '')} ${esc(g.data.proposed?.asset || '')}</b>: ${esc(g.data.reason)}</li>`).join('')}</ul>`));
  }
  const needs = S.pages.length
    ? `<ul class="acts">${S.pages.map((p) => `<li class="page">${seqLink(p.seq)}<span class="t">${esc(hhmm(p.t_sim))}</span> ${esc(p.data.brief)}</li>`).join('')}</ul>`
    : S.final?.data.status === 'DONE'
      ? `<p>Nothing. The verifier re-ran ${b ? `${b.pass} of ${b.checks.length}` : 'its'} checks itself before the incident was closed.</p>`
      : S.final ? '<p>Nothing.</p>' : '<p class="empty">Nothing yet. If the agents get stuck or a fix can’t be verified, they page you here.</p>';
  parts.push(sec('Needs you', needs));
  $('#incident').innerHTML = parts.join('');
}

function renderEvidence(S) {
  if (!S.photos.length) {
    $('#evidence').innerHTML = `<p class="empty">${S.final
      ? 'The agents didn’t use a camera in this incident: the fault showed up in telemetry, so the proof is in the verified checks below.'
      : 'No photos yet. The inspector photographs equipment with the site’s fixed cameras when a fault might be visible.'}</p>`;
    return;
  }
  const notes = (seq) => S.findings.filter((f) => (f.data.evidence || []).some((x) => x === seq || x === seq - 1));
  const byAsset = {};
  for (const p of S.photos) (byAsset[p.asset] ??= []).push(p);
  $('#evidence').innerHTML = Object.entries(byAsset).map(([asset, ps]) => {
    const vs = Object.values(S.verdicts).filter((v) => v.data.kind === 'visual' && assetsIn([v.data.evidence, v.data.check_id]).includes(asset));
    const verdict = vs.map((v) => `<div class="vv ${v.data.pass ? 'pass' : 'fail'}">${seqLink(v.seq)}Verifier ${v.data.pass ? 'confirmed' : 'rejected'} on its own camera: ${esc(short(v.data.evidence, 160))}</div>`).join('');
    return `<div class="asset-shots"><h3>${esc(asset)}</h3><div class="shots">${ps.map((p) => {
      const n = notes(p.seq).map((f) => `<div class="note">${seqLink(f.seq)}<b>${esc(f.data.worker || f.actor)}:</b> ${esc(short(f.data.text, 220))}</div>`).join('');
      return `<figure><a href="${frameUrl(p.image_path)}" target="_blank"><img src="${frameUrl(p.image_path)}" alt="${esc(p.viewpoint)} ${esc(p.mode)}" loading="lazy"></a>
        <figcaption>${seqLink(p.seq)}${esc(p.viewpoint)} · ${esc(p.mode)} · ${esc(hhmm(p.captured_at))}</figcaption>${n || '<div class="note muted">No finding cites this photo.</div>'}</figure>`;
    }).join('')}</div>${verdict}</div>`;
  }).join('');
}

// ---------- contractor view: the field work orders the agents dispatched, with lockout and site evidence ----------
function jobsOf(S) {
  return S.actions.filter((a) => a.name === 'dispatch_tech').map((a) => {
    const r = a.r, pin = rollup(r.asset), brk = isolator(r.asset);
    const done = S.now && r.eta && S.now >= r.eta;
    const ops = S.actions.filter((x) => x.name === 'operate' && x.r.asset === brk);
    const opened = ops.filter((x) => x.seq < a.seq && x.r.state === 'open').pop();
    const reclosed = opened && ops.find((x) => x.seq > a.seq && x.r.state === 'closed');
    const touches = (obj) => assetsIn(obj).some((x) => x === r.asset || (pin && rollup(x) === pin));
    return {
      a, r, pin, brk, done, opened, reclosed,
      left: !done && S.now && r.eta ? Math.max(0, minsBetween(S.now, r.eta)) : null,
      refused: S.holds.filter((g) => g.seq < a.seq && g.data.proposed?.asset === r.asset),
      why: S.findings.filter((f) => f.seq < a.seq && touches(f.data.text)).slice(-2),
      photos: S.photos.filter((p) => rollup(p.asset) === pin),
      after: Object.values(S.verdicts).filter((v) => v.seq > a.seq && touches([checkOf(S, v.data.check_id), v.data.evidence])),
    };
  });
}
const checkText = (c) => (c ? c.description ?? c.desc ?? c.text ?? '' : '');
const checkOf = (S, id) => dodChecks(S.dod).find((c) => (c.id ?? c.check_id) === id);

function renderContractor(S) {
  const jobs = jobsOf(S);
  const tile = (label, value, sub, cls = '') => `<div class="tile ${cls}"><span class="label">${label}</span><b>${value}</b><span class="sub">${sub}</span></div>`;
  const open = jobs.filter((j) => !j.done).length;
  const locked = [...new Set(S.actions.filter((x) => x.name === 'operate' && x.r.state).map((x) => x.r.asset))]
    .filter((b) => S.actions.filter((x) => x.name === 'operate' && x.r.asset === b).pop().r.state === 'open');
  const strip = [
    tile('Work orders', jobs.length ? `${open} open · ${jobs.length - open} complete` : 'None', jobs.length ? `dispatched by the operations agents for ${S.goal ? 'this incident' : 'this run'}` : 'nothing dispatched yet'),
    tile('Locked out now', locked.length ? locked.map(esc).join(', ') : 'None', locked.length ? 'breakers open for field work: treat downstream as isolated' : 'every breaker the agents touched is closed'),
    tile('Site time', S.now ? esc(hhmm(S.now)) : '–', S.now ? `simulated plant clock · ${esc(S.now.slice(0, 10))}` : 'no events yet'),
  ];
  const card = (j) => {
    const r = j.r;
    const state = j.done ? ['DONE', `Complete at ${esc(hhmm(r.eta))}`] : ['working', `In progress${j.left != null ? ` · ${j.left} min left` : ''}`];
    const safety = [];
    if (r.needs_deenergized) {
      safety.push(j.opened
        ? `<li class="ok">Locked out: <b>${esc(j.brk)}</b> opened at ${esc(hhmm(j.opened.t))} before dispatch ${seqLink(j.opened.seq)}</li>`
        : `<li class="bad">De-energize first: <b>${esc(j.brk || 'the isolating breaker')}</b> must be open. No lockout on record. Do not start.</li>`);
      if (j.reclosed) safety.push(`<li>Re-energized: ${esc(j.brk)} closed at ${esc(hhmm(j.reclosed.t))} after the work ${seqLink(j.reclosed.seq)}</li>`);
      else if (j.opened) safety.push(`<li>${esc(j.brk)} stays open until the operator restores it.</li>`);
    } else {
      safety.push('<li>Work can be done with the equipment energized. Follow standard site PPE.</li>');
    }
    j.refused.forEach((g) => safety.push(`<li class="bad">Safety gate refused an earlier dispatch: ${esc(g.data.reason)} ${seqLink(g.seq)}</li>`));
    const why = j.why.length
      ? j.why.map((f) => `<p>${seqLink(f.seq)}<b>${esc(f.data.worker || f.actor)}:</b> ${esc(short(f.data.text, 260))}</p>`).join('')
      : '<p class="empty">No finding names this equipment before the dispatch.</p>';
    const photos = j.photos.length
      ? `<div class="shots">${j.photos.map((p) => `<figure><a href="${frameUrl(p.image_path)}" target="_blank"><img src="${frameUrl(p.image_path)}" alt="${esc(p.viewpoint)} ${esc(p.mode)}" loading="lazy"></a>
          <figcaption>${seqLink(p.seq)}${esc(p.viewpoint)} · ${esc(p.mode)} · ${esc(hhmm(p.captured_at))}</figcaption></figure>`).join('')}</div>`
      : '<p class="empty">No site photos of this equipment yet.</p>';
    const after = j.after.length
      ? `<ul class="acts">${j.after.map((v) => `<li class="${v.data.pass ? 'ok' : 'bad'}">${seqLink(v.seq)}${v.data.pass ? 'Passed' : 'Failed'} <b>${esc(v.data.check_id)}</b> <span class="kind">${esc(v.data.kind)}</span>${esc(checkText(checkOf(S, v.data.check_id)))}</li>`).join('')}</ul>`
      : `<p class="empty">${j.done ? 'Not re-checked yet. The verifier reads fresh plant data before the incident closes.' : 'Checked by the verifier once the work completes.'}</p>`;
    return `<article class="card job">
      <header><span class="wo">${esc(r.ticket)}</span><h3>${esc(humanize(r.work_order))} · ${esc(r.asset)}</h3><span class="badge ${state[0]}">${state[1]}</span></header>
      <div class="meta">Dispatched ${esc(hhmm(r.created))} ${seqLink(j.a.seq)} · due ${esc(hhmm(r.eta))} · budget $${Number(r.cost_usd || 0).toLocaleString()}${j.pin && j.pin !== r.asset ? ` · part of ${esc(j.pin)}` : ''}</div>
      <h4>Safety</h4><ul class="acts safety">${safety.join('')}</ul>
      <h4>Why you’re being sent</h4>${why}
      <h4>Site photos</h4>${photos}
      <h4>Checked after the work</h4>${after}
    </article>`;
  };
  const body = jobs.length ? jobs.map(card).join('')
    : `<p class="empty card">${S.final ? 'No field work orders in this incident. The agents resolved it remotely or escalated without sending a technician.' : 'No work orders yet. When the agents need hands on site, the ticket appears here with its lockout steps and photos.'}</p>`;
  $('#work').innerHTML = `<div class="status">${strip.join('')}</div><div class="jobs">${body}</div>`;
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
    case 'dod': {
      const br = dodBranches(e) || {};
      const kinds = [...new Set(dodChecks(e).map((c) => c.kind))].map(esc).join(', ');
      return `Definition of done: ${Object.entries(br).map(([b, cs]) => `${esc(b)} ${cs.length}`).join(' · ')} checks (${kinds})`;
    }
    case 'tool_call': return `${esc(d.name)}(${esc(short(d.args, 90))})`;
    case 'tool_result': {
      const r = d.result || {};
      if (r.error) return `← ${esc(d.name)}: <span class="err">${esc(short(r.error, 120))}</span>`;
      const img = r.image_path ? `<img class="thumb" src="${frameUrl(r.image_path)}" alt="${esc(r.viewpoint)}">` : '';
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
  const branches = dodBranches(S.dod);
  if (!branches) {
    $('#dod').innerHTML = '<p class="empty">No definition of done in this log. In a full-loop run the planner emits it as a <code>dod</code> event before any worker acts, and the verifier fills in each check here.</p>';
    return;
  }
  // Show the branch the team is being held to: the final status, else the latest claim, else DONE.
  const names = Object.keys(branches);
  const want = S.final?.data?.status ?? S.claim?.data?.status;
  const branch = names.includes(want) ? want : names.includes('DONE') ? 'DONE' : names[0];
  const why = S.final && branch === want ? 'final status' : S.claim && branch === want ? 'claimed' : 'no claim yet';
  const checks = branches[branch] || [];
  const others = names.filter((b) => b !== branch).map((b) => `${esc(b)} ${branches[b].length}`).join(' · ');
  const head = Array.isArray(S.dod.data.checks) ? ''
    : `<div class="muted">Branch <b>${esc(branch)}</b> (${why})${others ? ` · also planned: ${others}` : ''}</div>`;
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
  $('#dod').innerHTML = `${head}<div class="muted">Requirements verified: ${pass}/${checks.length} (${pct}%) · ${seqLink(S.dod.seq)}</div>
    <div class="bar"><i style="width:${pct}%"></i></div><ul class="checks">${rows.join('')}</ul>`;
}

function renderAsset(S) {
  const el = $('#assetPanel');
  if (!selected) { el.innerHTML = '<p class="empty">Click a pin to see the photos the agents took of that equipment and the checks that touch it.</p>'; return; }
  const a = S.assets[selected];
  const photos = S.photos.filter((p) => rollup(p.asset) === selected);
  const ph = photos.length
    ? `<div class="photos">${photos.map((p) => `<figure><a href="${frameUrl(p.image_path)}" target="_blank"><img src="${frameUrl(p.image_path)}" alt="${esc(p.viewpoint)} ${esc(p.mode)}"></a>
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
  renderContractor(S);
  renderStatus(S); renderIncident(S); renderEvidence(S); renderPlant(S); renderAsset(S); renderDod(S);
  renderTimeline(); renderBoard(S); renderMemory(S); renderJudge(S);
  const last = events[cursor - 1];
  $('#cursor').textContent = `event ${cursor}/${events.length}${last?.t_sim ? ` · sim ${last.t_sim}` : ''}`;
}

// ---------- data + modes ----------
const runPath = (id) => id.split('/').map(encodeURIComponent).join('/');
const runUrl = (id, rep) => `?run=${encodeURIComponent(id).replace(/%2F/g, '/')}${rep ? '&replay=1' : ''}${view === 'contractor' ? '&view=contractor' : ''}`;
function setView(v) {
  view = v;
  document.body.dataset.view = v;
  document.querySelectorAll('.views button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.view === v)));
  if (runId) history.replaceState(null, '', runUrl(runId, replay));
}

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
  // A fresh clone sorts runs by clone time, so open on the committed demo incidents (runs/demo/) first.
  const demo = runs.filter((r) => r.id.startsWith('demo/'));
  const first = demo.find((r) => r.status === 'DONE') || demo[0] || runs[0];
  if (!runId && first) { runId = first.id; history.replaceState(null, '', runUrl(runId, replay)); }
  const sel = $('#runPick');
  if (runId && !runs.some((r) => r.id === runId)) runs.unshift({ id: runId, status: null });
  const label = (r) => `${r.scenario_id || r.id} · ${STATUS_WORD[r.status] || (r.status ? r.status : 'running')}${r.scenario_id ? ` (${r.id})` : ''}`;
  sel.innerHTML = [...demo, ...runs.filter((r) => !demo.includes(r))]
    .map((r) => `<option value="${esc(r.id)}"${r.id === runId ? ' selected' : ''}>${esc(label(r))}</option>`).join('');
  sel.onchange = () => { location.search = runUrl(sel.value, replay); };
}

async function main() {
  setView(view);
  document.querySelectorAll('.views button').forEach((b) => { b.onclick = () => setView(b.dataset.view); });
  await loadRuns();
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
    $('#incident').innerHTML = $('#work').innerHTML = '<span class="muted">No runs yet. Start one, for example <code>python -m gridtwin.bench --agent scripted_correct --scenario s06_stale_scada</code>, then reload.</span>';
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
