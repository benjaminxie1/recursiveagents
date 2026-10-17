# Full pitches: Afterimage and the Viewpoint Court

Two finalists from the council (`COUNCIL.md`).
Both share one stack, so the first 90 minutes of building are the same whichever we pick.
Model IDs below are the exact strings from Crusoe's `GET /v1/models` on Oct 17 2026.

---

## Part 1. The shared stack

### Inference: Crusoe Managed Inference

- Every model call goes to `https://api.inference.crusoecloud.com/v1` through the OpenAI Python SDK, wrapped by `gridtwin/llm.py` (retries on 429 and 503).
- Each role gets its own model, because Crusoe's rate limits are per model (30 requests per minute without a card, 600 with one).
- A concurrency governor (one async semaphore per model, plus a global spawn budget) sits in front of the client, so recursive fan-out queues instead of tripping 429s.
- System prompts and tool schemas stay byte-identical across calls, so Crusoe's prefix cache cuts latency and cost for the many small agent calls.

Models available to us:

| Model (Crusoe ID) | Family | What it is good at here |
|---|---|---|
| `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B` | Nemotron | Planning, long reasoning, writing reports |
| `nvidia/Nemotron-3.5-Lightning-30B-A3B` | Nemotron | Fast, cheap agent steps and tool calls at high fan-out |
| `nvidia/NVIDIA-Nemotron-3.5-Super-VL` | Nemotron | Image understanding: comparing crops, describing what a view shows |
| `nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B` | Nemotron | Image, video and audio input; quick checks and fallback vision |
| `openai/gpt-oss-120b` | gpt-oss | A different family for checking or arguing against Nemotron |
| `deepseek-ai/DeepSeek-V4-Pro`, `google/gemma-4-31b-it`, `zai-org/GLM-5.3`, `Qwen/Qwen3.8-27B` | others | Spares for rate-limit spreading |

### Decisions: Jev

- Jev (TypeSafe "System One") takes a JSON state plus named typed questions and returns calibrated probabilities, not prose.
- One `decide(state, questions)` client with three backends behind it: TypeSafe direct (`POST https://api.typesafe.ai/v1/systemone`, model `jev-latest`), OpenRouter (`typesafe/jev-1.14-20261014`), and OpenAI Decisions (`POST /v1/decisions`, model `gpt-6-luna`, the only one that accepts images).
- Last-resort fallback: the fraction of 5 Nemotron 3.5 Super VL samples that vote yes, shown in the UI as "local judge", never passed off as Jev.
- Every decision call is logged with its full input and output, which gives us the audit trail for free.

### Gaussian splat

- **Capture:** an iPhone with Scaniverse (trains on the phone, free, exports `.ply` or `.spz`), with Polycam or Luma as backups.
  Our Mac has no CUDA GPU, so we do not train splats on the laptop.
- **Viewer:** Spark (`@sparkjsdev/spark`, three.js based) in a Vite + TypeScript page.
- **Render bridge:** the visible viewer also listens on a WebSocket for `render(pose, fov, w, h)`, draws that pose to an offscreen target and returns a JPEG.
  Every render an agent asks for is drawn as a camera frustum flash, so the audience sees the agents looking.
  Backup: Puppeteer with `--use-angle=metal` on a second laptop.
- **Splat index:** the backend parses the splat once into numpy arrays (positions, colors, opacity, scales).
  Agents ask it for Gaussian counts and color variance inside any box, measure 3D distances, and get depth for any camera pose by projecting Gaussian centres into a z-buffer.
  This needs no WebGL, so depth works even if the render bridge does not.
- **Editing:** Spark's per-splat edits fade or hide Gaussians inside a box, which we use for "this object left" and for the Court's appeal.

### Backend and app

- Python, `asyncio` orchestrator, FastAPI with a WebSocket that streams events to the viewer.
- OpenCV and scikit-image for image diffs, numpy for geometry, `pupil-apriltags` for one-time camera calibration.
- Every spawn, render, model call, decision and merge is appended to `runs/<id>/events.jsonl`.
  The viewer is a pure function of that log, so any run can be replayed at stage speed if Wi-Fi dies.
- Risk to check at 10:45: the repo's venv is Python 3.14, and some wheels (OpenCV, AprilTag) may not exist for it yet; if not, rebuild the venv on Python 3.12.

### You.com

