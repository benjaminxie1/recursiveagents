// GridTwin incident page: the outcome and the operator's next action up top, the agent swarm as a live graph with each
// agent's thinking and context, an operator reprompt box, then four drone feeds, the interactive 3D model, assets and
// checks. Reads runs/<id>/events.jsonl (contract C2) only; the reprompt box posts to /api/reprompt.
// ?run=<id> picks a run (default: newest), &replay=1 plays it back on its own, &speed=2 speeds playback up.

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
const laneOf = (actor) => actor.replace(/^(worker|subagent):/, '');
const STEPS = ['Diagnose', 'Plan', 'Act', 'Verify', 'Close'];
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
    usd: 0, cached: 0, first: evs[0], at: [], minds: {}, reprompts: [], lastMind: null, calls: {}, dagents: {}, rounds: 0 };
  const STAGE = { diagnose: 0, plan: 1, dispatch: 2, contractor: 2, redispatch: 2, verify: 3 };
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
      case 'goal': if (d.reprompt) S.reprompts.push(e); else { S.goal = d.text; S.scenario = d.scenario_id; } break;
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
        if (who) (S.calls[laneOf(who)] ??= []).push(e);
        if (S.at[0] == null && who) mark(0, i, e);
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
        S.minds[laneOf(e.actor)] = { ...d, t: e.t_sim, i };
        S.lastMind = laneOf(e.actor);
        if (d.latency_s != null) { r.lat += d.latency_s; r.nlat++; }
        break;
      }
      case 'final': S.final = d; mark(4, i, e); break;
      // the end-to-end demo pipeline (gridtwin/demo): recursive agent teams, plant checks, memory flywheel
      case 'demo.stage': if (d.status === 'start' && STAGE[d.stage] != null) mark(STAGE[d.stage], i, e); S.stage = d.stage; S.rounds = Math.max(S.rounds, d.round || 0); break;
      case 'demo.agent': S.dagents[d.id] = { ...d, t: e.t_sim, i }; S.lastAgent = d.id; break;
      case 'demo.diagnosis': S.dfocus = d.asset; S.findings.push({ data: { text: d.summary || `${d.asset} ${d.fault}` } }); set(d.asset, 'bad', String(d.fault || 'fault').replace(/_/g, ' ')); break;
      case 'demo.workorder': S.acts.push({ data: { name: 'dispatch_tech', args: { asset: d.asset, work_order: d.wo_id } } }); set(d.asset, 'work', d.wo_id); break;
      case 'demo.check': S.verdicts[d.name] = { check_id: d.name, pass: d.pass, t: e.t_sim, detail: d.detail, kind: d.kind, evidence: d.evidence }; if (!d.pass && !S.failed.includes(d.name)) S.failed.push(d.name); break;
      case 'demo.verdict': if (!d.pass) S.replans.push(e); else set(S.dfocus, 'verified', 'verified'); break;
      case 'demo.memory': S.lessons.push({ data: { op: d.op === 'recall' ? 'read' : 'write', text: d.lessons?.[0]?.text || d.effect, effect: d.effect } }); break;
      case 'demo.final': S.final = { status: d.status === 'VERIFIED' ? 'DONE' : d.status, summary: d.summary }; S.usd = d.usd ?? S.usd; mark(4, i, e); break;
    }
  });
  S.focus = S.dfocus || focusAsset(S);
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
    case 'demo.agent': return `${d.status === 'done' ? 'Done' : 'Spawned'} · ${d.task || d.role}`;
    case 'demo.stage': return `${d.stage} ${d.status}${d.round ? ` · round ${d.round}` : ''}`;
    case 'demo.check': return `${d.pass ? 'Pass' : 'Fail'} · ${d.name}`;
    case 'demo.verdict': return d.pass ? 'Verified' : `Not verified · ${(d.failed || []).join(', ')}`;
    case 'demo.email': return `${d.direction === 'in' ? 'Email in' : 'Email out'} · ${d.subject}`;
    case 'demo.contractor': return d.note;
    case 'demo.memory': return d.effect;
    case 'demo.workorder': return `${d.wo_id} · ${d.work_order}`;
    case 'demo.diagnosis': return d.summary;
    case 'demo.final': return d.summary;
    default: return null;
  }
}

// ---------- hero, phases, checks ----------
const hhmm = (t) => String(t || '').replace(' ', 'T').slice(11, 16);
const actLine = (e) => { const a = e.data.args || {}; const w = (a.work_order || a.action || e.data.name).replace(/_/g, ' '); return `${w[0].toUpperCase()}${w.slice(1)} ${a.asset || ''}`.trim(); };
const doneChecks = (S) => {
  if (!S.dod && Object.keys(S.dagents).length) return Object.values(S.verdicts).map((v) => ({ id: v.check_id, kind: v.kind, title: v.detail }));
  const c = S.dod?.data?.checks; return c && !Array.isArray(c) && c.DONE ? c.DONE : dodChecks(S.dod);
};
const STATUS = { DONE: 'Resolved', ESCALATED: 'Escalated', NO_ACTION: 'No action', NOT_DONE: 'Not resolved' };
let signed = false;

