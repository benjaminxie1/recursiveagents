// Codes & standards library: publications and their clauses (cached text from kb/refs), the requirements that cite
// them and the assets they apply to. Reads GET /api/codes; with ?run=<id> it also marks each requirement with the
// run's evidence (DoD checks citing it, and their verdicts, from runs/<id>/events.jsonl).
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const params = new URLSearchParams(location.search);
const S = { view: params.get('view') === 'req' ? 'req' : 'lib', pub: params.get('pub'), clause: params.get('clause'),
  req: params.get('req'), asset: params.get('asset'), q: params.get('q') || '', run: params.get('run') };
let D, PUB = {}, REQ = {}, STD = {}, STATUS = {};

const words = () => S.q.toLowerCase().split(/\s+/).filter(Boolean);
const hit = (text) => { const w = words(); const t = text.toLowerCase(); return w.every((x) => t.includes(x)); };
const hl = (s) => {
  let out = esc(s);
  for (const w of words()) out = out.replace(new RegExp(`(${w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})(?![^<]*>)`, 'gi'), '<mark>$1</mark>');
  return out;
};
const clauseText = (c, p) => [p.name, p.title, c.number, c.heading, ...c.requirements, ...c.assets, ...c.standards,
  ...c.sources.flatMap((s) => [s.title, s.snippet, s.domain, s.query])].join(' ');
const reqText = (r) => [r.id, r.title, r.verify_by?.how, r.verify_by?.kind, r.component, r.component_code, ...r.standards, ...r.assets].join(' ');
const clauseOk = (c, p) => (!S.asset || c.assets.includes(S.asset)) && (!S.q || hit(clauseText(c, p)));
const reqOk = (r) => (!S.asset || r.assets.includes(S.asset)) && (!S.q || hit(reqText(r)));
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
const assetGroups = (ids) => {  // INV-1..4 -> "INV-1–4" so long lists stay one line
  const g = {};
  ids.forEach((a) => { const m = a.match(/^(.*?)(\d+(?:\.\d+)?)$/); (g[m ? m[1] : a] ||= []).push(m ? m[2] : ''); });
  return Object.entries(g).map(([k, v]) => (v.length > 2 ? `${k}${v[0]}–${v[v.length - 1]}` : v.map((x) => k + x).join(' ')));
};

function sync() {
  const p = new URLSearchParams();
  if (S.view === 'req') p.set('view', 'req');
  for (const k of ['pub', 'clause', 'req', 'asset', 'q', 'run']) if (S[k]) p.set(k, S[k]);
  history.replaceState(null, '', `${location.pathname}${p.toString() ? `?${p}` : ''}`);
}

function goStandard(sid) {
  const st = STD[sid];
  if (!st?.publication) return;
  S.view = 'lib'; S.pub = st.publication; S.q = ''; $('#q').value = '';
  const c = PUB[st.publication].clauses.find((x) => st.clause && (x.number === st.clause || x.number.startsWith(`${st.clause}.`) || x.number.startsWith(`${st.clause}(`)));
  S.clause = c?.id || null;
  render();
  if (c) document.getElementById(c.id)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
}

const statusOf = (rid) => STATUS[rid] || '';
const reqChip = (rid) => `<span class="chip req ${statusOf(rid)}" data-req="${esc(rid)}">${esc(rid)}</span>`;
const stdChip = (sid) => `<span class="chip" data-std="${esc(sid)}" title="${esc(STD[sid]?.title || '')}">${esc(sid)}</span>`;
const assetChips = (ids) => assetGroups(ids).map((g) => `<span class="chip a" data-asset="${esc(g.split(/[ –]/)[0])}">${esc(g)}</span>`).join('');

function renderPubs() {
  $('#pubs').innerHTML = D.collections.map((col) => {
    const pubs = D.publications.filter((p) => p.collection === col.id);
    if (!pubs.length) return '';
    return `<div class="coll"><div class="label"><span>${esc(col.name)}</span><span class="mono">${pubs.length}</span></div>${pubs.map((p) => {
      const n = (S.q || S.asset) ? p.clauses.filter((c) => clauseOk(c, p)).length : p.clauses.length;
      return `<div class="pub${p.id === S.pub && !S.q ? ' on' : ''}${(S.q || S.asset) && !n ? ' miss' : ''}" data-pub="${p.id}" title="${esc(p.title)}">
        <span class="n">${esc(p.name)}</span><span class="c">${n}<i class="${p.requirements.length ? 'r' : ''}"></i></span></div>`;
    }).join('')}</div>`;
  }).join('');
}