- You.com Search API (`YDC_API_KEY`) returns web results with URLs.
- In both concepts it brings outside facts into the record with a citation, such as a building or safety code.
- It matters much more to the Court than to Afterimage (details in each part).

### NVIDIA NemoClaw (optional in both)

- NemoClaw runs an always-on agent inside an NVIDIA OpenShell sandbox with network, file and process guardrails, and supports any OpenAI-compatible provider, so it can point at Crusoe.
- The honest cost: this Mac has no Docker or Colima, the install pulls a 2.4 GB sandbox image over venue Wi-Fi, it is alpha and "supported with limitations" on Apple Silicon, and it wraps OpenClaw, Hermes or LangChain Deep Agents rather than our own Python loop.
- What it would add: the story that the agent team runs sandboxed and unattended, with network access limited to Crusoe, OpenRouter and You.com.
- Recommendation: not on the critical path.
  If someone wants the NVIDIA angle, time-box it to one person, 11:00 to 11:45, on a second machine, and drop it at the first failure.

### Alike

- Alike stays the team's development memory (with `docs/MEMORY.md`), not part of the product.

---

## Part 2. Afterimage

### Pitch

"We scanned this scene this morning.
A judge secretly changes things in it.
A team of agents that has never seen the change finds every one, says what changed and where, and proves it looked everywhere."

The agents' team grows only where the evidence is uncertain: each region of the scene is an agent, and an uncertain region splits into eight smaller agents.
The splat is the agents' memory of how the world looked, and Jev decides, with calibrated probabilities, whether each region is clear, changed, or needs a closer look.

### Demo, beat by beat (3 minutes)

1. **0:00 Hook.** Fly through the morning splat of our tabletop scene, a small mock workspace with matte objects (a cable, a tool, a mug, a mini extinguisher, labelled boxes).
2. **0:15 The judge tampers.** We turn away; a judge changes up to three things in 20 seconds.
3. **0:35 One look.** A teammate takes one photo from each of five taped phone stations (about 30 seconds) and steps back.
4. **0:45 The tree grows.** Glowing boxes subdivide over the splat; regions go green when cleared and red when flagged, and the audience can see agents spending effort only where something happened.
5. **1:40 Explain.** Each flagged region spawns an inspector that writes a sentence such as "the cable moved about 15 cm toward the edge"; the splat fades the Gaussians where an object left and pins where something arrived.
6. **2:10 Reveal.** The judge reads their secret list; our card shows what we found, honestly including any miss.
7. **2:30 The number.** On 15 change sets staged during the day: precision, recall, and model calls used by the adaptive tree versus a uniform grid at the same finest resolution.
8. **2:50 Close.** "Site walk-downs, warehouse audits, security rounds: the world changes between looks, and these agents notice, explain, and prove they looked everywhere."

### System architecture

```
 10:45 one-time setup                            live run
 ---------------------                           --------
 iPhone + Scaniverse --> morning splat (.spz)    5 "after" photos from taped stations
 5 "before" photos from taped stations                       |
 AprilTag board in scene --> station poses (PnP, once)       |
            |                                                v
            v                                   +--------------------------+
   +-----------------+   pose -> JPEG    +------| DIFF STAGE (OpenCV)      |
   | SPARK VIEWER    |<------------------+      | before vs after photo    |
   | + render bridge |------------------>+      | per station: SSIM + color|
   | + octree overlay|   events (WS)            +------------+-------------+
   +--------+--------+                                       | change masks
            ^                                                v
            |                               +-------------------------------+
            |                               | 3D VOTING (splat index, numpy)|
            |                               | depth from z-buffer -> lift   |
            |                               | mask pixels -> vote into cells|
            |                               +---------------+---------------+
            |                                               | evidence per cell
            |                                               v
            |            +--------------------------------------------------+
            |            | SURVEYOR  Nemotron 3 Super                        |
            |            | sets budget, spawns 8 region agents, writes the   |
            |            | coverage certificate at the end                   |
            |            +---------------------+----------------------------+
            |                                  | spawn x8 (depth <= 3)
            |                  +---------------+----------------+
            |                  v                                v
            |        REGION AGENT  Nemotron 3.5 Lightning  ... (one per cell)
            |        reads evidence JSON, picks ONE action:
            |          split | closer_look | clear | flag
            |                  |                         ^
            |                  v                         | probabilities
            |        JEV decide(): route {clear, changed, split},
            |                      score change_magnitude
            |                  |
            |        closer_look -> render a tighter splat view (before)
            |                      + high-res crop of the station photo (after)
            |        flag  ------> INSPECTOR  Nemotron 3.5 Super VL
            |                      crop pair -> one-sentence explanation
            |                  |
            |                  v
            |        VERIFIER  gpt-oss-120b (different family)
            |        checks the coverage certificate: every cell cleared at
            |        enough depth or flagged; rejects a "nothing else changed"
            |        claim that is not backed by evidence
            |                  |
            +------------------+  splat edits (fade departed, pin arrived),
                                  report card, events.jsonl for replay
```