function renderHero(S) {
  const vs = Object.values(S.verdicts), passed = vs.filter((v) => v.pass).length, n = doneChecks(S).length;
  const st = S.final?.status;
  $('#crumb').textContent = (S.scenario || runId || '').replace(/^s\d+_/, (m) => m.toUpperCase().replace('_', ' · ')).replace(/_/g, ' ');
  $('#kicker').textContent = [st === 'DONE' ? 'Independently verified' : st ? 'Closed' : events.length ? 'Agents working' : 'Waiting for a run', hhmm(S.last?.t_sim)].filter(Boolean).join(' · ');
  $('#headline').textContent = S.focus || 'Plant';
  const sEl = $('#status');
  sEl.className = `status ${st === 'DONE' ? 'ok' : st ? 'bad' : 'work'}`;
  sEl.innerHTML = st ? esc(STATUS[st] || st.replace(/_/g, ' ').toLowerCase()) : events.length ? `In progress${DOTS}` : '';
  $('#summary').textContent = S.goal ? `“${S.goal}”` : '';
  const mins = Math.round(minutes(S.last?.t_sim) - minutes(S.first?.t_sim));
  const kpi = (k, v, u = '') => `<div><dt>${k}</dt><dd>${v}${u ? `<small> ${u}</small>` : ''}</dd></div>`;
  $('#kpis').innerHTML = kpi('Checks passing', vs.length ? `${passed}/${n || vs.length}` : '—')
    + kpi('Sim time', S.first ? mins : '—', S.first ? 'min' : '')
    + kpi('Agent cost', S.usd ? `$${S.usd.toFixed(2)}` : '—')
    + kpi('Tokens', S.usage ? (S.usage / 1000).toFixed(1) : '—', S.usage ? `k · ${Math.round((100 * S.cached) / Math.max(1, Object.values(S.roles).reduce((a, r) => a + r.prompt, 0)))}% cached` : '');
  $('#clock').textContent = (S.last?.t_sim || '').replace('T', ' ');
  // the operator's one action: what is left for a person once the agents are done
  const cta = $('#cta');
  const label = !events.length ? 'Waiting for a run' : !S.final ? 'Agents working' : st === 'DONE' ? (signed ? 'Signed off' : 'Sign off the fix')
    : st === 'ESCALATED' ? 'Take over' : 'Review';
  cta.disabled = !S.final || signed;
  cta.classList.toggle('busy', !S.final && events.length > 0);
  $('#ctaLabel').innerHTML = esc(label) + (!S.final && events.length ? DOTS : '');
}

function phaseState(S) {
  const vs = Object.values(S.verdicts), n = doneChecks(S).length, passed = vs.filter((v) => v.pass).length;
  const raw = [S.at[0] != null, !!S.dod, S.acts.length > 0, !!S.final && passed >= n && n > 0, !!S.final];
  const done = raw.map((d, i) => d || raw.slice(i + 1).some(Boolean));  // a later phase means the earlier ones are over
  const fail = [false, false, false, !!S.final && vs.some((v) => !v.pass), !!S.final && S.final.status !== 'DONE'];
  const now = S.final ? 4 : Math.max(0, done.indexOf(false));
  const sub = [S.findings.length ? `${S.findings.length} finding${S.findings.length === 1 ? '' : 's'}` : '', S.dod ? `${n} checks` : '',
    S.acts.length ? actLine(S.acts[S.acts.length - 1]) : '', vs.length ? `${passed}/${n || vs.length} passed` : '', STATUS[S.final?.status] || ''];
  return { done, fail, now, sub };
}

function renderPhases(S) {
  const P = phaseState(S);
  $('#phases').innerHTML = STEPS.map((s, i) => {
    const cls = P.fail[i] ? 'fail' : i === P.now && !S.final ? 'now' : P.done[i] ? 'done' : '';
    return `<div class="phase ${cls}"><span class="n">0${i + 1}${S.at[i] ? ` · ${hhmm(S.at[i].t)}` : ''}</span><span class="nm">${s}${i === P.now && !S.final && events.length ? DOTS : ''}</span><span class="sb">${esc(P.sub[i])}</span></div>`;
  }).join('');
}

function renderAssets(S) {
  const off = TILES.filter((t) => ['bad', 'work', 'warn', 'iso'].includes(S.assets[t]?.cls)).length;
  $('#health').textContent = `${TILES.length - off}/${TILES.length} healthy`;
  $('#assets').innerHTML = TILES.map((t) => {
    const a = S.assets[t];
    return `<div class="asset ${a?.cls || ''} ${picked === t ? 'picked' : ''}" data-asset="${t}"><span class="id">${t}</span><span class="bar"></span><span class="st">${esc(a?.st || 'running')}</span></div>`;
  }).join('');
}

