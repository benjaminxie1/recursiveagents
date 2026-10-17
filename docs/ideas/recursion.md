# Recursion as the novelty: four concepts

Lens: the shape of the recursion is the product.
Each concept below avoids the planner / worker / verifier / supervisor hierarchy.
Instead, the recursion tree is shaped by space, by counterfactual worlds, by argument, or by a market, and the tree itself is drawn inside the Gaussian splat on stage.

Weighted score formula used throughout: Technicality x 0.40 + Live Demo x 0.20 + Creativity x 0.25 + Future Potential x 0.15.

## Shared infrastructure (build once, used by every concept)

These pieces are common to all four concepts, so whichever concept wins, the first 90 minutes look the same.

- **Render bridge.** The stage browser is the render farm.
  The visible Spark viewer listens on a WebSocket for `render(pose, fov, w, h)` requests, renders that pose into an offscreen `WebGLRenderTarget`, and returns a JPEG.
  This avoids headless WebGL entirely and means every render an agent asks for is also something the audience can see (as a frustum flash in the scene).
  A Puppeteer instance with `--use-angle=metal` on a second laptop is the backup render worker.
- **Splat index.** On load, the backend parses the `.ply` / `.spz` once into numpy arrays (positions, colors, opacity, scales).
  Agents query it for Gaussian counts, density, and color variance inside any axis-aligned box without rendering anything.
  This is cheap and lets recursion decisions use geometry, not only pixels.
- **Jev client.** A single `decide(state, questions)` function with three backends behind one interface: TypeSafe direct, OpenRouter `typesafe/jev-1.14-20261014`, and a local fallback (described per concept).
  Every call is logged with its inputs and returned probabilities into the event log, so the audit trail is free.
- **Event log and replay.** Every spawn, render, model call, Jev call, and merge is an append-only JSONL event.
  The viewer is a pure function of the event log, so a recorded run can be replayed at stage speed if Wi-Fi dies.
- **Concurrency governor.** One global async semaphore per model (start at 40 in flight, well under the ~600 RPM limit), plus a global spawn budget.
  Children never call models directly; they enqueue, so fan-out cannot explode the rate limit.

---

## Concept 1: Octree of Minds

**Hook:** One agent is handed a whole room and told "find what matters here"; it splits itself into child agents only where the room turns out to be interesting, and you watch a living octree of minds grow toward the details.

### Demo, beat by beat (3 minutes)

- **0:00 - 0:20.** A photoreal splat of a cluttered workshop (or the hackathon venue corner) fills the screen.
  One translucent cube encloses the whole scene, labelled `root: "Inventory every safety hazard in this room."`
- **0:20 - 0:50.** The root agent renders six outward-facing views (the audience sees six camera frustums flash), sends them to the vision model, and asks Jev "which octants deserve their own agent?".
  Three of eight octants light up with spawn probabilities (0.91, 0.78, 0.64); the other five gray out with their probabilities shown (0.08, 0.12, ...).
  Three child cubes appear and each child gets a smaller copy of the parent goal, scoped to its box.
- **0:50 - 1:40.** The tree deepens in real time.
  Cubes subdivide around a workbench, then around a power strip on the bench, then around one frayed cable.
  Empty floor and blank walls never split; the audience can see the compute going where the detail is.
  A side panel shows the recursion tree as a 2D graph mirrored from the 3D one, with each node's spend in tokens and renders.
- **1:40 - 2:10. The wow moment.** A teammate asks an audience member to point at any spot in the scene.
  We drop a small pre-captured splat asset (a lit cigarette, a knife, a puddle) into that exact location, live, by appending its Gaussians to the point set.
  The octree, which had already settled, notices: the leaf owning that region gets a "world changed" event, Jev's re-open probability spikes, and a new branch grows straight down to the planted object within about 20 seconds.
- **2:10 - 2:40.** Leaves report up.
  Parents merge children's findings, deduplicate hazards seen from two boxes, and the root produces a ranked hazard list where every item is a clickable pin that flies the camera to the evidence view the leaf actually used.
- **2:40 - 3:00.** Closing stat: "312 Gaussian-region checks, 41 agents, 9 levels deep, 0 human inputs; 78 percent of compute went to 6 percent of the volume."

### Why judges have not seen it

The closest existing things are Recursive Language Models (Zhang and Khattab, 2025), which recursively split a long text context across sub-calls, and next-best-view planning in robotics, which picks camera poses to reduce uncertainty.
Octree level-of-detail is standard in splat renderers.
Nobody has combined them: an adaptive octree where every node is an LLM agent with its own goal, and the split decision is a calibrated probability rather than a fixed density threshold.
It is RLM recursion with space as the context window.

