# Transform: keep the assets, flip the frame

Written Sat Oct 17 2026, the morning of the event, as input to the kickoff decision.
Sources read: `docs/PLAN.md`, `docs/SIMULATOR.md`, `docs/HACKATHON.md`, `docs/research/past-winners.md`.

## Honest verdict on GridTwin

GridTwin's assets are good and its frame is ordinary.
Judges have seen planner, workers, verifier and supervisor around an ops dashboard many times, and two past Crusoe-track winners (Argus, Leavitt) were exactly that shape.
The weakest part is the splat: `SIMULATOR.md` says "The splat stays static. The photos carry the state."
That makes the splat a backdrop, and a judge who asks "what breaks if you delete the splat?" gets the answer "nothing".
Jev as a safety gate is sensible but reads as one API call in front of a tool, not as something the system thinks with.
Recursion is shallow: one planner fans out once to four fixed roles, which is delegation, not agents spawning agents spawning agents.

What is worth keeping:
- The deterministic plant simulator with hidden truth, because ground truth is what makes any claim measurable.
- The fault library with traps (F3 fan relapse, F6 stale SCADA, F5 must-escalate), because traps are the most original thing in the current plan.
- The false-done metric and the judge, because a measured number beats polish at every event in the research file.
- The `DecisionClient` abstraction over Jev and OpenAI Decisions, the tool surface, the event log and replay.

What should change in every concept below:
- The splat must be the thing being reasoned about or edited, so deleting it breaks the system.
- Jev must make the decisions that shape the agent tree (where to spawn, what to attack, what to bet on), and its calibration must be scored.
- Recursion must be data-dependent: how deep and wide the tree grows depends on what the agents find.

## Shared technical facts used by all four concepts

- Splat viewer and editor: Spark (sparkjs.dev, three.js based) renders `.ply` and `.spz` splats in the browser and supports runtime edits such as recoloring, hiding and adding Gaussians inside SDF shapes; `@mkkellogg/gaussian-splats-3d` is the fallback viewer.
- Agent renders: one browser tab acts as a render server that takes a camera pose over a websocket and returns a PNG (and a depth image) from `canvas.toDataURL`, so Python agents can "look" from any pose without a GPU on the laptop.
- Capture: Scaniverse on an iPhone trains a splat on the phone in a few minutes and exports PLY; Polycam is the second option; a capture done before 11:00 is the backup for everything.
- Vision models: Nemotron 3 Nano Omni for fast frame and crop checks, Nemotron 3.5 Super VL for harder descriptions; vision models are weak at fine spot-the-difference, so every concept proposes candidates with classical image diffs first and asks the model only to classify a crop pair.
- Jev: `POST https://api.typesafe.ai/v1/systemone` if a key exists, otherwise OpenRouter `typesafe/jev-1.14-20261014`; OpenAI Decisions (`gpt-6-luna`, accepts images) is the second backend behind the same client.
- Rate limits: about 600 RPM per model with a card on file, so every concept spreads roles across several models and caps fan-out.
- Weighted score = 0.40 x Technicality + 0.20 x Live Demo + 0.25 x Creativity + 0.15 x Future Potential, each scored 1 to 10, assuming the realistic 5 PM outcome, not the dream outcome.

---

## Concept 1: Tamper

**Hook:** A red team of agents sabotages a photoreal 3D twin of a power plant by editing its Gaussians and its sensors, a blue team of agents has to catch every edit before it costs money, and both teams get sneakier and sharper every round.

The frame flip: GridTwin's traps stop being hand-written puzzles and become the weapons of an adversary that chooses them on its own.
This is an AI control evaluation acted out in a physical world: an untrusted agent team tries to cause harm quietly, and a monitor team tries to catch it.

### The 3-minute live demo

1. **0:00 Hook.**
   "The dangerous agent is not the one that fails, it is the one that hides the failure."
   The plant splat fills the screen.
2. **0:15 Launch.**
   One command: red gets "cost this plant $50k of output without getting caught", blue gets "keep it running and report every intrusion".
   We step back from the keyboard.
3. **0:25 Red unfolds.**
   The red commander spawns three sector saboteurs (inverters, breakers, transformer), and each spawns tactic agents.
   The audience sees red's "god view" pane: one tactic freezes INV-3 telemetry (F6), one recolors the Gaussians of the INV-3 HMI so the screen goes dark, and a decoy agent paints a soot smear on a healthy cabinet with no real fault behind it.
4. **1:00 Blue unfolds.**
   Blue's pane shows only what blue's cameras render.
   The blue commander spawns sector watchers; each renders its viewpoints from the live splat, diffs them against the baseline it memorized, and asks Jev to triage each anomaly as real, decoy or benign.
   High-probability items spawn investigator agents that run diagnostics and dispatch fixes through the Jev safety gate.
