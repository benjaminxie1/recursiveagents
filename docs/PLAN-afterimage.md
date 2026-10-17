# Afterimage: game plan

**Pitch (one line):** We scan a site in the morning, a judge secretly changes it, and a team of agents that has never seen the change finds every difference, explains why it matters, and refuses to say "all clear" until it can prove it looked everywhere.

**Primary track: Done Means Done** (track names are still unconfirmed, see `docs/HACKATHON.md`).
"Done" here is "the site is certified back to baseline", and the agents may only claim it with a coverage certificate that an independent verifier checks.

**Side prize: Best Use of Nemotron** (RTX 5090).
Nemotron runs the planner, the region agents, the inspector and the fallback vision; only the verifier and the challenger use a different family, on purpose.

**Defaults taken without a team vote (flip any at kickoff):** the scene is a tabletop mock of a plant equipment area; the agents inspect, certify and make a final go or no-go call; flagged changes can be challenged in small sub-trials up to depth 2; Alike is team dev memory only, not a product feature.

This plan replaces GridTwin (`docs/PLAN.md`).
Section 9 lists what changed and why.

---

## 1. What the product does

1. **Remembers.** At 10:45 we build a tabletop mock of a plant equipment area (an inverter cabinet made from a box, cable runs, a mini extinguisher, a breaker panel printout, labelled parts) and capture it as a Gaussian splat with an iPhone.
   That splat is the agents' memory of the safe baseline.
   We also take one "before" photo from each of five taped phone stations.
2. **Gets a vague goal.** "Is the site back to its safe baseline? Find and explain every change since this morning, and certify it."
3. **Writes its definition of done, under rules it cannot weaken.** The planner adds goal-specific checks (for example "extinguisher on its mount", "cabinet door closed"), each safety check backed by a rule found with You.com.
   Two checks are mandatory and the planner cannot remove them: full coverage of the scene, and agreement across views for every finding.
4. **Searches recursively.** Region agents each own a box of the scene.
   A box whose evidence is uncertain splits into eight child agents at double resolution, so the team grows only where something may have happened.
5. **Argues before believing.** Every flagged change is a claim.
   A challenger agent from a different model family can dispute it with a different camera angle, and the dispute becomes a sub-trial that Jev rules on.
6. **Refuses a false "all clear".** A verifier checks the coverage certificate against the whole scene, not against a list the workers chose.
   If any region was never actually seen well enough, "all clear" is rejected and the agents go back.
7. **Recovers on its own.** If a region agent dies or a model starts failing, the supervisor notices the missing heartbeat, re-spawns the agent from its last checkpoint, and routes calls to a spare model.
8. **Decides and keeps watching.** The output is a 3D report: changes pinned and explained, why each matters with a citation, and a final Jev call such as "safe to re-energize: p = 0.12".
   A human is paged only for changes that need one.
   In watch mode, each new set of station photos dropped into the inbox starts a new patrol with nobody at the keyboard.

---

## 2. Architecture

```
 10:45 baseline (once)                          each patrol
 ─────────────────────                          ───────────
 iPhone + Scaniverse ──► morning splat (.spz)   5 "after" station photos ──► inbox
 5 "before" station photos                                     │
 AprilTag board ──► station poses in splat frame (once)       ▼
                                                 ┌────────────────────────────┐
                                                 │ DIFF + 3D VOTING           │
                                                 │ OpenCV before/after diff   │
                                                 │ depth from splat index     │
                                                 │ votes into octree cells    │
                                                 └─────────────┬──────────────┘
                                                               │ evidence per cell
 ┌─────────────────────────┐   ┌──────────────────────────────▼───────────────┐
 │ PLANNER                 │──►│ DEFINITION OF DONE                            │
 │ Nemotron 3 Super        │   │ mandatory: coverage, multi-view agreement     │
 │ + You.com (cited rules) │   │ added: safety and baseline checks with cites  │
 └─────────────────────────┘   └──────────────────────────────┬───────────────┘
                                                              │
                          ┌───────────────────────────────────▼───────────────┐
                          │ ROOT REGION AGENT  ──spawn x8──►  REGION AGENTS    │
                          │ Nemotron 3.5 Lightning, one per box, depth <= 3   │
                          │ each step picks ONE action:                       │
                          │   split | closer_look | clear | flag              │
                          │ JEV decide(): {clear, changed, split} + magnitude  │
                          │ neighbour messages merge changes across boxes     │
                          └──────┬──────────────────────────────┬─────────────┘
                                 │ flag                         │ closer_look
                                 ▼                              ▼
                 ┌──────────────────────────┐    tighter splat render (before)
                 │ INSPECTOR                │    + high-res photo crop (after)
                 │ Nemotron 3.5 Super VL    │
                 │ crop pair → one sentence │
                 └────────────┬─────────────┘
                              ▼ claim
                 ┌──────────────────────────┐
                 │ CHALLENGER  gpt-oss-120b │ disputes with another view
                 │ SUB-TRIAL (depth <= 2)   │ Jev rules: claim_holds
                 └────────────┬─────────────┘
                              ▼
                 ┌──────────────────────────┐
                 │ VERIFIER  gpt-oss-120b   │ checks the certificate against
                 │                          │ the whole scene; rejects a false
                 │                          │ "all clear"
                 └────────────┬─────────────┘
                              ▼
                 ┌──────────────────────────┐
                 │ SUPERVISOR (code + Jev)  │ heartbeats, re-spawn from
                 │                          │ checkpoint, model failover,
                 │                          │ budget, final go/no-go, page
                 └────────────┬─────────────┘
                              ▼
     SPARK VIEWER: octree overlay growing live, findings pinned in 3D,
     splat edits (fade what left, pin what arrived), report card;
     events.jsonl drives the viewer and the replay
```