### Load-bearing ingredients

- **Splat:** the splat is the only input; agents get their perception by rendering novel viewpoints from inside their own box, and the live Gaussian insertion is the wow moment.
- **Jev:** every split, stop, and re-open is a typed Jev call (`split_octant_k: noul`, `hazard_present: noul`, `confidence_enough: score`), so the octree shape is literally the shape of the calibrated probability field.
- **Recursion:** each child is a smaller self-similar copy of the parent with a scoped goal and a scoped box; depth is unbounded in principle and bounded by Jev and budget in practice.

### Architecture sketch

- **RegionAgent(box, goal, depth, budget).** The only agent class.
  Loop: query splat index for Gaussian stats in the box, request 2 to 6 renders from poses inside or at the face of the box, describe the views with the vision model, ask Jev the split and finding questions, then either spawn children or emit leaf findings.
- **Models.**
  Nemotron 3 Nano Omni for per-view perception (fast, vision input).
  Nemotron 3.5 Super VL as the escalation model when a leaf's finding confidence is in the 0.4 to 0.6 band.
  Nemotron 3.5 Lightning 30B for merging children's reports and writing child goals.
- **Split geometry.** Not a naive 8-way octree; children are the octants whose Gaussian count exceeds a floor (empty octants are pruned without any model call), then Jev ranks the survivors.
- **Termination.**
  A node stops when Jev `p(split worth it)` falls below a threshold that rises with depth (0.5 at depth 1, 0.8 at depth 6), when its box is smaller than 5 cm, when the box holds fewer than 2,000 Gaussians, or when its budget slice runs out.
  Budget is conserved: a parent splits its remaining budget among children in proportion to their Jev spawn probabilities.
  Global hard cap of 64 live agents.
- **Re-open.** A world-change event (Gaussians inserted or removed) is routed to the leaf whose box contains them; that leaf becomes active again with a fresh small budget.
- **Data flow.** Python asyncio orchestrator, render bridge to browser, event log to the viewer over WebSocket, viewer draws boxes (color by state: thinking, split, leaf, pruned), frustums, and pins.

### 6-hour build plan (3 engineers)

- **10:45 - 12:15.**
  A: splat loading, viewer, box and frustum overlays, render bridge.
  B: splat index (ply parse, box queries), event log, replay mode.
  C: Jev client with all three backends, model client with per-model semaphores, prompt for perception.
- **12:15 - 14:00.**
  A: tree side panel and pins with camera fly-to.
  B: RegionAgent loop end to end on depth 1, then recursion.
  C: tune Jev questions and thresholds on the real scene; record a first full run.
- **14:00 - 15:30.**
  A: live Gaussian insertion (append a small asset splat at a clicked point).
  B: world-change routing and re-open.
  C: merge and dedupe logic, ranked final report.
- **15:30 - 16:30.** Three full dress-rehearsal runs, record the best one as replay fallback, fix the ugliest visual glitch.
- **16:30 - 17:00.** Freeze, write submission, rehearse the 3 minutes twice.

### Build bottlenecks

1. **Splat acquisition.** A good room capture can take an hour to train.
   Mitigation: capture with Scaniverse or Polycam on a phone in the first 15 minutes (on-device processing, no laptop training), export `.ply` or `.spz`.
   Fallback: a public Mip-NeRF 360 scene (`room`, `counter`, `kitchen`) with a pretrained 3DGS checkpoint, which already has hazard-like clutter.
2. **Render latency and pose quality.** Renders from inside a tiny box can be degenerate (camera inside a Gaussian blob).
   Mitigation: place cameras just outside the box looking in, at a distance proportional to box size, and reject renders whose mean alpha is below a threshold.
   Fallback: a precomputed library of 200 poses on a sphere per level, and agents pick the nearest.
3. **Vision latency on fan-out.** Forty leaves times four views at 2 to 4 seconds each is the critical path.
   Mitigation: batch the four views into one multi-image call where the model allows it, cap views per node, and start the demo run 60 seconds before speaking so the tree is mid-growth when the talk starts.
   Fallback: cache perception results by (box, pose) hash so a rehearsed run replays instantly.
4. **Jev access.** Signups were reportedly paused on Sep 22.
   Mitigation: register the OpenRouter route in the morning and test it before 11:00.
   Fallback: a "Jev-shaped" local backend where Nemotron 3.5 Lightning answers each typed question with a single token and we read token logprobs (if Crusoe exposes `logprobs`) or sample 5 times and use the vote fraction; label it honestly in the UI.