function clauseHtml(c, p) {
  return `<article class="clause${c.id === S.clause ? ' on' : ''}" id="${esc(c.id)}" data-clause="${esc(c.id)}" data-pub="${p.id}">
    <div class="num${c.number ? '' : ' none'}">${c.number ? hl(c.number) : '§'}</div>
    <div>${c.sources.map((s, i) => `<div class="src">${i === 0 || s.title !== c.heading ? `<h3>${hl(s.title)}</h3>` : ''}
      <q>${hl(s.snippet)}</q>
      <div class="meta"><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.domain)} ↗</a>
        <span>${esc((s.fetched_at || '').slice(0, 10))}</span></div></div>`).join('')}
      ${c.requirements.length || c.assets.length ? `<div class="cites">${c.requirements.map(reqChip).join('')}${assetChips(c.assets)}</div>` : ''}
    </div></article>`;
}

function renderDoc() {
  if (S.q) {
    const groups = D.publications.map((p) => [p, p.clauses.filter((c) => clauseOk(c, p))]).filter(([, cs]) => cs.length);
    const n = groups.reduce((a, [, cs]) => a + cs.length, 0);
    $('#doc').innerHTML = `<div class="dhead"><div class="label">Search</div><h1>${plural(n, 'clause')}</h1>
      <div class="t">matching “${esc(S.q)}”${S.asset ? ` on ${esc(S.asset)}` : ''} across ${plural(groups.length, 'publication')}</div></div>
      ${groups.map(([p, cs]) => `<div class="ghead" data-pub="${p.id}"><b>${esc(p.name)}</b><span>${esc(p.title)}</span></div>${cs.map((c) => clauseHtml(c, p)).join('')}`).join('')
      || '<div class="empty">No clauses match.</div>'}`;
    return;
  }
  const p = PUB[S.pub];
  const cs = p.clauses.filter((c) => clauseOk(c, p));
  const assets = new Set(p.clauses.flatMap((c) => c.assets));
  D.standards.filter((s) => s.publication === p.id).forEach((s) => s.assets.forEach((a) => assets.add(a)));
  $('#doc').innerHTML = `<div class="dhead"><div class="label">${esc(D.collections.find((c) => c.id === p.collection)?.name)}</div>
    <h1>${esc(p.name)}</h1><div class="t">${esc(p.title)}</div>
    <div class="stats"><div><b>${p.clauses.length}</b><span>Clauses</span></div><div><b>${p.clauses.reduce((a, c) => a + c.sources.length, 0)}</b><span>Sources</span></div>
      <div><b>${p.requirements.length}</b><span>Requirements</span></div><div><b>${assets.size}</b><span>Assets</span></div></div></div>
    ${cs.map((c) => clauseHtml(c, p)).join('') || `<div class="empty">No clauses${S.asset ? ` apply to ${esc(S.asset)}` : ''}.</div>`}`;
}

function reqHtml(r) {
  return `<div class="rq${r.id === S.req ? ' on' : ''}" data-req="${esc(r.id)}">
    <div class="k"><i class="dot ${statusOf(r.id)}"></i>${hl(r.id)}<span class="kind">${esc(r.verify_by?.kind || '')}</span></div>
    <div class="tt">${hl(r.title)}</div><div class="how">${hl(r.verify_by?.how || '')}</div>
    <div class="cites">${r.standards.map(stdChip).join('')}${assetChips(r.assets)}</div></div>`;
}

function assetGrid(hits) {
  return `<div class="assets">${D.assets.map((a) => `<div class="asset${hits.has(a) ? ' hit' : ''}${a === S.asset ? ' on' : ''}" data-asset="${a}">${a}</div>`).join('')}</div>`;
}