### How each piece of the stack is used

| Piece | Role in Afterimage |
|---|---|
| Crusoe | Serves every model call; the governor spreads load across four model families |
| Nemotron 3 Super | Surveyor: budget, spawning plan, coverage certificate, final report |
| Nemotron 3.5 Lightning | Region agents: one tool call per step choosing split, closer look, clear or flag; this is the high fan-out role |
| Nemotron 3.5 Super VL | Inspector: compares before and after crops and writes what changed |
| Nemotron 3 Nano Omni | Fallback inspector, and a quick "is a hand or person in this photo" check before diffing |
| gpt-oss-120b | Verifier: a different family checks the certificate, so its mistakes do not line up with the workers' |
| Jev | Decides clear, changed or split for every cell from the evidence JSON (pixel-diff scores, 3D vote counts, how many stations agree, current depth, budget left); its probabilities shape the agent tree |
| Gaussian splat | The agents' memory of the morning scene: depth to place a change in 3D, tighter views for a closer look, and the edited "after" scene |
| You.com | Optional stretch: for a flagged change to a safety item (an extinguisher removed, a cable across a path), the inspector cites the relevant rule, so the report says why it matters |
| NemoClaw | Optional: run the patrol as an unattended, sandboxed watcher; not on the critical path |

### Recursion, and how it stops

- The root covers the whole scene; a cell splits into eight children only when Jev's split probability is high and budget remains.
- It stops at depth 3, when every cell is cleared or flagged, or when the global budget (for example 120 model calls) is spent.
- A change that straddles two cells is merged by neighbour messages between sibling agents.
- The certificate lists every leaf with its verdict and evidence, which is what the verifier checks.

### Build plan (3 people, 10:45 to 5:00)

| Time | A: agents and Jev | B: capture and vision | C: viewer and render |
|---|---|---|---|
| 10:45-11:30 | Jev client on OpenRouter, agent runtime skeleton, event log | Build the diorama, tape the stations, Scaniverse capture (three takes, keep the best), "before" photos | Spark viewer loads the splat, render bridge returns a JPEG for a pose |
| 11:30-12:30 | Octree cells, spawn and budget rules, evidence schema | AprilTag PnP for station poses, before and after diff per station | Splat index, depth z-buffer, 3D voting into cells |
| 12:30 | **Go/no-go:** a moved object lands in the right cell, or we switch to the Court | | |
| 12:30-2:30 | Region agent actions, Jev routing, certificate, verifier | Inspector prompts on crop pairs, stage and photograph 15 change sets with written ground truth | Octree overlay animation, splat fade and pins, report card |
| 2:30-3:30 | Benchmark: adaptive tree against uniform grid | Lighting and false-alarm tuning, You.com stretch | Replay mode, polish |
| 3:30-4:50 | Freeze, rehearse the live tamper twice, record a backup run, README, submit | | |

### Bottlenecks

| # | Bottleneck | Check by | Fallback |
|---|---|---|---|
| 1 | Scaniverse capture quality on a small matte scene | 11:30 | Re-capture; worst case a public tabletop splat plus our own photos |
| 2 | Jev access (no key yet) | 11:30 | OpenRouter, then OpenAI Decisions, then the labelled local judge |
| 3 | Station poses in the splat frame (AprilTag PnP) | 12:15 | Hand-align each station once in the viewer and save the pose |
| 4 | A change placed in the right 3D cell | 12:30 | Switch to the Viewpoint Court, keeping all the shared pieces |
| 5 | A full run under about 60 seconds | 2:30 | Judge tampers just before our slot; live part runs on a smaller set; replay as backup |

### Why it scores