function renderIncident(S) {
  const checks = doneChecks(S);
  $('#incident').hidden = !checks.length;
  if (!checks.length) return;
  const vs = Object.values(S.verdicts);
  $('#incState').textContent = `${vs.filter((v) => v.pass && checks.some((c) => (c.id ?? c.check_id) === v.check_id)).length}/${checks.length} pass`;
  $('#reqs').innerHTML = checks.map((c) => {
    const id = c.id ?? c.check_id;
    const v = S.verdicts[id];
    const reqs = [c.requirement, ...(c.requirements || [])].filter(Boolean);
    const title = REQS[reqs[0]]?.title || c.title || String(c.rule || c.op || c.kind || id).replace(/_/g, ' ');
    const stds = new Map(reqs.flatMap((r) => REQS[r]?.standards || []).map((sid) => [codeChip(sid), sid]));
    const chips = [...stds].map(([x, sid]) => `<a class="chip ${/^(NEC|NFPA|CA Fire)/.test(x) ? 'code' : ''}" href="codes.html?q=${encodeURIComponent(sid)}" target="_blank">${esc(x)}</a>`).join('');
    return `<li class="ck ${v ? (v.pass ? 'pass' : 'fail') : 'pending'}"><span class="mk">${v ? (v.pass ? '✓' : '✕') : DOTS}</span>
      <a class="lb" href="codes.html?view=req&req=${encodeURIComponent(reqs[0] || '')}" target="_blank">${esc(title)}</a><span class="tm">${v ? hhmm(v.t) : ''}</span>
      <span class="rs"><span class="mono">${esc(id)}</span>${chips}</span></li>`;
  }).join('');
}

let META = { roles: {}, scenarios: {} };
const shortModel = (m) => String(m || '').split('/').pop();
const vendor = (m) => String(m || '').split('/')[0];
const modelOf = (S, role) => S.roles[role]?.model || META.roles[role];
const roleOfLane = (l) => (['planner', 'verifier', 'supervisor', 'gate'].includes(l) ? l : 'worker');
const minutes = (ts) => (ts ? new Date(ts.replace(' ', 'T') + 'Z').getTime() / 60000 : 0);
const DOTS = '<span class="dots"><i></i><i></i><i></i></span>';

function activeSet(S) {
  if (S.final) return new Set();
  const out = new Set();
  events.slice(Math.max(0, cursor - 4), cursor).forEach((e) => {
    const a = e.actor === 'sim' ? e.data?.by : e.actor;
    if (a && !['judge', 'operator'].includes(a)) out.add(laneOf(a));
  });
  return out;
}