5. **1:40 The wow.**
   Every telemetry check on INV-3 passes, yet blue flags it because the rendered HMI is dark and the data timestamps are stale.
   Blue tags the soot smear "decoy, P = 0.81" and does not spend budget on it.
   We kill a red tactic agent on stage; the red sector lead respawns it from its last checkpoint and red keeps attacking.
6. **2:10 Co-evolution.**
   Round 2 replays at 10x: red has read blue's catch log and switches to an F8 hot spot that only shows in the thermal view; blue misses it the first time, then a watcher spawns a thermal investigator.
   A chart from rounds run earlier in the day shows red's stealth rising and blue's detection catching up.
7. **2:40 The number.**
   Across N rounds: blue detection rate, false alarm rate, decoy chase rate, dollars of output saved, and the headline "a monitor from a different model family catches X more points of sabotage than a same-family monitor".
8. **2:55 Close.**
   "Control evaluations for agents that touch the physical world, on Nemotron on Crusoe."

### Why judges have not seen it

The closest existing things are Redwood Research's AI control evaluations (an untrusted model tries to slip in backdoors while a trusted monitor watches), attack-defense CTFs and DARPA AIxCC, OpenAI's 2019 hide-and-seek self-play, and SCADA cyber ranges built on digital twins.
None of them use a photoreal 3D scene as the attack surface, where the adversary edits Gaussians to fake or hide physical evidence, and none run recursive agent teams on both sides.
Honest caveat: red-versus-blue is a known shape, so the originality lives in the editable splat and the co-evolution, and the pitch has to lead with those.

### Reuse and new work