- **Technicality (40%):** depth-aware 3D voting, a recursive agent tree shaped by calibrated decisions, a coverage certificate checked by a different model family, and splat editing, all built today.
- **Live Demo (20%):** a judge makes the change, so it cannot be canned.
- **Creativity (25%):** the agents' team structure follows the geometry of a real scene and the evidence in it, which we have not seen.
- **Future Potential (15%):** "prove you looked everywhere" is a general way to evaluate autonomous inspection.

---

## Part 3. The Viewpoint Court

### Pitch

"Type any claim about a real place.
Two opposing agent teams argue it, but the only admissible evidence is a camera angle a witness rendered from the 3D scene itself.
Any piece of evidence can be challenged into a smaller trial, and a calibrated judge rules at every level, so the final verdict is a tree of checkable claims, not a paragraph."

### Demo, beat by beat (3 minutes)

1. **0:00** A splat of a real space, such as the venue's exit corridor or our area.
   A judge types a claim, for example "a wheelchair user can get from the door to the fire extinguisher".
2. **0:20** Prosecution and defense each spawn witnesses with assignments ("show the doorway width", "show the floor near the desk").
   Witness camera frustums fly through the scene and land; each view becomes a numbered exhibit pinned in 3D.
3. **0:50** Defense challenges exhibit 4 ("the cable is not on the path, the angle misleads").
   That challenge becomes a sub-trial on a narrower claim, with its own counsel, witnesses and judge, nested in the tree.
4. **1:40 The flip.** The sub-trial rules (p = 0.93 the cable is on the path, from a top-down view a witness chose after the side view was disputed); the ruling flows up and the top verdict slides from "likely true" to "likely false" live.
5. **2:15 The appeal.** A judge deletes the cable from the splat with one click; only exhibits whose camera view covered it are invalidated, only their trials re-run, and the verdict flips back.
6. **2:45** Click any node to see the claim, exhibits with camera poses, testimony, cited sources, and the exact Jev question and probability.

### System architecture

```
 judge types a claim
        |
        v
 +--------------------------+       +-------------------------------+
 | CLERK  Nemotron 3.5      |       | SPLAT INDEX + POSE GRAPH       |
 | Lightning: splits claim  |       | walkable poses at eye and      |
 | into checkable elements, |       | wheelchair height; 3D distance |
 | checks sub-claims narrow |       | measurement (metric scale from |
 +------------+-------------+       | an AprilTag of known size)     |
              |                     +---------------+---------------+
              v                                     ^
 +------------------------------------------------- | ------------+
 | TRIAL(claim, depth)                              |             |
 |                                                  |             |
 |  PROSECUTION counsel        DEFENSE counsel      |             |
 |  Nemotron 3 Super           gpt-oss-120b         |             |
 |      | spawn 2-3                | spawn 2-3       |             |
 |      v                          v                 |             |
 |  WITNESS (visual)            WITNESS (visual) ---+  pose ->    |
 |   pose pick: Lightning        overview grid, refine 1-2 views  |
 |   testimony: Super VL (sees only pixels + its assignment)      |
 |                                                                |
 |  WITNESS (documentary)  Lightning + You.com search             |
 |   brings a cited standard into the record                      |
 |                                                                |
 |  JUDGE  Jev decide(): claim_holds, challenge_admissible,       |
 |         needs_subtrial  (JSON record only, no pixels)          |
 |      | needs_subtrial > 0.6 and sub-claim strictly narrower     |
 |      v                                                         |
 |  SUB-TRIAL(narrower claim, depth + 1)  ... up to depth 3       |
 |      | ruling flows back up as evidence; parent judge re-rules |
 +------+---------------------------------------------------------+
        |
        v
 SPARK VIEWER: exhibit pins, witness frustums, trial tree panel,
 probability bars; APPEAL: delete Gaussians -> invalidate exhibits whose
 frustum covers them (splat index) -> re-run only those subtrees
 events.jsonl -> replay
```

### How each piece of the stack is used