// ---------- the swarm: planner -> sub-agents -> safety gate -> verifier -> outcome, with the replan loop back ----------
let selLane = null;
function renderFlow(S) {
  if (Object.keys(S.dagents).length) return renderTeams(S);
  const act = activeSet(S);
  const node = (key, title, model, body, cls = '') =>
    `<div class="node ${cls} ${act.has(key) ? 'active' : ''} ${selLane === key ? 'sel' : ''}" data-lane="${key}"><div class="t">${title}${model ? `<span class="chip m">${esc(shortModel(model))}</span>` : ''}</div>${body}</div>`;
  const what = (key) => { const L = S.lanes[key]; return L ? `<div class="d">${esc(clip(L.what, 64))}${act.has(key) ? DOTS : ''}</div>` : ''; };
  const col = (key, html) => `<div class="col" data-col="${key}">${html}</div>`;
  const cols = [];

  const nChecks = doneChecks(S).length, depth = S.replans.length;
  cols.push(col('plan', `<div class="node op ${S.reprompts.length ? 'active' : ''}" data-lane="human"><div class="t">Operator</div><div class="d">${esc(clip(S.reprompts[S.reprompts.length - 1]?.data.text || S.goal || 'No goal yet', 64))}</div></div>`
    + node('planner', 'Planner', modelOf(S, 'planner'),
      `<div class="d">${S.dod ? `Definition of done · ${nChecks} checks` : 'Writing the definition of done'}${act.has('planner') ? DOTS : ''}</div>`
      + (S.failed.length ? `<div class="recur">${S.failed.slice(0, 3).map((c) => `<span class="sg">sub-goal ${esc(clip(c, 12))}</span>`).join('')}${S.failed.length > 3 ? `<span class="sg">+${S.failed.length - 3}</span>` : ''}</div>` : ''),
      S.dod ? 'done' : '')));

  const workers = [...new Set(['inspector', 'diagnoser', 'researcher', 'operator'].filter((w) => S.lanes[w])
    .concat(Object.keys(S.lanes).filter((l) => !['planner', 'verifier', 'supervisor', 'gate', 'sim', 'judge'].includes(l))))];
  cols.push(col('swarm', `<div class="col-lbl" title="${esc(modelOf(S, 'worker'))}">Sub-agents · ${esc(shortModel(modelOf(S, 'worker')).replace(/-\d+B-A\d+B$/, ''))}</div>`
    + (workers.length ? workers.map((w) => node(w, esc(w), null,
      `${what(w)}<div class="chip-row">${[...(S.sources[w] || [])].map((x) => `<span class="chip src">${esc(x)}</span>`).join('')}</div>`)).join('')
      : `<div class="node ghost"><div class="d">Waiting for tasks</div></div>`)));

  const pol = S.gates.filter((e) => e.data.source === 'policy');
  const mod = S.gates.filter((e) => e.data.source && e.data.source !== 'policy');
  const backend = mod[mod.length - 1]?.data.source || META.gate_backend;
  const modelName = backend === 'jev' ? 'Jev' : backend === 'openai' ? 'OpenAI Decisions' : shortModel(modelOf(S, 'gate'));
  const lastVeto = S.gates.filter((e) => e.data.route !== 'proceed').pop();
  cols.push(col('gate', node('gate', 'Safety gate', null,
    `<div class="split"><div><b>LOTO interlocks</b>hard-coded · ${pol.length}</div><div><b>${esc(modelName)}</b>veto-only · ${mod.length}</div></div>
     ${lastVeto ? `<div class="veto">VETO ${esc(String(lastVeto.data.proposed?.action || lastVeto.data.proposed?.tool || '').replace(/_/g, ' '))} ${esc(lastVeto.data.proposed?.asset || '')}</div>` : ''}`,
    S.gates.length ? (lastVeto ? 'fail' : 'done') : '')));

  const vm = modelOf(S, 'verifier');
  const vs = Object.values(S.verdicts);
  const vps = [...new Set(vs.map((v) => v.evidence?.viewpoint).filter(Boolean))];
  let soak = '';
  if (S.hold) {
    const el = Math.max(0, Math.min(30, minutes(S.last?.t_sim) - minutes(S.hold.t_sim)));
    soak = `<div class="soak">soak ${Math.floor(el)}:00 / 30:00<i><b style="width:${(100 * el) / 30}%"></b></i></div>`;
  }
  cols.push(col('verify', node('verifier', 'Verifier', vm,
    `<div class="chip-row">${vendor(vm) !== vendor(modelOf(S, 'worker')) ? '<span class="chip strong">different model family</span>' : ''}${vps.map((v) => `<span class="chip src">own view ${esc(v)}</span>`).join('')}</div>
     <div class="d">${vs.length ? `${vs.filter((v) => v.pass).length}/${nChecks || vs.length} passed` : 'Waiting for a claim'}${act.has('verifier') ? DOTS : ''}</div>${soak}`,
    vs.some((v) => !v.pass) ? 'fail' : vs.length ? 'done' : '')));

  const escalated = S.pages[S.pages.length - 1] || (S.final?.status === 'ESCALATED' ? { data: { brief: S.final.summary } } : null);
  const writes = S.lessons.filter((e) => e.data.op === 'write');
  let out = '';
  if (S.lessons.length) out += node('memory', 'Verified memory', null, `<div class="chip-row"><span class="chip strong">verified only</span><span class="chip">poisoning-resistant</span></div><div class="d">${S.lessons.filter((e) => e.data.op === 'read').length} read · ${writes.length} written</div>`, writes.length ? 'done' : '');
  if (escalated) out += `<div class="node esc" data-lane="supervisor"><div class="t">Human escalation</div><div class="d">${esc(clip(escalated.data.brief, 80))}</div></div>`;
  if (S.final?.status === 'DONE') out += `<div class="node done"><div class="t">Resolved</div><div class="d">${esc(clip(S.final.summary, 64))}</div></div>`;
  if (!out) out = `<div class="node ghost"><div class="t">Outcome</div><div class="d">${events.length ? `Open${DOTS}` : '—'}</div></div>`;
  cols.push(col('out', out));
  $('#flow').innerHTML = cols.join('');
  requestAnimationFrame(() => drawWires(S, act, workers, depth));
}

