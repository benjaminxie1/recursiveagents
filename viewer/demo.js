'use strict';
// GridTwin demo page. Everything on it comes from runs/<id>/events.jsonl (the demo.* events in gridtwin/demo/CONTRACT.md).
// Live mode polls the log every second; ?replay=1 steps through a finished run at a readable pace.

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const src = (p) => (/^(https?:)?\//.test(p || '') ? p : `/${p}`);
const runPath = (id) => id.split('/').map(encodeURIComponent).join('/');
// Run links keep the page's settings (rehearsal stub, fresh memory, scenario, theme) so picking another run never drops them.
const STICKY = ['stub', 'fresh', 'scenario', 'memory', 'theme'];
const runUrl = (id, rep) => `?run=${encodeURIComponent(id).replace(/%2F/g, '/')}${rep ? '&replay=1' : ''}`
  + STICKY.filter((k) => params.has(k)).map((k) => `&${k}=${encodeURIComponent(params.get(k))}`).join('');
const human = (s) => String(s ?? '').replace(/_/g, ' ');
const MODEL_WORDS = { deterministic: 'Rule in code', stub: 'Stub, no model call' };
const modelName = (m) => MODEL_WORDS[m] || String(m || '').replace(/^[a-z-]+\//, '').replace(/-/g, ' ');
const fmtUsd = (u) => (u == null ? '-' : !Number(u) ? '$0.00' : `$${Number(u).toFixed(Number(u) < 0.01 ? 4 : 3)}`);
const fmtWall = (s) => (s == null ? '-' : s < 90 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${String(Math.round(s % 60)).padStart(2, '0')} s`);
const fmtAge = (m) => (m >= 10 ? String(Math.round(m)) : (Math.round(m * 10) / 10).toFixed(1));

const params = new URLSearchParams(location.search);
const replay = params.get('replay') === '1';
let runId = params.get('run');
let events = [];   // everything fetched for this run
let cursor = 0;    // events applied; replay advances it, live keeps it at events.length
let playing = true, speed = 1, timer = null, polls = 0, finalPolls = 0;
let lastStageKey = null, firstRender = true, followPausedUntil = 0;
const openDetails = new Set();  // <details> keys the viewer opened, kept across re-renders
const painted = {};             // last HTML per element, so unchanged sections are not rebuilt

const STAGES = ['diagnose', 'plan', 'dispatch', 'contractor', 'verify', 'redispatch', 'memory', 'final'];
const ROUND_STAGES = new Set(['dispatch', 'contractor', 'verify', 'redispatch']);
const STAGE_LABEL = { diagnose: 'Diagnose', plan: 'Plan', dispatch: 'Dispatch', contractor: 'Contractor', verify: 'Verify',
  redispatch: 'Redispatch', memory: 'Memory', final: 'Final' };
const CHECK_KIND = { telemetry: 'Telemetry', visual: 'Camera', jev: 'JEV decision' };
const UNITS = { ac_kw: ['AC power', 'kW'], dc_kw: ['DC power', 'kW'], dc_v: ['DC voltage', 'V'], temp_c: ['Temperature', '°C'],
  heatsink_c: ['Heatsink', '°C'], state: ['State', ''], status: ['Status', ''] };
const teamOf = (a) => a.team || 'plan';  // logs from before teams carry only the planner's tree

// ---------- fold the event log into what each section shows ----------
function derive(evs) {
  const S = { goal: null, stages: {}, current: null, sensors: new Map(), cameras: [], diagnosis: null, agents: new Map(),
    workorders: [], thread: [], checks: [], jev: [], diffusion: [], verdicts: new Map(), recall: null, writes: [], scores: [], stats: null,
    final: null, rounds: new Set(), t0: null, t1: null, twinUrl: null };
  for (const e of evs) {
    const d = e.data || {};
    if (e.wall_ts) { S.t0 ??= e.wall_ts; S.t1 = e.wall_ts; }
    if (d.round >= 1 && e.type !== 'demo.stage') S.rounds.add(d.round);
    switch (e.type) {
      case 'goal': S.goal = d; break;
      case 'demo.stage': {
        const st = (S.stages[d.stage] ??= { rounds: new Map() });
        st.rounds.set(d.round ?? 0, d.status);
        if (d.twin_url) S.twinUrl = d.twin_url;
        if (d.status === 'start') S.current = { stage: d.stage, round: d.round ?? 0, seq: e.seq };
        if (ROUND_STAGES.has(d.stage) && d.round >= 1) S.rounds.add(d.round);
        break;
      }
      case 'demo.sensor': S.sensors.set(d.asset, d); break;
      case 'demo.camera': S.cameras.push({ ...d, actor: e.actor, seq: e.seq }); break;
      case 'demo.diagnosis': S.diagnosis = d; if (d.twin_url) S.twinUrl = d.twin_url; break;
      case 'demo.agent': {
        const prev = S.agents.get(d.id);
        S.agents.set(d.id, { ...(prev || {}), ...Object.fromEntries(Object.entries(d).filter(([, v]) => v != null)), seq: prev?.seq ?? e.seq });
        break;
      }
      case 'demo.workorder': S.workorders.push({ ...d, seq: e.seq }); break;
      case 'demo.email': S.thread.push({ kind: 'email', actor: e.actor, seq: e.seq, t: e.t_sim, ...d }); break;
      case 'demo.contractor': S.thread.push({ kind: 'visit', seq: e.seq, t: e.t_sim, ...d }); break;
      case 'demo.diffusion': S.diffusion.push({ ...d, seq: e.seq }); break;
      case 'demo.check': S.checks.push({ ...d, seq: e.seq }); break;
      case 'demo.jev': S.jev.push({ ...d, seq: e.seq }); break;
      case 'demo.verdict': S.verdicts.set(d.round, d); break;
      case 'demo.memory': // recall before planning; write, score and stats after the verdict
        if (d.op === 'recall') S.recall = d;
        else if (d.op === 'write') S.writes.push(d);
        else if (d.op === 'score') S.scores.push(d);
        else if (d.op === 'stats') S.stats = d;
        break;
      case 'demo.final': S.final = d; break;
    }
  }
  return S;
}

// A stage is active while any of its rounds has started and not ended (the map keeps the latest status per round).
const isActive = (S, name) => [...(S.stages[name]?.rounds.values() || [])].includes('start');
const roundsOf = (S, name) => [...(S.stages[name]?.rounds.keys() || [])].filter((r) => r >= 1).sort((a, b) => a - b);

// ---------- header and stage rail ----------
function renderHeader(S) {
  const scen = S.goal?.scenario_id || params.get('scenario') || 's06_stale_scada';
  const diag = S.diagnosis ? `<span>${esc(S.diagnosis.asset)}: ${esc(human(S.diagnosis.fault))}</span>` : '';
  paint('#scenario', `<b>${esc(scen)}</b> ${diag}`);
  const f = S.final;
  $('#cost').textContent = fmtUsd(f?.usd);
  $('#wall').textContent = f?.wall_s != null ? fmtWall(f.wall_s) : S.t0 != null ? fmtWall(S.t1 - S.t0) : '-';
  const state = $('#state');
  if (!runId) { state.className = 'state'; state.textContent = 'No run'; return; }
  const other = `<a href="${runUrl(runId, !replay)}">${replay ? 'watch live' : 'replay'}</a>`;
  if (replay) { state.className = 'state'; paint('#state', `Replay ${other}`); }
  else if (f) { state.className = `state ${f.status === 'VERIFIED' ? 'done' : 'bad'}`; paint('#state', `Finished ${other}`); }
  else { state.className = 'state live'; paint('#state', 'Live'); }
}

function railChips(S, name) {
  const chip = (cls, txt, title = '') => `<span class="r ${cls}"${title ? ` title="${esc(title)}"` : ''}>${esc(txt)}</span>`;
  const st = S.stages[name];
  switch (name) {
    case 'diagnose': return S.diagnosis ? chip('fail', S.diagnosis.asset, human(S.diagnosis.fault)) : '';
    case 'plan': {
      const kids = [...S.agents.values()].filter((a) => a.depth === 1 && teamOf(a) === 'plan').length;
      return kids ? chip(isActive(S, 'plan') ? 'live' : '', `${kids} sub-agents`) : '';
    }
    case 'verify': return roundsOf(S, name).map((r) => {
      const v = S.verdicts.get(r);
      return v ? chip(v.pass ? 'pass' : 'fail', `R${r} ${v.pass ? 'pass' : 'fail'}`) : chip('live', `R${r}`);
    }).join('');
    case 'contractor': return roundsOf(S, name).map((r) => {
      const visit = S.thread.find((t) => t.kind === 'visit' && t.round === r);
      return chip(visit?.shortcut ? 'warn' : st.rounds.get(r) === 'start' ? 'live' : '', `R${r}${visit?.shortcut ? ' shortcut' : ''}`,
        visit?.shortcut ? 'Took the shortcut: an inspect-only visit instead of the work order' : '');
    }).join('');
    case 'memory': {
      const n = S.writes.reduce((k, w) => k + (w.lessons?.length || 0), 0);
      return n ? chip('pass', `${n} written`) : st && !isActive(S, 'memory') ? chip('', 'none written') : '';
    }
    case 'final': return S.final ? chip(S.final.status === 'VERIFIED' ? 'pass' : 'fail', human(S.final.status).toLowerCase()) : '';
    default: return roundsOf(S, name).map((r) => chip(st.rounds.get(r) === 'start' ? 'live' : '', `R${r}`)).join('');
  }
}

function renderRail(S) {
  const html = STAGES.map((name) => {
    let cls = S.stages[name] ? (isActive(S, name) ? 'active' : 'done') : 'pending';
    if (name === 'verify' && cls === 'done') {
      const last = S.verdicts.get(Math.max(0, ...roundsOf(S, 'verify')));
      if (last) cls += last.pass ? ' passed' : ' failed';
    }
    if (name === 'final' && S.final) cls += S.final.status === 'VERIFIED' ? ' passed' : ' failed';
    return `<a class="stage ${cls}" href="#${sectionFor(S, name)}" data-stage="${name}">
      <span class="name">${STAGE_LABEL[name]}</span><span class="rounds">${railChips(S, name)}</span></a>`;
  }).join('');
  paint('#rail', html);
}

function sectionFor(S, name, round) {
  if (name === 'diagnose') return 'sec-diagnose';
  if (name === 'plan') return 'sec-plan';
  if (name === 'memory') return 'sec-memory';
  if (name === 'final') return 'final';
  return `round-${round ?? Math.max(1, ...roundsOf(S, name))}`;
}

// ---------- final banner ----------
function renderFinal(S) {
  const el = $('#final');
  const f = S.final;
  el.hidden = !f;
  if (!f) return;
  const ok = f.status === 'VERIFIED';
  const crashed = f.status === 'FAILED';  // the run itself crashed; error says why
  el.className = `final ${ok ? '' : 'bad'}`;
  const j = f.judge;
  // resolved is the plant's own truth; an escalated run can still have fixed the plant.
  const judge = j ? `<div class="judge">Scored afterwards by the bench judge from hidden sim truth: ${j.resolved ? (ok ? 'fixed' : 'fixed, but escalated') : 'not fixed'},
    ${j.false_done ? 'a false done' : 'no false done'}, ${j.safety_violations ?? 0} safety violation${j.safety_violations === 1 ? '' : 's'}.</div>` : '';
  const stub = f.stub ? ' <span class="chip sim">Stub run: fixed answers, no model calls</span>' : '';
  const rounds = !crashed && f.rounds ? ` ${ok ? 'in' : 'after'} ${f.rounds} round${f.rounds === 1 ? '' : 's'}` : '';
  const error = crashed && f.error ? `<div class="judge">Error: ${esc(f.error)}</div>` : '';
  paint('#final', `<div class="verdict">${ok ? 'Verified' : crashed ? 'Run failed' : 'Escalated'}${rounds}</div>
    <div class="sum">${esc(f.summary)}${stub}${error}${judge}</div>
    <div class="nums"><span class="meter"><span class="k">Cost</span><span class="v">${fmtUsd(f.usd ?? 0)}</span></span>
      <span class="meter"><span class="k">Wall time</span><span class="v">${fmtWall(f.wall_s)}</span></span></div>`);
}

// ---------- 1. diagnose ----------
function sensorCard(d) {
  const rows = Object.entries(d.latest || {}).slice(0, 4).map(([k, v]) => {
    const [label, unit] = UNITS[k] || [human(k), ''];
    return `<dt>${esc(label)}</dt><dd>${esc(typeof v === 'number' ? v.toLocaleString(undefined, { maximumFractionDigits: 1 }) : v)}${unit ? ` ${unit}` : ''}</dd>`;
  }).join('');
  const since = d.last_update_ts ? String(d.last_update_ts).slice(11, 16) : '';
  return `<div class="sensor ${d.stale ? 'stale' : ''}">
    <div class="asset">${esc(d.asset)}${d.stale ? '<span class="badge fail">Stale</span>' : '<span class="badge ok">Live</span>'}</div>
    <div class="age">${fmtAge(d.data_age_min)}<small>min</small></div>
    <div class="lbl">data age${d.stale && since ? `, frozen since ${esc(since)}` : ''}</div>
    <dl class="latest">${rows}</dl></div>`;
}

function photo(c, who = 'Nemotron Omni reads the photo') {
  return `<figure><a class="shot" href="${esc(src(c.image))}" target="_blank" rel="noopener">
      <img src="${esc(src(c.image))}" alt="${esc(c.viewpoint)} camera view"><span class="vp">${esc(c.viewpoint)}</span></a>
    <figcaption><span class="who">${esc(who)}</span>${esc(c.description)}</figcaption></figure>`;
}

function renderDiagnose(S) {
  const started = !!S.stages.diagnose;
  $('#sec-diagnose').classList.toggle('pending', !started);
  const goal = S.goal ? `<p class="goal">Operator: <q>${esc(S.goal.text)}</q></p>` : '';
  const sensors = [...S.sensors.values()].sort((a, b) => String(a.asset).localeCompare(String(b.asset)));
  const cams = S.cameras.filter((c) => c.actor !== 'verifier' && !(c.round >= 1));
  const d = S.diagnosis;
  const top = [goal];
  if (sensors.length) {
    top.push(`<h3>SCADA feed <span class="chip sim">Simulated plant</span></h3><div class="sensors">${sensors.map(sensorCard).join('')}</div>`);
  } else if (!started) top.push('<p class="empty">Waiting for the run to start. Press Run demo, or pick a run.</p>');
  paint('#diagTop', top.join(''));
  const parts = [];
  if (cams.length) parts.push(`<div><h3>Cameras <span class="chip real">Nemotron Omni on Crusoe</span></h3><div class="photos">${cams.map((c) => photo(c)).join('')}</div></div>`);
  if (d) {
    parts.push(`<div class="panel diagnosis"><h3>Diagnosis <span class="chip real">Nemotron on Crusoe</span></h3>
      <div class="fault"><span class="what">${esc(d.asset)}: ${esc(human(d.fault))}</span>
      ${d.confidence != null ? `<span class="conf">${Math.round(d.confidence * 100)}% confident</span>` : ''}</div>
      <p>${esc(d.summary)}</p>
      ${d.evidence?.length ? `<ul class="evidence">${d.evidence.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}</div>`);
  }
  const team = [...S.agents.values()].filter((x) => teamOf(x) === 'diagnose');
  if (team.length) parts.push(teamPanel(team, 'Diagnosis team', '<span class="chip real">Nemotron on Crusoe</span>'));
  paint('#diagMain', `<div class="stack">${parts.join('')}</div>`);
  renderTwin(S);
}

function renderTwin(S) {
  if (!runId) return;
  let url = S.twinUrl || `twin.html?scene=_dev&run=${encodeURIComponent(runId)}`;
  if (!/[?&]tour=1\b/.test(url)) url += `${url.includes('?') ? '&' : '?'}tour=1`;
  const frame = $('#twinFrame');
  if (frame.dataset.url !== url) {
    frame.dataset.url = url;
    frame.src = url;
    $('#twinOpen').href = url.replace(/[?&]tour=1\b/, '');
    $('#twinWait').hidden = true;
  }
}

// ---------- 2. plan ----------
function agentOutput(a) {
  if (a.output == null || a.output === '') return '';
  const key = `agent-${a.id}`;
  const body = typeof a.output === 'string' ? `<pre class="out">${esc(a.output)}</pre>` : `<pre class="json">${jsonHtml(a.output)}</pre>`;
  return `<details data-key="${esc(key)}"${openDetails.has(key) ? ' open' : ''}><summary>Output</summary>${body}</details>`;
}

// Diagnosis and verification teams: a lead and its sub-agents as a compact indented list.
function teamPanel(agents, title, chip) {
  if (!agents.length) return '';
  const sorted = [...agents].sort((x, y) => x.seq - y.seq);
  const leads = sorted.filter((a) => !a.depth);
  const row = (a) => `<div class="cnode ${esc(a.status)}"><div class="head"><span class="role">${esc(human(a.role))}</span>
      <span class="depth">depth ${esc(a.depth)}</span><span class="model" title="${esc(a.model)}">${esc(modelName(a.model))}</span>
      <span class="status ${esc(a.status)}">${esc(a.status === 'spawned' ? 'working' : a.status)}</span></div>
    ${a.task ? `<p class="task">${esc(a.task)}</p>` : ''}${agentOutput(a)}</div>`;
  const tree = leads.map((l) => `${row(l)}<ul class="kids-list">${sorted.filter((k) => k.parent === l.id).map((k) => `<li>${row(k)}</li>`).join('')}</ul>`).join('');
  const orphans = sorted.filter((a) => a.depth && !leads.some((l) => l.id === a.parent));
  return `<div class="panel team"><h3>${esc(title)} ${chip}</h3>${tree}${orphans.map(row).join('')}
    <div class="cap-rule"><b>Depth limit 1</b><span>The lead spawns; its sub-agents cannot.</span></div></div>`;
}

function agentNode(a, cls = '') {
  const key = `agent-${a.id}`;
  const out = agentOutput(a);
  const status = a.status === 'spawned' ? 'working' : a.status;
  return `<div class="node ${cls} ${esc(a.status)}">
    <div class="head"><span class="role">${esc(human(a.role))}</span><span class="depth">depth ${esc(a.depth)}</span><span class="status ${esc(a.status)}">${esc(status)}</span></div>
    <div class="model" title="${esc(a.model)}">${esc(modelName(a.model))}</div>
    ${a.task ? `<p class="task">${esc(a.task)}</p>` : ''}${out}</div>`;
}

// The pipeline writes forbid entries as whole sentences ("Do not substitute ..."); older runs wrote bare actions.
const forbidLine = (x) => (/^do not\b/i.test(String(x).trim()) ? String(x).trim() : `Do not: ${x}`);

function workOrder(wo) {
  const forbid = wo.forbid?.length
    ? `<ul class="forbid">${wo.forbid.map((x) => `<li>${esc(forbidLine(x))}</li>`).join('')}</ul>`
    : '<p class="none">Nothing forbidden.</p>';
  const lessons = wo.lessons_applied?.length
    ? wo.lessons_applied.map((l) => `<span class="lesson-tag">${esc(l)}</span>`).join(' ')
    : '<span class="none">None applied.</span>';
  return `<div class="panel wo"><h3>Work order</h3>
    <div class="id">${esc(wo.wo_id)} <span class="count">round ${esc(wo.round)}</span></div>
    <p class="title">${esc(wo.work_order)}</p>
    <h4>Steps</h4><ol>${(wo.steps || []).map((s) => `<li>${esc(s)}</li>`).join('')}</ol>
    <h4>Forbidden</h4>${forbid}
    <h4>Acceptance, checked by the verifier</h4><ul class="accept">${(wo.acceptance || []).map((s) => `<li>${esc(s)}</li>`).join('')}</ul>
    <h4>Lessons from memory</h4>${lessons}</div>`;
}

function renderPlan(S) {
  const started = !!S.stages.plan;
  $('#sec-plan').classList.toggle('pending', !started);
  const agents = [...S.agents.values()].filter((a) => teamOf(a) === 'plan').sort((a, b) => a.seq - b.seq);
  const roots = agents.filter((a) => !a.depth);
  const kids = agents.filter((a) => a.depth === 1);
  const deeper = agents.filter((a) => a.depth > 1);
  const r = S.recall;
  const recall = r ? `<div class="recall-note"><b>Memory recall:</b> ${r.lessons?.length
    ? `${r.lessons.map((l) => `<span class="lesson-tag">${esc(l.id)}</span>`).join(' ')} ${esc(r.effect)}`
    : esc(r.effect || 'no verified lessons yet.')}</div>` : '';
  let tree = '';
  if (agents.length) {
    tree = `<div class="tree">${roots.map((a) => agentNode(a, 'planner')).join('')}
      ${kids.length ? `<div class="wires"></div><div class="kids">${kids.map((a) => agentNode(a)).join('')}</div>` : ''}
      <div class="cap-rule"><b>Depth limit 1</b><span>Sub-agents cannot spawn agents of their own. The cap is enforced in code, not by the prompt.</span></div>
      ${deeper.length ? `<div class="blocked">${deeper.map((a) => agentNode(a)).join('')}</div>` : ''}</div>`;
  }
  const wo = S.workorders.find((w) => w.round === 1) || S.workorders[0];
  const body = started || agents.length
    ? `<div class="plan"><div>${recall}<h3>Agent tree <span class="chip real">Nemotron on Crusoe</span></h3>${tree || '<p class="empty">Spawning the planner.</p>'}</div>
        <div>${wo ? workOrder(wo) : '<p class="empty">The planner merges its sub-agents into a work order.</p>'}</div></div>`
    : '<p class="empty">Planning starts once the diagnosis is in.</p>';
  paint('#plan', body);
}

// ---------- 3. rounds: the mail thread beside the verifier ----------
function attachments(list) {
  if (!list?.length) return '';
  return `<div class="att">${list.map((a) => {
    const isImg = /\.(png|jpe?g|webp|gif)$/i.test(a.path || a.name || '');
    return `<a href="${esc(src(a.path))}" target="_blank" rel="noopener">${isImg ? `<img src="${esc(src(a.path))}" alt="">` : ''}${esc(a.name)}</a>`;
  }).join('')}</div>`;
}

function emailCard(m, S) {
  const back = m.direction === 'out' && m.actor === 'verifier';
  const label = m.direction === 'in' ? 'Contractor reply' : back ? 'Sent back by the verifier' : 'Work order sent to contractor';
  const diff = back && S.diffusion.find((d) => (m.attachments || []).some((a) => a.path === d.image));
  // seen is the verifier's failing photo; for the photo bank, source is the healthy reference, so never show it as seen.
  const seen = diff && (diff.seen || (diff.backend !== 'photo_bank' && diff.source)
    || S.cameras.filter((c) => c.actor === 'verifier' && c.round === diff.round).pop()?.image);
  const compare = diff ? `<div class="compare">
      ${seen ? `<figure class="seen"><a class="shot" href="${esc(src(seen))}" target="_blank" rel="noopener"><img src="${esc(src(seen))}" alt="Seen after the visit"></a>
        <figcaption>Seen after the visit</figcaption></figure>` : ''}
      <figure class="exp"><a class="shot" href="${esc(src(diff.image))}" target="_blank" rel="noopener"><img src="${esc(src(diff.image))}" alt="Expected state"></a>
        <figcaption>Expected when fixed, ${diff.backend === 'diffusion' ? 'generated by diffusion from that photo' : 'healthy photo from the photo bank'}</figcaption></figure></div>` : '';
  const rest = diff ? (m.attachments || []).filter((a) => a.path !== diff.image && a.path !== seen) : m.attachments;
  return `<article class="mail ${back ? 'back' : ''}" id="item-${m.seq}">
    <div class="hdr"><div class="dir"><b>${label}</b><span class="chip sim">Simulated email</span></div>
      <dl><dt>From</dt><dd>${esc(m.from)}</dd><dt>To</dt><dd>${esc(m.to)}</dd><dt>Subject</dt><dd class="subj">${esc(m.subject)}</dd></dl></div>
    ${compare}<pre class="txt">${esc(m.body)}</pre>${attachments(rest)}
    <div class="foot"><span>${esc(m.wo_id)}, round ${esc(m.round)}${m.t ? `, sim time ${esc(String(m.t).slice(11, 16))}` : ''}</span>
      ${m.eml ? `<a href="${esc(src(m.eml))}" target="_blank" rel="noopener">Open .eml file</a>` : ''}</div></article>`;
}

const sentence = (t) => { const x = String(t || '').trim(); return x && x[0].toUpperCase() + x.slice(1) + (/[.!?]$/.test(x) ? '' : '.'); };

function visitCard(v) {
  const args = (a) => Object.values(a || {}).map((x) => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ');
  return `<div class="visit ${v.shortcut ? 'shortcut' : ''}" id="item-${v.seq}">
    <div class="head"><b>Contractor on site</b><span class="chip sim">Simulated contractor</span></div>
    <ol>${(v.actions || []).map((a) => `<li><code>${esc(a.tool)}(${esc(args(a.args))})</code>${a.ok ? '<span class="ok">done</span>' : '<span class="no">failed</span>'}</li>`).join('')}</ol>
    ${v.shortcut ? `<div class="warnline">${esc(sentence(v.note) || 'Took the shortcut: an inspect-only visit instead of the work order.')}</div>`
    : v.note ? `<p class="note">${esc(sentence(v.note))}</p>` : ''}</div>`;
}

function jsonHtml(obj) {
  return esc(JSON.stringify(obj, null, 2)).replace(
    /(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
    (m, str, colon, lit, num) => (str ? `<span class="${colon ? 'jk' : 'js'}">${str}</span>${colon || ''}` : lit ? `<span class="jb">${lit}</span>` : `<span class="jn">${num}</span>`));
}

function jevBlock(j, r) {
  const o = j.output || {};
  const inp = j.input || {};
  const p = Math.max(0, Math.min(1, Number(o.p_verified ?? 0)));
  const u = Math.max(0, Number(o.uncertainty ?? 0));
  const stations = inp.stations || [];
  const seeing = stations.filter((s) => s.sees_asset).length;
  const cov = inp.coverage;
  const summary = [stations.length ? (cov?.registered_stations != null
    ? `${cov.registered_stations} of ${stations.length} camera stations placed in the twin, ${cov.stations_seeing_asset ?? seeing} see ${inp.asset}`
    : `${stations.length} camera stations, ${seeing} see ${inp.asset}`) : '',
    inp.sensors?.data_age_min != null ? `data age ${fmtAge(inp.sensors.data_age_min)} min` : '',
    inp.visual?.length ? `${inp.visual.length} visual claims` : ''].filter(Boolean).join('; ');
  const key = `jev-${r}-${j.seq}`;
  const backend = o.backend === 'jev' ? 'TypeSafe Jev' : 'Local fallback on Crusoe';
  return `<div class="panel jev"><h3>JEV decision <span class="chip real">${backend}</span></h3>
    <div class="decision"><span class="word ${esc(o.verdict)}">${esc(human(o.verdict))}</span>
      <div><div class="gauge" role="img" aria-label="p verified ${p.toFixed(2)}">
        <span class="unc" style="left:${Math.max(0, p - u) * 100}%;right:${(1 - Math.min(1, p + u)) * 100}%"></span><i style="left:${p * 100}%"></i></div>
        <div class="gauge-lbl"><span>0</span><span>p verified <b>${p.toFixed(2)}</b>${u ? ` ± ${u.toFixed(2)}` : ''}</span><span>1</span></div></div></div>
    ${o.reason ? `<p class="reason">${esc(o.reason)}</p>` : ''}
    <details data-key="${esc(key)}"${openDetails.has(key) ? ' open' : ''}><summary>Interpreted input: twin xyz, sensors and camera checks as one JSON state</summary>
      ${summary ? `<div class="summary-line">${esc(summary)}</div>` : ''}<pre class="json">${jsonHtml(inp)}</pre></details></div>`;
}

function verifyColumn(S, r) {
  const checks = S.checks.filter((c) => c.round === r);
  const active = S.stages.verify?.rounds.get(r) === 'start';
  const head = '<h3>Verifier checks <span class="chip real">gpt-oss-120b on Crusoe</span></h3>';
  if (!checks.length && !active && !S.verdicts.get(r)) {
    return `<div class="verify"><div class="panel">${head}
      <p class="empty">Runs its own checks on the plant once the contractor says done. It never takes the contractor's word.</p></div></div>`;
  }
  const cams = S.cameras.filter((c) => c.actor === 'verifier' && c.round === r);
  const seen = new Set(checks.map((c) => c.kind));
  const waiting = active ? ['telemetry', 'visual', 'jev'].filter((k) => !seen.has(k)) : [];
  const rows = checks.map((c) => `<li><span class="mark ${c.pass ? 'pass' : 'fail'}">${c.pass ? '✓' : '✗'}</span>
      <div><div class="k">${esc(CHECK_KIND[c.kind] || human(c.kind))}<small>${esc(c.name)}</small></div><div class="d">${esc(c.detail)}</div></div></li>`)
    .concat(waiting.map((k) => `<li><span class="mark wait">·</span><div><div class="k">${CHECK_KIND[k]}</div><div class="d">Checking…</div></div></li>`));
  const jev = S.jev.filter((j) => j.round === r).map((j) => jevBlock(j, r)).join('')
    + teamPanel([...S.agents.values()].filter((a) => teamOf(a) === 'verify' && a.round === r), 'Verification team',
      '<span class="chip real">gpt-oss-120b and rules in code</span>');
  return `<div class="verify"><div class="panel">${head}
      ${cams.map((c) => `<div class="vshot">${photo(c, 'A camera the contractor did not use, read by Nemotron Omni')}</div>`).join('')}
      <ul class="checks">${rows.join('')}</ul></div>${jev}</div>`;
}

function expectedCard(t) {
  return `<div class="panel" id="item-${t.seq}"><h3>Expected-state image <span class="chip real">${t.backend === 'diffusion' ? 'Diffusion' : 'Photo bank'}</span></h3>
    <figure class="exp"><a class="shot" href="${esc(src(t.image))}" target="_blank" rel="noopener"><img src="${esc(src(t.image))}" alt="Expected state"></a>
    <figcaption>${esc(t.prompt)}</figcaption></figure></div>`;
}

function renderRounds(S) {
  const rounds = [...S.rounds].sort((a, b) => a - b);
  const host = $('#rounds');
  for (const r of rounds) {
    let sec = document.getElementById(`round-${r}`);
    if (!sec) {
      sec = document.createElement('section');
      sec.id = `round-${r}`;
      sec.className = 'band round';
      sec.innerHTML = '<div class="gutter"></div><div class="body"></div>';
      host.appendChild(sec);
    }
    const v = S.verdicts.get(r);
    const nFail = v?.failed?.length || 0;
    const tag = v ? `<span class="verdict-tag ${v.pass ? 'pass' : 'fail'}">${v.pass ? 'Verified' : `Not verified, ${nFail} check${nFail === 1 ? '' : 's'} failed`}</span>`
      : '<span class="verdict-tag live">In progress</span>';
    const gloss = r === 1 ? 'The work order goes out, the contractor acts, the verifier checks the plant itself.'
      : 'Back to the contractor with what failed and a picture of what fixed looks like.';
    paintEl(sec.querySelector('.gutter'), `<h2><span class="n">${2 + r}</span>Round ${r}</h2><p class="gloss">${gloss}</p>${tag}`);
    const prev = S.workorders.find((w) => w.round === r - 1);
    const wo = S.workorders.find((w) => w.round === r);
    const added = wo && prev ? (wo.forbid || []).filter((x) => !(prev.forbid || []).includes(x)) : [];
    const revised = added.length ? `<div class="panel wo"><h3>Work order revised for round ${r}</h3>
      <ul class="forbid">${added.map((x) => `<li>${esc(forbidLine(x))}</li>`).join('')}</ul></div>` : '';
    const items = S.thread.filter((t) => t.round === r);
    // The verifier's email back may carry the next round's number; it shows the expected-state image wherever it lands.
    const linked = new Set(S.thread.filter((t) => t.kind === 'email').flatMap((m) => (m.attachments || []).map((a) => a.path)));
    // An image no email carries still belongs to the round it was made for: the next one, when there is one.
    const loose = S.diffusion.filter((d) => !linked.has(d.image) && (S.rounds.has(d.round + 1) ? d.round + 1 : d.round) === r)
      .map((d) => ({ kind: 'diffusion', ...d }));
    const thread = [...items, ...loose].sort((a, b) => a.seq - b.seq)
      .map((t) => (t.kind === 'email' ? emailCard(t, S) : t.kind === 'visit' ? visitCard(t) : expectedCard(t))).join('');
    paintEl(sec.querySelector('.body'), `<div class="thread">${revised}${thread || '<p class="empty">Waiting for dispatch.</p>'}</div>${verifyColumn(S, r)}`);
  }
  for (const sec of [...host.children]) {
    if (!rounds.includes(Number(sec.id.slice(6)))) { delete painted[`${sec.id}:gutter`]; delete painted[`${sec.id}:body`]; sec.remove(); }
  }
  $('#memN').textContent = String(3 + (rounds.length || 1));
}

// ---------- memory ----------
// A lesson reads "L-id: when <symptom>. <evidence>, fix = <action>; do not: <shortcut>". Show the first sentence,
// the fix and the do-not; the whole text stays one click away.
function lessonParts(text) {
  const t = String(text || '').replace(/^L-[\w.-]+:\s*/, '');
  const fix = t.match(/\bfix = ([^;]+?)\s*(?:;|$)/)?.[1];
  const dont = t.match(/\bdo not:\s*(.+)$/i)?.[1]?.replace(/\s*\[score [\d.]+\]$/, '');
  const lead = t.split(/,?\s*fix = /)[0];
  const head = lead.match(/^.*?[.;](?=\s|$)/)?.[0] || lead;
  return { head: sentence(head.replace(/;$/, '.')), fix, dont, more: head.length < t.length - 2 };
}

function lessonCard(l) {
  const p = lessonParts(l.text);
  const key = `lesson-${l.id}`;
  return `<div class="lesson"><div class="id">${esc(l.id)}</div><p class="t">${esc(p.head)}</p>
    ${p.fix ? `<p class="rule"><b>Fix</b><span>${esc(human(p.fix))}</span></p>` : ''}
    ${p.dont ? `<p class="rule dont"><b>Do not</b><span>${esc(p.dont.replace(/^do not\s+/i, ''))}</span></p>` : ''}
    ${p.more ? `<details data-key="${esc(key)}"${openDetails.has(key) ? ' open' : ''}><summary>Whole lesson</summary><p class="full">${esc(l.text)}</p></details>` : ''}</div>`;
}

// The money shot: rounds to a verified fix for each run the lesson store has seen, oldest first.
function flywheel(S) {
  const st = S.stats?.stats;
  const runs = (st?.rounds_by_run || []).slice(-12);
  if (!runs.length) return '';
  const top = Math.max(2, ...runs.map((x) => x.rounds || 0));
  const first = runs[0];
  const last = runs[runs.length - 1];
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const trend = runs.length > 1 && last.verified && first.rounds > last.rounds
    ? `Rounds to a verified fix fell from ${first.rounds} to ${last.rounds}.`
    : runs.length > 1 && last.verified && last.rounds === 1 ? 'Verified on the first visit.' : '';
  const store = `${plural(st.runs ?? runs.length, 'run')} and ${plural(st.lessons ?? 0, 'lesson')} in the store.`;
  const bars = runs.map((x) => {
    const n = x.rounds || 0;
    const word = x.verified ? `${n} round${n === 1 ? '' : 's'}` : `not verified after ${n}`;
    return `<li class="${x.verified ? 'ok' : 'bad'}${x.run_id === runId ? ' me' : ''}" title="${esc(`${x.run_id}: ${word}`)}">
      <span class="stack"><span class="n">${n}</span><span class="col" style="--h:${Math.max(0.05, n / top)}"></span></span>
      <span class="lbl">${esc(x.run_id)}${x.run_id === runId ? ' <b>this run</b>' : ''}</span></li>`;
  }).join('');
  const delta = new Map(S.scores.flatMap((e) => (e.lessons || []).map((l) => [l.id, l.text])));
  const scores = (st.lesson_scores || []).map((l) => `<li><span class="lesson-tag">${esc(l.id)}</span>
      <span class="w">weight ${Number(l.score).toFixed(2)}</span><small>${esc(`${l.wins} win${l.wins === 1 ? '' : 's'} in ${l.uses} use${l.uses === 1 ? '' : 's'}`)}${delta.has(l.id) && /\(([+-][\d.]+)\)/.test(delta.get(l.id)) ? `, ${esc(Number(delta.get(l.id).match(/\(([+-][\d.]+)\)/)[1]).toFixed(2).replace(/^(?!-)/, '+'))} this run` : ''}</small>
      ${l.flagged ? '<span class="chip flag">Flagged: led to a failed round</span>' : ''}</li>`).join('');
  return `<div class="panel flywheel"><h3>Rounds to a verified fix, run by run <span class="chip real">Lesson store</span></h3>
    <div class="fw"><ol class="bars">${bars}</ol>
      <div class="fw-side">${trend ? `<p class="trend">${esc(trend)}</p>` : ''}<p class="effect">${esc(store)}</p>
        ${scores ? `<h4>Lesson weights</h4><ul class="scores">${scores}</ul>` : ''}
        ${S.scores[0]?.effect ? `<p class="why">${esc(sentence(S.scores[0].effect))}</p>` : ''}</div></div></div>`;
}

function renderMemory(S) {
  $('#sec-memory').classList.toggle('pending', !S.stages.memory && !S.recall);
  const r = S.recall;
  const hints = Object.entries(r?.hints || {}).filter(([, v]) => v?.length);
  const notes = r?.contractor_notes || [];
  const hintKey = 'recall-hints';
  const recall = `<div class="panel"><h3>Recalled before planning</h3>${!r ? '<p class="empty">Checked when planning starts.</p>'
    : `${r.lessons?.length ? r.lessons.map(lessonCard).join('') : '<p class="empty">Nothing recalled.</p>'}
      ${r.effect ? `<p class="effect"><b>What it changed:</b> ${esc(sentence(r.effect))}</p>` : ''}
      ${notes.length ? `<h4>Contractor history</h4><ul class="notes">${notes.map((n) => `<li>${esc(sentence(n))}</li>`).join('')}</ul>` : ''}
      ${hints.length ? `<details data-key="${hintKey}"${openDetails.has(hintKey) ? ' open' : ''}><summary>Hints handed to ${hints.length} role${hints.length === 1 ? '' : 's'}</summary>
        <dl class="hints">${hints.map(([role, hs]) => `<dt>${esc(human(role))}</dt>${hs.map((h) => `<dd>${esc(String(h).replace(/^L-[\w.-]+:\s*/, ''))}</dd>`).join('')}`).join('')}</dl></details>` : ''}`}</div>`;
  const w = S.writes;
  const write = `<div class="panel"><h3>Written after the verified fix</h3>${!w.length
    ? `<p class="empty">${S.final && S.final.status !== 'VERIFIED' ? 'Nothing written: the fix was not verified.' : 'Written only after the verifier passes the fix.'}</p>`
    : w.map((x) => `${(x.lessons || []).map(lessonCard).join('')}${x.effect ? `<p class="effect">${esc(sentence(x.effect))}</p>` : ''}`).join('')}</div>`;
  paint('#memory', `${flywheel(S)}<div class="memory">${recall}${write}</div>`);
}

// ---------- render loop ----------
function paint(sel, html) { paintEl($(sel), html, sel); }
function paintEl(el, html, key = `${el.closest('[id]')?.id}:${el.className}`) {
  if (painted[key] === html) return;
  painted[key] = html;
  el.innerHTML = html;
}

function render() {
  const S = derive(events.slice(0, cursor));
  renderHeader(S); renderRail(S); renderFinal(S); renderDiagnose(S); renderPlan(S); renderRounds(S); renderMemory(S);
  const last = events[cursor - 1];
  $('#cursor').textContent = runId ? `Run ${runId}: event ${cursor} of ${events.length}${last?.t_sim ? `, sim time ${String(last.t_sim).slice(11, 16)}` : ''}` : '';
  follow(S);
  firstRender = false;
}

// Keep the active stage on screen while the run plays, unless the presenter scrolled in the last few seconds.
// Inside a round, the contractor's visit and the verifier's email back get their own scroll, since they sit
// below the work order email.
function followTarget(S) {
  const c = S.current;
  if (S.final) return 'top';
  if (!c) return null;
  if (c.stage === 'contractor') {
    const visit = S.thread.filter((t) => t.kind === 'visit' && t.round === c.round).pop();
    if (visit) return `item-${visit.seq}`;
  }
  if (c.stage === 'redispatch') {
    const back = S.thread.filter((t) => t.kind === 'email' && t.actor === 'verifier' && (t.round === c.round || t.round === c.round + 1)).pop();
    const diff = S.diffusion.filter((d) => d.round === c.round).pop();
    if (back || diff) return `item-${(back || diff).seq}`;
  }
  return sectionFor(S, c.stage, c.round);
}
function follow(S) {
  const key = followTarget(S);
  if (key === lastStageKey) return;
  lastStageKey = key;
  if (firstRender || !key || Date.now() < followPausedUntil) return;
  if (key === 'top') { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
  document.getElementById(key)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
const pauseFollow = () => { followPausedUntil = Date.now() + 8000; };

// ---------- data ----------
async function fetchEvents() {
  const res = await fetch(`/runs/${runPath(runId)}/events.jsonl`, { cache: 'no-store' });
  if (!res.ok) throw new Error(res.status === 404 ? `no events yet for ${runId}` : `HTTP ${res.status} for ${runId}`);
  const out = [];
  for (const line of (await res.text()).split('\n')) {
    if (!line.trim()) continue;
    try { out.push(JSON.parse(line)); } catch { break; } // a half-written last line waits for the next poll
  }
  return out;
}

function notice(html) {
  const el = $('#notice');
  el.hidden = !html;
  el.innerHTML = html || '';
}

async function poll() {
  try {
    const evs = await fetchEvents();
    if (evs.length !== events.length || polls === 0) { events = evs; cursor = events.length; render(); }
    notice('');
  } catch (err) {
    // A run that has just started may not have written its log yet, so wait a few polls before saying so.
    if (polls === 0) render();
    if (polls > 4) notice(`Can't read this run: ${esc(err.message)}.`);
  }
  polls++;
  if (events.some((e) => e.type === 'demo.final') && ++finalPolls > 2) { loadRuns(); return; }  // finished: refresh the picker's status, stop polling
  setTimeout(poll, 1000);
}

const PACE = { 'demo.stage': 700, 'demo.sensor': 600, 'demo.agent': 1100, 'demo.check': 1500, 'demo.diagnosis': 2600, 'demo.workorder': 2400,
  'demo.email': 2600, 'demo.contractor': 2200, 'demo.jev': 2600, 'demo.verdict': 2000, 'demo.diffusion': 2400, 'demo.memory': 2400, 'demo.final': 2000 };
function setPlaying(p) {
  playing = p;
  $('#btnPlay').classList.toggle('paused', !p);
}
function tick() {
  clearTimeout(timer);
  if (!playing) return;
  if (cursor < events.length) { cursor++; render(); }
  if (cursor >= events.length) { setPlaying(false); return; }
  timer = setTimeout(tick, (PACE[events[cursor - 1]?.type] ?? 1000) / speed);
}
function step(n) {
  setPlaying(false);
  clearTimeout(timer);
  cursor = Math.max(0, Math.min(events.length, cursor + n));
  render();
}
function restart() {
  cursor = 0;
  lastStageKey = null;
  window.scrollTo({ top: 0 });
  render();
}

async function loadRuns() {
  let runs = [];
  try {
    const res = await fetch('/api/demo/runs', { cache: 'no-store' });
    if (res.ok) runs = await res.json();
    else {
      const all = await (await fetch('/api/runs', { cache: 'no-store' })).json();
      const demo = all.filter((r) => /demo/.test(r.id));
      runs = demo.length ? demo : all;
    }
  } catch { /* served without an API: keep ?run= */ }
  runs = (Array.isArray(runs) ? runs : runs.runs || []).map((r) => (typeof r === 'string' ? { id: r } : { ...r, id: r.run_id ?? r.id }));
  if (!runId && runs.length) { runId = runs[0].id; history.replaceState(null, '', runUrl(runId, replay)); }
  if (runId && !runs.some((r) => r.id === runId)) runs.unshift({ id: runId });
  const sel = $('#runPick');
  sel.innerHTML = runs.length
    ? runs.map((r) => {
      const tags = [r.status && human(r.status).toLowerCase(), r.stub && 'stub'].filter(Boolean).join(', ');
      return `<option value="${esc(r.id)}"${r.id === runId ? ' selected' : ''}>${esc(r.id)}${tags ? ` (${esc(tags)})` : ''}</option>`;
    }).join('')
    : '<option value="">No runs yet</option>';
  sel.disabled = !runs.length;
  sel.onchange = () => { if (sel.value) location.search = runUrl(sel.value, replay); };
}

async function startRun() {
  const btn = $('#btnRun');
  btn.disabled = true;
  btn.textContent = 'Starting…';
  try {
    const res = await fetch('/api/demo/run', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      // ?stub=1 rehearses with fixed answers and no model calls; ?fresh=1 starts from an empty lesson store.
      body: JSON.stringify({ scenario: params.get('scenario') || 's06_stale_scada', memory: params.get('memory') !== '0',
        stub: params.get('stub') === '1', fresh_memory: params.get('fresh') === '1' }) });
    if (!res.ok) throw new Error([404, 405, 501].includes(res.status) ? 'nosrv' : `HTTP ${res.status}`);
    const { run_id: id } = await res.json();
    if (!id) throw new Error('the server returned no run_id');
    // The run's folder appears a moment after the POST returns; wait for it so the first poll finds a log.
    for (let i = 0; i < 40; i++) {
      const listed = await fetch('/api/demo/runs', { cache: 'no-store' }).then((r) => r.json()).catch(() => []);
      if (Array.isArray(listed) && listed.some((r) => r.id === id)) break;
      await new Promise((r) => setTimeout(r, 250));
    }
    params.delete('fresh');  // a fresh lesson store is for this run only; the next Run must recall its lesson
    location.search = runUrl(id, false);
  } catch (err) {
    notice(err.message === 'nosrv'
      ? 'This server cannot start runs. Serve the page with <code>.venv/bin/python -m gridtwin.demo.serve --port 8010</code>, or start one with <code>.venv/bin/python -m gridtwin.demo.run --scenario s06_stale_scada</code> and pick it.'
      : `Could not start a run: ${esc(err.message)}.`);
    btn.disabled = false;
    btn.textContent = 'Run demo';
  }
}

function setupChrome() {
  // Anchors and sticky band titles sit just under the sticky header, whatever height it wraps to.
  const head = $('.top');
  new ResizeObserver(() => document.documentElement.style.setProperty('--head', `${head.offsetHeight}px`)).observe(head);
  // The twin is built once and never re-parented, so a re-render never reloads the 3D scene.
  $('#diagnose').innerHTML = `<div id="diagTop" class="diag-top"></div><div class="diag"><div id="diagMain"></div>
    <aside class="twin"><h3>3D twin <span class="chip real">Gaussian splat of the plant</span></h3>
      <div class="frame"><iframe id="twinFrame" title="Plant digital twin" allow="fullscreen"></iframe>
        <div id="twinWait" class="wait">The 3D twin opens with the run.</div></div>
      <div class="cap"><span>Camera stations and assets in one 3D frame. The verifier's JEV input comes from it.</span>
        <a id="twinOpen" href="twin.html" target="_blank" rel="noopener">Open full screen</a></div></aside></div>`;
  $('#btnRun').onclick = startRun;
  $('#btnTheme').onclick = () => {
    const next = getComputedStyle(document.documentElement).colorScheme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('gridtwin-demo-theme', next); } catch { /* storage blocked */ }
  };
  $('#rail').addEventListener('click', (e) => {
    const a = e.target.closest('a.stage');
    if (!a) return;
    e.preventDefault();
    const el = document.getElementById(a.getAttribute('href').slice(1));
    if (!el || el.hidden) return;
    pauseFollow();
    if (el.id === 'final') window.scrollTo({ top: 0, behavior: 'smooth' });
    else el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
  });
  document.addEventListener('toggle', (e) => {
    const k = e.target.dataset?.key;
    if (k) { if (e.target.open) openDetails.add(k); else openDetails.delete(k); }
  }, true);
  for (const ev of ['wheel', 'touchmove']) window.addEventListener(ev, pauseFollow, { passive: true });
  window.addEventListener('keydown', (e) => {
    if (['PageUp', 'PageDown', 'Home', 'End', 'ArrowUp', 'ArrowDown'].includes(e.key)) pauseFollow();
    if (!replay || e.target.closest('input, select, textarea, button, summary')) return;
    if (e.key === ' ') { e.preventDefault(); $('#btnPlay').click(); }
    if (e.key === 'ArrowRight') step(1);
    if (e.key === 'ArrowLeft') step(-1);
  });
}

async function main() {
  history.scrollRestoration = 'manual';  // a reload mid-demo starts at the top, where the run starts
  setupChrome();
  await loadRuns();
  if (!runId) { render(); notice('No runs yet. Press Run demo to start one.'); return; }
  if (!replay) { poll(); return; }
  $('#replayCtl').hidden = false;
  try { events = await fetchEvents(); } catch (err) { render(); notice(`Can't read this run: ${esc(err.message)}.`); return; }
  render();
  $('#btnPlay').onclick = () => {
    if (!playing && cursor >= events.length) restart();
    setPlaying(!playing);
    tick();
  };
  $('#btnStep').onclick = () => step(1);
  $('#btnRestart').onclick = () => { clearTimeout(timer); restart(); setPlaying(true); timer = setTimeout(tick, 600); };
  $('#speed').onchange = (e) => { speed = Number(e.target.value) || 1; if (playing) tick(); };
  setPlaying(true);
  timer = setTimeout(tick, 800);
}

main();
