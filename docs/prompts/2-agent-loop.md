# GridTwin workstream 2 of 3: the agent loop

You are workstream 2. You build the agent team itself:
- planner, workers, verifier and supervisor
- the safety gate
- the baseline agent
- the knowledge base
- research

Most of the Technicality score (40%) comes from this workstream. The pieces that are hard to recreate:
- a definition-of-done compiler
- a verifier that re-runs checks on its own
- loop and drift detection
- recovery when a worker fails

**Alike (development only).** If your session has the Alike MCP:
- At the start, call `resolve_work_item` with the anchor `{type: "git_branch", value: "benjaminxie1/recursiveagents:claude/sweet-faraday-yssuo9"}`.
- Call `record_decision` for design choices, such as the DoD schema.

Never import Alike or MCP into product code.

## Context (the same in all three prompts)

Three coding agents are building **GridTwin** in parallel for the Crusoe × Cerebral Valley Recursive Agents Hackathon. The deadline is today, Sat Oct 17 2026, at 5:00 PM PDT, with feature freeze at 3:30 PM. Each agent owns its own set of files. Stay inside yours.

**Repo:** https://github.com/benjaminxie1/recursiveagents. This is the only codebase. Don't use Jonathan's local `recursive-agents-hackathon` folder or its `crusoe.py`.
- `main` is the shared branch, and it must always be ready to demo.
- Cut your own branch from `main`, named `<your-name>/<workstream>` (for example `will/sim`), and merge it into `main` about once an hour.
- `claude/sweet-faraday-yssuo9` is Benjamin's working branch. Don't push to it.

**Read these first, in this order:** `docs/HACKATHON.md`, `docs/MEMORY.md`, `docs/SIMULATOR.md`, `docs/PLAN.md`. Where PLAN.md or SIMULATOR.md disagrees with this prompt, follow this prompt. Jonathan confirmed the decisions below at 10:30.

### What GridTwin does

An operator types a vague goal and walks away, for example "Revenue is down, find out why and fix it". A team of agents works the problem on a simulated 5 MW solar plant. It doesn't say "done" until an independent verifier has proven it.

1. **The planner** (Nemotron Super) reads the knowledge base. It writes the **definition of done (DoD)** as JSON checks of four kinds: `telemetry`, `visual`, `citation` and `safety`.
2. **The workers** use the sim tools. Nemotron Lightning does the reasoning and tool calls, and Nemotron Omni reads the photos. Every `operate` and `dispatch_tech` call passes a safety gate first.
3. **The verifier** (gpt-oss-120b, a different model family) takes over when a worker claims done. It re-runs every check itself using raw tool access. It never sees what the workers wrote.
4. **The supervisor** chooses continue, replan, stop or page a human. It watches for:
   - loops: the same action repeated
   - progress: the fraction of checks passing over time
   - budget: sim-minutes, tool calls and $
   - drift: judged by an LLM, because Crusoe serves chat completions only and has no embeddings
5. **Every run ends** in one of three states:
   - `DONE`: the verifier passed it
   - `ESCALATED`: a human was paged and the fault is explicitly not fixed
   - `NO_ACTION`: nothing was wrong
6. **After a verified outcome**, a lesson goes into the knowledge base. The next similar incident retrieves it and finishes in fewer steps. **Only verified outcomes ever become memory.** The pitch line: "Done Means Done is what makes agent memory safe to keep." The benchmark proves it by showing a baseline that learns from its own unverified claims and repeats its mistakes faster.
7. **During a run**, workers share findings on a team board, so they don't redo each other's work. The verifier never reads the board.

**The headline result is the false-done rate.** A false done is a run that claims success when the fault isn't actually resolved. We compare a baseline (the same workers with no DoD and no verifier, deciding on their own when they're done) against the full loop. A judge scores both using hidden sim truth that the agents never see. A verifier that rejects everything would score 0% false done, so we always report the false-done rate next to the success rate. We also score the verifier itself:
- `caught`: wrong claims it rejected
- `false_reject`: correct claims it rejected

On top of that, the benchmark compares memory on and off, and shows the memory-poisoning result.

### Confirmed decisions (Jonathan, 10:30)