const pulse = (d, dur, phase) => `<circle class="pulse" r="3.5" style="offset-path:path('${d}');animation-duration:${dur}s;animation-delay:${-phase * dur}s"/>`;
// wires between the columns; live ones carry pulses, the replan wire loops back over the top to the planner
function drawWires(S, act, workers, depth) {
  const svg = $('#wires'), box = $('#flow').getBoundingClientRect();
  svg.setAttribute('viewBox', `0 0 ${box.width} ${box.height}`);
  const at = (sel, side) => {
    const el = $('#flow').querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return [side === 'r' ? r.right - box.left : r.left - box.left, r.top - box.top + Math.min(r.height / 2, 28), r];
  };
  const wire = (a, b, live, cls = '') => {
    if (!a || !b) return '';
    const mx = (a[0] + b[0]) / 2;
    const d = `M${a[0]},${a[1]} C${mx},${a[1]} ${mx},${b[1]} ${b[0]},${b[1]}`;
    return `<path class="w ${live ? 'live' : ''} ${cls}" d="${d}"/>` + (live ? [0, 0.5].map((k) => pulse(d, 1.2, k)).join('') : '');
  };
  let g = '';
  const pl = at('[data-lane="planner"]', 'r');
  workers.forEach((w) => { g += wire(pl, at(`[data-col="swarm"] [data-lane="${w}"]`, 'l'), act.has(w)); });
  if (!workers.length) g += wire(pl, at('[data-col="swarm"] .node', 'l'), act.has('planner'));
  const gl = at('[data-lane="gate"]', 'l');
  workers.forEach((w) => { g += wire(at(`[data-col="swarm"] [data-lane="${w}"]`, 'r'), gl, act.has('gate') && act.has(w)); });
  g += wire(at('[data-lane="gate"]', 'r'), at('[data-lane="verifier"]', 'l'), act.has('verifier'));
  g += wire(at('[data-lane="verifier"]', 'r'), at('[data-col="out"] .node', 'l'), !!S.final);
  const op = at('[data-lane="human"]', 'l');
  if (op) g += `<path class="w" d="M${op[0] + 16},${op[1] + 12} L${op[0] + 16},${(pl?.[2].top ?? 0) - box.top}"/>`;
  if (depth) {  // recursion: a failed check becomes a sub-goal and the planner replans
    const v = at('[data-lane="verifier"]', 'r'), p = at('[data-lane="planner"]', 'r');
    if (v && p) {
      const y = 6, d = `M${(v[2].left + v[2].right) / 2 - box.left},${v[2].top - box.top} C${(v[2].left + v[2].right) / 2 - box.left},${y} ${(p[2].left + p[2].right) / 2 - box.left},${y} ${(p[2].left + p[2].right) / 2 - box.left},${p[2].top - box.top}`;
      const live = act.has('supervisor') || act.has('planner');
      g += `<path class="w replan ${live ? 'live' : ''}" d="${d}"/><text class="wl" x="${((v[2].left + p[2].right) / 2) - box.left}" y="${y + 14}" text-anchor="middle">↺ replan · depth ${depth}</text>`
        + (live ? pulse(d, 1.6, 0) : '');
    }
  }
  svg.innerHTML = g;
}

