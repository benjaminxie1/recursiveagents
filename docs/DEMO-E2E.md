# GridTwin end-to-end demo: script, Plan B ladder, go/no-go

Owner: Will.
Written by the demo-pivot session on branch `will/agent-build`.
This is the script for the end-to-end flow in `gridtwin/demo/CONTRACT.md` (scenario `s06_stale_scada`).
It supersedes the stage plan in `docs/DEMO.md` for the live demo; the hook and the backup-recording rules still come from there.
Judge answers that are not about this flow stay in `docs/JUDGE_QA.md`.

Status: final for the 15:30 freeze, 15:20, against `efd063d` on `will/agent-build`; both golden runs are recorded and every stage line below matches them.
Every `[fill]` is a value that only the golden run can give; nothing in brackets gets said on stage until it is filled from a real run.
Every `[confirm]` is a page detail to check against the built page at go/no-go.

Update 14:26: this is the team's one pitch script.
It folds in the useful parts of `docs/PITCH.md` (Will's main checkout, untracked, written about 14:15 for the same idea): the F3 numbers, the benchmark lines, the claims to avoid, two stage tactics and four judge questions.
PITCH.md is built around F3 on INV-1; what is built is `s06_stale_scada` on INV-3, so this file keeps s06, and wherever the two disagree about paths, names or modules, this file follows the code.

Update 15:20: golden runs recorded at `efd063d` with the diagnosis and verification teams and real Jev; the history of this file is in git.

Facts from the code at `efd063d` that the steps below rely on:
- `run.py` flags: `--scenario`, `--memory <dir>` (default `runs/e2e-kb`), `--fresh-memory` (empties that store's lessons first), `--run-id`, `--max-rounds` (default 3), `--stub` (deterministic stand-ins for rehearsal; not offline, because `jev.decide` still makes two gpt-oss calls per run, per the test track at 14:50).
- A run writes `runs/<run_id>/` with `events.jsonl`, `mail/NN-out|in-<wo>-r<round>.eml`, `img/expected-r<round>.png`, `frames/` and `result.json`.
- The demo server's Run button names runs `e2e-s06-<YYYYmmdd-HHMMSS>`, outside the `runs/demo-*` names git keeps; `memory: false` gives the run an empty store of its own.
- The planner's three sub-agents each run on their own Nemotron model: `safety` on Nano, `repair` on Lightning, `acceptance` on Super (`SUB_MODEL_ROLE` in `gridtwin/demo/brains.py`).
- The twin scene is `GRIDTWIN_TWIN_SCENE` (default `_dev`); the JEV step reads `assets/splats/<scene>.registration.json` and falls back to the same file in `~/claude/recursiveagents`.
- `gridtwin.demo.serve` serves everything `gridtwin.serve` does, so the evidence view (`/`), the wall (`/wall.html`) and the twin (`/twin.html`) are on the same port as `demo.html`.
- `JEV_API_KEY` is set, so JEV asks TypeSafe Jev (`backend: jev`); its fallback (`backend: local`) asks the `verifier` role, gpt-oss-120b on Crusoe (`LOCAL_ROLE` in `gridtwin/demo/jev.py`).
- `demo.final.usd` is the process-wide total from `gridtwin/llm.py`, so it covers every model call in the run, the Omni camera calls included.

## Measured: the golden runs (real runs only)

Recorded live on Crusoe at 15:09 and 15:10, code at `efd063d`, with a fresh lesson store `runs/e2e-kb-golden`.
These are the numbers that go on stage; both runs pass the contract validator (`tests/demo/contract.py`) with no errors.

Golden run 1, `runs/demo-20261017-golden1/` (`s06_stale_scada`, empty memory):
- VERIFIED in 2 rounds; the judge scores it success, no false done, 0 safety violations.
- Wall time 42.8 s; model cost $0.004 for the whole run.
- Stage starts, seconds after the goal: diagnose 0, plan 12, dispatch and contractor 23, verify round 1 at 24, redispatch 32, contractor and verify round 2 at 35, memory 41, final 41.
- INV-3 at diagnosis: `data_age_min` 91, frozen since 09:29, while INV-1, INV-2 and INV-4 read 0.0 minutes; Omni on `inv3_cabinet`: "The inverter's display is not lit and shows no readings; it appears as a dark, unilluminated screen."
- Diagnosis team: lead on Nemotron Super; `sensors` and `vision` on Nemotron Lightning; `spatial` is a rule in code.
- Plan team: planner on Nemotron Super; `safety` on Nemotron Nano, `repair` on Nemotron Lightning, `acceptance` on Nemotron Super.
- Verify team, each round: lead on gpt-oss-120b; `telemetry_judge`, `visual_judge` and `jev_judge` are rules in code; `meta_judge` on gpt-oss-120b.
- Round 1: the contractor took the shortcut (`dispatch_tech INV-3 inspect`); `scada_fresh_and_running`, `display_lit@inv3_cabinet` and `jev_fix_verified` failed; TypeSafe Jev (`backend: jev`) said `not_verified`, `p_verified` 0.0.
- Round 2: open `52-I3`, `reseat_comms_card`, close; every check passed on `inv3_display`; Jev said `verified`, `p_verified` 0.78.
- Expected-state image: `photo_bank` (the stored healthy INV-3 photo with the banner).

Golden run 2, `runs/demo-20261017-golden2/` (`s06b_stale_scada_inv2`, the same failure on INV-2, memory from run 1):
- VERIFIED in 1 round; judge success, no false done, 0 safety violations.
- Wall time 37.9 s; model cost $0.0043.
- INV-2 at diagnosis: `data_age_min` 76; the diagnosis team adds a `history` sub-agent on Nemotron Lightning because memory has a lesson.
- The work order lists `L-demo-20261017-golden1` in `lessons_applied` and forbids "an inspect-only visit or a power-cycle"; the contractor skipped the shortcut and every check passed in round 1; Jev said `verified`, `p_verified` 0.82.
- Memory stats line: "demo-20261017-golden1: 2 rounds -> demo-20261017-golden2: 1 round".

Live reliability at current code: 4 of 4 live runs since `efd063d` reached VERIFIED (`e2e-v2-1`, `e2e-v2-2` and both golden runs).
Six live runs between 14:28 and 14:46 ended ESCALATED, five of them on false vetoes by the earlier meta judge, which `b4e9c6f` limited to a failed judge or a contradicting plant reading.

Earlier interim runs (`e2e-live-1` at 14:13: 2 rounds, 68.3 s, $0.004; `e2e-live-2` at 14:18: 1 round, 30.3 s, $0.0037) predate the teams and real Jev; don't quote them.

A live run finishes about 1:05 into the script (0:20 plus 43 s), before Will reaches the plan stage's end; he narrates cards that are already on the page, which is what the script assumes.

## The story in one breath

A plant fault is not fixed when someone says it is fixed; it is fixed when the plant proves it.
GridTwin is an agent team that diagnoses a fault from sensors and cameras, writes the work order with sub-agents, sends it to a contractor, and then checks the plant itself instead of trusting the contractor's "done".
When the check fails, it emails the contractor back with what failed and a picture of what done looks like, and only a verified fix becomes memory, so the next incident is right first time.

## Before going on stage (T minus 15 minutes)

Run everything from `~/claude/recursiveagents-build`.
This worktree has no `.venv` of its own (at 14:12), so link the main checkout's once: `ln -s ~/claude/recursiveagents/.venv .venv` (`.venv/` is gitignored).

1. Pull and check the branch: `git -C ~/claude/recursiveagents-build log --oneline -5`.
2. Check Crusoe and every role's model: `.venv/bin/python -m scripts.smoke_test`.
3. Warm the endpoints (one call per role, so no first call on stage is cold): `.venv/bin/python -m gridtwin.run --warm`.
4. Put the twin scene into this worktree, because splats are gitignored and only the main checkout has them:
   `cp ~/claude/recursiveagents/assets/splats/_dev.{ply,registration.json} assets/splats/`, or regenerate it here with `.venv/bin/python scripts/twin_dev_scene.py`.
   For the real scan instead, copy its files the same way and start the server with `GRIDTWIN_TWIN_SCENE=<scene>`; the scene name must resolve in `twin.html` (a bare name loads `<scene>.ply`) and in the pipeline (`<scene>.registration.json`) `[confirm scene name with the scene-prep session]`.
5. Empty the stage memory so the live run starts with no lessons and takes the shortcut: `rm -rf runs/e2e-kb/lessons` (or tick the page's fresh-memory option if it has one `[confirm]`).
6. Start the demo server: `.venv/bin/python -m gridtwin.demo.serve --port 8010`.
7. Open these tabs, left to right, and leave each one where it says:
   - Tab 1, live: `http://localhost:8010/demo.html` (idle, Run button visible).
   - Tab 2, golden run 1 (no memory, two rounds): `http://localhost:8010/demo.html?run=demo-20261017-golden1&replay=1`, paused at the start.
   - Tab 3, golden run 2 (with memory, one round): `http://localhost:8010/demo.html?run=demo-20261017-golden2&replay=1`, paused at the start.
   - Tab 4, twin: `http://localhost:8010/twin.html?scene=_dev&tour=1&run=demo-20261017-golden1&replay=1`.
   - Tab 5, Finder on `runs/demo-20261017-golden1/mail/`, so the `.eml` files open in Mail with a double click.
8. Phone hotspot on and joined, in case venue Wi-Fi drops; every page, photo and library is local, only the model calls need the internet.
   Stop every other live run and bench that uses this Crusoe key: a run makes about 11 model calls, and two runs at once hit rate limits on Nemotron Lightning and Super (test track, 14:50).
9. Diffusion, only if Will has said yes to it: the sd-turbo weights must already be in the local Hugging Face cache (`.venv/bin/python -m gridtwin.demo.diffusion --download`, once), because a demo run never downloads them.
   Run one rehearsal on the venue Wi-Fi so the first generation is not on stage, and note how long `demo.diffusion` took; if it is slow, restart the server with `GRIDTWIN_NO_DIFFUSION=1` and use the photo bank.

## Three-minute script

The live run starts at 0:20 in tab 1.
If it is still behind the script at 1:40, switch to tab 2 at the same stage and say "this is the run we recorded at 15:09; same code, same models".
If it falls behind earlier, narrate the next stage from tab 2 and switch back to tab 1 for the verdict, so the verdict judges see is the live one.

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

On screen (events `demo.sensor`, `demo.camera`, `demo.diagnosis`, and `demo.agent` with `team: diagnose`):
- The SCADA panel: INV-3 reads normal numbers but is marked stale, 91 minutes, "frozen since 09:29"; the other three inverters read 0.0 minutes.
- The camera card for `inv3_cabinet`: Nemotron Omni says the display is dark.
- The diagnosis team: a lead on Nemotron Super; `sensors` and `vision` on Nemotron Lightning; `spatial` as a rule in code; then the diagnosis card, "Comms failure with stale data", 95% confident.
- Don't point at the second camera card (`inv1_cabinet`, the healthy peer): its photo is an unpowered unit and Omni calls it dark too `[drop this bullet if the peer photo is swapped and the golden runs re-recorded]`.

Will says:

> Every number on inverter 3 looks perfect.
> They're also 91 minutes old, because its comms card died and the feed froze on its last good reading.
> A diagnosis lead on Nemotron Super spawns three sub-agents: one reads the sensors, one reads the camera through Nemotron Omni, one maps the twin's cameras to the equipment.
> They agree: the data is frozen and the display is dark.

### 0:55 Plan with sub-agents (25 s)

On screen (events `demo.agent`, `demo.workorder`):
- The agent tree: the planner on Nemotron Super at depth 0, and three sub-agents at depth 1, `safety` on Nemotron Nano, `repair` on Nemotron Lightning and `acceptance` on Nemotron Super, each going from spawned to done.
- The merged work order card, with its steps, its forbid list and its acceptance list.

Will says:

> The planner, Nemotron Super, spawns three sub-agents, each on its own Nemotron model.
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

On screen (events `demo.check`, `demo.jev`, `demo.verdict`, `demo.diffusion`, and `demo.agent` with `team: verify`, then round 2):
- The round 1 verify team: a lead on gpt-oss-120b, three judges that are rules in code (`telemetry_judge`, `visual_judge`, `jev_judge`) and a `meta_judge` on gpt-oss-120b.
- The round 1 checks: `scada_fresh_and_running`, `display_lit@inv3_cabinet` and `jev_fix_verified` red.
- The JEV card: the interpreted JSON (camera stations, distances, which ones see INV-3, sensor age) and TypeSafe Jev's answer, `not_verified`, `p_verified` 0.0, `backend: jev`.
- The email back (`mail/03-out-wo-1-r2.eml`) with the failed checks and the expected-state image (`img/expected-r1.png`, the stored healthy photo under its "EXPECTED STATE" banner).
- Round 2: the contractor opens `52-I3`, reseats the comms card and closes the breaker; every check turns green on `inv3_display`; Jev says `verified` at 0.78; the final banner reads VERIFIED.

Will says:

> Now verification reads the plant, never the email.
> Code checks the SCADA feed: still stale.
> Nemotron Omni reads a camera the contractor didn't use: still dark.
> JEV, TypeSafe's decision model, gets the twin's camera positions and the sensor data as one JSON record, and says not verified.
> And a judge from a different model family, gpt-oss, can veto the fix, but it can never pass one the checks failed.
> So the contractor gets an email back with what failed and a picture of what done looks like.
> Round two, they lock out the breaker, reseat the comms card, and this time the plant proves it.

If the live run's JEV card shows `backend: local` (Jev unreachable), say "the typed decision runs on gpt-oss on Crusoe" instead of naming TypeSafe.
The picture is the stored healthy photo (`photo_bank`) in both golden runs, so never say "generated".

### 2:15 Memory (20 s)

On screen (event `demo.memory`):
- Tab 1: the lesson card being written (symptoms, the fix, the shortcut that failed, the forbid and acceptance items).
- Tab 3: run 2 (`s06b_stale_scada_inv2`, the same failure on INV-2) recalls that lesson; its work order shows `lessons_applied` and forbids an inspect-only visit or a power-cycle; the contractor gets it right first time; VERIFIED in one round.

Will says:

> Only a verified fix becomes memory, and the lesson includes the shortcut that failed.
> Here's the same failure on a different inverter: the work order now says no inspect-only visit, reseat the card.
> The contractor gets it right first time, one round instead of two.

Golden run 2 is `s06b_stale_scada_inv2` on INV-2, so "a different inverter" is true; its memory stats line reads "2 rounds -> 1 round".

### 2:35 The number (18 s)

On screen: the final banner (`demo.final`) with rounds, cost and wall time; `results.md` in a second window.

Will says:

> Two rounds, 43 seconds, and under half a cent of model calls, with every language model on Crusoe.
> We score agents against hidden ground truth with a deterministic judge, not a model.
> A scripted agent that takes the obvious fix is falsely done 94% of the time.
> A plain Nemotron agent mostly gives up: on nine held-out faults it fixed none of the six fixable ones.

Jev answered from TypeSafe's API in both golden runs, so the line says "every language model on Crusoe", never "all on Crusoe"; if the live run on stage differs, read its own banner instead.
Say "a scripted agent that takes the obvious fix", never just "an agent", for the 94% line.
If there are seconds left, add: "Told to check its own work, it fixed two, and still called a dirty, overgrown array nothing wrong."

Sources:
- 94%: `scripted_trap` on all 17 scenarios, 5.9% success and 94.1% false done, 100% false done on the held-out trap types; `scripted_correct` 100% success and 0% false done (team log, Jonathan, 12:09; these arms make no model calls).
- Baseline: `results.md` at `9bea5b1` in Will's bench worktree, held-out split (9 scenarios) at `1d2f85d`, runs 12:33 to 13:35 PDT: `baseline` 11.1% success, 11.1% false done, 0% claimed done; `self_verify` 22.2% success, 11.1% false done.
- If the `6b507a6` re-run lands before the freeze and its numbers differ, use the new ones, or drop the baseline line.
- There is no GridTwin held-out number: the full-loop bench was reported at 14:19 as likely to miss the freeze (Jonathan's session, relayed by the PITCH.md session), and this pipeline has never been benchmarked.
  If a full-loop row lands in `results.md` before 15:30, add one line from it; otherwise say nothing about a GridTwin rate.

### 2:53 Close (7 s)

Will says:

> Done means done: proven against the plant, not taken from an email, and remembered only when it's true.
> We're GridTwin.

## What is real and what is simulated

| Real (say it plainly) | Simulated (say so if asked, never hide it) | Depends on what ran (check the run before saying it) |
|---|---|---|
| Every model call is live on Crusoe Managed Inference: Nemotron Super (planner and the `acceptance` sub-agent), Nemotron Lightning (the `repair` sub-agent), Nemotron Nano (the `safety` sub-agent and the safety gate), Nemotron Omni (camera reading), gpt-oss-120b (the verify lead, the meta judge, the email back and the JEV fallback). | The plant: a simulator with a hidden ground truth (`gridtwin/sim/`). | JEV: TypeSafe Jev, a hosted API, answered in both golden runs (`backend: jev`); if it is unreachable, `local` means the same typed questions answered by gpt-oss-120b on Crusoe. |
| The sim's ground-truth judge: the hidden state is written to `truth.jsonl`, which only the judge reads, never an agent; `result.json` carries its verdict on every demo run. | The contractor: a deterministic actor with one documented rule (`gridtwin/demo/contractors.py`), take the cheap shortcut (an inspect-only visit that power-cycles the display) on a first visit unless the work order forbids it, and always follow explicit `forbid` and `steps`; all its work goes through the sim's tools and the safety gate. | The expected-state image: `diffusion` means sd-turbo img2img on the contractor's own photo, run on this laptop's GPU, not on Crusoe; `photo_bank` means a stored photo of the healthy unit; both carry an "EXPECTED STATE" banner, and only the email back uses it. |
| The verification reads the plant through the same tools as everyone else (telemetry age, a camera view), never the contractor's reply. | Email delivery: work orders and replies are `.eml` files under `runs/<id>/mail/`, never sent. | The twin's coordinates: real registered stations only if the scene's `registration.json` comes from `register_viewpoints` or `place_pins`; `_dev` is a synthetic scene. |
| The depth cap on sub-agents is code, not a prompt. | The camera photos: INV-2 and INV-3 photos are AI-generated (gpt-image, listed in `assets/photos/LICENSES.csv`), not photos of a real plant. | Live run or replay: say which one is on screen. |
| The memory write and recall: a lesson file in `runs/e2e-kb/lessons/` that run 2 finds with the same BM25 search as the full loop. | The 3D scene: `_dev` is a synthetic scene we generate; if the real scan is used, it is a stand-in such as "Tugboat Bat engine room, Trieste" by tosolini, CC BY 4.0, with our pins on it, never a solar plant. | `--stub` runs (no model calls) are rehearsals only and never shown as a run. |

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
- Don't say "every model runs on Crusoe" when Jev or diffusion ran; say "every language model runs on Crusoe".
- Don't call the expected-state image evidence; it is a reference for the technician, it carries an "EXPECTED STATE" banner, and no check reads it.
- Don't say the agents look at the splat; the JEV interpreter reads its geometry (registered camera stations and asset positions), never its pixels.
- Don't say "prefix caching"; every bench run so far, and both live demo runs, report 0 cached tokens.
- Don't say gpt-oss checks the plant on its own; the plant checks are rules in code over SCADA, Omni's camera reading and Jev, and gpt-oss leads the team and can only veto.
- Don't say the email back made the contractor do the real fix; the simulated contractor does the real fix on any second visit, so only the first work order's forbid list changes the outcome (test track, finding 10).
- Don't repeat the page's "Gaussian splat of the plant" label; the twin scene is `_dev`, a synthetic stand-in, so say "a synthetic stand-in scene" if asked.
- Don't pick a run from the page's run list on stage; it also lists stub and scratch runs, so use the tab URLs.
- Don't use the "$10 billion lost to broken equipment" figure; it has no source in `kb/refs/`.
- Don't promise a GridTwin benchmark rate; only a row in `results.md` can be quoted, and none exists yet.

## Judge questions (the six likely ones first)

1. **"Isn't the contractor just scripted to fail?"**
   Yes, on purpose, and the rule is written down in `gridtwin/demo/contractors.py`: an inspect-only visit on the first call unless the work order forbids it.
   What isn't scripted is the catch: the verifier never sees the rule or the reply text, only the plant, and the next work order forbids the shortcut because a verified lesson said so.
2. **"Where's the recursion? This looks like a fixed pipeline."**
   The planner writes each sub-agent's task at run time and merges their output; the cap of one level is a deliberate limit on cost and on how far a mistake can spread.
   The loop that matters is verify, email back, verify again: it repeats until the plant proves the fix, at most three rounds, then it escalates to a human.
3. **"What does the Gaussian splat actually add?"**
   It is the spatial map: the JEV interpreter turns registered camera positions and the asset's position into distances and angles, so the verdict knows which views really see INV-3 and how many independent views it has.
   It is a static stage today; the photos carry the state, and a real plant would refresh the scan from a drone pass.
4. **"Why should I trust the verification? Who checks it?"**
   It never reads anyone's narrative, only the plant: SCADA age and status checked in code, Nemotron Omni reading a camera the contractor's photo did not come from, and JEV's typed verdict, and all three must pass.
   The verification team's meta judge runs on gpt-oss, a different model family from the Nemotron planner, and can veto a pass but can never grant one.
   The sim's judge checks the whole run against hidden ground truth: `scripted_trap` runs end as false dones 94.1% of the time on 17 episodes and `scripted_correct` succeeds 100%, so the judge itself is tested (team log, 12:09).
   Omni's camera check is calibrated: 12 of 12 correct on the INV-2 and INV-3 photos, three runs each at 0.95 or higher confidence (team log, 12:53).
5. **"What does a run cost, and how long does it take?"**
   Golden run 1: $0.004 and 42.8 s for two rounds; golden run 2: $0.0043 and 37.9 s for one round; each is the run's own `demo.final` total of every model call it made.
   Agents only wake on an anomaly, so cost grows with incidents, not with the number of inverters.
6. **"What stops memory from learning the wrong fix?"**
   Only a fix the verifier proved against the plant is written, and the lesson records the shortcut that failed, so the next work order forbids it.
   A run that ends ESCALATED writes nothing.

### Four more, if there is time (adapted from `docs/PITCH.md`)

7. **"Why JEV when you already have checks?"**
   They fail differently: the telemetry and camera checks each read one signal, and JEV scores the combined typed record (sensor age, visual claims, which stations really see the asset) as a probability.
   JEV is one of the checks that must all pass, so it can block a fix but can never pass one on its own.
8. **"Why build an interpreter?"**
   JEV answers typed questions over a JSON state, so `interpret` in `gridtwin/demo/jev.py` turns the splat registration's xyz, the SCADA feed and the camera claims into one compact record with units.
   It is plain code with no model, and every field traces back to a sensor reading or a registered camera.
9. **"Why a diffusion model if it can't be evidence?"**
   It is for the technician: it shows what done looks like, made from their own photo.
   It carries an "EXPECTED STATE" banner, goes only into the email back, and no check ever reads it; if it did not run, the email carries the stored healthy photo instead.
10. **"Does this work for anything but one fault?"**
    The simulator has 17 scenarios with hidden ground truth, 9 of them held out, and the scripted and baseline numbers above come from them.
    The demo pipeline is wired for the stale-SCADA failure on any inverter today; the next case is the fan relapse below, where the obvious fix is a fake fix.

### Second case, if a judge asks: F3 fan relapse on INV-1

Not runnable through the demo pipeline today; describe it, never offer it live.
Every number is from `gridtwin/sim/plant.yaml` and `gridtwin/sim/plant.py`, checked at 14:26.

- Each inverter is rated 1,250 kWac, in a 5 MWac plant.
- The heatsink derate starts at 50 °C, latches at 55 °C with output capped at 60% (750 kW), trips at 60 °C, and releases at 45 °C.
- `fan_rpm` reads 2,400 when the fan works and the unit runs, and 0 otherwise.
- The real fix is `replace_fan_filter`: 60 minutes, $1,800, and it needs the inverter de-energized, so breaker `52-I1` opens first.
- The obvious fix is a fake fix: resetting the alarm clears the latch and output recovers while the heatsink is cool, then under load it climbs back into derate because the fan is still dead; the sim's tests show that relapse inside a 60-minute hold (Benjamin, 11:00).
- Sources in the KB: a Solis note that a failed fan "induces over-temperature protection" (`kb/refs/inverter-fan-failure-over-temperature-pr-4ff39167.json`) and DOE FEMP's O&M guidance on "reacceptance testing following a repair" (`kb/refs/pv-o-m-plan-reacceptance-testing-after-a-d0c6a75e.json`).

## Plan B ladder

Work down a ladder only as far as needed; every rung keeps the story true.
The replay URL for every rung that needs one: `http://localhost:8010/demo.html?run=demo-20261017-golden1&replay=1` (and `golden2` for the memory run).

### Crusoe slow or rate-limited

Signal: a stage sits still for more than 20 s, or the server log shows 429 or timeouts.

1. Keep talking; the stage bar shows what it is waiting on, and "this is live on Crusoe" is true.
2. Before going on, re-warm: `.venv/bin/python -m gridtwin.run --warm`, and check every role answers: `.venv/bin/python -m scripts.smoke_test`.
3. Switch to tab 2 (golden run 1) at the same stage and say "this is the run we recorded at 15:09; same code, same models".
4. If Crusoe is down before the demo starts, run the whole demo from tabs 2 and 3 and say up front that it is a recorded run.
   Never use `--stub` on stage: its stand-ins are a rehearsal, not the product, and it still calls Crusoe for JEV, so it does not help when Crusoe is down.

### Jev down

Signal: the JEV card shows `backend: local`, or the verifier logs a Jev error.

1. Nothing to do: `jev.decide` never raises and falls back to the same typed questions on a Crusoe model.
2. Change one line: say "the typed decision runs on Nemotron on Crusoe" instead of naming TypeSafe Jev.
3. If the JEV card is missing altogether, skip the JEV sentence; the telemetry and camera checks still fail the shortcut on their own.

### Diffusion missing

Signal: the diffusion card shows `backend: photo_bank`, or no image.

1. Nothing to do: `expected_state_image` falls back to the healthy photo in `assets/photos/INV-3/healthy/` when diffusion is missing, errors or runs past its timeout.
   To force the fallback for every run (diffusion slow on stage, or no yes from Will), restart the server with `GRIDTWIN_NO_DIFFUSION=1 .venv/bin/python -m gridtwin.demo.serve --port 8010`.
2. Say "a reference photo of the healthy unit" instead of "a generated image".
3. If no image appears at all, open the email back in tab 5 and show its text; the failed checks are the point.

### Page broken (demo.html)

Signal: blank page, a stuck spinner, or the console shows an error.

1. Hard reload (Cmd+Shift+R); the run keeps going on the server.
2. Restart the server: stop it, then `.venv/bin/python -m gridtwin.demo.serve --port 8010`, and reopen `demo.html?run=<id>`; the page rebuilds from `events.jsonl`.
3. Use the existing viewers on the same run and the same port, which read the same log: `http://localhost:8010/?run=<id>` (evidence view) and `http://localhost:8010/wall.html?run=<id>` (camera stations); they show the camera calls and the final status, not the `demo.*` cards `[confirm]`.
4. Show the artifacts directly: tab 5 (the `.eml` files in Mail), and the run's own stage trace in a terminal:
   `.venv/bin/python -m gridtwin.demo.run --scenario s06_stale_scada --run-id e2e-s06-cli` prints one line per stage while it runs.
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
.venv/bin/python -m gridtwin.demo.run --scenario s06_stale_scada --memory runs/e2e-kb-golden --fresh-memory --run-id demo-20261017-golden1
```

Golden run 2, with the lesson golden run 1 wrote, expected one round and VERIFIED, on the same failure on a different inverter (the pipeline takes the asset from its diagnosis, not from a constant):

```bash
.venv/bin/python -m gridtwin.demo.run --scenario s06b_stale_scada_inv2 --memory runs/e2e-kb-golden --run-id demo-20261017-golden2
```

If golden run 2 does not recall the lesson on `s06b`, re-run it on `s06_stale_scada` and change the 2:15 line to "the same failure again".

- The run id goes straight into the folder name, so each run lands in `runs/demo-20261017-golden1/` and `runs/demo-20261017-golden2/`, which match the one pattern git keeps (`.gitignore` has `!runs/demo-*/`), and no event path needs rewriting.
- Check each in tab 2 or 3 with `&replay=1` before committing: every stage card, every image and both emails load.
- Commit only those two folders (`git add runs/demo-20261017-golden1 runs/demo-20261017-golden2`); the `demo-fixture*` and `demo-kb-stub` test folders also match `demo-*` and stay out of commits (the lesson store `runs/e2e-kb-golden/` and the server's `e2e-*` runs do not match).
- `truth.jsonl` stays in the folder if the run wrote one; the server refuses to serve it, and the judge needs it.
- Fill every `[fill]` in this file from the golden runs' `result.json` and events, and nothing else.

## Go/no-go at 15:15

At 15:15, read the last commits on `will/agent-build` and the test track's report (session "demo-test"), then decide each piece below and write one line in `docs/MEMORY.md` as `HH:MM Will (demo-pivot): ...`.

| Piece | Keep if | Fall back to | Cut if |
|---|---|---|---|
| Pipeline (`gridtwin/demo/run.py`) | a full live run reaches VERIFIED on Crusoe twice in a row | a golden-run replay, said out loud | no run has ever reached VERIFIED; then the demo is `docs/DEMO.md` on the existing loop |
| Sub-agents (depth 1) | `demo.agent` events show three sub-agents and the depth-cap test passes | no fallback | they fail or the test is red; drop the 0:55 stage's sub-agent lines |
| Diagnosis and verification teams (integrating about 15:00) | the golden runs show `team: diagnose` and `team: verify` agents and the tests pass | the 082bdd0-style single diagnoser and verifier, which both live runs used | they are not in the golden runs; the script then says nothing about teams outside the plan stage |
| Contractor and email | the shortcut and the reply appear as `.eml` files | no fallback | it is the core of the story; without it, cut to `docs/DEMO.md` |
| Verify and redispatch | round 1 fails on the plant and round 2 passes | golden replay | no fallback; it is the claim |
| JEV | the JEV card shows a verdict | `backend: local` on Crusoe | the interpreter has no registration file; drop the JEV sentence |
| Diffusion | `backend: diffusion` gives a usable image | photo bank | never; the photo bank always works |
| Memory flywheel | golden run 2 is one round with `lessons_applied` | golden run 2 replay | no run 2 exists; drop the 2:15 stage and give its 20 s to the verify stage |
| demo.html | the page shows every stage live and in replay | the evidence view and wall | the page cannot show a run; then use the viewers in rung 3 |
| Twin | the scene loads with pins in this worktree | `_dev` scene or the wall map | it never loads; nothing in the script depends on it |

Decision at 15:20 (demo-pivot, for Will), from the commits through `c96918b`, the test track's 15:10 report (`test-track-part2.md` in the shared notes) and the two golden runs:

- Pipeline: keep, live on stage, with the golden replays as the fallback; 4 of 4 live runs at `efd063d` reached VERIFIED with judge success.
- Diagnosis team, plan sub-agents, verification team with the gpt-oss meta judge: keep; all three are in both golden runs, and the script names them.
- Contractor and `.eml` loop: keep.
- JEV: keep TypeSafe Jev (`backend: jev` in both golden runs); the fallback is gpt-oss on Crusoe, with the one-line change in the Plan B ladder.
- Diffusion: fall back to the photo bank; the sd-turbo weights are not in place, both golden runs used `photo_bank`, and the script never says "generated".
- Memory flywheel: keep; golden run 2 is a different inverter in one round.
- `demo.html`: keep; both replays serve every image and email (HTTP 200) and the test track saw no console errors; the frontend's uncommitted edits to `viewer/demo.js` and `viewer/demo.css` must be committed before 15:30.
- Twin: fall back to the `_dev` synthetic scene; no run uses the tugboat scan, and it is not committed.
- Healthy-peer camera (`inv1_cabinet`): open; agent-build was asked at 15:14 to swap the photo or drop the card, and until then Will doesn't point at it.
- Benchmark line: keep the scripted-trap and baseline lines; no GridTwin rate.