### Key pieces

1. **Baseline capture (replaces the plant simulator).**
   A real scene replaces a simulated one, so ground truth comes from the world, not from code we wrote.
   One-time setup: Scaniverse capture (three takes, keep the best), five taped phone stations, and an AprilTag board so each station's pose in the splat frame is solved once with PnP and then frozen.
2. **Diff and 3D voting.**
   For each station, OpenCV compares before and after photos (SSIM plus color difference), with a quick Nano Omni check that no hand or person is in frame.
   The splat index gives depth for each station pose by projecting Gaussian centres into a z-buffer, so every changed pixel lifts to a 3D point and votes into octree cells.
   A real change agrees across stations in 3D; a lighting flicker does not.
3. **Recursive region agents.**
   The tree starts at the whole scene, and a box splits into eight children only when Jev says the evidence is uncertain and budget remains.
   It stops at depth 3, when every box is cleared or flagged, or when the budget (about 120 model calls) is spent.
   Each step is a real choice: split, render a closer splat view and crop the photo tighter, clear, or flag.
4. **Definition of done with fixed floors.**
   The planner writes checks in a small safe JSON language: `coverage`, `agreement`, `baseline` (an item is at its morning position), `safety` (a rule with a You.com citation), and `decision`.
   The `coverage` and `agreement` checks are inserted by code and cannot be removed, so the agents cannot make "done" easy by writing weak checks.
   The verifier also asks Jev whether the checks cover the goal before any check runs.
5. **Challenges as sub-trials.**
   A flagged change becomes a claim.
   The challenger (gpt-oss-120b) may file one dispute per claim with a camera angle it picks; that opens a sub-trial with its own evidence, and Jev rules `claim_holds`.
   Sub-trial rulings feed back to the parent claim, at most two levels deep.
6. **Verifier.**
   It gets the certificate and raw tool access, never the workers' prose.
   It re-renders splat views, re-reads the diffs, and rejects "all clear" if any leaf lacks evidence at the required depth.
7. **Supervisor and recovery.**
   Code, not an LLM, for the hard rules: heartbeats per agent, re-spawn a dead agent from its last event-log checkpoint, fail over to a spare model on repeated 429 or 503, loop detection, and budget.
   Jev makes the final go or no-go call and decides whether a human gets paged (push notification with a one-paragraph brief).
8. **Splat viewer.**
   Spark (three.js) shows the octree boxes growing over the splat, green when cleared and red when flagged, plus findings as 3D pins.
   It also serves renders for the agents (the render bridge), so every view an agent asks for flashes as a camera frustum in the scene.
   It edits the splat to show the result: fade Gaussians where an object left, pin where something arrived.

---

## 3. The number that wins: claimed clear vs. actually clear

- **Test set, made by someone else:** 15 change sets staged during the day by a teammate who does not tune the agents, or by volunteers from other teams, each with a written answer key sealed before the run.
  The judge's live tamper is the 16th.
- **Baselines:**
  - one-shot vision: Nemotron Super VL gets the before and after photos and lists the changes;
  - flat grid: the same agents on a uniform grid at the finest resolution, with no recursion.
- **Ours:** the full recursive loop with challenges and the verifier.
- **Report:** recall, precision, **false-clear rate** (said "nothing else changed" when something had), model calls, wall time, and cost per patrol.
- Headline shape: "the one-shot model missed X of Y changes and still said all clear; Afterimage found Z with N calls, a flat grid needed M."
  Real numbers only; never fake them.

---

## 4. Demo script (3 minutes live, plus a 1-minute video)

