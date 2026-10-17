// GridTwin incident page: the outcome up top, the five phases, then the plant twin (drone passes, then the unified
// splat) with asset health beside the agent team's execution graph and the definition-of-done checks. Reads runs/<id>/events.jsonl (contract C2) only.
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
const STEPS = ['Diagnose', 'Plan', 'Act', 'Verify', 'Close'];
let selPhase = null;
const dodChecks = (d) => { const c = d?.data?.checks; return !c ? [] : Array.isArray(c) ? c : Object.values(c).flat(); };

let REQS = {}, STDS = {};
// Standards ids from plant.yaml as the code families operators know: CEC adopts the NEC article numbers.
const codeChip = (id) => {
  let m;
  if ((m = id.match(/^CEC-(\d+(?:\.\d+)?)/))) return `NEC ${m[1]}`;
  if ((m = id.match(/^NFPA-70E/))) return 'NFPA 70E';
  if ((m = id.match(/^CCR8/))) return 'NFPA 70E · Cal/OSHA LOTO';
  if ((m = id.match(/^CFC-([\d.]+)/))) return `CA Fire Code ${m[1]}`;
  return id.replace(/-/g, ' ');
};

// ---------- fold the log ----------
function derive(evs) {
  const S = { goal: null, dod: null, final: null, assets: {}, lanes: {}, verdicts: {}, findings: [], acts: [], usage: 0, last: null,
    shots: {}, lastActor: null, roles: {}, gates: [], replans: [], hold: null, lessons: [], pages: [], failed: [], sources: {}, dods: 0,
    usd: 0, cached: 0, first: evs[0], at: [] };
  const mark = (k, i, e) => { if (S.at[k] == null) S.at[k] = { i, t: e.t_sim }; };
  const set = (a, cls, st) => { const t = a && tileOf(a); if (t) S.assets[t] = { cls, st: a === t ? st : `${a} ${st}`, raw: a }; };
  let actor = 'planner';
  evs.forEach((e, i) => {
    const d = e.data || {};
    S.last = e;
    if (e.type === 'tool_call') S.callBy = d.by || null;
    const who = e.actor === 'sim' ? (d.by || (e.type === 'tool_result' && S.callBy) || actor) : e.actor;
    if (!['sim', 'judge', 'operator'].includes(e.actor) || e.type === 'claim') actor = e.actor === 'operator' ? actor : e.actor;
    if (who && !['judge', 'operator'].includes(who)) {
      const L = (S.lanes[laneOf(who)] ??= { ticks: [], what: '' });
      L.ticks.push({ i, type: e.type, pass: d.pass });
      L.what = describe(e) || L.what;
      L.at = i;
    }
    if (e.type === 'tool_result' && d.name === 'get_camera_view' && who) {
      const src = (S.sources[laneOf(who)] ??= new Set());
      src.add('camera inspection wall');
      if (d.result?.render_path) src.add('Gaussian-splat twin');
    }
    if (e.type === 'tool_call' && who && ['read_telemetry', 'list_alarms', 'run_diagnostic'].includes(d.name)) (S.sources[laneOf(who)] ??= new Set()).add('telemetry');
    if (e.type === 'tool_call' && who && d.name === 'search_web') (S.sources[laneOf(who)] ??= new Set()).add('web');
    switch (e.type) {
      case 'goal': S.goal = d.text; S.scenario = d.scenario_id; break;
      case 'dod': S.dod = e; S.dods++; mark(1, i, e); break;
      case 'gate': S.gates.push(e); break;
      case 'supervisor':
        if (d.decision === 'replan') S.replans.push(e);
        if (d.decision === 'hold') S.hold = e;
        if (d.decision === 'page') S.pages.push({ data: { brief: d.reason } });
        break;
      case 'lesson': S.lessons.push(e); break;
      case 'finding': S.findings.push(e); mark(0, i, e); break;
      case 'tool_call':
        if (d.name === 'operate') { S.acts.push(e); mark(2, i, e); set(d.args?.asset, d.args?.action === 'open' ? 'iso' : 'work', d.args?.action === 'open' ? 'isolated' : String(d.args?.action || '').replace(/_/g, ' ')); }
        if (d.name === 'dispatch_tech') { S.acts.push(e); mark(2, i, e); set(d.args?.asset, 'work', d.args?.work_order?.replace(/_/g, ' ')); }
        break;
      case 'tool_result':
        if (d.name === 'get_camera_view' && d.result?.asset) (S.shots[d.result.asset] ??= []).push(d.result);
        break;
      case 'verdict': {
        S.verdicts[d.check_id] = { ...d, t: e.t_sim };
        if (S.acts.length) mark(3, i, e);
        if (!d.pass && !S.failed.includes(d.check_id)) S.failed.push(d.check_id);
        const check = dodChecks(S.dod).find((c) => (c.id ?? c.check_id) === d.check_id);
        // a failed check marks only the assets its evidence blames (below peers, alarming, failing a reading), not every asset it covers
        const ev = d.evidence || {};
        const blamed = d.pass ? [] : mentioned([ev.below, ev.active_alarms, (ev.readings || []).filter((r) => r.reason), ev.stale, ev.problems]);
        (d.pass ? mentioned([check?.asset, check?.viewpoint, check?.claim, ev]) : blamed)
          .forEach((a) => { const cur = S.assets[tileOf(a)];
            if (!(d.pass && cur?.cls === 'bad' && cur.chk !== d.check_id)) { set(a, d.pass ? 'verified' : 'bad', d.pass ? 'verified' : 'check failed'); if (S.assets[tileOf(a)]) S.assets[tileOf(a)].chk = d.check_id; } });
        break;
      }
      case 'page': S.pages.push(e); mentioned(d.brief).forEach((a) => set(a, 'bad', 'escalated')); break;
      case 'usage': {
        S.usage += (d.prompt || 0) + (d.completion || 0);
        const r = (S.roles[d.role] ??= { model: d.model, calls: 0, lat: 0, nlat: 0, cached: 0, prompt: 0 });
        r.model = d.model || r.model; r.calls++; r.cached += d.cached || 0; r.prompt += d.prompt || 0;
        S.usd += d.usd || 0; S.cached += d.cached || 0;
        if (d.latency_s != null) { r.lat += d.latency_s; r.nlat++; }
        break;
      }
      case 'final': S.final = d; mark(4, i, e); break;
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

// ---------- hero, phases, checks ----------
const hhmm = (t) => String(t || '').replace(' ', 'T').slice(11, 16);
const PHASE_HEAD = (S, i) => {
  const f = S.focus || 'the plant';
  const vs = Object.values(S.verdicts), n = doneChecks(S).length;
  return [
    S.findings.length ? `${S.findings.length} finding${S.findings.length === 1 ? '' : 's'} on ${f}.` : `Looking for what is wrong with ${f}.`,
    S.dod ? `${n} checks define done.` : 'Writing the definition of done.',
    S.acts.length ? `${actLine(S.acts[S.acts.length - 1])}.` : 'Choosing a safe action.',
    vs.length ? `${vs.filter((v) => v.pass).length} of ${n || vs.length} checks pass.` : 'An independent verifier re-runs every check.',
    S.final ? (S.final.status === 'DONE' ? 'Independent checks agree: it is fixed.' : S.final.status === 'ESCALATED' ? 'Handed to a person, with the reason.' : `Closed as ${S.final.status.replace(/_/g, ' ').toLowerCase()}.`) : 'Closing the incident.',
  ][i];
};
const actLine = (e) => { const a = e.data.args || {}; const w = (a.work_order || a.action || e.data.name).replace(/_/g, ' '); return `${w[0].toUpperCase()}${w.slice(1)} ${a.asset || ''}`.trim(); };
const doneChecks = (S) => { const c = S.dod?.data?.checks; return c && !Array.isArray(c) && c.DONE ? c.DONE : dodChecks(S.dod); };

function renderHero(S) {
  const f = S.focus;
  const vs = Object.values(S.verdicts), passed = vs.filter((v) => v.pass).length, n = doneChecks(S).length;
  const st = S.final?.status;
  $('#crumb').textContent = (S.scenario || runId || '').replace(/^s\d+_/, (m) => m.toUpperCase().replace('_', ' · ')).replace(/_/g, ' ');
  $('#kicker').textContent = [st === 'DONE' ? 'Resolved by agents · independently verified' : st ? `Closed ${st.replace(/_/g, ' ').toLowerCase()}` : events.length ? 'Agents working' : 'Waiting for a run',
    hhmm(S.last?.t_sim)].filter(Boolean).join(' · ');
  const h = st === 'DONE' ? `${f || 'The plant'} is back in service.` : st === 'ESCALATED' ? `${f || 'This incident'} needs a person.`
    : st ? `${f || 'The plant'}: ${st.replace(/_/g, ' ').toLowerCase()}.` : f ? `Agents are working on ${f}.` : 'Agents are on it.';
  $('#headline').innerHTML = esc(h) + (!st && events.length ? DOTS : '');
  $('#summary').textContent = S.final?.summary || S.goal || '';
  const mins = Math.round(minutes(S.last?.t_sim) - minutes(S.first?.t_sim));
  const kpi = (k, v, u = '') => `<div><dt>${k}</dt><dd>${v}${u ? `<small> ${u}</small>` : ''}</dd></div>`;
  $('#kpis').innerHTML = kpi('Checks passing', vs.length ? `${passed}/${n || vs.length}` : '—')
    + kpi('Sim time', S.first ? mins : '—', S.first ? 'min' : '')
    + kpi('Agent cost', S.usd ? `$${S.usd.toFixed(2)}` : '—')
    + kpi('Tokens', S.usage ? (S.usage / 1000).toFixed(1) : '—', S.usage ? `k · ${Math.round((100 * S.cached) / Math.max(1, Object.values(S.roles).reduce((a, r) => a + r.prompt, 0)))}% cached` : '');
  $('#clock').textContent = (S.last?.t_sim || '').replace('T', ' ');
}

function phaseState(S) {
  const vs = Object.values(S.verdicts), n = doneChecks(S).length, passed = vs.filter((v) => v.pass).length;
  const done = [S.findings.length > 0, !!S.dod, S.acts.length > 0, !!S.final && passed >= n && n > 0, !!S.final];
  const fail = [false, false, false, S.acts.length > 0 && vs.some((v) => !v.pass), !!S.final && S.final.status !== 'DONE'];
  const now = S.final ? 4 : Math.max(0, done.indexOf(false));
  const sub = [`${S.findings.length} finding${S.findings.length === 1 ? '' : 's'}`, S.dod ? `${n} checks${S.replans.length ? ` · ${S.replans.length} replans` : ''}` : '',
    S.acts.length ? actLine(S.acts[S.acts.length - 1]) : '', vs.length ? `${passed}/${n || vs.length} passed` : '', S.final?.status?.replace(/_/g, ' ') || ''];
  return { done, fail, now, sub };
}

function renderPhases(S) {
  const P = phaseState(S);
  const sel = selPhase ?? P.now;
  $('#phases').innerHTML = STEPS.map((s, i) => {
    const cls = P.fail[i] ? 'fail' : i === P.now && !S.final ? 'now' : P.done[i] || i < P.now ? 'done' : '';
    return `<button type="button" class="phase ${cls} ${i === sel ? 'sel' : ''}" data-phase="${i}">
      <span class="n">0${i + 1}${S.at[i] ? ` · ${hhmm(S.at[i].t)}` : ''}</span><span class="nm">${s}</span>
      <span class="sb">${esc(P.sub[i])}${i === P.now && !S.final && events.length ? DOTS : ''}</span></button>`;
  }).join('');
  $('#phaseKicker').textContent = `Phase 0${sel + 1} of 05${S.at[sel] ? ` · ${hhmm(S.at[sel].t)}` : ''}`;
  $('#phaseHead').innerHTML = esc(PHASE_HEAD(S, sel)) + (sel === P.now && !S.final && events.length ? DOTS : '');
  $('#plantAt').textContent = S.last?.t_sim ? `Plant at ${hhmm(S.last.t_sim)}` : 'Plant';
  const atEnd = cursor >= events.length;
  $('#nextLabel').textContent = atEnd ? 'Replay from the start' : sel < 4 ? `Next: ${STEPS[sel + 1]}` : 'Jump to now';
}

function renderAssets(S) {
  const bad = Object.values(S.assets).filter((a) => a.cls === 'bad').length;
  $('#health').textContent = `${TILES.length - bad} of ${TILES.length} healthy`;
  $('#assets').innerHTML = TILES.map((t) => {
    const a = S.assets[t];
    return `<div class="asset ${a?.cls || ''}" data-asset="${t}"><span class="id">${t}</span><span class="bar"></span><span class="st">${esc(a?.st || 'running')}</span></div>`;
  }).join('');
}

function renderIncident(S) {
  const checks = doneChecks(S);
  $('#incident').hidden = !checks.length;
  if (!checks.length) return;
  const vs = Object.values(S.verdicts);
  $('#incState').textContent = `${vs.filter((v) => v.pass && checks.some((c) => (c.id ?? c.check_id) === v.check_id)).length} of ${checks.length} pass`;
  $('#reqs').innerHTML = checks.map((c) => {
    const id = c.id ?? c.check_id;
    const v = S.verdicts[id];
    const reqs = [c.requirement, ...(c.requirements || [])].filter(Boolean);
    const title = REQS[reqs[0]]?.title || String(c.rule || c.op || c.kind || id).replace(/_/g, ' ');
    const stds = new Map(reqs.flatMap((r) => REQS[r]?.standards || []).map((sid) => [codeChip(sid), sid]));
    const chips = [...stds].map(([x, sid]) => `<a class="chip ${/^(NEC|NFPA|CA Fire)/.test(x) ? 'code' : ''}" href="codes.html?q=${encodeURIComponent(sid)}" target="_blank">${esc(x)}</a>`).join('');
    const how = [id, c.op || c.rule, c.point, c.asset || (c.assets || []).join(' ')].filter(Boolean).join(' · ');
    return `<li class="ck ${v ? (v.pass ? 'pass' : 'fail') : 'pending'}"><span class="mk">${v ? (v.pass ? '✓' : '✕') : DOTS}</span>
      <a class="lb" href="codes.html?view=req&req=${encodeURIComponent(reqs[0] || '')}" target="_blank">${esc(title)}</a><span class="tm">${v ? hhmm(v.t) : ''}</span>
      <span class="rs"><span class="mono">${esc(clip(how, 48))}</span>${chips}</span></li>`;
  }).join('');
}

let META = { roles: {}, scenarios: {} };
const shortModel = (m) => String(m || '').split('/').pop();
const vendor = (m) => String(m || '').split('/')[0];
const modelOf = (S, role) => S.roles[role]?.model || META.roles[role];
const minutes = (ts) => (ts ? new Date(ts.replace(' ', 'T') + 'Z').getTime() / 60000 : 0);
const DOTS = '<span class="dots"><i></i><i></i><i></i></span>';

function activeSet(S) {
  if (S.final) return new Set();
  const out = new Set();
  events.slice(Math.max(0, cursor - 4), cursor).forEach((e) => {
    const a = e.actor === 'sim' ? e.data?.by : e.actor;
    if (a) out.add(laneOf(a));
  });
  return out;
}

function renderGraph(S) {
  const act = activeSet(S);
  const node = (key, title, model, body, cls = '') =>
    `<div class="node ${cls} ${act.has(key) ? 'active' : ''}"><div class="t">${title}${model ? `<span class="chip m">${esc(shortModel(model))}</span>` : ''}</div>${body}</div>`;
  const edge = (live) => `<div class="edge ${live ? 'live' : ''}"></div>`;
  const what = (key) => { const L = S.lanes[key]; return L ? `<div class="d">${esc(clip(L.what, 70))}${act.has(key) ? DOTS : ''}</div>` : ''; };
  const g = [];

  // 1. planner
  const nChecks = dodChecks(S.dod).length;
  const depth = S.replans.length;
  g.push(node('planner', 'Planner', modelOf(S, 'planner'),
    `<div class="d">${S.dod ? `Executable definition of done · ${nChecks} checks` : 'Writing the definition of done'}${act.has('planner') ? DOTS : ''}</div>`
    + (depth || S.failed.length ? `<div class="recur">${S.failed.map((c) => `<span class="sg">sub-goal ${esc(c)}</span>`).join('')}
        ${depth ? `<span class="back">↺ replan · depth ${depth}</span>` : ''}</div>` : ''),
    S.dod ? 'done' : ''));
  g.push(edge(act.has('planner')));

  // 2. sub-agents fan out
  const workers = Object.keys(S.lanes).filter((l) => !['planner', 'verifier', 'supervisor', 'gate', 'sim', 'judge', 'operator'].includes(l) || l === 'operator');
  const wl = [...new Set(['inspector', 'diagnoser', 'researcher', 'operator'].filter((w) => S.lanes[w]).concat(workers.filter((w) => !['planner', 'verifier', 'supervisor', 'gate'].includes(w))))];
  if (wl.length) {
    g.push(`<div class="fan-lbl">Sub-agents · ${esc(shortModel(modelOf(S, 'worker')))} · in parallel</div>`);
    g.push(`<div class="fan">${wl.map((w) => node(w, esc(w), null,
      `${what(w)}<div class="chip-row">${[...(S.sources[w] || [])].map((x) => `<span class="chip src">${esc(x)}</span>`).join('')}</div>`)).join('')}</div>`);
    g.push(edge(wl.some((w) => act.has(w))));
  }

  // 3. safety gate
  if (S.gates.length) {
    const pol = S.gates.filter((e) => e.data.source === 'policy');
    const mod = S.gates.filter((e) => e.data.source && e.data.source !== 'policy');
    const backend = mod[mod.length - 1]?.data.source || META.gate_backend;
    const modelName = backend === 'jev' ? 'Jev' : backend === 'openai' ? 'OpenAI Decisions' : shortModel(modelOf(S, 'gate'));
    const vetoes = S.gates.filter((e) => e.data.route !== 'proceed');
    const lastVeto = vetoes[vetoes.length - 1];
    g.push(node('gate', 'Safety gate', null,
      `<div class="split"><div><b>LOTO interlocks</b>hard-coded · ${pol.length} decision${pol.length === 1 ? '' : 's'}</div>
       <div><b>${esc(modelName)}</b>veto-only decision model · ${mod.length}</div></div>
       ${lastVeto ? `<div class="veto">VETO · ${esc(String(lastVeto.data.proposed?.action || lastVeto.data.proposed?.tool || '').replace(/_/g, ' '))} ${esc(lastVeto.data.proposed?.asset || '')} · ${esc(clip(lastVeto.data.reason, 60))}</div>` : ''}`,
      lastVeto ? 'fail' : 'done'));
    g.push(edge(act.has('gate')));
  }

  // 4. verifier, with soak timer
  if (Object.keys(S.verdicts).length || act.has('verifier')) {
    const vm = modelOf(S, 'verifier');
    const diff = vendor(vm) !== vendor(modelOf(S, 'worker'));
    const vs = Object.values(S.verdicts);
    const vps = [...new Set(vs.map((v) => v.evidence?.viewpoint).filter(Boolean))];
    let soak = '';
    if (S.hold) {
      const hold = 30, el = Math.max(0, Math.min(hold, minutes(S.last?.t_sim) - minutes(S.hold.t_sim)));
      soak = `<div class="soak">soak ${Math.floor(el)}:00 / ${hold}:00<i><b style="width:${(100 * el) / hold}%"></b></i>${el >= hold ? 're-verify' : ''}</div>`;
    }
    g.push(node('verifier', 'Verifier', vm,
      `<div class="chip-row">${diff ? '<span class="chip strong">different model family</span>' : ''}${vps.map((v) => `<span class="chip src">own view ${esc(v)}</span>`).join('')}</div>
       <div class="d">${vs.filter((v) => v.pass).length}/${vs.length} checks passed${act.has('verifier') ? DOTS : ''}</div>${soak}`,
      vs.some((v) => !v.pass) ? 'fail' : vs.length ? 'done' : ''));
  }

  // 5. verified memory
  const writes = S.lessons.filter((e) => e.data.op === 'write');
  if (S.lessons.length) {
    g.push(edge(false));
    g.push(node('memory', 'Verified memory', null,
      `<div class="chip-row"><span class="chip strong">verified only</span><span class="chip">poisoning-resistant</span>
       <span class="chip src">${S.lessons.filter((e) => e.data.op === 'read').length} read · ${writes.length} written</span></div>`, writes.length ? 'done' : ''));
  }

  // 6. human escalation, only when the loop escalates
  const escalated = S.pages[S.pages.length - 1] || (S.final?.status === 'ESCALATED' ? { data: { brief: S.final.summary } } : null);
  if (escalated) {
    g.push(edge(false));
    g.push(`<div class="node esc"><div class="t">Human escalation</div><div class="d">${esc(clip(escalated.data.brief, 90))}</div></div>`);
  }
  $('#graph').innerHTML = g.join('');

  // footer: live per-role routing from the usage events
  const roles = Object.entries(S.roles);
  $('#roles').innerHTML = roles.length ? roles.map(([r, x]) => `<b>${esc(r)}</b><span>${esc(shortModel(x.model))}</span>
    <span>${x.nlat ? `${(x.lat / x.nlat).toFixed(1)} s` : '—'}</span><span>${(x.cached / 1000).toFixed(1)}k cached</span>`).join('')
    : Object.entries(META.roles || {}).filter(([r]) => ['planner', 'worker', 'verifier', 'gate'].includes(r)).map(([r, m]) => `<b>${esc(r)}</b><span>${esc(shortModel(m))}</span><span></span><span></span>`).join('');
  const lesson = S.lessons.filter((e) => e.data.op === 'write').pop() || S.lessons[S.lessons.length - 1];
  $('#lesson').innerHTML = lesson ? `“${esc(clip(lesson.data.text, 160))}”<div class="muted">${lesson.data.op === 'write' ? 'Written after the verifier passed' : 'Recalled from verified memory'}</div>`
    : `<span class="muted">${S.final ? 'Nothing new to keep.' : 'Kept only once the verifier passes.'}</span>`;
  const sc = META.scenarios || {};
  const total = Object.values(sc).reduce((a, b) => a + b, 0);
  $('#bench').textContent = ['Hidden ground truth', total ? `${total} scenarios, ${sc.heldout || 0} held out` : ''].filter(Boolean).join(' · ');
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
  const st = $('#state');
  st.className = `live mono ${S.final ? (S.final.status === 'DONE' ? 'ok' : 'warn') : events.length ? 'work' : ''}`;
  st.textContent = `${replay || cursor < events.length ? 'Replay' : S.final ? 'Closed' : events.length ? 'Live' : 'Idle'}${S.first ? ` · sim ${S.first.t_sim.slice(0, 10)}` : ''}`;
  renderHero(S); renderPhases(S); renderAssets(S); renderIncident(S); renderGraph(S); syncTwin();
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
  try {
    const evs = await fetchEvents();
    if (evs.length !== events.length) { const live = cursor >= events.length; events = evs; if (live) cursor = evs.length; render(); }
  } catch { /* run not started */ }
  setTimeout(poll, 1000);
}

let playing = replay;
function tick() {
  if (!playing) return;
  if (cursor < events.length) { cursor++; render(); }
  const e = events[cursor - 1];
  const slow = e && (e.type === 'tool_call' ? 450 : e.type === 'tool_result' ? 250 : 1100);
  if (cursor < events.length) setTimeout(tick, (slow || 600) / speed);
}

async function main() {
  try { META = await (await fetch('/api/meta')).json(); } catch { /* optional */ }
  if (META.plant) $('#plant').textContent = META.plant.replace(/^GridTwin\s+/, '');
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
  // phases: jump the log to where a phase starts; the main button steps forward or replays from the start
  const seek = (i) => { const S = derive(events); selPhase = i; cursor = S.at[i] ? S.at[i].i + 1 : events.length; playing = false; render(); };
  $('#phases').onclick = (e) => { const b = e.target.closest('[data-phase]'); if (b) seek(+b.dataset.phase); };
  $('#prev').onclick = () => seek(Math.max(0, (selPhase ?? phaseState(S0).now) - 1));
  $('#next').onclick = () => {
    if (cursor >= events.length) { selPhase = null; cursor = 0; playing = true; render(); setTimeout(tick, 400); return; }
    const i = selPhase ?? phaseState(S0).now;
    if (i < 4 && derive(events).at[i + 1]) seek(i + 1); else { selPhase = null; cursor = events.length; render(); }
  };
  $('#crumb').ondblclick = () => runId && window.open(`/?run=${encodeURIComponent(runId).replace(/%2F/g, '/')}`, '_blank');  // operator view
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