5. **Fan-out explosion.** A noisy perception call can make every octant look interesting.
   Mitigation: budget conservation plus the depth-rising threshold make the total bounded by construction; the 64-agent cap is the last line.
   Fallback: a demo flag that fixes the max depth at 5.

### Self-score

| Criterion | Score | Why |
| --- | --- | --- |
| Technicality (40%) | 8 | Adaptive spatial recursion, render bridge, splat index, live editing, and budget conservation are well beyond an average hack. |
| Live Demo (20%) | 8 | The growing octree is legible in one glance and the planted-object beat is interactive. |
| Creativity (25%) | 7 | Octree of agents is new, but "agents search a scene for things" is a familiar task. |
| Future Potential (15%) | 7 | Clear story for adaptive compute allocation and RLM-style recursion over non-text worlds. |
| **Weighted** | **7.60** | |

---

## Concept 2: Forking Worlds

**Hook:** Agents do not imagine counterfactuals, they fork the actual 3D world: every "what if we moved this?" becomes a child agent living in its own edited copy of the splat, which can fork again, and the winning branch merges back into reality on stage.

### Demo, beat by beat (3 minutes)

- **0:00 - 0:20.** A splat of a real room (a cramped apartment living room or the venue's lounge).
  The goal is typed live: "Make this room safe for a toddler and still usable for a wheelchair, without buying anything."
- **0:20 - 0:50.** The root agent renders the room, identifies movable objects (pre-segmented clusters: coffee table, floor lamp, bookshelf, rug, chair), and asks Jev which interventions are worth exploring.
  It forks three child worlds.
  The screen splits into a "multiverse strip": the main view plus three thumbnail viewports, each a live render of a different edited copy of the room (lamp moved behind the sofa; coffee table rotated against the wall; bookshelf contents lowered).
- **0:50 - 1:40.** Children recurse on their own worlds.
  World B (table against the wall) discovers that this now blocks the wheelchair path to the window, so it forks two grandchildren that try fixing that inside its own world.
  World A is pruned: Jev's `p(safe for toddler)` for its renders stays at 0.31, and its thumbnail fades to a ghost.
  A 3D tree of worlds floats above the room; each node is a tiny live splat render of that world, so the tree is made of rooms.
- **1:40 - 2:15. The wow moment.** Two surviving branches made compatible edits (one moved the lamp, one cleared the wheelchair path).
  The merge agent composes them into a single world, re-renders it from the "toddler eye height" camera (50 cm) and a "wheelchair path" camera sequence, and Jev confirms both criteria.
  The main view then physically animates the furniture Gaussians from the original positions to the merged arrangement, and the original room rearranges itself in front of the audience.
- **2:15 - 2:45.** Toggle "ghosts": the losing branches render as translucent overlays in the same room, showing every counterfactual explored, each labelled with the Jev verdict that killed it.
- **2:45 - 3:00.** "17 worlds forked, 6 pruned on calibrated verdicts, 2 merged; every decision is replayable from the audit log."

### Why judges have not seen it

The closest existing things are Tree of Thoughts and MCTS over LLM plans, where branches are text, and world-model planning (Dreamer, Genie-style video world models), where branches are learned latent rollouts.
Interior design generators exist, but they produce one answer image.
Here the branches are actual, inspectable, editable 3D worlds, and each agent's perception comes from rendering its own world, so a fork is a real copy-on-write edit of reality rather than a paragraph.
"Recursion over worlds" with a merge step that composes edits from different branches is, as far as we know, unshown.

### Load-bearing ingredients

- **Splat:** every fork is a copy-on-write edit of the Gaussian point set (transform a cluster), and every evaluation is a rendered viewpoint from inside that edited world; without editable splats there is nothing to fork.
- **Jev:** Jev scores each world against typed criteria (`toddler_safe: noul`, `wheelchair_path_clear: noul`, `aesthetic_cost: score`) and decides `fork`, `prune`, or `merge`, so branch life and death is calibrated and auditable rather than vibes.
- **Recursion:** each child agent owns a world, can fork its own world into grandchildren, and the tree's leaves merge back up by composing compatible edits; the recursion tree is literally a tree of worlds.

### Architecture sketch

- **World.** An immutable base splat plus an ordered list of edits `(cluster_id, transform)`.
  Forking appends an edit; rendering a world applies its transforms to the cluster's Gaussians on the GPU via a per-cluster matrix uniform in the viewer (Spark supports per-object transforms if each cluster is loaded as its own splat mesh).
- **Clusters.** Pre-segmented in the morning: crop each movable object into its own `.ply` by hand-drawn bounding box in SuperSplat (about 10 minutes for 6 objects), with the room minus those objects as the static base.
- **WorldAgent(world, goal, depth).**
  Renders 3 to 5 criterion-specific cameras (toddler eye height, wheelchair path sweep, overview), describes them with Nemotron 3 Nano Omni, asks Jev the criteria, then asks gpt-oss-120b to propose up to 3 edits as typed JSON (`{cluster, dx, dy, dz, yaw}`), validated by a cheap collision check against the splat index (no Gaussian overlap above a threshold, objects stay on the floor plane).
  Jev then ranks the proposed edits by `p(improves goal)` and the top k become child worlds.
- **MergeAgent.** Takes two leaf worlds, checks edit compatibility (disjoint clusters, no collision when both applied), composes them, and sends the composed world back through evaluation.
  Run by DeepSeek V4 or GLM 5.3 for the reasoning step.
- **Termination.**
  A world is pruned when Jev's criteria product falls below its parent's (no improvement), when depth reaches 3, or when the global world budget (24 worlds) is spent.
  A world is accepted when all criteria exceed 0.85.
  Merge runs only between accepted or near-accepted leaves.
- **Data flow.** Orchestrator owns the world tree; the render bridge accepts `render(world_id, pose)`, applies that world's transforms, renders, and resets.
  The viewer subscribes to the event log for the multiverse strip, the tree of worlds, and the final animation.

### 6-hour build plan (3 engineers)

- **10:45 - 12:15.**
  A: capture or load the room, segment 5 to 6 movable clusters in SuperSplat, load base plus clusters as separate splat meshes, per-cluster transforms.
  B: World data model, render bridge with `world_id`, collision check over the splat index.
  C: Jev client and fallbacks, criteria definitions, perception prompts for each criterion camera.
- **12:15 - 14:00.**
  A: multiverse strip (multiple viewports rendering different worlds from one scene graph).
  B: WorldAgent fork and prune loop end to end, depth 2.
  C: edit-proposal prompt with typed JSON and validation; tune criteria on real renders.
- **14:00 - 15:30.**
  A: tree-of-worlds visualization (nodes are small cached renders), ghost overlays.
  B: MergeAgent and composition.
  C: final merge animation (interpolate transforms), end-to-end rehearsal run.
- **15:30 - 17:00.** Rehearse, record replay, fix visual polish, submission.

### Build bottlenecks

1. **Object segmentation of the splat.** Automatic 3D segmentation (SAGA, Gaussian Grouping) will not happen in 6 hours.
   Mitigation: hand-crop clusters with bounding boxes in SuperSplat in the first hour, and pick a room with well-separated furniture.
   Fallback: insert movable objects as separately captured small splats (a chair, a lamp) into a static room, which makes clusters clean by construction.
2. **Holes left behind when furniture moves.** Moving a cluster reveals an unreconstructed void.
   Mitigation: choose objects against walls or on a rug; fill the vacated floor footprint with a duplicated, offset patch of floor Gaussians.
   Fallback: accept the holes and render them with a subtle outline, which honestly reads as "this is an edit".
3. **Many worlds, one GPU.** Rendering several edited worlds per second in one browser.
   Mitigation: a world is only a transform list, so switching worlds is setting six uniforms; render to small 384 px targets for the thumbnails.
   Fallback: cache one render per world per camera and only render live for the main view.
4. **Edit proposals that are physically absurd.** LLM-proposed transforms can float a table or push it through a wall.
   Mitigation: the edit schema is constrained (floor plane translation plus yaw only) and the collision check runs before any child spawns.
   Fallback: a discrete action menu per cluster (8 candidate placements precomputed), and the LLM picks indices.
5. **Rate limits and Jev access.** The project notes say 30 RPM per model without a card and 600 RPM with one.
   Mitigation: put a card on file before 11:00, spread perception, proposal, and merge across three different models, and verify the OpenRouter Jev route early.
   Fallback: local Jev-shaped backend via vote fraction over 5 short samples, clearly labelled.

### Self-score

| Criterion | Score | Why |
| --- | --- | --- |
| Technicality (40%) | 9 | Copy-on-write world forks, multi-world rendering, collision-checked edits, and branch composition are hard to recreate. |
| Live Demo (20%) | 7 | The rearranging room is a strong wow, but there are more moving parts that can fail on stage. |
| Creativity (25%) | 9 | A recursion tree whose nodes are rooms is genuinely new. |
| Future Potential (15%) | 8 | Grounded counterfactual planning in reconstructed real spaces is a direct step toward agents that act on physical environments. |
| **Weighted** | **8.45** | |

---

## Concept 3: The Viewpoint Court

**Hook:** A claim about a real place goes on trial; prosecution and defense recursively spawn witness agents who may only testify with a camera angle they rendered themselves, any testimony can be challenged into a sub-trial, and a calibrated judge rules at every level.

### Demo, beat by beat (3 minutes)

- **0:00 - 0:20.** A splat of a real space (a server room, a kitchen, the venue's emergency exit corridor).
  The claim is typed by an audience member from a short menu or freely: "Someone in a wheelchair can get from the door to the fire extinguisher."
- **0:20 - 0:50.** Two counsel agents open.
  Prosecution argues the claim is false and spawns three witnesses with assignments ("show the doorway width", "show what is on the floor near the desk", "show the extinguisher mounting height").
  Defense spawns its own witnesses.
  Each witness flies a camera through the splat; the audience sees witness frustums move and land, and each landed view becomes an "exhibit" pinned in 3D with a number.
- **0:50 - 1:40.** Cross-examination recurses.
  Defense challenges prosecution exhibit 4 ("that cable is not on the path, the angle is misleading").
  That challenge becomes a sub-trial on a narrower claim ("the cable lies on the shortest path"), with its own counsel and witnesses, nested inside the main courtroom.
  The trial tree floats in the scene as a stack of courtrooms; each node shows the claim text and a live probability bar.
- **1:40 - 2:15. The wow moment.** A sub-sub-trial resolves (the cable is on the path, p = 0.93, from a top-down exhibit a witness chose after the side view was disputed).
  The verdict propagates up: the sub-trial's ruling changes the evidence available to its parent, the parent's probability bar slides from 0.55 to 0.18, and the top-level verdict flips from "likely true" to "likely false" live, with the decisive exhibit highlighted in the scene.
  Then an engineer deletes the cable Gaussians from the splat (one click), files an appeal, and only the affected subtree is retried; the verdict flips back.
- **2:15 - 2:45.** Click any node: the full record shows claim, exhibits with camera poses, testimony, the exact Jev question and its probability.
  Point out that the final answer is a tree of grounded, falsifiable claims, not a paragraph.
- **2:45 - 3:00.** "A claim about the physical world, decomposed into 23 sub-claims, every one grounded in a viewpoint anyone can re-render."

### Why judges have not seen it

The closest existing things are AI Safety via Debate (Irving, Christiano, Amodei, 2018), recursive and "doubly efficient" debate protocols (Brown-Cohen, Irving, Piliouras, 2023), and multi-agent debate for factuality (Du et al., 2023).
Those debates are over text, and their recursion is mostly theoretical; demos show two models arguing in a chat log.
Here every claim is grounded in a rendered viewpoint of a real place, the adversaries choose their own camera angles (so evidence selection is part of the game), sub-claims recurse into nested trials, and the judge is a calibrated probability API instead of another LLM.
We have not seen recursive debate run as a live, spatially grounded, auditable protocol.

### Load-bearing ingredients

- **Splat:** testimony is only admissible as a rendered viewpoint with a stored camera pose, so evidence is reproducible, adversaries can re-render the same spot from a different angle to rebut, and the appeal beat edits the splat itself.
- **Jev:** Jev is the judge at every node, answering `claim_holds: noul` given the exhibits and testimony, plus `challenge_admissible: noul` and `needs_subtrial: noul`, so every ruling is a calibrated number with a logged input state.
- **Recursion:** a challenge to any piece of evidence spawns a full sub-trial (counsel, witnesses, judge) on a narrower claim; the trial tree is self-similar and verdicts propagate upward.

### Architecture sketch

- **Trial(claim, depth).** Holds two Counsel agents and a list of Exhibits.
- **Counsel(side).** gpt-oss-120b for prosecution and DeepSeek V4 for defense (different model families reduce collusion and make the adversarial split real).
  Each counsel spawns 2 to 3 Witness agents with a viewpoint assignment, and after the exhibits land, may file at most one challenge per round.
- **Witness(assignment).** Picks a camera pose by first rendering a coarse overview grid (4 views), choosing which one to refine via a small model call, then rendering 1 to 2 close views; testimony is generated by Nemotron 3.5 Super VL from the images only.
  A witness cannot see text the counsel wrote except its assignment, which keeps testimony grounded in pixels.
- **Judge.** Jev, with state `{claim, exhibits:[{id, pose, testimony, side}], subtrial_rulings:[...]}`.
  For the image-aware variant, the OpenAI Decisions API path can receive the exhibit images directly.
- **Recursion rule.** A challenge becomes a sub-trial only if Jev rules `needs_subtrial` above 0.6 and the sub-claim is strictly narrower (checked by asking Nemotron 3.5 Lightning whether the sub-claim mentions a smaller region or a single object, plus a region-of-interest box that must be smaller than the parent's).
- **Termination.**
  A trial ends when the judge's probability is outside [0.15, 0.85], when both counsel decline to challenge, at depth 3, or when the global witness budget (60 renders) is spent.
  Sub-trial rulings enter the parent's state as evidence, and the parent judge re-rules.
- **Appeal.** Splat edits invalidate every exhibit whose camera frustum intersects the edited Gaussians (checked against the splat index), and only trials depending on those exhibits re-run.
  This is incremental recomputation over the trial tree, which is the engineering depth judges will appreciate.

### 6-hour build plan (3 engineers)

- **10:45 - 12:15.**
  A: splat viewer, render bridge, exhibit pins with numbered labels, frustum visualization.
  B: trial data model, event log, Jev client with fallbacks.
  C: witness pose selection and testimony prompt, tested on the scene.
- **12:15 - 14:00.**
  A: trial-tree visualization (stacked courtrooms in 3D, plus a 2D tree panel).
  B: counsel loop and judging, depth 1 end to end.
  C: challenges and sub-trials, verdict propagation.
- **14:00 - 15:30.**
  A: node inspector (claim, exhibits, Jev question and answer).
  B: appeal path: Gaussian deletion, frustum-intersection invalidation, partial re-run.
  C: pick and rehearse 3 claims that produce interesting trees on the chosen scene.
- **15:30 - 17:00.** Dress rehearsals, record replay, submission.

### Build bottlenecks

1. **Witness views that are unhelpful.** Freely chosen poses can be inside walls or pointing at nothing.
   Mitigation: witnesses choose from a precomputed walkable pose graph (sampled at eye height and wheelchair height along free space in the splat index), then refine yaw and pitch only.
   Fallback: a fixed set of 80 named poses, and witnesses pick by index.
2. **Trees that do not branch.** If counsel never challenge, the recursion never shows.
   Mitigation: rehearse claims that are genuinely ambiguous from some angles; require each counsel to file one challenge in round 1 if any opposing exhibit has testimony confidence under 0.7.
   Fallback: a "contested claim" preset known to produce a depth-3 tree.
3. **Jev without images.** Jev takes JSON state, so the judge sees testimony text, not pixels.
   Mitigation: this is a feature for the protocol (the judge rules on the record), but we also run the OpenAI Decisions API path with images for the top-level verdict if access works.
   Fallback: local Jev-shaped judge via Nemotron 3.5 Super VL vote fraction over 5 samples, labelled.
4. **Vision latency.** 60 witness renders and 60 testimony calls.
   Mitigation: witnesses run in parallel inside each round, and rounds overlap across sibling sub-trials.
   Fallback: cached testimony keyed by pose hash for the rehearsed claims.
5. **Wi-Fi and rate limits on stage.** Mitigation: phone hotspot, card on file for 600 RPM, models spread across roles.
   Fallback: replay of the rehearsed run from the event log, plus a live appeal against the cached tree, which only needs a few calls.

### Self-score

| Criterion | Score | Why |
| --- | --- | --- |
| Technicality (40%) | 8 | Recursive adversarial protocol, pose graph, incremental invalidation after splat edits, and verdict propagation. |
| Live Demo (20%) | 8 | The verdict flip and the appeal flip are dramatic and easy to follow. |
| Creativity (25%) | 8 | Recursive debate exists on paper, but grounded in self-chosen viewpoints of a real place is new. |
| Future Potential (15%) | 9 | A scalable-oversight protocol where every claim is checkable; a direct contribution to how we evaluate agents. |
| **Weighted** | **8.15** | |

---

## Concept 4: The Spawn Exchange

**Hook:** No agent is allowed to spawn a child for free; every sub-agent is a contract bought on an internal market at a price set by its calibrated odds of success, contractors can subcontract, lineages that bid dishonestly go bankrupt, and parents rewrite the prompts of failing lineages, so the team learns to be calibrated while you watch.

### Demo, beat by beat (3 minutes)

- **0:00 - 0:20.** A splat of a cluttered home office.
  Goal: "Produce an insurance inventory of this room: every item over 50 dollars, with an estimated replacement cost and the photo that proves it."
  The root agent holds a wallet of 10,000 credits, where 1 credit is 100 real tokens of model usage, measured from the API `usage` field.
- **0:20 - 0:50.** The root posts bounties on an order book that is drawn as a ticker beside the scene: "items on the desk: 1,800 credits", "items on the shelf: 1,500", "electronics brand ID: 900".
  Candidate contractor agents bid; each bid is a Jev call returning `p(deliver verified)` plus a cost estimate, and the root accepts a bid only when bounty x p minus cost is positive.
  Accepted contracts spawn as agents, drawn as glowing markers in the region of the scene they are working on, with a thread back to their parent.
- **0:50 - 1:40.** Contractors subcontract.
  The desk contractor posts its own smaller bounties ("identify the monitor model", "is that a mechanical keyboard?") and passes a slice of its bounty down.
  The recursion tree is a supply chain of contracts, and every edge shows money flowing down and verified deliveries flowing up.
- **1:40 - 2:15. The wow moment.** Delivery is checked against a held-out viewpoint: the clearing house renders the claimed item from a camera the contractor never saw, and Jev rules `p(claim consistent with held-out view)`.
  One lineage has been bidding 0.9 and delivering 50 percent of the time.
  Its calibration chart (bid probability vs actual delivery) on screen is far off the diagonal; it goes bankrupt, its subtree is liquidated, and its parent rewrites the child prompt (the diff is shown: "you overclaim brand identification from logos smaller than 20 pixels; bid lower or request a closer render").
  The next generation of that lineage bids 0.55, delivers 0.6, and the calibration curve visibly snaps toward the diagonal.
- **2:15 - 2:45.** The final inventory: every row is a pin in the scene with its proof render, cost estimate, the contract chain that produced it, and the credits it cost.
- **2:45 - 3:00.** "Total spend 6,240 credits of a 10,000 budget; the market, not a hard-coded depth limit, decided when to stop recursing."

### Why judges have not seen it

The closest existing things are the Contract Net Protocol (Smith, 1980), Eric Baum's Hayek machine (economies of agents in the 1990s), and recent agent marketplace or "agent economy" prototypes where agents pay each other for tool calls.
Prompt-evolution systems (Promptbreeder, EvoPrompt) rewrite prompts based on a fitness score.
What is new is the combination: calibrated probability bids from Jev as the price signal, verification by held-out 3D viewpoints that the contractor cannot have seen (so the market cannot be gamed by restating the evidence), and lineage-level calibration feeding prompt rewrites, all inside a recursive spawn tree where the economics are the termination condition.

### Load-bearing ingredients

- **Splat:** the splat makes held-out verification possible, because any claim about an object can be checked by rendering a new camera the contractor never had, which is what keeps the market honest.
- **Jev:** every bid is a calibrated Jev probability, every delivery ruling is a Jev call, and the whole market runs on the gap between predicted and realized probability, so calibration is not decorative, it is the currency.
- **Recursion:** contractors subcontract with a slice of their own bounty, so the tree grows exactly as deep as it is profitable to grow, and liquidation prunes a whole subtree at once.

### Architecture sketch

- **ClearingHouse.** The single trusted component: holds wallets, the order book, the held-out viewpoint set per region (sampled at startup and hidden from all agents), and the ledger.
- **Agent(region, goal, wallet, prompt_genome).**
  Perceives through renders it pays for (each render and each model call is debited at measured token cost).
  Bids on bounties via Jev with state `{bounty, region_stats, own_lineage_brier, recent_outcomes}` and questions `p_deliver: noul`, `cost_estimate: score`.
  Posts sub-bounties when Jev `p(subcontracting beats doing it myself)` exceeds 0.6.
- **Models.**
  Nemotron 3 Nano Omni for item detection in renders.
  Nemotron 3.5 Super VL for brand and model identification on close renders.
  Qwen 3.8 or GLM 5.3 for cost estimation and report writing.
  gpt-oss-120b as the prompt rewriter for failing lineages (input: lineage genome plus its failure cases with exhibits; output: a constrained diff to the system prompt).
- **Termination.**
  An agent cannot spawn below a minimum wallet (200 credits), bids with negative expected value are never accepted, and the root's total budget is fixed, so the tree is finite by conservation of money.
  Depth cap of 4 as a safety net, and a global 48-agent cap.
- **Learning loop.** After each verified or rejected delivery, the lineage's Brier score updates; a lineage below a solvency threshold is liquidated and respawned with a rewritten genome (at most 2 rewrites per lineage for the demo).

### 6-hour build plan (3 engineers)

- **10:45 - 12:15.**
  A: viewer, render bridge, region markers and contract threads.
  B: ClearingHouse (wallets, order book, ledger, token metering from `usage`), event log.
  C: Jev client with fallbacks, bid and delivery question schemas.
- **12:15 - 14:00.**
  A: order book ticker and calibration chart panel.
  B: Agent loop with bidding and spawning, depth 2 end to end.
  C: held-out viewpoint sampling and verification rulings.
- **14:00 - 15:30.**
  A: final inventory view with proof pins.
  B: subcontracting, liquidation, budget conservation checks.
  C: prompt rewriter and lineage genomes; seed one deliberately overconfident genome so the demo has a lineage to fix.
- **15:30 - 17:00.** Rehearse, record replay, submission.

### Build bottlenecks

1. **The learning beat needs enough outcomes to be visible.** Calibration curves need dozens of resolved bids.
   Mitigation: make delivery granular (each item is one bid), so a single run produces 40 to 80 resolutions, and seed one overconfident genome so the correction is guaranteed to have something to correct.
   Fallback: start the demo from a pre-run ledger (generation 1 already resolved) and show generation 2 live.
2. **Held-out verification that is too strict or too lax.** A held-out view may not show the item at all.
   Mitigation: sample held-out cameras that look at the claimed item's 3D position (back-projected from the contractor's render via splat depth) from at least 30 degrees away, and treat "not visible" as no ruling rather than a failure.
   Fallback: hand-place 3 held-out cameras per region in the morning.
3. **Market dynamics that stall or explode.** Mitigation: conservation of credits, minimum wallet, and EV-positive acceptance bound the tree by construction; tune bounty sizes on one rehearsal.
   Fallback: fixed bid acceptance threshold instead of EV, which still shows the market UI.
4. **Jev access and throughput.** Every bid is a Jev call, so Jev is on the hot path.
   Mitigation: batch several named questions into one Jev call per bid round, verify the OpenRouter route before 11:00.
   Fallback: local Jev-shaped backend (vote fraction over 5 Nemotron 3.5 Lightning samples); the calibration chart then honestly measures that backend.
5. **Explaining a market in 3 minutes.** The concept is denser than the others.
   Mitigation: lead with the ticker and the bankruptcy beat, keep finance words to "bounty", "bid", "bankrupt".
   Fallback: cut subcontracting from the narration and show it only in the tree.

### Self-score

| Criterion | Score | Why |
| --- | --- | --- |
| Technicality (40%) | 8 | Token-metered economy, held-out 3D verification, lineage calibration, and prompt rewriting. |
| Live Demo (20%) | 6 | The bankruptcy beat is strong, but a market is harder to read on stage than a growing octree or a flipping verdict. |
| Creativity (25%) | 7 | Agent markets have history; calibrated bids plus held-out viewpoints is the new part. |
| Future Potential (15%) | 8 | Self-funding, self-correcting recursion with honest uncertainty is a real direction for autonomy. |
| **Weighted** | **7.35** | |

---

## Comparison and recommendation

| Concept | Tech | Demo | Creativity | Future | Weighted |
| --- | --- | --- | --- | --- | --- |
| 1. Octree of Minds | 8 | 8 | 7 | 7 | 7.60 |
| 2. Forking Worlds | 9 | 7 | 9 | 8 | 8.45 |
| 3. The Viewpoint Court | 8 | 8 | 8 | 9 | 8.15 |
| 4. The Spawn Exchange | 8 | 6 | 7 | 8 | 7.35 |

Forking Worlds scores highest but carries the most build risk, because it depends on clean object clusters and on rendering several edited worlds at once.
The Viewpoint Court is the safest high scorer, because every risky piece has a cheap fallback (pose graph, cached testimony, replay) and its two verdict flips are easy for judges to read.
The pieces are composable: the Octree of Minds split rule can serve as the Court's witness-placement policy, and the Exchange's budget conservation can govern any of the other three.
A practical plan is to commit to the Court by 11:15, and keep Forking Worlds as the stretch beat ("the court rules the room unsafe, so the agents fork the world to fix it") only if clusters are segmented and working by 14:00.