function renderLinks() {
  let reqs, title, hits;
  if (S.req && REQ[S.req]) {
    const r = REQ[S.req];
    const cl = D.publications.flatMap((p) => p.clauses.filter((c) => c.requirements.includes(r.id)).map((c) => [p, c]));
    $('#links').innerHTML = `<section class="lsec"><div class="head"><span class="label">Requirement</span><span class="chip" data-close="1">Close</span></div>
      <div class="rq on" style="cursor:default"><div class="k"><i class="dot ${statusOf(r.id)}"></i>${esc(r.id)}<span class="kind">${esc(r.verify_by?.kind || '')}</span></div>
      <div class="tt" style="font-size:15px">${esc(r.title)}</div><div class="how">${esc(r.verify_by?.how || '')}</div></div></section>
      <section class="lsec"><div class="head"><span class="label">Component</span><span class="mono dim">${esc(r.component_code || '')}</span></div>
      <div style="margin-bottom:10px">${esc((r.component || '').replace(/_/g, ' '))}</div>${assetGrid(new Set(r.assets))}</section>
      <section class="lsec"><div class="head"><span class="label">Cited sources</span><span class="mono dim">${cl.length}</span></div>
      ${r.standards.map((sid) => `<div class="comp" style="grid-template-columns:1fr"><span>${stdChip(sid)}</span><span class="dim" style="font-size:12px">${esc(STD[sid]?.title || '')}</span></div>`).join('')}
      ${cl.map(([p, c]) => `<div class="comp" data-goto="${esc(c.id)}" data-pub="${p.id}" style="cursor:pointer"><span class="cc">${esc(p.name)}</span><span>${esc(c.number || c.heading)}</span><span class="cc">${esc(c.sources[0]?.domain || '')}</span></div>`).join('')}</section>`;
    return;
  }
  if (S.q) {
    reqs = D.requirements.filter(reqOk); title = 'Matching requirements';
    hits = new Set(reqs.flatMap((r) => r.assets));
  } else {
    const p = PUB[S.pub];
    const c = p.clauses.find((x) => x.id === S.clause);
    const ids = new Set(c ? c.requirements : p.requirements);
    reqs = D.requirements.filter((r) => ids.has(r.id) && (!S.asset || r.assets.includes(S.asset)));
    title = c ? `Cited by · ${c.number || 'clause'}` : 'Cited by';
    hits = new Set(c ? c.assets : [...p.clauses.flatMap((x) => x.assets), ...D.standards.filter((s) => s.publication === p.id).flatMap((s) => s.assets)]);
  }
  const comps = {};
  D.requirements.forEach((r) => { if (r.assets.some((a) => hits.has(a))) comps[r.component_code] = r; });
  $('#links').innerHTML = `<section class="lsec"><div class="head"><span class="label">${esc(title)}</span><span class="mono dim">${reqs.length}</span></div>
      ${reqs.map(reqHtml).join('') || '<div class="dim">No requirement cites this yet.</div>'}</section>
    <section class="lsec"><div class="head"><span class="label">Applies to</span><span class="mono dim">${hits.size} assets</span></div>
      ${Object.values(comps).sort((a, b) => a.component_code.localeCompare(b.component_code)).map((r) => `<div class="comp"><span class="cc">${esc(r.component_code)}</span>
        <span>${esc(r.component.replace(/_/g, ' '))}</span><span class="ids">${esc(assetGroups(r.assets.filter((a) => hits.has(a))).join(' '))}</span></div>`).join('')}
      <div style="margin-top:10px">${assetGrid(hits)}</div></section>`;
}

function renderReqView() {
  const reqs = D.requirements.filter(reqOk);
  const n = { pass: 0, fail: 0, pending: 0 };
  reqs.forEach((r) => { if (n[statusOf(r.id)] !== undefined) n[statusOf(r.id)]++; });
  const tot = reqs.length || 1;
  const pubOf = (sid) => PUB[STD[sid]?.publication]?.name || '';
  $('#reqView').innerHTML = `<div class="dhead"><div class="label">Requirements engine · codes × components</div>
    <h1>${plural(reqs.length, 'requirement')}</h1>
    <div class="t">${S.run ? `Evidence from run <span class="mono">${esc(S.run)}</span>` : 'Each one is proven by the verifier with evidence before an incident can close.'}</div>
    ${S.run ? `<div class="progress"><i class="p" style="width:${(100 * n.pass) / tot}%"></i><i class="f" style="width:${(100 * n.fail) / tot}%"></i><i class="w" style="width:${(100 * n.pending) / tot}%"></i></div>
    <div class="legend"><span><i class="dot pass"></i>${n.pass} verified</span><span><i class="dot fail"></i>${n.fail} failed</span><span><i class="dot pending"></i>${n.pending} pending</span>
      <span><i class="dot"></i>${reqs.length - n.pass - n.fail - n.pending} not checked this run</span></div>` : ''}</div>
    <table><thead><tr><th>ID</th><th>Requirement</th><th>Component</th><th>Citations</th><th>Assets</th><th>Evidence</th></tr></thead><tbody>
    ${reqs.map((r) => `<tr class="row" data-req="${esc(r.id)}"><td class="id">${hl(r.id)}</td>
      <td>${hl(r.title)}<div class="how">${esc(r.verify_by?.kind || '')} · ${hl(r.verify_by?.how || '')}</div></td>
      <td class="cc">${esc(r.component_code)}<br>${esc(r.component.replace(/_/g, ' '))}</td>
      <td><div class="cites">${r.standards.map((s) => `<span class="chip" data-std="${esc(s)}" title="${esc(STD[s]?.title || '')}">${esc(s)}</span>`).join('')}</div>
        <div class="how">${esc([...new Set(r.standards.map(pubOf))].join(' · '))}</div></td>
      <td><div class="cites">${assetChips(r.assets)}</div></td>
      <td class="st"><span><i class="dot ${statusOf(r.id)}"></i>${{ pass: 'Verified', fail: 'Failed', pending: 'Pending' }[statusOf(r.id)] || '—'}</span></td></tr>`).join('')}
    </tbody></table>`;
}