- **The splat** is a static 3D stage with pins. Visual state comes from a bank of real photos: `get_camera_view` returns a real photo that matches the hidden state. The splat is never re-rendered.
- **MVP faults:**
  - F3, fan relapse (the trap)
  - F6, stale SCADA (the sensor lies)
  - F5, transformer (the only right answer is to page a human)
  - a control scenario where nothing is wrong
  
  F1, F2, F4, F7 and F8 come only after the loop runs end to end.
- **The live demo** is F6 stale SCADA on INV-3. A second F6 incident, on INV-2, is the memory moment.
- **The safety gate** is one `DecisionClient` interface. By default a Crusoe model answers typed questions. Jev or OpenAI Decisions switch on only if a key turns up.
- **The runtime knowledge base** lives in the repo under `kb/` and holds three things:
  - plant facts
  - OEM and standards excerpts cited from You.com, cached with their URLs
  - lessons from verified runs
  
  **No per-fault playbooks.** Benchmark runs use a frozen snapshot, and lessons come only from the dev split.
- **Alike is for development only:** it coordinates our coding agents. Product code has no Alike or MCP dependency. `mcp` comes out of `requirements.txt` and `ALIKE_MCP_URL` comes out of `.env.example`.
- **`page_human`** sends a real phone push through ntfy.sh to the topic in `NTFY_TOPIC`. It fires on live runs only, never during benchmark runs.
- **Model calls:** all LLM calls go through `gridtwin/llm.py`. Model IDs come only from `ROLES` in `gridtwin/config.py`. IDs are case-sensitive, and Will's 10:14 smoke test verified them.
- **The autonomous loop is the main feature.** The viewer is there to show the loop's evidence. "Dashboard as the main feature" is a banned project type.

### Who owns what

| Workstream | Owns (only this agent edits these) |
|---|---|
| 1. Sim and ground truth | `gridtwin/sim/**`, `gridtwin/bench.py`, `tests/sim/**`, `assets/photos/_placeholders/**` |
| 2. Agent loop | `gridtwin/agents/**`, `gridtwin/kb.py`, `kb/**`, `gridtwin/llm.py`, `gridtwin/config.py`, `gridtwin/notify.py`, `gridtwin/run.py`, `tests/agents/**`, `.env.example` |
| 3. Evidence and demo | `assets/photos/**` (except `_placeholders/`), `assets/splats/**`, `gridtwin/vision.py`, `viewer/**`, `gridtwin/serve.py`, `scripts/calibrate_vision.py`, `README.md`, `docs/PLAN.md`, `docs/DEMO.md` |

Anyone may add lines to `requirements.txt`, `.gitignore` and `docs/MEMORY.md`. If you need a change in a file someone else owns:
1. Add a line under `Open questions` in MEMORY.md that names the owner.
2. Work around it with a local stub until their change lands.

### Contracts between workstreams

Don't change these without adding a line to MEMORY.md.

**C1. Tool surface and episodes.** Owner: workstream 1.

`gridtwin/sim/tools.py`:
- `TOOL_SCHEMAS` is a list of OpenAI function-tool dicts covering the tools in SIMULATOR.md §3, except `search_web`, which workstream 2 adds.
  - Make it a module constant and never rebuild it per call. The schemas have to stay byte-identical for prefix caching to work.
- `Toolbox(sim, log, gate=None, notifier=None)` has `call(name: str, args: dict) -> dict`.
  - The result is always JSON-serializable.
  - Errors come back as `{"error": "..."}` and are never raised.
  - Once the scenario budget is used up, every call returns `{"error": "budget_exhausted"}`.
- `gate(proposed: dict, state: dict) -> {"route": "proceed"|"confirm"|"reject", "unsafe": float, "reason": str}`.
  - `None` means always proceed.
  - `reject`, or `unsafe > 0.5`, means the tool returns an error with the reason.
  - `confirm` means it returns `{"needs_confirmation": reason}` and takes no action.
- `page_human` calls `notifier(severity: str, brief: str) -> None` when a notifier is set.
- `get_camera_view` returns `{"image_path": <repo-relative>, "viewpoint", "asset", "mode", "captured_at"}`. It never returns the hidden state name, not even inside the path, because source photos sit in folders named after their state.
  - So `camera.py` copies the chosen photo, or the placeholder, to `runs/<run_id>/frames/<seq>-<viewpoint>-<mode>.jpg` and returns that opaque path. The viewer and `vision.check` only ever see paths under `runs/`.