// demo runs: one column per team (diagnose -> plan -> verify), each lead with the sub-agents it spawned; a failed
// verification sends the work back round by round
function renderTeams(S) {
  const all = Object.values(S.dagents);
  const teams = [...new Set(all.map((a) => a.team))];
  const latest = (team) => { const leads = all.filter((a) => a.team === team && a.depth === 0); return leads[leads.length - 1]; };
  const node = (a) => `<div class="node ${a.status === 'done' ? (a.output?.pass === false ? 'fail' : 'done') : 'active'} ${a.depth ? 'kid' : ''} ${selLane === a.id ? 'sel' : ''}" data-lane="${esc(a.id)}">
    <div class="t">${esc(String(a.role).replace(/_/g, ' '))}${a.model ? `<span class="chip m">${esc(shortModel(a.model))}</span>` : ''}</div>
    <div class="d">${esc(clip(a.task, 70))}${a.status !== 'done' ? DOTS : ''}</div></div>`;
  const cols = [`<div class="col" data-col="op"><div class="node op" data-lane="human"><div class="t">Operator</div><div class="d">${esc(clip(S.reprompts[S.reprompts.length - 1]?.data.text || S.goal || '', 70))}</div></div>
    ${S.lessons.length ? `<div class="node ${S.lessons.some((l) => l.data.op === 'write') ? 'done' : ''}" data-lane="memory"><div class="t">Verified memory</div><div class="chip-row"><span class="chip strong">verified only</span><span class="chip">poisoning-resistant</span></div><div class="d">${esc(clip(S.lessons[S.lessons.length - 1].data.effect || '', 70))}</div></div>` : ''}</div>`];
  teams.forEach((team) => {
    const lead = latest(team);
    const kids = all.filter((a) => a.parent === lead?.id);
    cols.push(`<div class="col" data-col="${esc(team)}"><div class="col-lbl">${esc(team)}${lead?.round ? ` · round ${lead.round}` : ''}</div>${lead ? node(lead) : ''}${kids.map(node).join('')}</div>`);
  });
  const fin = S.final ? `<div class="node ${S.final.status === 'DONE' ? 'done' : 'esc'}"><div class="t">${S.final.status === 'DONE' ? 'Verified' : esc(S.final.status)}</div><div class="d">${esc(clip(S.final.summary, 70))}</div></div>`
    : `<div class="node ghost"><div class="t">Outcome</div><div class="d">Open${DOTS}</div></div>`;
  cols.push(`<div class="col" data-col="out">${fin}</div>`);
  $('#flow').style.gridTemplateColumns = `0.9fr ${teams.map(() => '1.2fr').join(' ')} .8fr`;
  $('#flow').innerHTML = cols.join('');
  requestAnimationFrame(() => {
    const svg = $('#wires'), box = $('#flow').getBoundingClientRect();
    svg.setAttribute('viewBox', `0 0 ${box.width} ${box.height}`);
    const pt = (el, side) => { const r = el.getBoundingClientRect(); return [side === 'r' ? r.right - box.left : side === 'l' ? r.left - box.left : r.left - box.left + 14, side === 'b' ? r.bottom - box.top : side === 't' ? r.top - box.top : r.top - box.top + Math.min(r.height / 2, 24), r]; };
    const wire = (a, b, live) => {
      const mx = (a[0] + b[0]) / 2, d = `M${a[0]},${a[1]} C${mx},${a[1]} ${mx},${b[1]} ${b[0]},${b[1]}`;
      return `<path class="w ${live ? 'live' : ''}" d="${d}"/>` + (live ? pulse(d, 1.2, 0) + pulse(d, 1.2, 0.5) : '');
    };
    let g = '';
    const heads = [...$('#flow').querySelectorAll('.col')].map((c) => c.querySelector('.node'));
    heads.forEach((h, k) => { const nx = heads[k + 1]; if (h && nx) g += wire(pt(h, 'r'), pt(nx, 'l'), nx.classList.contains('active')); });
    teams.forEach((team) => {
      const col = $('#flow').querySelector(`[data-col="${CSS.escape(team)}"]`);
      const [lead, ...kids] = col.querySelectorAll('.node');
      kids.forEach((k) => { const a = pt(lead, 'x'), b = pt(k, 'x'); const d = `M${a[0]},${a[2].bottom - box.top} L${a[0]},${b[1]} L${b[2].left - box.left},${b[1]}`; g += `<path class="w ${k.classList.contains('active') ? 'live' : ''}" d="${d}"/>` + (k.classList.contains('active') ? pulse(d, 1, 0) : ''); });
    });
    if (S.replans.length && teams.includes('verify') && teams.includes('plan')) {  // recursion: failed verification goes back to planning
      const v = pt($('#flow').querySelector('[data-col="verify"] .node'), 't'), p = pt($('#flow').querySelector('[data-col="plan"] .node'), 't');
      const y = -26, d = `M${v[0] + 40},${v[1]} C${v[0] + 40},${y} ${p[0] + 40},${y} ${p[0] + 40},${p[1]}`;
      g += `<path class="w replan ${S.final ? '' : 'live'}" d="${d}"/><text class="wl" x="${(v[0] + p[0]) / 2 + 40}" y="${y + 6}" text-anchor="middle">↺ redo · round ${S.rounds}</text>`;
    }
    svg.innerHTML = g;
  });
}

// ---------- the selected (or most recent) agent's mind: context, thinking, live feed ----------
function renderMind(S) {
  const da = S.dagents[selLane] || (!S.dagents[selLane] && Object.keys(S.dagents).length && !S.minds[selLane] ? S.dagents[S.lastAgent] : null);
  if (da) {
    $('#mindWho').innerHTML = `${esc(String(da.role).replace(/_/g, ' '))}${da.status !== 'done' ? DOTS : ''}`;
    $('#mindModel').textContent = shortModel(da.model);
    const kids = Object.values(S.dagents).filter((a) => a.parent === da.id);
    $('#ctxNum').textContent = `depth ${da.depth} · ${kids.length} sub-agent${kids.length === 1 ? '' : 's'}${da.round ? ` · round ${da.round}` : ''}`;
    $('#ctxBar').style.width = `${Math.min(100, 30 + 20 * kids.length)}%`; $('#ctxCached').style.width = '0';
    const out = da.output == null ? '' : typeof da.output === 'string' ? da.output : JSON.stringify(da.output);
    $('#thinking').innerHTML = `${esc(clip(da.task, 160))}${out ? `<br><br>→ ${esc(clip(out, 260))}` : '<span class="caret"></span>'}`;
  }
  const lane = selLane && (S.minds[selLane] || S.lanes[selLane]) ? selLane : S.lastMind || [...activeSet(S)][0] || 'planner';
  const m = S.minds[lane];
  if (!da) {
  $('#mindWho').innerHTML = `${esc(lane)}${activeSet(S).has(lane) ? DOTS : ''}`;
  $('#mindModel').textContent = shortModel(m?.model || modelOf(S, roleOfLane(lane)) || '');
  const maxPrompt = Math.max(1, ...Object.values(S.minds).map((x) => x.prompt || 0));
  $('#ctxNum').textContent = m ? `${((m.prompt || 0) / 1000).toFixed(1)}k tokens · ${((m.cached || 0) / 1000).toFixed(1)}k cached` : '—';
  $('#ctxBar').style.width = `${(100 * (m?.prompt || 0)) / maxPrompt}%`;
  $('#ctxCached').style.width = `${(100 * (m?.cached || 0)) / maxPrompt}%`;
  const think = m?.thinking || m?.said || S.lanes[lane]?.what || '';
  $('#thinking').innerHTML = think ? `${esc(clip(think, 420))}${activeSet(S).has(lane) ? '<span class="caret"></span>' : ''}` : '<span class="muted">No thoughts logged yet.</span>';
  }
  const feed = [];
  for (let i = cursor - 1; i >= 0 && feed.length < 7; i--) {
    const e = events[i];
    if (['usage', 'tool_result', 'judge', 'demo.sensor', 'demo.jev', 'demo.camera', 'demo.diffusion'].includes(e.type)) continue;
    const who = e.actor === 'sim' ? e.data?.by || 'sim' : e.actor;
    const txt = describe(e) || (e.type === 'goal' ? e.data.text : e.type);
    feed.push(`<li class="${i === cursor - 1 && !S.final ? 'new' : ''}"><span class="tm">${hhmm(e.t_sim)}</span><b>${esc(laneOf(who))}</b><span>${esc(clip(txt, 80))}</span></li>`);
  }
  $('#feed').innerHTML = feed.join('');
}