function render() {
  $('#lib').hidden = S.view !== 'lib';
  $('#reqView').hidden = S.view !== 'req';
  $('#vLib').classList.toggle('on', S.view === 'lib');
  $('#vReq').classList.toggle('on', S.view === 'req');
  const chip = $('#assetChip');
  chip.hidden = !S.asset; chip.textContent = S.asset || '';
  if (S.view === 'lib') { renderPubs(); renderDoc(); renderLinks(); } else renderReqView();
  sync();
}

async function loadRun() {
  STATUS = {};
  if (!S.run) return;
  try {
    const res = await fetch(`/runs/${S.run.split('/').map(encodeURIComponent).join('/')}/events.jsonl`, { cache: 'no-store' });
    if (!res.ok) return;
    let checks = [];
    const verdicts = {};
    for (const line of (await res.text()).split('\n')) {
      let e; try { e = JSON.parse(line); } catch { continue; }
      if (e.type === 'dod') { const c = e.data?.checks; checks = !c ? [] : Array.isArray(c) ? c : Object.values(c).flat(); }
      if (e.type === 'verdict') verdicts[e.data.check_id] = e.data.pass;
    }
    checks.forEach((c) => [c.requirement, ...(c.requirements || [])].filter(Boolean).forEach((r) => {
      const v = verdicts[c.id ?? c.check_id];
      const s = v === undefined ? 'pending' : v ? 'pass' : 'fail';
      STATUS[r] = STATUS[r] === 'fail' || s === 'fail' ? 'fail' : STATUS[r] === 'pending' || s === 'pending' ? 'pending' : 'pass';
    }));
  } catch { /* no run */ }
}

document.addEventListener('click', (e) => {
  const t = e.target;
  if (t.closest('a[href]')) return;
  const el = (a) => t.closest(`[data-${a}]`);
  if (el('close')) { S.req = null; return render(); }
  if (el('std')) { e.stopPropagation(); return goStandard(el('std').dataset.std); }
  if (el('asset') && !t.closest('#assetChip')) {
    const a = el('asset').dataset.asset; S.asset = S.asset === a ? null : a; return render();
  }
  if (el('req')) { S.req = el('req').dataset.req; S.view = 'lib'; if (!PUB[S.pub]?.requirements.includes(S.req)) S.pub = D.publications.find((p) => p.requirements.includes(S.req))?.id || S.pub; return render(); }
  if (el('goto')) { S.pub = el('goto').dataset.pub; S.clause = el('goto').dataset.goto; S.req = null; render(); return document.getElementById(S.clause)?.scrollIntoView({ block: 'start', behavior: 'smooth' }); }
  if (el('clause')) { const c = el('clause'); S.clause = S.clause === c.dataset.clause ? null : c.dataset.clause; S.pub = c.dataset.pub; S.req = null; return render(); }
  if (el('pub')) { S.pub = el('pub').dataset.pub; S.clause = null; S.req = null; S.q = ''; $('#q').value = ''; $('#doc').scrollTop = 0; return render(); }
});

async function main() {
  D = await (await fetch('/api/codes')).json();
  D.publications.forEach((p) => { PUB[p.id] = p; });
  D.requirements.forEach((r) => { REQ[r.id] = r; });
  D.standards.forEach((s) => { STD[s.id] = s; });
  if (!PUB[S.pub]) S.pub = D.publications[0].id;
  let runs = [];
  try { runs = await (await fetch('/api/runs')).json(); } catch { /* no api */ }
  const sel = $('#runPick');
  sel.innerHTML = `<option value="">Evidence: none</option>${runs.map((r) => `<option value="${esc(r.id)}"${r.id === S.run ? ' selected' : ''}>${esc(r.id)}</option>`).join('')}`;
  sel.onchange = async () => { S.run = sel.value || null; await loadRun(); render(); };
  sel.hidden = !runs.length;
  if (S.run) $('#navDash').href = `dashboard.html?run=${encodeURIComponent(S.run).replace(/%2F/g, '/')}`;
  const q = $('#q');
  q.value = S.q;
  q.oninput = () => { S.q = q.value.trim(); S.req = null; render(); };
  q.onkeydown = (e) => { if (e.key === 'Escape') { q.value = ''; S.q = ''; render(); q.blur(); } };
  document.addEventListener('keydown', (e) => { if (e.key === '/' && document.activeElement !== q) { e.preventDefault(); q.focus(); } });
  $('#assetChip').onclick = () => { S.asset = null; render(); };
  $('#vLib').onclick = () => { S.view = 'lib'; render(); };
  $('#vReq').onclick = () => { S.view = 'req'; render(); };
  await loadRun();
  render();
  if (S.clause) document.getElementById(S.clause)?.scrollIntoView({ block: 'start' });
}

main();
