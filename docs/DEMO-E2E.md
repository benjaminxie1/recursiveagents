# GridTwin end-to-end demo: script, Plan B ladder, go/no-go

Owner: Will.
Written by the demo-pivot session on branch `will/agent-build`.
This is the script for the end-to-end flow in `gridtwin/demo/CONTRACT.md` (scenario `s06_stale_scada`).
It supersedes the stage plan in `docs/DEMO.md` for the live demo; the hook and the backup-recording rules still come from there.
Judge answers that are not about this flow stay in `docs/JUDGE_QA.md`.

Status: draft, 14:15, written against the pipeline at `082bdd0` (`gridtwin/demo/run.py`, `pipeline.py`, `contractors.py`, `memory.py`); `viewer/demo.html`, `jev.py`, `diffusion.py` and `serve.py` were not committed yet.
Every `[fill]` is a value that only the golden run can give; nothing in brackets gets said on stage until it is filled from a real run.
Every `[confirm]` is a page detail to check against the built page at go/no-go.

Facts from the code at `082bdd0` that the steps below rely on:
- `run.py` flags: `--scenario`, `--memory <dir>` (default `runs/demo-kb`), `--fresh-memory` (empties that store's lessons first), `--run-id`, `--max-rounds` (default 3), `--stub` (no model calls, deterministic stand-ins, rehearsal only).
- A run writes `runs/<run_id>/` with `events.jsonl`, `mail/NN-out|in-<wo>-r<round>.eml`, `img/expected-r<round>.png`, `frames/` and `result.json`.
- The demo server's Run button names runs `demo-s06-<YYYYmmdd-HHMMSS>`; `memory: false` gives the run an empty store of its own.
- The twin scene is `GRIDTWIN_TWIN_SCENE` (default `_dev`); the JEV step reads `assets/splats/<scene>.registration.json` and falls back to the same file in `~/claude/recursiveagents`.
- `gridtwin.demo.serve` serves everything `gridtwin.serve` does, so the evidence view (`/`), the wall (`/wall.html`) and the twin (`/twin.html`) are on the same port as `demo.html`.
- The JEV fallback (`backend: local`) asks the `gate` role, Nemotron Nano on Crusoe.
- `demo.final.usd` is the process-wide total from `gridtwin/llm.py`, so it covers every model call in the run, the Omni camera calls included.

## Measured so far (real runs only)

First live run on Crusoe: `runs/e2e-live-1/` (not `--stub`), about 14:13, code at `082bdd0` plus the uncommitted `jev.py` and `diffusion.py`.
These numbers are interim; the golden runs replace them, and only the golden runs' numbers go on stage.

- Result: VERIFIED in 2 rounds; the judge scores it success, no false done, 0 safety violations.
- Wall time 68.3 s; model cost $0.004 for the whole run.
- Stage starts, seconds after the goal: diagnose 0, plan 16, dispatch and contractor 40, verify round 1 at 40, redispatch 48, contractor round 2 at 58, verify round 2 at 59, memory 64, final 65.
- INV-3 at diagnosis: `data_age_min` 91, stale.
- Omni on the INV-3 camera at diagnosis: "The inverter's display is not lit and shows no readings; it appears dark."
- Sub-agents: planner on Nemotron Super at depth 0; `planner.safety`, `planner.repair` and `planner.acceptance` on Nemotron Lightning at depth 1.
- Round 1 verdict failed `scada_fresh_and_running`, `display_lit@inv3_cabinet` and `jev_fix_verified`; JEV said `not_verified`, `p_verified` 0.04.
- Round 2 verdict passed every check; JEV said `verified`, `p_verified` 0.97.
- JEV backend `local` and diffusion backend `photo_bank` in both rounds.

So a live run finishes about 1:30 into the script, before Will reaches the verify stage; he narrates cards that are already on the page, which is what the script assumes.

## The story in one breath

A plant fault is not fixed when someone says it is fixed; it is fixed when the plant proves it.
GridTwin is an agent team that diagnoses a fault from sensors and cameras, writes the work order with sub-agents, sends it to a contractor, and then checks the plant itself instead of trusting the contractor's "done".
When the check fails, it emails the contractor back with what failed and a picture of what done looks like, and only a verified fix becomes memory, so the next incident is right first time.

## Before going on stage (T minus 15 minutes)

Run everything from `~/claude/recursiveagents-build`.
This worktree has no `.venv` of its own (at 14:12), so link the main checkout's once: `ln -s ~/claude/recursiveagents/.venv .venv` (`.venv/` is gitignored).

1. Pull and check the branch: `git -C ~/claude/recursiveagents-build log --oneline -5`.
2. Check Crusoe and every role's model: `.venv/bin/python -m scripts.smoke_test`.
3. Warm the prefix cache: `.venv/bin/python -m gridtwin.run --warm`.
4. Put the twin scene into this worktree, because splats are gitignored and only the main checkout has them:
   `cp ~/claude/recursiveagents/assets/splats/_dev.{ply,registration.json} assets/splats/`, or regenerate it here with `.venv/bin/python scripts/twin_dev_scene.py`.
   For the real scan instead, copy its files the same way and start the server with `GRIDTWIN_TWIN_SCENE=<scene>`; the scene name must resolve in `twin.html` (a bare name loads `<scene>.ply`) and in the pipeline (`<scene>.registration.json`) `[confirm scene name with the scene-prep session]`.
5. Empty the stage memory so the live run starts with no lessons and takes the shortcut: `rm -rf runs/demo-kb/lessons` (or tick the page's fresh-memory option if it has one `[confirm]`).
6. Start the demo server: `.venv/bin/python -m gridtwin.demo.serve --port 8010`.
7. Open these tabs, left to right, and leave each one where it says:
   - Tab 1, live: `http://localhost:8010/demo.html` (idle, Run button visible).
   - Tab 2, golden run 1 (no memory, two rounds): `http://localhost:8010/demo.html?run=demo-20261017-golden1&replay=1`, paused at the start.
   - Tab 3, golden run 2 (with memory, one round): `http://localhost:8010/demo.html?run=demo-20261017-golden2&replay=1`, paused at the start.
   - Tab 4, twin: `http://localhost:8010/twin.html?scene=_dev&tour=1&run=demo-20261017-golden1&replay=1`.
   - Tab 5, Finder on `runs/demo-20261017-golden1/mail/`, so the `.eml` files open in Mail with a double click.
8. Phone hotspot on and joined, in case venue Wi-Fi drops; every page, photo and library is local, only the model calls need the internet.

## Three-minute script

The live run starts at 0:20 in tab 1.
If it is still behind the script at 1:40, switch to tab 2 at the same stage and say "this is the run we recorded at [fill time]; same code, same models".

### 0:00 Hook (20 s)

On screen: tab 1, idle, with inverter 3 showing normal numbers.

Will says:

> AI data centers are adding load faster than the US can build plants.
> New gas turbines ship around 2031, and transformers take three years.
> So the plants we have must stay up.
> The costliest failure isn't a fault, it's a repair someone marked fixed when it wasn't.
> In Winter Storm Elliott, plants that weren't ready paid about 1.8 billion dollars in penalties.

Sources: `README.md` "Why this matters now".

### 0:20 One goal, then walk away (10 s)

On screen: click Run in tab 1; the run id appears and the stage bar starts on `diagnose`.

Will says:

> The owner says inverter 3 looks fine on the dashboard but revenue is down.
> I press run once, and from here nobody touches the keyboard.

### 0:30 Diagnose (25 s)

On screen (events `demo.sensor`, `demo.camera`, `demo.diagnosis`):
- The sensor panel shows INV-3's numbers looking normal, with `data_age_min` at [fill, 91 in e2e-live-1] and a stale badge.
- The camera card shows the INV-3 display dark, with Nemotron Omni's one-line description.
- The diagnosis card names the comms card, with its confidence and evidence list.

Will says:

> Every number on inverter 3 looks perfect.
> They're also [fill, 91 in e2e-live-1] minutes old, because its comms card died and the feed froze on its last good reading.
> Nemotron Omni reads the camera and agrees with the timestamps, not the numbers: the display is dark.

### 0:55 Plan with sub-agents (25 s)

On screen (events `demo.agent`, `demo.workorder`):
- The agent tree: the planner on Nemotron Super at depth 0, and three sub-agents on Nemotron Lightning at depth 1 (`safety`, `repair`, `acceptance`), each going from spawned to done.
- The merged work order card, with its steps, its forbid list and its acceptance list.

Will says:

> The planner, Nemotron Super, spawns three sub-agents on Nemotron Lightning.
> One writes the lockout steps, one the repair, and one the proof we will demand at the end.
> They can't spawn agents of their own; the depth cap is enforced in code.
> That acceptance list is what done means, written before anyone touches the plant.

### 1:20 Dispatch and the contractor's shortcut (20 s)

On screen (events `demo.email`, `demo.contractor`):
- The outgoing work-order email (`mail/01-out-wo-1-r1.eml`).
- The contractor card showing `dispatch_tech INV-3 inspect` and the shortcut flag.
- The contractor's reply (`mail/02-in-wo-1-r1.eml`): inspected the cabinet, power-cycled the display, done.

Will says:

> The work order goes out as an email to a contractor.
> Our contractor is simulated, and it does what a rushed contractor does on a first visit: it looks, power-cycles the display, and replies done.

### 1:40 Verify, fail, email back, verify again (35 s)

On screen (events `demo.check`, `demo.jev`, `demo.verdict`, `demo.diffusion`, then round 2):
- The checks list, round 1: `scada_fresh_and_running` red, `display_lit@inv3_cabinet` red, `jev_fix_verified` red (names from the stub rehearsal at 14:12; the live run's names come from its planner).
- The JEV card: the interpreted JSON (camera stations, distances, which ones see INV-3, sensor age) and its verdict, [fill: `not_verified` with `p_verified` 0.04 in e2e-live-1].
- The email back to the contractor (`mail/03-out-wo-1-r2.eml`), with the failed checks and the expected-state image (`img/expected-r1.png`).
- Round 2: the contractor opens breaker `52-I3` (lockout), reseats the comms card, every check turns green, and the final banner reads VERIFIED.

Will says:

> Now a model from a different family, gpt-oss on Crusoe, checks the plant, not the email.
> The data is still stale and the display is still dark.
> JEV takes the camera positions from the 3D twin and the sensor data, and returns a typed verdict: [fill: not verified, in e2e-live-1].
> So the contractor gets an email back with what failed and a picture of what done looks like.
> Round two, they lock out the breaker, reseat the comms card, and this time the plant proves it.

Say "a picture of what done looks like" either way; say "generated" only if `demo.diffusion.backend` is `diffusion` (see the box below).

### 2:15 Memory (20 s)

On screen (event `demo.memory`):
- Tab 1: the lesson card being written (symptoms, the fix, the shortcut that failed, the forbid and acceptance items).
- Tab 3: run 2 (`s06b_stale_scada_inv2`, the same failure on INV-2) recalls that lesson; its work order shows `lessons_applied` and forbids an inspect-only visit or a power-cycle; the contractor gets it right first time; VERIFIED in one round.

Will says:

> Only a verified fix becomes memory, and the lesson includes the shortcut that failed.
> Here's the same failure on a different inverter: the work order now says no inspect-only visit, reseat the card.
> The contractor gets it right first time, one round instead of two.

If golden run 2 had to fall back to `s06` (see Golden runs), say "the same failure again" instead of "on a different inverter".

### 2:35 The number (15 s)

On screen: the final banner (`demo.final`) with rounds, cost and wall time; `results.md` in a second window if there is time.

Will says:

> [fill rounds, 2 in e2e-live-1] rounds, about [fill wall_s, 68 in e2e-live-1] seconds, and under half a cent of model calls, all on Crusoe.
> For comparison, on nine held-out faults our plain agent baseline fixed none of the six fixable ones.
> Told to check its own work, it fixed two, and still called a dirty, overgrown array "nothing wrong".

Sources: `results.md` at `9bea5b1` in Will's bench worktree, held-out split (9 scenarios) at `1d2f85d`, runs 12:33 to 13:35 PDT.
If the 6b507a6 re-run lands before the freeze and its numbers differ, use the new ones, or drop the last two lines.

### 2:50 Close (10 s)

Will says:

> Done means done: proven against the plant, by a different model, and remembered only when it's true.
> We're GridTwin.

## What is real and what is simulated

| Real (say it plainly) | Simulated (say so if asked, never hide it) | Depends on what ran (check the run before saying it) |
|---|---|---|
| Every model call is live on Crusoe Managed Inference: Nemotron Super (planner), Nemotron Lightning (sub-agents), Nemotron Omni (camera reading), gpt-oss-120b (verifier and the email back), Nemotron Nano (safety gate and the JEV fallback). | The plant: a simulator with a hidden ground truth (`gridtwin/sim/`). | JEV: TypeSafe Jev only if `demo.jev.output.backend` is `jev`; our `.env` has no `JEV_API_KEY` today, so expect `local`, which is the same typed questions answered by Nemotron Nano on Crusoe (`local` in `e2e-live-1`). |
| The sim's ground-truth judge: the hidden state is written to `truth.jsonl`, which only the judge reads, never an agent; `result.json` carries its verdict on every demo run. | The contractor: a deterministic actor with one documented rule (`gridtwin/demo/contractors.py`), take the cheap shortcut (an inspect-only visit that power-cycles the display) on a first visit unless the work order forbids it, and always follow explicit `forbid` and `steps`; all its work goes through the sim's tools and the safety gate. | The expected-state image: generated only if `demo.diffusion.backend` is `diffusion`; `photo_bank` means a stored photo of the healthy unit. |
| The verification reads the plant through the same tools as everyone else (telemetry age, a camera view), never the contractor's reply. | Email delivery: work orders and replies are `.eml` files under `runs/<id>/mail/`, never sent. | The twin's coordinates: real registered stations only if the scene's `registration.json` comes from `register_viewpoints` or `place_pins`; `_dev` is a synthetic scene. |
| The depth cap on sub-agents is code, not a prompt. | The camera photos: INV-2 and INV-3 photos are AI-generated (gpt-image, listed in `assets/photos/LICENSES.csv`), not photos of a real plant. | Live run or replay: say which one is on screen. |
| The memory write and recall: a lesson file in `runs/demo-kb/lessons/` that run 2 finds with the same BM25 search as the full loop. | The 3D scene: `_dev` is a synthetic scene we generate; if the real scan is used, it is a stand-in such as "Tugboat Bat engine room, Trieste" by tosolini, CC BY 4.0, with our pins on it, never a solar plant. | `--stub` runs (no model calls) are rehearsals only and never shown as a run. |

## What not to claim

- Don't say an email was sent; they are `.eml` files on disk.
- Don't say the contractor "decided" to cut a corner; its shortcut is a documented rule, and the point is that the verifier doesn't know the rule and catches it from the plant.
- Don't say TypeSafe Jev made the call unless the JEV card shows `backend: jev`.
- Don't say the expected-state image is generated unless the diffusion card shows `backend: diffusion`.
- Don't say the agents recurse to any depth; it is exactly one level of sub-agents, by design.
- Don't say the splat is re-rendered or shows the fault; it is a static stage with pins, and photos carry the visual state.
- Don't say the scan is a solar plant, or that the photos are from a real site.
- Don't say the reseat-the-comms-card procedure comes from an OEM manual; no source in `kb/refs/` says that (Jonathan, 12:47).
- Don't put the demo pipeline next to the bench table as if it had been benchmarked; the full loop and this pipeline have no held-out numbers yet.
- Don't say the baselines can never fix stale SCADA; `self_verify` fixed `s06b` in the 9-scenario run.
- Don't quote the 7-scenario table at `be126ac`; it predates three fixes (see `results.md`).
- Don't give any cost, time or round count that isn't on the golden run's `demo.final` line.

## Six judge questions

1. **"Isn't the contractor just scripted to fail?"**
   Yes, on purpose, and the rule is written down in `gridtwin/demo/contractors.py`: an inspect-only visit on the first call unless the work order forbids it.
   What isn't scripted is the catch: the verifier never sees the rule or the reply text, only the plant, and the next work order forbids the shortcut because a verified lesson said so.
2. **"Where's the recursion? This looks like a fixed pipeline."**
   The planner writes each sub-agent's task at run time and merges their output; the cap of one level is a deliberate limit on cost and on how far a mistake can spread.
   The loop that matters is verify, email back, verify again: it repeats until the plant proves the fix, at most three rounds, then it escalates to a human.
3. **"What does the Gaussian splat actually add?"**
   It is the spatial map: the JEV interpreter turns registered camera positions and the asset's position into distances and angles, so the verdict knows which views really see INV-3 and how many independent views it has.
   It is a static stage today; the photos carry the state, and a real plant would refresh the scan from a drone pass.
4. **"Why should I trust the verifier? Who checks it?"**
   It is a different model family from every worker (gpt-oss, not Nemotron), and it reads the plant, not anyone's narrative.
   The sim's judge checks it against hidden ground truth: `scripted_trap` runs end as false dones 94.1% of the time on 17 episodes and `scripted_correct` succeeds 100%, so the judge itself is tested (team log, 12:09).
   Omni's camera check is calibrated: 12 of 12 correct on the INV-2 and INV-3 photos, three runs each at 0.95 or higher confidence (team log, 12:53).
5. **"What does a run cost, and how long does it take?"**
   This run: [fill usd, $0.004 in e2e-live-1] and [fill wall_s, 68.3 s in e2e-live-1], from the run's own `demo.final` line, which totals every model call the run made.
   Agents only wake on an anomaly, so cost grows with incidents, not with the number of inverters.
6. **"What stops memory from learning the wrong fix?"**
   Only a fix the verifier proved against the plant is written, and the lesson records the shortcut that failed, so the next work order forbids it.
   A run that ends ESCALATED writes nothing.

## Plan B ladder

Work down a ladder only as far as needed; every rung keeps the story true.
The replay URL for every rung that needs one: `http://localhost:8010/demo.html?run=demo-20261017-golden1&replay=1` (and `golden2` for the memory run).

### Crusoe slow or rate-limited

Signal: a stage sits still for more than 20 s, or the server log shows 429 or timeouts.

1. Keep talking; the stage bar shows what it is waiting on, and "this is live on Crusoe" is true.
2. Before going on, re-warm: `.venv/bin/python -m gridtwin.run --warm`, and check every role answers: `.venv/bin/python -m scripts.smoke_test`.
3. Switch to tab 2 (golden run 1) at the same stage and say "this is the run we recorded at [fill time]; same code, same models".
4. If Crusoe is down before the demo starts, run the whole demo from tabs 2 and 3 and say up front that it is a recorded run.
   Never use `--stub` on stage: it makes no model calls, so it is a rehearsal, not the product.

### Jev down

Signal: the JEV card shows `backend: local`, or the verifier logs a Jev error.

1. Nothing to do: `jev.decide` never raises and falls back to the same typed questions on a Crusoe model.
2. Change one line: say "the typed decision runs on Nemotron on Crusoe" instead of naming TypeSafe Jev.
3. If the JEV card is missing altogether, skip the JEV sentence; the telemetry and camera checks still fail the shortcut on their own.

### Diffusion missing

Signal: the diffusion card shows `backend: photo_bank`, or no image.

1. Nothing to do: `expected_state_image` falls back to the healthy photo in `assets/photos/INV-3/healthy/`.
2. Say "a reference photo of the healthy unit" instead of "a generated image".
3. If no image appears at all, open the email back in tab 5 and show its text; the failed checks are the point.

### Page broken (demo.html)

Signal: blank page, a stuck spinner, or the console shows an error.

1. Hard reload (Cmd+Shift+R); the run keeps going on the server.
2. Restart the server: stop it, then `.venv/bin/python -m gridtwin.demo.serve --port 8010`, and reopen `demo.html?run=<id>`; the page rebuilds from `events.jsonl`.
3. Use the existing viewers on the same run and the same port, which read the same log: `http://localhost:8010/?run=<id>` (evidence view) and `http://localhost:8010/wall.html?run=<id>` (camera stations); they show the camera calls and the final status, not the `demo.*` cards `[confirm]`.
4. Show the artifacts directly: tab 5 (the `.eml` files in Mail), and the run's own stage trace in a terminal:
   `.venv/bin/python -m gridtwin.demo.run --scenario s06_stale_scada --run-id demo-s06-cli` prints one line per stage while it runs.
   For a finished run: `jq -c 'select(.type|startswith("demo.")) | {type, actor, data}' runs/<id>/events.jsonl | cut -c1-160`.
5. Last rung: the backup recording.

### Twin not loading

Signal: the twin tab stays on the splash, shows a black canvas, or prints a 404 for the scene.

1. Check the scene files are in this worktree: `ls assets/splats/`; if missing, copy them (pre-flight step 4) and reload.
2. If the real scan fails, go back to the synthetic scene: restart the server without `GRIDTWIN_TWIN_SCENE`, open `twin.html?scene=_dev&tour=1&run=<id>`, and say "a synthetic stand-in".
3. Use the 2D map with view cones: `http://localhost:8010/wall.html?run=<id>`.
4. Skip the twin; it carries no line of its own in the script, only the JEV sentence's "camera positions from the 3D twin", which stays true as long as the registration file loaded.

### The pipeline itself fails mid-run

Signal: `demo.final` never arrives, or the run ends ESCALATED on stage.

1. ESCALATED after three rounds is a true outcome; say "it wouldn't say done, so it called a human", then show tab 2 for the fix.
2. A crash: switch to tab 2 and say it is the recorded run.

### Laptop or venue failure

1. The golden runs are committed as `runs/demo-20261017-golden1/` and `runs/demo-20261017-golden2/`, so the replay works offline on any teammate's clone.
2. The backup recording (see `docs/DEMO.md`, Backup recording) lives on a second device.

## Golden runs

Record them as soon as `gridtwin/demo/run.py` lands, then again after the freeze if the code changed.

Golden run 1, `s06_stale_scada` with an empty memory, expected two rounds and VERIFIED:

```bash
.venv/bin/python -m gridtwin.demo.run --scenario s06_stale_scada --memory runs/demo-kb-golden --fresh-memory --run-id demo-20261017-golden1
```

Golden run 2, with the lesson golden run 1 wrote, expected one round and VERIFIED, on the same failure on a different inverter (the pipeline takes the asset from its diagnosis, not from a constant):

```bash
.venv/bin/python -m gridtwin.demo.run --scenario s06b_stale_scada_inv2 --memory runs/demo-kb-golden --run-id demo-20261017-golden2
```

If golden run 2 does not recall the lesson on `s06b`, re-run it on `s06_stale_scada` and change the 2:15 line to "the same failure again".

- The run id goes straight into the folder name, so each run lands in `runs/demo-20261017-golden1/` and `runs/demo-20261017-golden2/`, which match the one pattern git keeps (`.gitignore` has `!runs/demo-*/`), and no event path needs rewriting.
- Check each in tab 2 or 3 with `&replay=1` before committing: every stage card, every image and both emails load.
- Commit only those two folders (`git add runs/demo-20261017-golden1 runs/demo-20261017-golden2`); `runs/demo-kb-golden/`, the server's `demo-s06-*` runs and the `demo-fixture*` folders also match `demo-*` and stay out of commits.
- `truth.jsonl` stays in the folder if the run wrote one; the server refuses to serve it, and the judge needs it.
- Fill every `[fill]` in this file from the golden runs' `result.json` and events, and nothing else.

## Go/no-go at 15:15

At 15:15, read the last commits on `will/agent-build` and the test track's report (session "demo-test"), then decide each piece below and write one line in `docs/MEMORY.md` as `HH:MM Will (demo-pivot): ...`.

| Piece | Keep if | Fall back to | Cut if |
|---|---|---|---|
| Pipeline (`gridtwin/demo/run.py`) | a full live run reaches VERIFIED on Crusoe twice in a row | a golden-run replay, said out loud | no run has ever reached VERIFIED; then the demo is `docs/DEMO.md` on the existing loop |
| Sub-agents (depth 1) | `demo.agent` events show three sub-agents and the depth-cap test passes | no fallback | they fail or the test is red; drop the 0:55 stage's sub-agent lines |
| Contractor and email | the shortcut and the reply appear as `.eml` files | no fallback | it is the core of the story; without it, cut to `docs/DEMO.md` |
| Verify and redispatch | round 1 fails on the plant and round 2 passes | golden replay | no fallback; it is the claim |
| JEV | the JEV card shows a verdict | `backend: local` on Crusoe | the interpreter has no registration file; drop the JEV sentence |
| Diffusion | `backend: diffusion` gives a usable image | photo bank | never; the photo bank always works |
| Memory flywheel | golden run 2 is one round with `lessons_applied` | golden run 2 replay | no run 2 exists; drop the 2:15 stage and give its 20 s to the verify stage |
| demo.html | the page shows every stage live and in replay | the evidence view and wall | the page cannot show a run; then use the viewers in rung 3 |
| Twin | the scene loads with pins in this worktree | `_dev` scene or the wall map | it never loads; nothing in the script depends on it |

Decision: `[filled at 15:15]`.