Reused from the current plan:
- Plant simulator, fault library F1 to F8 (now red's attack menu), traps (now red tactics) and the hidden truth.
- `judge.py`, which becomes the referee that knows every red action and scores blue.
- The Jev `DecisionClient`, the operator safety gate, the verifier pattern (now blue's investigators), the event log and replay, and the benchmark harness (now the round runner).

New:
- A splat edit API for red: recolor, fade or hide Gaussians inside a named anchor's box, and insert small object splats (soot, a tool left behind, an open door).
- Render-and-diff for blue against its memorized baseline, per viewpoint and per mode (RGB, a false-color "thermal" shader).
- A generic recursive agent runtime (spawn, budget, checkpoint, kill, respawn) shared by both teams.
- Round memory: each team reads the last round's referee report before planning the next round.
- The two-pane UI (red god view, blue camera view).

### Why each ingredient is load-bearing

- **Splat:** red's visual attacks are Gaussian edits and blue's only visual sense is rendering the edited splat, so without editable splats there is no visual attack surface and no decoys.
- **Jev:** red asks Jev P(detected) for each candidate attack to pick the stealthiest one within budget, blue asks Jev to triage every anomaly and gates every action, and both teams' Jev calibration is scored with a Brier score each round.
- **Recursion:** both sides are trees of commander, sector leads and tactic or investigator agents that spawn on demand, so blue's tree grows wherever red's attacks land.

### Architecture sketch

```
                     ROUND RUNNER (virtual clock, seeds, budgets)
                                   |
        +--------------------------+---------------------------+
        |                                                      |
  RED COMMANDER (Nemotron Super)                     BLUE COMMANDER (gpt-oss-120b)
   | asks Jev: P(detected) per attack                 | memorized baseline renders
   +-> sector saboteur x3 (Lightning)                 +-> sector watcher x3 (Omni)
         +-> tactic agents                                  | render, diff, Jev triage
              | sim.inject_fault(...)                       +-> investigator agents (Lightning)
              | splat.edit(anchor, kit)                          | run_diagnostic, operate
              | splat.add_object(decoy)                          | Jev safety gate
        |                                                      |
        v                                                      v
  +-----------------+    edits    +------------------+   renders   +-------------+
  | PLANT SIM       |<----------->| SPLAT SERVER     |------------>| RENDER TAB  |
  | hidden truth    |             | Spark, edit log  |             | pose -> PNG |
  +--------+--------+             +---------+--------+             +-------------+
           |                                |
           +-------------> REFEREE <--------+   knows every red action, scores blue,
                           (judge.py)           writes round report to both memories
```

Model split: red on Nemotron, blue on a different family (gpt-oss-120b commander, Nemotron Omni eyes), which is itself an experiment the demo reports.

### 6-hour build plan for 3 people

| Time | A: agents and runtime | B: sim, referee, Jev | C: splat and vision |
|---|---|---|---|
| 10:45-11:30 | Keys, smoke test, spawn/budget/kill runtime skeleton | Jev key via OpenRouter, `DecisionClient` both backends | Capture or source the plant splat, load in Spark |
| 11:30-1:00 | Red and blue commanders and sector leads, one round end to end in the terminal | Attack API over the sim (F3, F6, F8 first), referee scoring | Edit API: anchor boxes, recolor, hide, insert decoy; render tab over websocket |
| 1:00-2:30 | Round memory, respawn on kill, decoy handling | Jev P(detected) for red, triage and gate for blue, Brier scoring | Baseline diff per viewpoint, thermal shader, calibrate Omni on 8 edit kits |
| 2:30-3:30 | Run rounds in the background for the chart | Results table and co-evolution chart | Two-pane UI with live edits and pins |
| 3:30-4:15 | Feature freeze, demo path only, record backup round | README, "Built during the hackathon" section | Polish the 3D edit animation |
| 4:15-4:50 | Rehearse twice, submit | | |

### Build bottlenecks

1. **Splat edits the vision model cannot see.**
   A recolored HMI might be 20 pixels in a render.
   Mitigation: define 8 edit kits on named anchors with close-up viewpoints and calibrate Omni on them by 1:30.
   Fallback: an edit swaps the photo-bank image for that viewpoint, which keeps the game but weakens the splat claim.
2. **No good plant splat.**
   There is no licensed solar-farm splat in hand.
   Mitigation: capture a venue electrical room or a tabletop mock-up with Scaniverse at 10:45, and label anchors on it.
   Fallback: World Labs Marble from a CC-licensed aerial photo, checked for output rights.
3. **Jev access.**
   Signups are paused.
   Mitigation: OpenRouter key first thing, OpenAI Decisions as the second backend.
   Fallback: a Nemotron classifier that returns probabilities, named honestly as a stand-in in the README.
4. **Two recursive teams are slow and hit rate limits.**
   Mitigation: card on file for 600 RPM, roles spread over five models, fan-out capped at 3 and depth at 3, sim clock is virtual.
   Fallback: the live demo runs one round, and the chart comes from rounds run during the afternoon.
5. **An unbalanced game makes a boring demo.**
   If red always wins or never wins, there is no story.
   Mitigation: tune red's budget and blue's camera budget by 2:30 so detection sits near 60 to 80 percent.
   Fallback: pick a seed for the live round known to produce one catch and one miss, and say it is seeded.

### Self-score

| Criterion | Score | Why |
|---|---|---|
| Technicality (40%) | 8 | Two recursive teams, splat edit API, render-and-diff, calibrated referee; harder than average, but each part is known |
| Live Demo (20%) | 7 | Live edits and a kill-and-respawn are fun to watch; two panes are busy on a projector |
| Creativity (25%) | 7 | Editable-splat sabotage is new; red versus blue is familiar |
| Future Potential (15%) | 8 | Control evaluations for embodied agents is a real research direction |
| **Weighted total** | **7.55** | |

---

## Concept 2: Afterimage

**Hook:** We scanned this room at 10:45, a judge secretly changes something, and a self-growing tree of agents that has never seen the change tells you exactly what moved, where, and whether it matters.

The frame flip: the hackathon venue is the plant, the judge is the fault injector, and the splat is the agents' memory of the world rather than a display.
The recursion is spatial: the agent tree is an octree over the room, and it grows deeper only where the evidence is uncertain.

### The 3-minute live demo

1. **0:00 Hook.**
   "This is our corner of this room at 10:45 this morning."
   We fly through the morning splat of our table, the wall behind it and the pillar next to it.
2. **0:15 The judge tampers.**
   We hand a judge three sticky notes and say "change up to three things in this area, we will look away".
   The judge moves a bottle, takes a badge, or opens a laptop, in about 20 seconds.
3. **0:35 One look.**
   A teammate walks the phone through the area for 30 seconds of video.
   The agents get the morning splat and the new video, nothing else, and we step back.
4. **0:45 The tree grows in 3D.**
   The root surveyor registers the video frames into the splat and spawns eight region agents, one per octant of the scene.
   Each region agent renders the morning splat from the new frames' poses, diffs, and asks Jev "clear, changed, or split?".
   Uncertain regions spawn eight children at double resolution, and the audience watches a lattice of glowing boxes subdivide over the splat, going green when cleared and red when flagged.
   This is the wow: recursion you can see, shaped by the room itself.
5. **1:40 Explain the change.**
   Each flagged leaf spawns an inspector that sends side-by-side crops to Nemotron Super VL and writes a sentence such as "red bottle moved about 40 cm left" or "badge removed from the lanyard hook".
   The agents then edit the morning splat into the afternoon state: Gaussians fade where an object left and a pin appears where something arrived.
6. **2:10 The reveal.**
   The judge reads out the secret list, and our card shows 3 of 3 found with zero false alarms, or honestly shows what it missed.
   It often also reports a change nobody staged, such as a backpack someone dropped, and the judge can confirm it.
7. **2:30 The number.**
   On 15 staged change sets captured during the day: precision, recall, and VLM calls used by the adaptive octree versus a uniform grid at the same finest resolution.
   We also show Jev's calibration on "changed" against ground truth.
8. **2:50 Close.**
   "Construction progress, warehouse audits, security rounds, plant walk-downs: the world changes between looks, and these agents notice, explain, and can prove they looked everywhere."

### Why judges have not seen it

The closest existing things are the research paper 3DGS-CD (2024), which detects object rearrangement between Gaussian splat captures, construction progress tools such as OpenSpace, Avvir and Doxel that compare scans against plans, satellite change detection, and spot-the-difference benchmarks for vision models.
None of them make the search itself a recursive agent tree whose shape is decided by calibrated probabilities, and none run live on the venue with a judge as the adversary.
The live tamper also proves the demo is not canned, which is the strongest possible answer to "is this real?".

### Reuse and new work

Reused from the current plan:
- The verifier's core move, "render a named viewpoint and ask vision", which becomes the leaf agent.
- The definition-of-done idea, which becomes a coverage certificate: the team may claim "nothing else changed" only if every cell is cleared at sufficient resolution or flagged, and the verifier checks the certificate.
- The false-done metric, which becomes "false clear": claiming a region unchanged when it changed.
- The supervisor's budget and loop rules, which become the spawn governor; the splat viewer and pins; the benchmark harness; the `DecisionClient`.

New, and honestly most of the code:
- Marker-based registration of phone frames into the splat.
- A depth-aware diff that projects 2D change masks into 3D and votes them into octree cells across many views, so a real change is consistent in 3D while lighting noise is not.
- The octree agent runtime with neighbor messages for objects that straddle a cell boundary.
- The staged change-set capture protocol and the octree overlay in the viewer.
- The plant simulator is dropped, which is the main loss of reuse.

### Why each ingredient is load-bearing

- **Splat:** the morning splat is the agents' memory of the room; they render it from any new camera pose to know what should be there, and they edit it into the updated state, so without it there is nothing to compare against from arbitrary viewpoints.
- **Jev:** every octree node sends its multi-view evidence to Jev and gets calibrated probabilities for clear, changed and split, and those probabilities plus the remaining budget decide whether children are spawned, so Jev literally shapes the agent tree.
- **Recursion:** each uncertain region spawns eight child agents at double resolution, so depth is data-dependent (usually 3 to 5 levels) and the team grows its own structure under a Jev-governed budget.

### Architecture sketch

```
 morning splat (10:45, AprilTags in scene)        new 30 s phone video
          |                                              |
          |                                   keyframes + AprilTag PnP poses
          v                                              v
 +------------------+   pose -> RGB + depth   +----------------------------+
 | RENDER TAB       |<------------------------| SURVEYOR (Nemotron Super)  |
 | Spark, websocket |------------------------>| registers frames, root cell|
 +------------------+                         +-------------+--------------+
                                                            | spawn 8
                          +----------------+----------------+----------------+
                          v                v                                 v
                    REGION AGENT     REGION AGENT       ...           REGION AGENT
                    (Lightning)       render, diff, 3D vote
                          | evidence JSON per cell
                          v
                    JEV: route {clear | changed | split}, score change_magnitude
                          | split -> spawn 8 children (budget permitting)
                          | changed -> INSPECTOR (Super VL on crop pairs)
                          v
                 COVERAGE CERTIFICATE -> VERIFIER (different family) -> report
                          |
                          v
                 SPLAT EDITOR: fade departed Gaussians, pin arrivals, octree overlay
```

### 6-hour build plan for 3 people

| Time | A: octree agents and Jev | B: capture and registration | C: render, diff, viewer |
|---|---|---|---|
| 10:45-11:30 | Keys, Jev via OpenRouter, runtime skeleton | Print and place 6 to 8 AprilTags, capture the morning splat three times, keep the best | Spark viewer loads it, render tab returns RGB and depth for a pose |
| 11:30-1:00 | Octree cells, spawn and budget rules, evidence schema | Align splat frame to tag frame, PnP poses for video keyframes, check reprojection error | 2D diff (SSIM plus color), project masks into 3D with depth |
| 1:00-2:30 | Jev routing per node, coverage certificate, verifier | Stage and capture 15 change sets with a written ground-truth list each | Inspector crops for Super VL, octree overlay animation |
| 2:30-3:30 | Run the benchmark: adaptive versus uniform grid | Person and lighting filters, re-tune thresholds on the dev split | Splat edit for departed and arrived objects, result card |
| 3:30-4:15 | Feature freeze, full dry run with a teammate as the judge, record backup | README, "Built during the hackathon" | Pixel polish on the overlay |
| 4:15-4:50 | Rehearse the live tamper twice, submit | | |

### Build bottlenecks

1. **Registering phone frames into the splat.**
   Without good poses, every render-and-diff is noise.
   Mitigation: AprilTags in the scene from 10:45, PnP per frame, and a reprojection-error check that drops bad frames.
   Fallback: five fixed photo stations marked with tape, with poses calibrated once in the morning, which loses the free walk-through but keeps everything else.
2. **A busy venue makes false changes.**
   People walk through, light shifts, laptop screens change.
   Mitigation: confine the scene to our table, wall and pillar, require a change to agree across at least three views in 3D, and drop person pixels with a cheap segmentation pass.
   Fallback: a tabletop diorama we build and control, captured the same way.
3. **Splat quality from a phone.**
   Reflective laptops and clutter make floaters that look like changes.
   Mitigation: three morning captures, keep the best, cover shiny surfaces, keep matte props in the area.
   Fallback: the diorama.
4. **The live run must finish in about 60 seconds.**
   Mitigation: 12 to 20 keyframes only, all region agents in parallel across Lightning and Omni, pre-computed morning renders at the station poses, prompt caches warmed.
   Fallback: the judge tampers during the walk-up before our slot, we process before we go on, and the live part re-runs on a smaller change set.
5. **Vision models are weak at fine differences.**
   Mitigation: the classical diff and 3D voting find where, and the model only answers what, on tight crop pairs.
   Fallback: report location and a crop thumbnail without a sentence.

### Self-score

| Criterion | Score | Why |
|---|---|---|
| Technicality (40%) | 9 | Registration, depth-aware 3D voting, adaptive octree agents and a coverage certificate are hard to recreate in a day |
| Live Demo (20%) | 7 | A judge tampering live is the strongest moment of any concept, but registration on venue Wi-Fi and light is the riskiest path |
| Creativity (25%) | 9 | Spatial recursion governed by calibrated decisions, with the room as the plant, is new |
| Future Potential (15%) | 8 | Inspection, audits and construction progress are large markets, and "prove you looked everywhere" is an evaluation idea |
| **Weighted total** | **8.45** | |

---

## Concept 3: Trapsmith

**Hook:** An agent team whose only job is to invent traps that make other agent teams falsely say "done", proving each trap is fair before using it, so the benchmark grows meaner every hour and ships a live ranking of which open models lie.

The frame flip: the false-done benchmark stops being the slide at the end of the pitch and becomes the product.
GridTwin's operator team becomes one contestant among many, and the system that builds the traps is the thing being demoed.

### The 3-minute live demo

1. **0:00 Hook.**
   "Every agent says it is reliable. We built agents whose job is to prove them wrong."
   A ranking shows six operator teams, one per Crusoe model, with false-done rate and calibration.
2. **0:20 The forge starts.**
   The Trapsmith root reads the ranking and asks Jev, for each contestant, which trap family it is most likely to fail.
   It spawns trap designers for the three most informative gaps.
3. **0:40 Traps are built and proved.**
   Each designer composes a scenario from typed primitives: sim faults, timing, and a splat edit that plants or hides visual evidence.
   Each designer spawns a solvability prover that must solve the trap with privileged access, and a fairness critic from another model family that rejects traps a careful human operator could not solve.
   The audience sees one trap rejected as unfair and rebuilt.
4. **1:10 The arena runs.**
   Accepted traps go to all contestants in parallel, shown as a grid of small splat views with each team's pins and claims.
5. **1:40 The wow.**
   The current leader claims "done", the hidden judge says no, and its rank drops live.
   We open the trap in 3D: the alarm cleared, the telemetry recovered, but the HMI in the splat is still dark because the forge combined F3 relapse with F6 stale data, a combination nobody on our team wrote.
6. **2:20 The number.**
   Generated traps against our hand-written ones: the false-done rate each induces, the share proved solvable (100 percent by construction), and Jev's predicted failure probability against actual failures as a calibration curve.
7. **2:45 Close.**
   "Benchmarks saturate, this one grows. Point it at any agent behind an OpenAI-compatible endpoint."

### Why judges have not seen it

The closest existing things are Dynabench (humans write examples that fool models), "Red Teaming Language Models with Language Models" (Perez et al., 2022), agent benchmarks such as SWE-bench, tau-bench and METR's task suites, and arena rankings such as LMArena.
Automated adversarial generation exists for text prompts, but not for agentic, multi-modal, physically grounded tasks where each generated trap comes with a machine-checked solvability proof.
Honest caveat: judges have seen leaderboards, so the pitch must make the forge loop the product and the ranking only its output.

### Reuse and new work

Reused from the current plan, more than any other concept:
- The simulator, fault library, traps, scenario YAML (now generated), `judge.py`, the benchmark harness, the held-out split and the false-done metric.
- The GridTwin operator team, which becomes the reference contestant.
- The verifier, which becomes the solvability prover, and the `DecisionClient`.

New:
- A typed trap grammar over F1 to F8 primitives (fault, asset, onset, relapse timing, telemetry mask, splat edit kit), so every generated trap is a valid scenario.
- The forge tree: root, designers, provers, critics.
- Jev-based adaptive selection of the next trap, in the spirit of item response theory in human testing.
- A multi-model arena runner and per-scenario splat edits.

### Why each ingredient is load-bearing

- **Splat:** each trap carries a Gaussian edit that plants or hides visual evidence, and contestants see the plant only through renders, so traps can lie in one modality and tell the truth in another.
- **Jev:** the forge asks Jev for P(contestant M falsely claims done given trap features) to pick the most informative next trap, and Jev's own predictions are scored for calibration on every run.
- **Recursion:** the forge is root, designers, provers and critics, and every contestant is itself a recursive team, so the arena runs teams of teams.

### Architecture sketch

```
            RANKING + run history
                    |
          TRAPSMITH ROOT (Nemotron Super)
           | asks Jev: P(M fails | trap features) for each contestant
           +-> TRAP DESIGNER x3 (Qwen / GLM / DeepSeek)
                 | composes trap from typed grammar (faults, timing, splat kit)
                 +-> SOLVABILITY PROVER (truth access, must reach resolved)
                 +-> FAIRNESS CRITIC (gpt-oss-120b, rejects unfair traps)
                    |
            accepted traps -> scenario YAML + splat edit
                    |
        +-----------+-----------+-----------+
        v           v           v           v
   CONTESTANT   CONTESTANT  CONTESTANT  ...  (each a recursive operator team
   Nemotron     Qwen        DeepSeek          on its own sim copy and splat view)
        |           |           |
        +-----------+-----------+--> HIDDEN JUDGE (judge.py) -> ranking, Brier for Jev
```

### 6-hour build plan for 3 people

| Time | A: forge agents | B: sim, judge, arena | C: splat and viewer |
|---|---|---|---|
| 10:45-11:30 | Keys, Jev via OpenRouter, runtime skeleton | Sim with F3, F6, F5 and the judge | Capture or source a plant splat, Spark viewer |
| 11:30-1:00 | Trap grammar and designer prompts | Contestant operator team (GridTwin loop, thin), runs one scenario | Edit kits on named anchors, render tab |
| 1:00-2:30 | Prover and critic, Jev adaptive selection | Arena runner across 4 to 6 models, background loop from 1:30 | Calibrate Omni on edit kits, grid of small views |
| 2:30-3:30 | Generated versus hand-written comparison | Calibration curve, ranking history | Trap reveal view in 3D |
| 3:30-4:15 | Feature freeze, record backup | README, "Built during the hackathon" | Polish |
| 4:15-4:50 | Rehearse, submit | | |

### Build bottlenecks

1. **Generated traps are unsolvable or trivially broken.**
   Mitigation: generation only through the typed grammar, the prover must reach `resolved` with truth access, and pytest invariants per primitive.
   Fallback: generation limited to mutating parameters of the 12 hand-written scenarios.
2. **Not enough runs by 3:30 for honest numbers.**
   Six models times many scenarios times recursive teams is a lot of calls.
   Mitigation: start the arena loop at 1:30 and never stop it, virtual sim clock, tool-call caps.
   Fallback: three contestants and report N plainly.
3. **The ranking reads as a dashboard, which is reportedly a banned project type.**
   Mitigation: pitch and show the forge running unattended as the product, with the ranking as its output.
   Fallback: cut the ranking from the demo and show only one trap's life cycle end to end.
4. **The demo is less visceral than the other concepts.**
   Numbers on a grid do not wow a room.
   Mitigation: the trap reveal in 3D and one contestant's lie replayed step by step.
   Fallback: lead with the replay and show numbers only at the end.
5. **Jev predictions are noisy with small N.**
   Mitigation: coarse features (trap family and modality), report calibration with error bars.
   Fallback: show Jev only as the selector and drop the calibration curve.

### Self-score

| Criterion | Score | Why |
|---|---|---|
| Technicality (40%) | 8 | Typed trap grammar, solvability proofs and adaptive selection are solid engineering |
| Live Demo (20%) | 6 | Hard to make a forge exciting in three minutes |
| Creativity (25%) | 7 | Self-growing benchmarks are an emerging idea, though this grounding is new |
| Future Potential (15%) | 9 | Directly about evaluation of agents, which the rubric names |
| **Weighted total** | **7.50** | |

---

## Concept 4: Many Worlds

**Hook:** Before touching anything, the agent team forks the plant into a tree of ghost futures, renders each one in 3D, bets on them with Jev, acts once, and then grades its own imagination against what really happened.

The frame flip: the digital twin stops being a mirror of the present and becomes a tree of possible futures, one per hypothesis and action.
The splat is no longer a display of the plant; it is how the agents picture the consequences of their choices.

### The 3-minute live demo

1. **0:00 Hook.**
   "Agents act, then find out. Ours find out first."
   The plant splat fills the screen.
2. **0:15 Goal.**
   We type "Unit 2 keeps tripping, get it back to full output safely" and step back.
3. **0:25 The tree of futures.**
   A diagnoser spawns one hypothesis agent per plausible cause (ground fault, fan failure, nuisance trip).
   Each hypothesis agent spawns action-fork agents, and each fork runs its own copy of the simulator, seeded with that hypothesis rather than the hidden truth, 60 sim-minutes forward.
   The wow: the 3D view fans out into a ring of translucent ghost plants, one per future, and in the ghost where "close the breaker" meets a ground fault, smoke rises from the cabinet.
4. **1:15 The bet.**
   Jev scores every leaf for P(resolved) and P(unsafe), and the tree collapses.
   Often the winning move is not a fix but a diagnostic, because it splits the hypotheses, and the agents say so.
5. **1:45 Reality answers.**
   The team acts on the real plant once.
   The real camera render appears next to each surviving ghost; the ghosts that predicted wrong fade out, their hypotheses are pruned, and the tree regrows from the survivor.
6. **2:15 Recovery.**
   We kill a fork agent on stage; its parent notices the missing leaf, respawns it from the sim snapshot, and the search continues.
7. **2:30 The number.**
   On held-out scenarios: safety violations and false-done rate for an act-first baseline against Many Worlds, the Brier score of Jev's P(resolved) against reality, and average tree size and dollars of inference per incident.
8. **2:50 Close.**
   "A world model for agents does not have to be a giant video model. A simulator plus editable Gaussians is a world model you can audit, frame by frame."

### Why judges have not seen it

The closest existing things are Monte Carlo tree search and AlphaZero-style planning, Tree of Thoughts, model-predictive control, world-model agents such as Dreamer and Genie-style video models, and the "what-if" scenario features in industrial twin software from vendors such as Siemens and AVEVA.
Tree search is old and industrial what-if twins are human-driven.
What is new is a visible tree of recursive agents planning over hypotheses, each future rendered as an edited 3D scene, every bet calibrated, and every prediction graded against a real render afterwards.
Honest caveat: a sharp judge will ask "is this MCTS with extra steps?", and the answer must be the hypothesis-level search, the value-of-information move, and the measured calibration.

### Reuse and new work

Reused from the current plan:
- The simulator, which needs only `snapshot()`, `clone()` and "instantiate from a hypothesized fault", easy because it is already deterministic and seeded.
- The fault library (now the hypothesis space), traps, `judge.py`, the benchmark harness and held-out split.
- The decision gate (now the leaf scorer), the verifier (now the reality-versus-prediction checker), and the supervisor's budget rules (now the tree budget).

New:
- A belief-state forking runtime: hypothesis agents, action forks, outcome forks, snapshots, pruning on observation.
- A mapping from predicted sim state to splat edit kits, so every fork has a rendered end-state.
- A ghost viewer that shows several edited copies of the scene at once.
- An imagination-error check that compares predicted renders to the real render in image space and per anchor.

### Why each ingredient is load-bearing

- **Splat:** every fork's predicted end-state is rendered as an edited splat and compared to the real render after the action, so the splat is the visual half of the world model and the thing predictions are graded in.
- **Jev:** Jev scores each leaf with typed questions (P(resolved), P(unsafe), route) and picks where the tree expands next, its probabilities are what the tree backs up to the root, and their calibration is measured against reality.
- **Recursion:** hypothesis agents spawn action forks that spawn outcome forks, a tree of agents that grows where Jev is least certain and is pruned by real observations.

### Architecture sketch

```
                      goal + current observations
                                 |
                      DIAGNOSER (Nemotron Super)
                       | proposes hypotheses from the fault library
        +--------------+---------------+
        v              v               v
   HYPOTHESIS      HYPOTHESIS      HYPOTHESIS        (Lightning, one per cause)
   ground fault    fan failure     nuisance trip
        | spawns action forks (isolate, restart, close, diagnose, page)
        v
   ACTION FORK -> sim.clone(hypothesis).run(action, 60 min) -> predicted state
        |          -> splat edit kits -> ghost render
        |          -> optional outcome forks (depth 2 to 3)
        v
   JEV: P(resolved), P(unsafe), route per leaf -> back up values -> choose action
        |
        v
   REAL PLANT SIM (hidden truth) -> real render
        |
        v
   IMAGINATION CHECK (Omni, different family verifier): real vs each ghost
        -> prune hypotheses that predicted wrong -> regrow tree -> repeat until DONE
```

### 6-hour build plan for 3 people

| Time | A: fork tree and Jev | B: sim and judge | C: splat and ghost viewer |
|---|---|---|---|
| 10:45-11:30 | Keys, Jev via OpenRouter, runtime skeleton | Sim with F1, F3, F4, F5; add `snapshot`, `clone` | Capture or source a plant splat, Spark viewer |
| 11:30-1:00 | Hypothesis and action-fork agents, budget, tree log | Instantiate sim from a hypothesis, judge metrics | Edit kits (smoke, dark HMI, open breaker, derate glow), render tab |
| 1:00-2:30 | Jev leaf scoring, back-up, value-of-information diagnostic | Act-first baseline, benchmark runner | State-to-kit mapping, ghost ring with 4 to 6 copies |
| 2:30-3:30 | Pruning on observation, kill and respawn | Run held-out benchmark, Brier score | Imagination check view (real versus ghost) |
| 3:30-4:15 | Feature freeze, record backup | README, "Built during the hackathon" | Pixel polish of the ghost fan-out |
| 4:15-4:50 | Rehearse, submit | | |

### Build bottlenecks

1. **Several splats on screen at once are slow in the browser.**
   Mitigation: one small scene, at most six ghosts, low detail for ghosts, and the full-quality render only for the selected one.
   Fallback: a ring of rendered images around the live splat instead of live ghost meshes.
2. **The tree explodes and hits rate limits.**
   Mitigation: beam width 3, depth 3, Jev-guided expansion, sim forks are plain Python with no model calls per tick, and models are called only at nodes.
   Fallback: depth 2 live and depth 3 in the benchmark runs.
3. **Hypothesis-seeded sims do not match the real fault parameters.**
   Mitigation: hypotheses are typed instances of the fault library with coarse parameters, and pruning tolerates small mismatches.
   Fallback: hypotheses limited to the four faults in the demo scenario.
4. **"Is it just MCTS?"**
   Mitigation: show the diagnostic chosen for its value of information, the visible hypothesis pruning, and the calibration number.
   Fallback: say it plainly in the pitch, "tree search, but every node is an agent and every bet is calibrated", before a judge does.
5. **Ghost renders are a lookup, not a learned prediction.**
   The state-to-kit mapping is hand-written, which a judge may see as staged.
   Mitigation: say so, and show that the prediction itself comes from the forked sim and the agents' hypotheses, and the render is only the readout.
   Fallback: drop the render claim and keep the image-space imagination check on the real side only.

### Self-score

| Criterion | Score | Why |
|---|---|---|
| Technicality (40%) | 9 | Belief-state forking, calibrated tree search and image-space prediction checks, built on a real sim, are hard to recreate |
| Live Demo (20%) | 7 | The ghost fan-out is a striking moment, but the explanation is denser than Afterimage's |
| Creativity (25%) | 8 | Counterfactual 3D twins driven by recursive agents are new; tree search is not |
| Future Potential (15%) | 8 | "Imagine, bet, act, grade your imagination" is a clean story for autonomy and learning |
| **Weighted total** | **8.20** | |

---

## Side by side

| Concept | Reuse of GridTwin | Riskiest dependency | Wow moment | Weighted |
|---|---|---|---|---|
| 1. Tamper | High: sim, faults, judge, gate | Edits visible to vision at render size | Blue catches a dark HMI that every telemetry check passed | 7.55 |
| 2. Afterimage | Low in code, high in ideas (verifier move, coverage as done) | Registering phone frames into the splat in a busy room | A judge tampers live and an octree of agents finds it in 3D | 8.45 |
| 3. Trapsmith | Highest: the benchmark becomes the product | Enough arena runs by 3:30 | The leader falls to a trap nobody on the team wrote | 7.50 |
| 4. Many Worlds | High: sim gains clone and hypothesis seeding | Ghost rendering and tree size | The plant fans out into ghost futures, then reality prunes them | 8.20 |

## Recommendation

Afterimage scores highest because the live tamper by a judge is the hardest demo to fake and the easiest to remember, and its recursion is the most visible of the four.
It drops the plant simulator, so the team should choose it only if someone is willing to own registration from 10:45 with no other job.
Many Worlds is the best choice if the team wants to keep most of the GridTwin code, because it changes the frame from "verify after acting" to "imagine before acting" while reusing the sim, faults and judge almost unchanged.
Either way, the first 45 minutes must produce a working Jev call, a splat in Spark with a render tab, and for Afterimage the morning capture with tags in place, because each later step depends on those three.
