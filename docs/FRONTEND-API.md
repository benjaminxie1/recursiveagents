# GridTwin demo: API for the frontend

This is everything a frontend needs to drive and show the end-to-end demo.
The backend is done and tested; a page only has to call these endpoints and render the JSON.
The event-level contract is in `gridtwin/demo/CONTRACT.md`; this file is the page-level view of it.

## Start the backend

```bash
.venv/bin/python -m gridtwin.demo.serve --port 8010
```

The API is then at `http://localhost:8010`.
Any page served from `http://localhost:<any port>` or `http://127.0.0.1:<any port>` may call it (CORS is on for local origins).
To allow another origin, set `GRIDTWIN_CORS_ORIGINS=https://example.dev,https://other.dev` before starting the server.
Other origins can read nothing and cannot start runs.

Live runs call Nemotron and gpt-oss on Crusoe and take about 40 to 70 seconds.
Stub runs (`"stub": true`) make no model calls, finish in about 3 seconds and tell the same story, so build the page against stub runs first.

## Endpoints

| Method and path | What it returns |
|---|---|
| `GET /api/demo/health` | `{ok, schema, scenario, models: {role: model id}, keys: {crusoe: bool, jev: bool}, diffusion: "diffusion" or "photo_bank"}` |
| `POST /api/demo/run` | Body `{scenario?: "s06_stale_scada", stub?: false, memory?: true, fresh_memory?: false}`. Returns at once with `{run_id, kb, cmd, urls: {snapshot, events, stream, events_jsonl, memory, page}}`. `kb` is the lesson store the run reads and writes: `e2e-kb` for live runs, `e2e-kb-stub` for stub runs (so rehearsals never touch the live store), a throwaway store when `memory` is false. `fresh_memory` empties that store first. |
| `GET /api/demo/runs` | Demo runs, newest first: `[{id, mtime, status, rounds, scenario_id, stub}]`. `status` is `RUNNING`, `VERIFIED`, `ESCALATED` or `FAILED`. |
| `GET /api/demo/runs/<id>` | The whole run as one snapshot (below). Poll it, or use the stream. |
| `GET /api/demo/runs/<id>/events?after=<seq>` | `{run_id, events: [raw event], last_seq, done}`: the raw log after `seq`, for pages that fold events themselves. |
| `GET /api/demo/runs/<id>/stream` | Server-Sent Events. One `demo` event per log line (its `id` is the `seq`, so `EventSource` resumes after a drop), then one `end` event whose data is the final snapshot. |
| `GET /api/demo/memory?kb=e2e-kb` | The memory flywheel: `{kb, exists, stats: {runs, lessons, rounds_by_run, lesson_scores}, lessons: [{id, summary, fix, forbid, acceptance, source_run}]}`. |
| `GET /<path>` | Files: every image path and `.eml` path in the JSON is repo-relative (`runs/...` or `assets/...`); load it as `http://localhost:8010/<path>`. |

Errors are JSON `{error}` with status 400 (bad input), 403 (origin not allowed) or 404 (no such run).

## Follow a run live

```js
const API = 'http://localhost:8010';
const { run_id, urls } = await fetch(`${API}/api/demo/run`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ stub: false, memory: true }),
}).then((r) => r.json());

const es = new EventSource(API + urls.stream);
es.addEventListener('demo', (m) => onEvent(JSON.parse(m.data)));      // one raw event per message
es.addEventListener('end', (m) => { render(JSON.parse(m.data)); es.close(); });  // final snapshot
```

The simplest page ignores the raw events and re-fetches `GET /api/demo/runs/<id>` whenever a `demo` message arrives (or every second), then renders the snapshot.
A replay of a finished run is the same: fetch the snapshot, or `events?after=0` and step through the events at your own pace.

## The snapshot (`gridtwin.demo.snapshot/1`)

Every list is in the order things happened; every item carries `seq` (its position in the log).
`round` is 0 for diagnose and plan, then 1, 2, 3 for each contractor visit and its verification.

