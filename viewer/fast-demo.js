// Preload real recordings, then stream their original events into the existing dashboard.
// No new inference, synthetic results, slide transitions, or alternate UI.
const IDS = ['demo-20261017-golden1', 'demo-20261017-golden2'];
export function eventDelay(e) {
  return ({ 'demo.diagnosis': 1400, 'demo.workorder': 1000, 'demo.contractor': 1600,
    'demo.verdict': 2200, 'demo.final': 3500, 'demo.memory': 700,
    'demo.camera': 800, 'demo.check': 900, 'demo.agent': 320,
    tool_call: 100, tool_result: 60, usage: 50 })[e.type] ?? 160;
}

export function validateRecordings(runs) {
  if (runs.length !== 2) throw new Error('Both demo recordings are required');
  for (const [i, run] of runs.entries()) {
    const done = run.events.find((e) => e.type === 'demo.final')?.data;
    if (!done || done.stub !== false || done.status !== 'VERIFIED' || done.rounds !== (i ? 1 : 2)
      || done.judge?.success !== true || done.judge?.false_done !== false) throw new Error(`Recording is not verified: ${run.id}`);
    const verdicts = run.events.filter((e) => e.type === 'demo.verdict');
    if (JSON.stringify(verdicts.map((e) => e.data.pass)) !== JSON.stringify(i ? [true] : [false, true])
      || verdicts.some((e, n) => e.data.round !== n + 1)
      || run.events.indexOf(verdicts.at(-1)) >= run.events.findIndex((e) => e.type === 'demo.final')) {
      throw new Error(`Unexpected repair sequence: ${run.id}`);
    }
  }
  const lesson = runs[0].events.find((e) => e.type === 'demo.final').data.lesson;
  if (!runs[1].events.some((e) => e.type === 'demo.workorder' && e.data.lessons_applied?.includes(lesson))) {
    throw new Error('The second recording does not apply the first lesson');
  }
}

export function evidenceImages(events) {
  const paths = [];
  for (const { type, data: d } of events) {
    if (type === 'demo.camera') paths.push(d.image);
    if (type === 'demo.diffusion') paths.push(d.image, d.seen);
    if (type === 'demo.check') paths.push(d.evidence?.image);
    if (type === 'demo.email') paths.push(...(d.attachments || []).map((a) => a.path));
  }
  return [...new Set(paths.filter((p) => typeof p === 'string' && /^(?:\/?runs\/|\/?assets\/)[\w/ .-]+\.(?:png|jpe?g|webp)$/i.test(p)).map((p) => `/${p.replace(/^\//, '')}`))];
}

async function preload(src) {
  const image = new Image();
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Evidence image timed out')), 15000);
    image.onload = () => { clearTimeout(timer); resolve(); };
    image.onerror = () => { clearTimeout(timer); reject(new Error('Evidence image missing')); };
    image.src = src;
  });
  if (image.decode) await image.decode();
  return image;
}

export async function mountFastDemo({ show, ready }) {
  const button = document.querySelector('#fastDemo');
  button.hidden = false;
  let runs, images;
  try {
    const twin = ready();
    runs = await Promise.all(IDS.map(async (id) => {
      const response = await fetch(`/runs/${id}/events.jsonl`, { cache: 'no-cache' });
      if (!response.ok) throw new Error('Recording missing');
      return { id, events: (await response.text()).trim().split('\n').map((line) => JSON.parse(line)) };
    }));
    validateRecordings(runs);
    show(runs[0], runs[0].events.length, true);
    images = await Promise.all([...new Set(runs.flatMap((r) => evidenceImages(r.events)))].map(preload));
    await twin;
  } catch (error) {
    button.textContent = 'Demo unavailable'; button.title = error.message;
    return;
  }
  // Hold the decoded image resources for the lifetime of the control.
  button.preloadedEvidence = images;
  button.disabled = false;
  button.textContent = 'Run demo';
  button.title = 'Play the preloaded recordings at a faster pace';
  let run = 0, cursor = 0, playing = false, started = false, finished = false, timer;
  const tick = () => {
    if (!playing) return;
    if (cursor >= runs[run].events.length) {
      if (run === runs.length - 1) { playing = false; finished = true; button.textContent = 'Replay demo'; return; }
      run++; cursor = 0; show(runs[run], 0, true);
    }
    const event = runs[run].events[cursor++];
    show(runs[run], cursor);
    timer = setTimeout(tick, eventDelay(event));
  };
  button.onclick = () => {
    if (playing) { playing = false; clearTimeout(timer); button.textContent = 'Resume demo'; return; }
    if (!started || finished) { run = 0; cursor = 0; started = true; finished = false; show(runs[0], 0, true); }
    playing = true; button.textContent = 'Pause demo'; tick();
  };
}