1. **(0:00) Hook.** "Agents say 'all clear' when they haven't looked. This is our site at 10:45 this morning." Fly through the splat.
2. **(0:15) The judge tampers.** We turn around; a judge changes up to three things in 20 seconds.
3. **(0:35) One look, then walk away.** A teammate takes the five station photos and drops them in the inbox; we step back from the keyboard.
4. **(0:45) The tree grows.** Boxes subdivide over the splat only where something happened; findings pin in 3D with one-line explanations and cited rules.
5. **(1:20) The catch.** A fast first pass says "all clear" for a region behind the cabinet; the verifier rejects it because no station saw that box well enough; the agents take a closer look and find the hidden change.
6. **(1:45) The fight.** The challenger disputes one finding from another angle; a sub-trial opens and Jev rules; the claim holds or falls, live.
7. **(2:00) The crash.** We hit the chaos switch: a region agent is killed and one model returns errors; the supervisor re-spawns the agent from its checkpoint and fails over, and the run finishes.
8. **(2:15) The reveal.** The judge reads their secret list; our report shows what we found, honestly including any miss, and the Jev call on whether the site is safe to re-energize.
9. **(2:30) The number.** False-clear rate and calls used: one-shot vision vs. flat grid vs. Afterimage.
10. **(2:50) Close.** "Site walk-downs, construction progress, audits: the world changes between looks, and these agents notice, explain, and prove they looked everywhere, on Nemotron on Crusoe."

**Backup:** record a full run during the 3:30 freeze, and keep the event-log replay ready if Wi-Fi or rate limits fail.
Pre-warm the prefix cache before going on.
If judging is at a different location, the tabletop scene and taped station board travel with us.

---

## 5. Timeline (hacking 10:45 → submissions 5:00)

| Time | Milestone |
|---|---|
| 10:45–11:30 | Card on Crusoe (30 → 600 RPM). Jev working on OpenRouter. Build the tabletop scene, tape stations, Scaniverse capture, "before" photos. Spark viewer loads the splat. Ask organizers where demos are judged |
| 11:30 | **Checkpoint:** splat captured and loading, Jev answering. If not: re-capture, or use the labelled local judge |
| 11:30–12:30 | Station poses (AprilTag PnP). Diff per station. Splat index and depth. 3D voting into cells. Agent runtime and event log |
| 12:30 | **Go/no-go:** a moved object lands in the right cell. If not: switch to the Viewpoint Court (`docs/ideas/PITCHES.md`), which reuses everything built so far |
| 12:30–2:00 | Region agents with Jev routing, definition of done with fixed floors, verifier, inspector, octree overlay |
| 2:00–3:00 | Challenger and sub-trials, supervisor recovery and chaos switch, You.com citations, splat edits |
| 2:00–3:30 (in parallel) | Stage and photograph the 15 change sets with sealed answer keys; run the benchmark and both baselines |
| 3:30–4:15 | **Feature freeze.** Polish the demo path only. Rehearse the live tamper twice. Record the backup run |
| 4:15–4:50 | 1-minute video, README with results and the "Built during the hackathon" section, repo public, submit |
| 4:50 | Submitted, with a 10-minute buffer |

---

## 6. Team split (3 people)

- **A: Agents.** Region agent runtime, Jev routing, definition of done, verifier, challenger and sub-trials, supervisor and recovery.
- **B: Capture and vision.** Tabletop scene, splat capture, station poses, diff and 3D voting, inspector prompts, the 15 staged change sets and the benchmark.
- **C: Viewer and story.** Spark viewer, render bridge, octree overlay, pins and splat edits, replay, demo script, video, README and submission.

---

## 7. Judging checklist

Official weights: Technicality 40%, Live Demo 20%, Creativity 25%, Future Potential 15%.

- [ ] Technicality: show the 3D voting, the recursive tree, the fixed-floor definition of done, sub-trials and recovery working live, not on slides
- [ ] Live Demo: the judge's tamper is the input; the run works offline from the cached splat, with only inference over the network
- [ ] Creativity: lead with the team growing its own structure over a real scene; do not lead with "planner and workers"
- [ ] Future Potential: pitch "prove you looked everywhere" as a general way to trust autonomous agents, beyond plants
- [ ] Repo is **public**
- [ ] README has a **"Built during the hackathon"** section separating our code from third-party pieces (Spark, Scaniverse, OpenCV, AprilTag, Jev, You.com, Crusoe)
- [ ] The main feature is the autonomous loop; the viewer exists to show its evidence, so it is not a "dashboard as main feature"
- [ ] Nemotron in every core role, with a one-line reason per model
- [ ] Ready answers for "how do you know it looked everywhere?", "what stops it from looping?", and "why agents and not a for-loop over boxes?" (each box makes a real choice of what to look at next)

---

## 8. Stack and integrations