| Field | What the page shows with it |
|---|---|
| `run_id`, `scenario`, `goal` | Header: the operator's complaint (`goal`) and the scenario. |
| `status`, `done`, `error` | `RUNNING` until the end, then `VERIFIED` (fixed and proven), `ESCALATED` (not proven after 3 rounds; a human is paged) or `FAILED` (the run process died; `error` says why). |
| `current` | `{stage, round}` while running, else `null`: the step to highlight. |
| `stages` | `[{stage, round, status: "active" or "done"}]`; stages are `diagnose, plan, dispatch, contractor, verify, redispatch, memory, final`. |
| `rounds` | Contractor rounds so far. |
| `sensors` | The simulated SCADA feed per inverter: `{asset, data_age_min, stale, last_update_ts, latest: {ac_kw, dc_kw, heatsink_c, status}}`. INV-3 is stale: its comms card failed, so its numbers are frozen and look fine. |
| `cameras` | Camera views: `{viewpoint, asset, image, description, actor: "diagnoser" or "verifier", round}`. The verifier always picks a different camera than the contractor's photo. |
| `twin_url` | Link to the Gaussian-splat twin of the plant with this run's evidence (`twin.html?...` on the same server). |
| `diagnosis` | `{asset, fault, confidence, summary, evidence: [str], model}`. |
| `agents` | Every agent: `{id, parent, depth (0 or 1), role, model, task, status: "spawned", "done" or "failed", output, team, round}`. |
| `teams` | The one-level recursion, ready to draw: `[{team: "diagnose", "plan" or "verify", round, lead, model, status, members: [agent id]}]`. Leads are depth 0; members are depth 1 and cannot spawn. |
| `work_orders` | `{wo_id, asset, work_order, steps, forbid, acceptance, lessons_applied, round}`; a new one per redispatch. |
| `emails` | The thread with the simulated contractor: `{direction: "out" or "in", from, to, subject, body, eml, attachments: [{name, path}], round}`. Nothing is sent; `eml` is the file. |
| `contractor_visits` | What the contractor did on the plant: `{round, shortcut, note, actions: [{tool, args, ok}]}`. On round 1 it takes a shortcut unless the work order forbids it. |
| `checks` | Verification against the plant, never the contractor's word: `{round, kind: "telemetry", "visual", "jev" or "meta", name, pass, detail, evidence}`. |
| `jev` | The xyz-to-JSON interpreter and decision: `input` is the interpreted JSON (asset and camera positions in the splat frame, distances, angles, coverage, sensors, visual claims); `output` is `{verdict, p_verified, uncertainty, backend, reason}`. |
| `diffusion` | Expected-state image sent back to the contractor when verification fails: `{round, image, backend: "diffusion" or "photo_bank", prompt, source}`. |
| `verdicts` | One per round: `{round, pass, failed: [check name], meta: {verdict, reason, model}}`. |
| `memory` | The flywheel: `recall` (lessons recalled at the start, with per-role `hints` and `contractor_notes`), `writes` (lesson written after a verified fix), `scores`, `stats` (`{runs, lessons, rounds_by_run, lesson_scores}`, for a "rounds per run" trend). |
| `final` | `{status, rounds, summary, usd, wall_s}`. |
| `judge` | The simulator's hidden-truth check of the run: `{success, false_done, safety_violations, resolved}`. `false_done` must always be false. |
| `usd`, `wall_s` | Cost of the model calls and wall time. |

## The story, mapped to the data

1. Diagnose one failure from simulated sensors and camera views: `sensors`, `cameras`, `twin_url`, `diagnosis`, team `diagnose`.
2. Recursive agents on Crusoe, one level: `teams` and `agents` (Nemotron Super leads, Nemotron Lightning and Nano sub-agents, gpt-oss judges).
3. Next steps to a simulated contractor by email: `work_orders`, `emails` with `direction: "out"`.
4. The contractor says it is done: `contractor_visits`, `emails` with `direction: "in"`.
5. Verification from multimodal data and JEV: `checks`, `jev`, `verdicts`, team `verify`.
6. If it is not fixed, email the contractor back with an expected-state image: `diffusion`, the next `emails` and `work_orders`.
7. The agents improve through memory: `memory`; run the demo twice with the same `kb` and the second run is verified in round 1 (`GET /api/demo/memory` shows the trend).

## A two-run demo

```bash
curl -s -X POST localhost:8010/api/demo/run -d '{"fresh_memory": true}'
```

```bash
curl -s -X POST localhost:8010/api/demo/run -d '{}'
```

The first run catches the contractor's shortcut and is fixed in round 2; the second recalls the lesson and is fixed in round 1.
Both runs use the lesson store `runs/e2e-kb`; `GET /api/demo/memory` shows it (for stub runs, `GET /api/demo/memory?kb=e2e-kb-stub`, or follow `urls.memory` from the POST).