function renderLedger(S) {
  const roles = Object.entries(S.roles);
  $('#roles').innerHTML = roles.length ? roles.map(([r, x]) => `<b>${esc(r)}</b><span>${esc(shortModel(x.model))}</span>
    <span>${x.nlat ? `${(x.lat / x.nlat).toFixed(1)} s` : '—'}</span><span>${(x.cached / 1000).toFixed(1)}k cached</span>`).join('')
    : Object.entries(META.roles || {}).filter(([r]) => ['planner', 'worker', 'verifier', 'gate'].includes(r)).map(([r, m]) => `<b>${esc(r)}</b><span>${esc(shortModel(m))}</span><span></span><span></span>`).join('');
  const lesson = S.lessons.filter((e) => e.data.op === 'write').pop() || S.lessons[S.lessons.length - 1];
  $('#lesson').innerHTML = lesson ? `“${esc(clip(lesson.data.text, 160))}”<div class="muted">${lesson.data.op === 'write' ? 'Written after the verifier passed' : 'Recalled from verified memory'}</div>`
    : `<span class="muted">${S.final ? 'None' : 'Kept once verified'}</span>`;
  const sc = META.scenarios || {};
  const total = Object.values(sc).reduce((a, b) => a + b, 0);
  $('#bench').textContent = ['Hidden ground truth', total ? `${total} scenarios, ${sc.heldout || 0} held out` : ''].filter(Boolean).join(' · ');
}

// ---------- drones: four simultaneous camera feeds, then the interactive model ----------
// tiles without a rendered flight show a slow pan over a pre-rendered view of the same area
const STILLS = ['assets/splats/renders/b3_corner/array_normal.jpg', 'assets/splats/renders/xfmr1_yard/normal.jpg',
  'assets/splats/renders/b3_array/array_normal.jpg', 'assets/splats/renders/b4_array/array_normal.jpg'];
async function initDrones() {
  let list = [];
  try { list = await (await fetch('/assets/splats/drones/drones.json')).json(); } catch { /* not rendered yet */ }
  list = [1, 2, 3, 4].map((k) => list.find((d) => d.id === `drone${k}`) || { id: `drone${k}`, row: `B${k}` });  // always four tiles
  $('#feeds').innerHTML = list.slice(0, 4).map((d, k) => `<figure class="feed-tile">
    ${d.file ? `<video src="/${esc(d.file)}" autoplay muted loop playsinline></video>` : `<img class="pan" src="/${STILLS[k]}" alt="">`}
    <figcaption><span class="rec"></span>CAM ${String(k + 1).padStart(2, '0')} · DRONE ${k + 1} · ${esc(d.row || '')}</figcaption>
    <span class="hud">ALT ${d.altitude_m || 20} m · RGB</span><span class="ts mono"></span></figure>`).join('');
  $('#droneNote').textContent = '4 live';
  setInterval(() => {
    const t = (S0?.last?.t_sim || '').replace('T', ' ');
    const s = String(Math.floor(performance.now() / 1000) % 60).padStart(2, '0');
    document.querySelectorAll('.feed-tile .ts').forEach((el) => { el.textContent = t ? `${t}:${s}` : ''; });
  }, 1000);
}