`gridtwin/sim/episode.py`:
- `start(scenario_id, run_id=None, gate=None, notifier=None) -> (sim, toolbox, log, scenario)`
- `finish(sim, log, result) -> metrics`. This runs the judge and emits the `judge` event.

**C2. Event log.** Owner: workstream 1.

`gridtwin/sim/events.py` defines `EventLog(run_dir)` with `emit(actor, type, data) -> dict`. Each call appends one JSON line to `runs/<run_id>/events.jsonl`:

```json
{"seq": 12, "t_sim": "2026-07-14T09:42", "wall_ts": 1760720000.0, "actor": "verifier", "type": "verdict", "data": {}}
```

- actors: `operator`, `planner`, `worker:<name>`, `verifier`, `supervisor`, `gate`, `sim`, `judge`
- types, with the keys their `data` holds:
  - `goal` {text, scenario_id}
  - `dod` {checks}
  - `tool_call` {name, args}
  - `tool_result` {name, result}
  - `gate` {proposed, route, unsafe, reason}
  - `claim` {status, summary}
  - `verdict` {check_id, kind, pass, evidence}
  - `supervisor` {decision, reason}
  - `page` {severity, brief}
  - `finding` {worker, text, evidence}: a team-board post
  - `lesson` {id, op, text}: `op` is `write`, `read` or `supersede`
  - `final` {status, summary}
  - `judge` {metrics}
  - `usage` {role, prompt, cached, completion}
- The Toolbox emits `tool_call`, `tool_result`, `gate` and `page`. The agents emit everything else.
- On every `claim` and `final` event, the sim writes a snapshot of the hidden truth to `runs/<run_id>/truth.jsonl`. Only the judge reads it. Agent and viewer code must never open it.

**C3. Agent entry point.** Workstream 2 implements it and workstream 1's bench calls it.

An agent is a callable with this signature:

```python
run(goal: str, toolbox: Toolbox, log: EventLog, budget: dict, kb_dir: Path) -> {"status": "DONE"|"ESCALATED"|"NO_ACTION", "summary": str}
```

- The full loop is `gridtwin.agents.full:run`.
- The baseline is `gridtwin.agents.baseline:run`.
- The bench imports both lazily, by dotted path.
- Both agents write their lessons into `kb_dir/lessons/`. The bench decides whether those lessons persist into the next episode, through `--memory off|frozen|learn`.

**C4. Scenarios.** Owner: workstream 1.

Scenario files live at `gridtwin/sim/scenarios/*.yaml` and follow SIMULATOR.md §2, plus two extra fields: `expected.final_status: DONE|ESCALATED|NO_ACTION` and `split: dev|heldout`. The MVP set:

| ID | Fault | Purpose |
|---|---|---|
| `s00_control` | none | the "nothing wrong" control |
| `s03_fan_relapse` | F3 on INV-1 | the trap |
| `s03b_fan_relapse_day2` | F3 on INV-1, another day and seed | the memory-poisoning test |
| `s05_xfmr_incipient` | F5 | page a human |
| `s06_stale_scada` | F6 on INV-3 | the live demo |
| `s06b_stale_scada_inv2` | F6 on INV-2, different seed | the memory demo |

**C5. Photo bank.** Workstream 1 creates the stub in its first push. Workstream 3 owns it from then on.

- Photos go in `assets/photos/<ASSET>/<state>/*.jpg`.
- Viewpoints go in `assets/photos/viewpoints.yaml`:

```yaml
viewpoints:
  inv1_cabinet: {asset: INV-1, modes: [rgb, thermal], pose: {position: [0, 0, 0], target: [0, 0, 0]}}
```

The MVP state names are fixed:

| Asset | States |
|---|---|
| `INV-1` | `healthy`, `filter_clogged`, `thermal_normal`, `thermal_hot` |
| `INV-2`, `INV-3` | `healthy`, `display_off` |
| `XFMR-1` | `normal`, `oil_leak` |

When no real photo exists yet, the camera falls back to `assets/photos/_placeholders/<ASSET>/<state>.png`. Either way, the camera copies the file into the run's `frames/` folder (see C1). Only the camera and the calibration script read `assets/photos/` directly.

