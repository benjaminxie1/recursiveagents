import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';
import { eventDelay, validateRecordings, evidenceImages } from '../viewer/fast-demo.js';

const ids = ['demo-20261017-golden1', 'demo-20261017-golden2'];
const runs = ids.map((id) => ({ id, events: readFileSync(new URL(`../runs/${id}/events.jsonl`, import.meta.url), 'utf8')
  .trim().split('\n').map((line) => JSON.parse(line)) }));
const copy = () => structuredClone(runs);
const find = (recordings, index, type, predicate = () => true) =>
  recordings[index].events.find((event) => event.type === type && predicate(event.data));

test('the two real recordings validate without being modified', () => {
  const before = JSON.stringify(runs);
  assert.doesNotThrow(() => validateRecordings(runs));
  assert.equal(JSON.stringify(runs), before);
  assert.deepEqual(runs.map((run) => run.events.filter((e) => e.type === 'demo.verdict').map((e) => [e.data.round, e.data.pass])),
    [[[1, false], [2, true]], [[1, true]]]);
});

test('both complete recordings are required in the correct order', () => {
  for (const invalid of [[], [runs[0]], [...runs, runs[0]], [runs[1], runs[0]]]) {
    assert.throws(() => validateRecordings(invalid));
  }
  const missingFinal = copy();
  missingFinal[1].events = missingFinal[1].events.filter((e) => e.type !== 'demo.final');
  assert.throws(() => validateRecordings(missingFinal));
});

test('stub, unsuccessful, false-completion and wrong-round outcomes cannot pass', () => {
  const corruptions = [
    (d) => { d.stub = true; },
    (d) => { d.status = 'ESCALATED'; },
    (d) => { d.rounds = 3; },
    (d) => { d.judge.success = false; },
    (d) => { d.judge.false_done = true; },
    (d) => { delete d.judge; },
  ];
  for (const index of [0, 1]) for (const corrupt of corruptions) {
    const recordings = copy();
    corrupt(find(recordings, index, 'demo.final').data);
    assert.throws(() => validateRecordings(recordings), /verified/i);
  }
});

test('missing, duplicated, flipped and reordered verdicts are rejected', () => {
  for (const mutate of [
    (r) => { r[0].events = r[0].events.filter((e) => !(e.type === 'demo.verdict' && e.data.round === 1)); },
    (r) => { r[0].events.push(structuredClone(find(r, 0, 'demo.verdict'))); },
    (r) => { find(r, 0, 'demo.verdict', (d) => d.round === 1).data.pass = true; },
    (r) => { find(r, 0, 'demo.verdict', (d) => d.round === 2).data.pass = false; },
    (r) => { find(r, 1, 'demo.verdict').data.pass = false; },
    (r) => { find(r, 1, 'demo.verdict').data.pass = 'true'; },
    (r) => {
      const events = r[0].events;
      const indexes = events.flatMap((e, i) => e.type === 'demo.verdict' ? [i] : []);
      [events[indexes[0]], events[indexes[1]]] = [events[indexes[1]], events[indexes[0]]];
    },
  ]) {
    const recordings = copy(); mutate(recordings);
    assert.throws(() => validateRecordings(recordings));
  }
});

test('verdict round labels must match their repair order', () => {
  for (const [index, round] of [[0, 2], [1, 2]]) {
    const recordings = copy();
    find(recordings, index, 'demo.verdict').data.round = round;
    assert.throws(() => validateRecordings(recordings), 'misnumbered verdict must not support the recorded repair sequence');
  }
});

test('a successful final cannot precede the verification evidence', () => {
  for (const index of [0, 1]) {
    const recordings = copy(), events = recordings[index].events;
    const finalIndex = events.findIndex((e) => e.type === 'demo.final');
    const [final] = events.splice(finalIndex, 1);
    const verdictIndex = events.findIndex((e) => e.type === 'demo.verdict');
    events.splice(verdictIndex, 0, final);
    assert.throws(() => validateRecordings(recordings), 'an outcome reordered before proof must be rejected');
  }
});

test('the second work order must actually apply the first run lesson', () => {
  for (const applied of [[], ['unrelated-lesson'], undefined]) {
    const recordings = copy();
    find(recordings, 1, 'demo.workorder').data.lessons_applied = applied;
    assert.throws(() => validateRecordings(recordings), /lesson/i);
  }
  const recordings = copy();
  find(recordings, 0, 'demo.final').data.lesson = 'different-lesson';
  assert.throws(() => validateRecordings(recordings), /lesson/i);
});

test('all ten distinct recording evidence images exist and are nonempty', () => {
  const paths = [...new Set(runs.flatMap((run) => evidenceImages(run.events)))];
  assert.equal(paths.length, 10);
  assert.ok(paths.some((path) => path.endsWith('/img/expected-r1.png')));
  for (const id of ids) assert.ok(paths.some((path) => path.startsWith(`/runs/${id}/`)));
  for (const path of paths) {
    assert.match(path, /^\/(runs|assets)\//);
    assert.match(path, /\.(png|jpe?g|webp)$/i);
    const file = statSync(new URL(`..${path}`, import.meta.url));
    assert.ok(file.isFile() && file.size > 0, path);
  }
});

test('evidence collection normalizes local image paths and ignores non-image attachments', () => {
  const events = [
    { type: 'demo.camera', data: { image: 'runs/example/camera.jpg' } },
    { type: 'demo.check', data: { evidence: { image: '/runs/example/camera.jpg' } } },
    { type: 'demo.diffusion', data: { image: 'assets/expected.png', seen: 'runs/example/seen.webp' } },
    { type: 'demo.email', data: { attachments: [
      { path: 'runs/example/camera.jpg' }, { path: 'runs/example/reply.eml' },
      { path: 'https://example.com/remote.jpg' }, { path: 'data:image/png;base64,AAAA' },
    ] } },
    { type: 'demo.check', data: {} },
  ];
  assert.deepEqual(evidenceImages(events), ['/runs/example/camera.jpg', '/assets/expected.png', '/runs/example/seen.webp']);
});

test('playback timing is finite and retains time for evidence; report the actual recording duration', (t) => {
  const durations = runs.map((run) => {
    const delays = run.events.map(eventDelay);
    assert.ok(delays.every((delay) => Number.isFinite(delay) && delay > 0));
    const seconds = delays.reduce((sum, delay) => sum + delay, 0) / 1000;
    t.diagnostic(`${run.id}: ${run.events.length} events, ${seconds.toFixed(2)} seconds`);
    return seconds;
  });
  assert.ok(eventDelay({ type: 'demo.verdict' }) > eventDelay({ type: 'tool_result' }));
  assert.ok(eventDelay({ type: 'demo.final' }) > eventDelay({ type: 'demo.agent' }));
  assert.ok(Number.isFinite(eventDelay({ type: 'unknown.event' })));
  t.diagnostic(`Total uninterrupted playback: ${durations.reduce((sum, seconds) => sum + seconds, 0).toFixed(2)} seconds (excluding preload and pauses)`);
});