**Crusoe Managed Inference.** All calls through `gridtwin/llm.py` (OpenAI SDK, retries on 429 and 503), behind a per-model concurrency governor and a global spawn budget.
Keep prompts and tool schemas byte-identical for prefix-cache hits.

| Role | Model (exact Crusoe ID) | Why |
|---|---|---|
| Planner, report | `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B` | Strongest Nemotron for planning and writing the definition of done |
| Region agents | `nvidia/Nemotron-3.5-Lightning-30B-A3B` | Fast and cheap for many small decisions at high fan-out |
| Inspector | `nvidia/NVIDIA-Nemotron-3.5-Super-VL` | Compares before and after crops |
| Frame check, fallback vision | `nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B` | Quick person-in-frame check; backup inspector |
| Challenger, verifier | `openai/gpt-oss-120b` | A different family, so its mistakes do not line up with Nemotron's |
| Failover spares | `deepseek-ai/DeepSeek-V4-Pro`, `google/gemma-4-31b-it` | Used only when the supervisor fails over |

**Jev (decisions).** One `decide(state, questions)` client: TypeSafe direct (`POST https://api.typesafe.ai/v1/systemone`, `jev-latest`), then OpenRouter (`typesafe/jev-1.14-20261014`), then OpenAI Decisions (`gpt-6-luna`, accepts images).
Last resort: a Nemotron vote fraction, labelled "local judge" in the UI.
Jev is used three ways: split, clear or changed for every box (it shapes the agent tree); `claim_holds` in sub-trials; and the final go or no-go plus whether to page a human.

**Gaussian splat.** Capture with Scaniverse on an iPhone (Polycam or Luma as backup); the Mac has no CUDA, so no laptop training.
Viewer: Spark (`@sparkjsdev/spark`) in Vite and TypeScript.
Splat index in numpy for depth, region queries and measurements, so depth does not depend on WebGL.

**You.com.** The planner and inspector cite the rule behind each safety check (for example extinguisher mounting and clearance); the verifier fetches each cited URL before a `safety` check can pass.

**Backend.** Python with asyncio and FastAPI plus a WebSocket to the viewer; OpenCV and scikit-image; `pupil-apriltags`; every event in `runs/<id>/events.jsonl`.
Check at 10:45: the venv is Python 3.14, and if OpenCV or AprilTag wheels are missing, rebuild it on 3.12.

**NVIDIA NemoClaw (optional).** Would let the patrol run sandboxed and unattended with network access limited to Crusoe, OpenRouter and You.com.
It needs Docker or Colima and a 2.4 GB image on venue Wi-Fi, and it is alpha on Apple Silicon, so only try it if someone is free, time-boxed, on a second machine.

**Alike.** Team dev memory with `docs/MEMORY.md`; not in the product.

---

## 9. What changed from GridTwin, and why

| GridTwin gap | Fix in Afterimage |
|---|---|
| The splat was a static backdrop and could not show the fault; photos carried the state | The splat is the baseline memory: it gives depth to place each change in 3D, closer views for a second look, and the edited "after" scene |
| Fixed roles one level deep; no agent spawned sub-agents | Region agents split into eight children where evidence is uncertain, up to depth 3; challenges open sub-trials up to depth 2; siblings message each other |
| We wrote the simulator, faults, ground truth and baseline ourselves | A judge makes the live change; the 15 test sets are staged by someone else with sealed answer keys; the baselines are real (one-shot vision, flat grid) |
| Agents wrote their own checks and nothing checked the checks | Coverage and agreement checks are fixed by code; Jev checks the list covers the goal; the verifier checks against the whole scene |
| No recovery from real failures; one short run | Supervisor heartbeats, re-spawn from checkpoint, model failover, a chaos switch in the demo, and watch mode for new photo sets |
| Jev only gated actions | Jev shapes the tree, rules sub-trials and makes the final call |
| Scope sized for four people, no checkpoints | Simulator and Alike integration dropped; checkpoints at 11:30, 12:30 and 3:30 with named fallbacks |
| Plan depended on an unseen reference video | Nothing depends on it; if Ben's video shows a construction-site splat demo, Afterimage already matches that shape |
| Alike memory beat added OAuth and rate-limit risk | Removed from the product |

Kept from GridTwin: the "agents say done when they aren't" hook, a different model family for checking, a measured headline number, You.com citations, Nemotron in the core roles, the Crusoe client and model roles, and the event log.

---

## 10. Still open

1. Team agreement on Afterimage over GridTwin, and on the four defaults above.
2. Where demos are judged (our table or a stage); the tabletop scene travels either way.
3. A Jev key, or confirmation that OpenRouter access works.
4. Crusoe card on file before 11:00.
5. Whether `main` or `claude/sweet-faraday-yssuo9` is the team branch.