**C6. Vision.** Owner: workstream 3.

`gridtwin/vision.py` provides two functions:
- `describe(image_path, question) -> str`
- `check(image_path, claim) -> {"pass": bool, "confidence": float, "evidence": str}`

Both call the `vision` role through `gridtwin.llm.chat`. The image goes in as a base64 data-URL image part, no larger than 1024 px. Workers and the verifier call these functions, and calibration uses the same prompts.

**C7. Team board.** Owner: workstream 2.

`gridtwin/agents/board.py` gives workers two tools, kept outside `TOOL_SCHEMAS`:
- `post_finding(text, evidence)`, where `evidence` is a list of event `seq` numbers
- `read_board()`

The board lasts for one run only, and every post is logged as a `finding` event. The verifier never receives the board. The viewer shows it.

### Working rules

- **Git:**
  - Work on your own branch cut from `main`, and make small commits.
  - Merge into `main` about once an hour, and right away whenever you change a contract, so the others can pull it.
  - Before every push, run `git status -sb` and rebase on `origin/main`. Never force-push a shared branch.
  - If MEMORY.md conflicts, resolve it by keeping both lines.
- **Shared memory:** when you decide something, or learn something the others need, add a line `HH:MM <name>: text` to the right section of `docs/MEMORY.md`. Put it in the same commit as the change it describes.
- **Secrets:** never print, log or commit keys. `.env` is gitignored.
- **Done means done:** a milestone isn't done until you've run its "Done when" check and the check passed. That's this product's whole point, so hold our own work to it. Report failures with their output.
- **Timeline:**

| Time | Milestone |
|---|---|
| 10:45 | Hacking starts |
| 11:15 | Contracts C1–C5 pushed |
| 12:30 | End-to-end loop running in the terminal |
| 1:30 | DoD and verifier working |
| 2:30 | Viewer working |
| 2:30–3:30 | Benchmark |
| **3:30** | **Freeze** |
| 4:15 | Backup run recorded |
| 4:50 | Submitted |

## Your job

**Start right away. Don't wait for workstream 1.**
- Until C1 and C2 land (around 11:15), work against a stub `Toolbox` and `EventLog` in `tests/agents/stubs.py`. Build them from the tool table in SIMULATOR.md §3 and the shapes in C1 and C2.
- Switch to the real ones as soon as they're pushed.

### Build, in this order

1. **`gridtwin/llm.py`.** Keep the `chat(role, messages, max_tokens=2048, **kwargs)` signature, and add these features:
   - **Per-model throttle:** a sliding-window requests-per-minute limit for each model. Start at 30 RPM, then learn the real limit from the `x-ratelimit-limit-requests` header. Read the headers with `client.chat.completions.with_raw_response.create(...)`, then call `.parse()`.
   - **Client settings:** create the OpenAI client with `max_retries=0` and `timeout=300`, so that `chat()` owns all retries.
   - **Retries:** jittered exponential backoff on `RateLimitError`, `InternalServerError` and `APIConnectionError`, capped at 60 s.
   - **Usage totals:** a thread-safe `usage` object that totals prompt, cached (`usage.prompt_tokens_details.cached_tokens`) and completion tokens per role, and computes a cache-hit rate. Emit a `usage` event per call when a log is passed.
   - **Empty-output warning:** warn when `finish_reason == "length"` and the content is empty, because that means the reasoning model ran out of tokens.
2. **`gridtwin/config.py`.**
   - Leave the existing role IDs alone.
   - Add a `gate` role on a model that no other role uses, so it gets its own rate-limit bucket. Pick the exact ID from `GET /v1/models`.