| Piece | Role in the Court |
|---|---|
| Crusoe | Serves every model call; counsel, witnesses and clerk are on different models to spread load |
| Nemotron 3 Super | Prosecution counsel: plans the case, assigns witnesses, files challenges |
| gpt-oss-120b | Defense counsel: a different family, so the two sides do not think alike and cannot collude |
| Nemotron 3.5 Lightning | Clerk (splits the claim, checks a sub-claim is narrower) and witness pose selection |
| Nemotron 3.5 Super VL | Visual witness testimony: describes only what its rendered views show |
| Nemotron 3 Nano Omni | Fallback testimony model and a cheap "is this view useful or inside a wall" filter |
| Jev | The judge at every node: rules on the written record (exhibits, testimony, citations, sub-trial rulings) and decides when a challenge becomes a sub-trial |
| OpenAI Decisions | Optional image-aware ruling on the top-level verdict, if we get access |
| Gaussian splat | The only source of visual evidence: every exhibit is a stored camera pose anyone can re-render; the appeal edits the splat itself |
| You.com | Documentary witnesses: a counsel can call one to find the governing rule (a minimum clear doorway width, a maximum extinguisher mounting height), and the citation enters the record next to the measured value |
| NemoClaw | Optional, same as Afterimage; not on the critical path |

The original concept had DeepSeek V4 for defense; we moved prosecution to Nemotron 3 Super so Nemotron carries the planning, decomposition and vision roles, which helps the Best Use of Nemotron prize.

### Recursion, and how it stops

- A challenge becomes a sub-trial only if Jev rates `needs_subtrial` above 0.6 and the sub-claim covers a smaller region or a single object than its parent.
- A trial ends when the judge's probability leaves the 0.15 to 0.85 band, when neither side challenges, at depth 3, or when the global budget (60 renders) is spent.
- After the appeal, only trials that depended on invalidated exhibits re-run, which is incremental recomputation over the tree.

### Build plan (3 people, 10:45 to 5:00)

| Time | A: viewer and splat | B: trial engine and Jev | C: witnesses |
|---|---|---|---|
| 10:45-12:15 | Capture the scene, Spark viewer, render bridge, exhibit pins and frustums | Trial data model, event log, Jev client with fallbacks | Pose graph from the splat index, witness pose choice and testimony prompts |
| 12:15-2:00 | Trial tree panel and probability bars | Counsel loop and judging at depth 1, end to end | Challenges, sub-trials, verdict flowing up; You.com documentary witness |
| 2:00-3:30 | Node inspector (claim, exhibits, Jev question and answer) | Appeal: Gaussian delete, frustum invalidation, partial re-run | Find and rehearse 3 claims that genuinely branch on this scene |
| 3:30-4:50 | Freeze, dress rehearsals, record the replay, README, submit | | |

### Bottlenecks

| # | Bottleneck | Check by | Fallback |
|---|---|---|---|
| 1 | Jev access | 11:30 | OpenRouter, then the labelled local judge |
| 2 | Render bridge returns a JPEG for a pose on the Mac | 12:15 | Batch-render about 80 named poses once; witnesses pick by index |
| 3 | Witness views that show nothing useful | 12:15 | Walkable pose graph at eye and wheelchair height; Omni filter |
| 4 | Trees that do not branch (testimony too generic) | 2:30 | Pick claims that are genuinely ambiguous from some angles; let the judge pick the claim live |
| 5 | A depth-2 trial finishes in under 90 seconds | 2:30 | Cached testimony by pose for rehearsed claims; replay plus a live appeal, which needs only a few calls |

### Why it scores

- **Technicality (40%):** a recursive adversarial protocol, a pose graph from the splat, incremental invalidation after splat edits, and verdicts propagating up a tree.
- **Live Demo (20%):** the verdict flip and the appeal flip are easy to follow; the judge types the claim, so it is not canned.
- **Creativity (25%):** debate between models exists on paper, but grounding every argument in viewpoints the agents chose themselves is new; this is the score most at risk.
- **Future Potential (15%):** a scalable way to oversee agents, where every claim is checkable by anyone.

---

## Part 4. Side by side

| | Afterimage | Viewpoint Court |
|---|---|---|
| Live proof it is real | A judge changes the physical scene | A judge types the claim and edits the splat |
| Hardest part | Placing a change in the right 3D cell | Witnesses that produce testimony worth challenging |
| Creativity risk | Low | Medium (debate is a known research idea) |
| Build risk | Higher (registration, lighting) | Lower (one capture, everything on screen) |
| You.com | Stretch only | Load-bearing (cited standards) |
| Nemotron roles | Surveyor, region agents, inspector, fallback vision | Prosecution, clerk, pose choice, testimony, fallback vision |
| Council pick | Architect, Skeptic | Pragmatist, Critic |