// the fifth view: the walkable twin (viewer/twin-embed.html), plant outside and the equipment room inside;
// asset selection syncs both ways and the twin colours assets by their state here
let S0 = null, twinReady = false, twinStatus = '', picked = null;
const twin = () => $('#twin')?.contentWindow;
function flyTo(asset) {
  picked = asset;
  twin()?.postMessage({ type: 'focus', asset }, '*');
  document.querySelectorAll('.asset.picked').forEach((el) => el.classList.remove('picked'));
  document.querySelector(`.asset[data-asset="${CSS.escape(asset)}"]`)?.classList.add('picked');
  $('.twin').scrollIntoView({ behavior: 'smooth', block: 'center' });
}
function syncTwin() {
  if (!twinReady || !S0) return;
  const assets = {};
  for (const [id, a] of Object.entries(S0.assets)) assets[a.raw || id] = a.cls === 'bad' ? 'bad' : a.cls === 'verified' ? 'ok' : 'work';
  const key = JSON.stringify(assets);
  if (key !== twinStatus) { twinStatus = key; twin()?.postMessage({ type: 'status', assets }, '*'); }
}
// once the agents name the faulted asset, jump to the twin and open it there, no clicks (demo: the asset's data panel)
let focused = null, pendingFocus = null;
function autoFocus(S) {
  const diagnosed = S.dfocus || (S.findings.length && S.focus);
  if (!diagnosed || diagnosed === focused || (!replay && S.final && !focused && cursor === events.length && params.get('focus') !== '1')) return;
  focused = diagnosed;
  if (twinReady) flyTo(diagnosed); else pendingFocus = diagnosed;
}

function initTwin() {
  $('#twin').src = `twin-embed.html?mode=orbit${runId ? `&run=${encodeURIComponent(runId)}` : ''}`;
  addEventListener('message', (e) => {
    const m = e.data;
    if (!m || m.source !== 'gridtwin-twin') return;
    if (m.type === 'ready') { twinReady = true; twinStatus = ''; syncTwin(); if (pendingFocus) { flyTo(pendingFocus); pendingFocus = null; } }
    if (m.type === 'select' && m.asset) {
      picked = tileOf(m.asset) || m.asset;
      document.querySelectorAll('.asset.picked').forEach((el) => el.classList.remove('picked'));
      document.querySelector(`.asset[data-asset="${CSS.escape(tileOf(m.asset) || m.asset)}"]`)?.classList.add('picked');
    }
  });
}

// ---------- data ----------
function render() {
  const S = derive(events.slice(0, cursor));
  S0 = S;
  const st = $('#state');
  st.className = `live mono ${S.final ? (S.final.status === 'DONE' ? 'ok' : 'warn') : events.length ? 'work' : ''}`;
  st.textContent = `${replay ? 'Replay' : S.final ? 'Closed' : events.length ? 'Live' : 'Idle'}${S.first ? ` · sim ${S.first.t_sim.slice(0, 10)}` : ''}`;
  autoFocus(S);
  renderHero(S); renderPhases(S); renderFlow(S); renderMind(S); renderAssets(S); renderIncident(S); renderLedger(S); syncTwin();
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

// replay runs on its own: no clicks, the agents act and the page follows
function tick() {
  if (cursor < events.length) { cursor++; render(); }
  const e = events[cursor - 1];
  const slow = e && (e.type === 'usage' ? 120 : e.type === 'tool_call' ? 450 : e.type === 'tool_result' ? 200 : 1100);
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
  $('#flow').onclick = (e) => { const n = e.target.closest('[data-lane]'); if (n) { selLane = selLane === n.dataset.lane ? null : n.dataset.lane; render(); } };
  $('#crumb').ondblclick = () => runId && window.open(`/?run=${encodeURIComponent(runId).replace(/%2F/g, '/')}`, '_blank');  // operator view
  $('#cta').onclick = () => {
    if (S0?.final?.status === 'DONE') { signed = true; render(); return; }
    $('#rpText').focus();  // escalated or not resolved: a person takes over by telling the agents what to do
    $('#reprompt').scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  $('#reprompt').onsubmit = async (e) => {
    e.preventDefault();
    const text = $('#rpText').value.trim();
    if (!text || !runId) return;
    const res = await fetch('/api/reprompt', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ run: runId, text }) }).catch(() => null);
    $('#rpNote').textContent = res?.ok ? (S0?.final ? 'Saved. This run is closed, so it applies to the next run.' : 'Sent. The planner reads it next round.') : 'Could not send';
    if (res?.ok) $('#rpText').value = '';
  };
  addEventListener('resize', () => S0 && render());
  initDrones();
  initTwin();
  render();
  if (!runId) return;
  if (!replay) { poll(); return; }
  events = await fetchEvents();
  cursor = 0;
  render();
  setTimeout(tick, 1500);
}

main();