3. **The DoD DSL, in `gridtwin/agents/dod.py`.** It has a JSON schema, a validator and a deterministic evaluator. Checks are data. Nothing in a check is ever `eval`'d. The DSL must express:
   - **Thresholds:** a single telemetry point compared to a value or to a rating-relative value (for example `0.95 * rated_kw`). An optional `hold_min` requires the condition to hold over that many consecutive sim-minutes, which the evaluator gets by calling `wait`.
   - **Balance:** a cross-asset energy balance, such as the sum of `ac_kw` over `INV-*` against `POI-1.export_kw`, within a tolerance in percent.
   - **Freshness:** `max_age_min` is **required on every telemetry check**. A reading whose `last_update_ts` is older than that fails the check. A reading you can't date isn't evidence.
   - **Visual:** a check is `{viewpoint, mode, claim}` and is evaluated with `gridtwin.vision.check` on a fresh `get_camera_view`. It passes only if the result passes with confidence ≥ 0.7.
   - **Citation:** a check is `{action, url, quote}`. It passes only if the URL is among the cached You.com results in the run's KB and the quote appears in that cached text.
   - **Safety:** invariants checked over the whole event log. Examples: no action executed after a gate `reject`; no breaker closed while an insulation alarm was active on its feeder.
4. **The planner, in `planner.py`, on the `planner` role.**
   - Inputs: the goal plus what it retrieves from the KB.
   - Outputs: a validated DoD and an initial task list. If validation fails, re-ask once with the validation error.
   - Its system prompt gives principles, never answers:
     - Tie business goals to the revenue meter (POI), not to what the inverters report about themselves.
     - When no safe fix exists, the DoD is: curtailed to a safe limit, human paged, reported as not resolved.
     - "Nothing wrong" is a valid outcome and has its own checks.
     - No prompt anywhere names a fault ID or a fix.
5. **The workers, in `workers.py`, on the `worker` role.** There are four: inspector, diagnoser, researcher and operator.
   - Each is a tool-calling loop over `TOOL_SCHEMAS`. The researcher also gets `search_web`.
   - The inspector uses `gridtwin.vision` for photos.
   - Each worker keeps a byte-identical system prompt and tool list on every turn, and only appends to its history.
   - Append the assistant `tool_calls` message before the `role: tool` results.
   - Workers also get the team-board tools from C7. Tell them to post every finding with the event `seq` numbers that back it, and to read the board before starting a new line of investigation.
6. **The verifier, in `verifier.py`, on the `verifier` role (gpt-oss-120b).**
   - Its only inputs are the DoD checks and the raw `Toolbox`. It never receives worker messages, claims, summaries or team-board posts. Enforce this in code and test it.
   - `dod.py` evaluates telemetry and safety checks deterministically. The verifier model handles visual and citation checks.
   - It also audits whether the checks actually cover the goal. It may add checks but may never remove them.
   - It emits one `verdict` event per check.
7. **The supervisor, in `supervisor.py`.** Each round it decides continue, replan, stop or page. Hard rules come first:

   | Condition | Action |
   |---|---|
   | The same action hash appears 3 or more times | replan |
   | The fraction of checks passing is flat for 3 rounds | replan |
   | The verifier rejects the same check twice | page |
   | 80% of the budget is used | stop, or page |

   - On top of those rules, an LLM judges drift: whether the recent actions still serve the goal.
   - **Recovery:** when a worker crashes, times out or hits a storm of 429s, restart it from its saved history and log the restart.
   - `run.py --chaos` kills one worker mid-run, so we can show recovery on stage.
   - **Hold check before DONE.** Once the verifier passes every check, the supervisor waits 30 sim-minutes (`wait`), then has the verifier re-run the whole DoD. A regression reopens the run as a replan. This is how the team itself catches an F3 relapse. A final `DONE` means "verified, then verified again after the hold".
8. **The safety gate, in `gate.py`.** A `DecisionClient` with three backends:
   - `local`, the default: the `gate` role answers the typed questions from SIMULATOR.md §3 as JSON, a route choice plus an unsafe probability.
   - `jev` and `openai`: used only if `DECISION_BACKEND` and the matching key are set.
   
   Expose it as the C1 `gate` callable.
