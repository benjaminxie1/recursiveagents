# GridTwin workstream 3 of 3: evidence, vision and demo

You are workstream 3: everything that makes the loop visible and keeps the entry valid. That covers:
- the bank of real photos
- the vision module
- the splat viewer
- the demo script
- the backup recording
- the README

Live Demo is worth 20% and Creativity 25%. Two things disqualify the entry outright: assets we don't have rights to, and a missing "Built during the hackathon" section.

**Alike (development only).** If your session has the Alike MCP:
- At the start, call `resolve_work_item` with the anchor `{type: "git_branch", value: "benjaminxie1/recursiveagents:claude/sweet-faraday-yssuo9"}`.
- Record decisions with `record_decision`.

Never import Alike into product code.

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

### Build, in this order

1. **Photo bank, by about 12:00 (C5).**
   - Collect at least 3 real photos for each MVP state: 10 states, so about 30 photos.
   - Allowed sources:
     - Wikimedia Commons, under CC0, CC-BY or CC-BY-SA
     - PV defect or infrared datasets with permissive licenses (read each dataset's license file)
     - our own phone photos of electrical cabinets, breaker panels or displays we're allowed to photograph. For `display_off` and `healthy`, a display or HMI photographed switched off and then on is fine.
   - Never use stock sites or Google Images.
   - Scale every photo down to 1024 px on the long edge.
   - Give every file a row in `assets/photos/LICENSES.csv` with the columns `file, source_url, author, license`.
2. **`gridtwin/vision.py` (C6) and `scripts/calibrate_vision.py`.**
   - Run `check` on every photo with the fixed prompts, and write `assets/photos/calibration.json` with the expected state, the verdict and the confidence for each photo.
   - Swap out any photo the model misreads. A trap should come from the agents' reasoning, not from an ambiguous photo.
   - Use the `vision` role (Nemotron Omni). Fall back to `vision_fast` (Gemma) only if Omni is unavailable or too slow, and record the switch in MEMORY.md.
3. **The splat, time-boxed to 12:30.** Try these in order (SIMULATOR.md §4):
   1. a permissively licensed splat or drone capture of a solar site or substation
   2. image-to-3D from a CC-licensed aerial photo, after checking the tool's terms for rights to its output
   3. our own Scaniverse or Polycam capture (PLY) of a tabletop mock-up or an electrical room we're allowed to film
   
   If none of these works by 12:30, ship the viewer's 2D fallback and keep the splat as a stretch goal. The fallback is an SVG one-line diagram of the plant with the same pins.

   Record the splat's source and license. Fill in the `pose` for each viewpoint in `viewpoints.yaml`.
4. **The viewer: `viewer/` and `gridtwin/serve.py`.**
   - `python -m gridtwin.serve` serves `viewer/`, `assets/` and `runs/` on `localhost:8000`.
   - Vendor the splat library (Spark, or `@mkkellogg/gaussian-splats-3d`) into `viewer/vendor/`, so everything except inference works with Wi-Fi off.
   - It shows five things:
     - The splat, or the 2D fallback, with one pin per asset, colored by its latest status.
     - Click a pin to see the photos the agents saw for that asset (from `get_camera_view` tool results) and the verifier's verdicts that touch it.
     - The DoD checklist, with pass/fail updating live.
     - A run timeline: goal → DoD → claims → verdicts → supervisor decisions → final.
     - A large banner on `page` and on `final`, and tokens and $ when present.
     - The team board: `finding` events, each linking to the events that back it.
     - A memory panel: lessons read and written, from `lesson` events, each showing the run and verdicts behind it, with superseded lessons struck through.
   - **Live mode** polls `runs/<id>/events.jsonl` every second. `?run=<id>&replay=1` steps through a finished run at a speed you can adjust.
   - Until workstream 2 produces real runs, build against a hand-written `viewer/sample/events.jsonl` that follows C2.
   - The viewer is an evidence view for the autonomous loop, not a dashboard product. Write all copy accordingly.
5. **`docs/DEMO.md`: the 3-minute live script.** Update PLAN.md §4 to match. The F6 flow:

   | Step | What happens |
   |---|---|
   | Hook | Open on the problem the product solves |
   | Goal | Type the `s06_stale_scada` goal and walk away from the keyboard |
   | Plan | The DoD appears |
   | False done | Workers report that telemetry looks normal |
   | Catch | The verifier rejects: INV-3's `last_update_ts` is stale, the energy balance is off, and the INV-3 photo shows the display dark |
   | Replan | The supervisor replans |
   | Fix | `comms_ping` fails, a tech is dispatched, and the fix is verified as `DONE` |
   | Memory | `s06b` finishes in fewer steps; show both counts |
   | Poisoning | One table row: the baseline with memory learned the trap fix and failed `s03b` again, while GridTwin only remembers what it verified. "Done Means Done is what makes agent memory safe to keep." |
   | Number | The benchmark table: false-done rate, baseline vs. full |
   | Close | — |

   - Prepare a phone subscribed to `NTFY_TOPIC` to show a real page, from an `s05` run or a recorded clip.
   - Write 1–2 minute answers to "how do you know it's actually done?" and "what stops it from looping?" The first answer cites the verifier's caught and false-reject numbers and the hold check before DONE.
6. **`docs/PLAN.md`.** Bring it in line with the confirmed decisions:
   - §1 and §2: the splat is static, and the photos carry the state.
   - The Shared Brain is the in-repo knowledge base, and Alike is for development only.
   - Drift is judged by an LLM.
   - §4: the demo is F6.
   - §8: the Alike section becomes development-only.
7. **README.**
   - Architecture diagram, how to run it, and the role of each model with the reason it was picked.
   - The benchmark results table, with real numbers from `gridtwin/bench.py`. It includes:
     - the false-done rate, next to the success rate
     - the verifier's precision and recall
     - memory off vs. on
     - the memory-poisoning result, baseline vs. full
   - The **"Built during the hackathon"** section: what we wrote, versus the third-party pieces. The third-party list:
     - Crusoe Managed Inference
     - Nemotron and the other open models
     - You.com
     - Jev or OpenAI Decisions, only if used
     - ntfy.sh
     - the splat viewer library
     - the capture or image-to-3D tool
     - the photo sources (point to `LICENSES.csv`)
     - Alike, used only to coordinate development
   - Before 4:30, ask Ben to make the repo public.
8. **Backup run, by 4:15.**
   - Make a screen recording of a full live `s06` run with the viewer.
   - Commit that run's folder as `runs/demo-s06/` so it can be replayed.
   - Make the 1-minute video by 4:50.

## Done when

- `python scripts/calibrate_vision.py` reports every MVP photo classified correctly, and `calibration.json` is committed.
- With Wi-Fi off (inference aside), `python -m gridtwin.serve` plus `localhost:8000/?run=<id>` shows the pins, photos, checks and verdicts for a real `s06` run. It works in both live and replay mode.
- `LICENSES.csv` has a row for every file in `assets/photos/` and `assets/splats/`, and the README's provenance section is complete.
- You've run the demo script end to end once against a timer, and recorded the time in MEMORY.md.