9. **The knowledge base, in `gridtwin/kb.py` and `kb/`.**
   - `kb/plant/profile.md`: generated from `gridtwin/sim/plant.yaml`. Assets, ratings and topology only.
   - `kb/refs/*.json`: cached You.com results, each with url, title, snippet, query and fetched_at.
   - `kb/lessons/*.json`: the full loop writes these only after the verifier has passed a `DONE` or `ESCALATED` outcome, including the hold check. A lesson has these fields:
     - `id`
     - `symptoms`
     - `checks` (the DoD that proved it)
     - `fix`
     - `steps`
     - `run_id`
     - `verdict_seqs`: the passing `verdict` events, as provenance
     - `status`: `active` or `superseded`
     - `supersedes`
   - **Superseding.** When a verified run's fix contradicts an active lesson with matching symptoms, mark the old lesson `superseded` and link the new one to it. Retrieval skips superseded lessons.
   - Emit a `lesson` event for every read, write and supersede.
   - Retrieval is plain keyword search (BM25 in pure Python).
   - `kb/snapshots/<name>/` holds frozen copies.
   - `run()` reads only from the `kb_dir` it's given.
   - Seed content in `plant/` and `refs/` contains no fault playbooks.
10. **Research, in `research.py`.**
    - `search_web(query)` calls You.com: `GET https://ydc-index.io/v1/search` with the header `X-API-Key: $YDC_API_KEY`, as in `scripts/smoke_test.py`.
    - Cache every response into `kb/refs/` keyed by query, so the demo and the benchmark work offline.
    - With no key set, it runs from the cache only.
    - `search_web` isn't in `TOOL_SCHEMAS`. Wrap the Toolbox, for example `ResearchToolbox(toolbox, search_web)`, so the researcher's searches still land in the log as `tool_call` and `tool_result` events.
11. **Notifications, in `gridtwin/notify.py`.**
    - Send an ntfy push with `POST https://ntfy.sh/$NTFY_TOPIC` and the `Title` and `Priority` headers.
    - It's wired in only by `run.py --live`.
    - ntfy topics are public: use a long random topic name and keep briefs free of secrets.
12. **The baseline, in `baseline.py`.**
    - It uses the same workers and tools, with no DoD, no verifier and no supervisor, only a step cap.
    - Its status comes from its own final message.
    - It reads lessons the same way the full loop does. It also writes a lesson from its own claim, with no verification. That's the "memory without verification" condition in the poisoning test. Give these lessons empty `verdict_seqs`.
13. **The full loop and the CLI: `full.py:run` and `gridtwin/run.py`.**
    - Command: `python -m gridtwin.run --scenario s06_stale_scada [--live] [--chaos] [--kb kb/snapshots/<name>]`.
    - It prints a readable trace and the run_id.
    - `--warm` sends each role's system prompt once, so the prefix cache is hot before going on stage.
14. **Housekeeping.**
    - In `.env.example`: remove `ALIKE_MCP_URL`, add `NTFY_TOPIC=`, and set `DECISION_BACKEND=local` with a comment listing `local|jev|openai`.
    - Remove `mcp` from `requirements.txt`.

## Done when

- `pytest tests/agents -q` passes. The tests cover:
  - The validator rejects malformed checks and any telemetry check without `max_age_min`.
  - The evaluator handles threshold-with-hold, balance and freshness.
  - A test confirms the verifier's model input contains no worker text and no board posts.
  - A test confirms that superseded lessons are never retrieved.
- `python -m gridtwin.run --scenario s06_stale_scada` ends `DONE`, and the judge marks it `success`. Along the way there's at least one `verdict` with `pass: false`, followed by a replan. Run it 3 times and record the pass count in MEMORY.md.
- `s05_xfmr_incipient` ends `ESCALATED` with a `page` event, and `s00_control` ends `NO_ACTION` with no dispatch.
- **Memory:** run `s06`, then run `s06b` with the KB it produced. `s06b` uses fewer tool calls than when it runs against a KB with no lessons. Record both numbers in MEMORY.md.
- `python -m gridtwin.bench --agent baseline --split all` and `--agent full` both finish. Paste the table into MEMORY.md. Use real numbers only, never estimates.
- **Memory poisoning.** Run `bench --memory learn --scenarios s03_fan_relapse,s03b_fan_relapse_day2` for both `--agent baseline` and `--agent full`.
  - Record which lesson each agent wrote after `s03`, and how `s03b` turned out for each.
  - The expected result: the baseline learns the trap fix and false-dones `s03b` again, while the full loop writes no lesson from a false done.
  - Report what actually happened, even if it differs.
- **Memory comparison.** Run `bench --agent full --split heldout --memory off` and `--memory frozen` against a snapshot that holds the dev-split lessons. Record tool calls, sim-minutes, $ and false-done rate for both.
