# Unexpected-domain concepts

Lens: unexpected domains (games, history, film, accessibility), with the physical demo room as the stage.
Every concept drops the ops dashboard entirely.
Weighted score = 0.40 x Technicality + 0.20 x Demo + 0.25 x Creativity + 0.15 x Future.

Shared infrastructure note: all four concepts need the same two primitives, so whichever one the team picks, these get built first.
- `render(pose) -> png`: a headless Spark or gaussian-splats-3d page driven by Playwright, returning a frame for any camera pose, batched (8-16 poses per call) and cached by pose hash.
- `gate(state, questions) -> probabilities`: a thin Jev client with an identical-signature local fallback (a Nemotron 3.5 Lightning call that returns JSON probabilities), so the demo never depends on Jev being reachable.

---

## Concept 1: Inside Job

**Hook:** A recursive heist crew plans how to steal the trophy off the judges' table without any judge seeing it happen, then a teammate does it live while the judges watch for him.

### Live demo, beat by beat

- 0:00 - The screen shows the hackathon room as a splat captured at 10:30 this morning, with the judges' table, the stage, and the actual trophy (or a sticker, a water bottle, a Crusoe mug) visible.
- 0:15 - We click four chairs in the splat: "the judges sit here."
- 0:20 - We type the goal: "Take the mug from the judges' table to the back door without being seen."
- 0:25 - The Mastermind agent appears in an agent tree on the left and spawns a Casing crew: one Lookout per judge seat, one Scout per room zone.
- 0:45 - Each Lookout renders the room from its judge's eye height and gaze cone, and the screen tiles those renders: four "judge-cam" views of the room.
- 1:00 - Scouts propose path segments; each segment point is rendered from every judge's viewpoint and gated by Jev ("is a person at this point visible to judge 2?").
- 1:15 - A heat map of "seen probability" paints itself onto the splat floor, red near the stage, a blue shadow lane behind the camera tripod and the pillar.
- 1:30 - A Scout's lane is rejected because one judge has line of sight; the Mastermind spawns a Distraction sub-crew that proposes "teammate B asks judge 3 a question at T+20s", which rotates that judge's gaze cone and reopens the lane.
- 1:50 - The final plan is a timed path with cues, read out by TTS.
- 2:00 - Wow moment: teammate A actually does it, following the audio cues in an earpiece, while teammate B performs the distraction.
- 2:30 - The judges either see it or they do not, and we show the Jev probability for each judge next to what actually happened ("judge 1: 0.08 seen, judge 4: 0.31 seen").
- 2:45 - Close: the tree of every sub-crew spawned, the plans pruned, and the cost in tokens.

### Why judges have not seen it

Stealth-game AI and guard vision cones exist in every game engine, and robotics has visibility-aware path planning.
Nobody has turned the judges themselves into the adversarial observers inside a photoreal reconstruction of the room they are sitting in, and then executed the plan in front of them.
The closest thing is "AI plans a museum heist" text-only prompt demos, which have no geometry and no live execution.

### Load-bearing ingredients

- Splat: the only source of what each judge can actually see from their real seat, including clutter, laptops, and people standing in the way that no floor plan contains.
- Jev: thousands of per-point, per-judge "seen yes/no" calls need calibrated probabilities that compose into a path risk score, not prose; the sum of log-probabilities is the plan's objective.
- Recursion: the Mastermind decides how many Scouts per zone and spawns Distraction or Reroute sub-crews only when a lane fails, so the tree shape is the plan's failure history.

### Architecture sketch

- Mastermind (gpt-oss-120b): owns the goal, the budget, the plan tree, and the spawn decisions.
- Lookout per judge (Nemotron 3.5 Super VL): given judge pose and a rendered frame of a candidate point with a red marker, describes occluders and reports visibility.
- Scout per zone (Nemotron 3.5 Lightning 30B): proposes waypoints on a 2D floor grid derived from the splat's ground plane.
- Distraction sub-crew (Qwen 3.8): proposes human actions that change a judge's gaze, outputs a new gaze cone and a time window.
- Jev gate: state = {judge pose, point, Lookout description, render id}, questions = noul "visible", score "confidence", choice "occluder type".
- Critic (DeepSeek V4): rejects plans with timing conflicts or impossible geometry, and forces re-plan.
- Data flow: splat -> floor grid -> Scout waypoints -> render from each judge -> VL description -> Jev probability -> heat map -> Mastermind prune or spawn.
- Fast path: a ray-march against splat Gaussian centers gives a geometric visibility prior, so the VL model is only called on ambiguous points (prior between 0.2 and 0.8).

### 6-hour build plan (3 people)

- 10:45-11:15 all: capture the room with Scaniverse or Polycam on two phones, start a Postshot or nerfstudio training as backup, agree on the pose and frame JSON contracts.
- Person A (render and geometry): headless renderer, judge-cam rendering, floor grid extraction, visibility prior, heat-map overlay in the viewer.
- Person B (agents): Mastermind, Scouts, Lookouts, Distraction, Critic, spawn and budget logic, event stream to the UI.
- Person C (gate, UI, demo): Jev client plus fallback, agent-tree panel, judge-cam tile view, TTS cue playback to an earpiece, rehearsal of the physical heist.
- 13:00 milestone: one Lookout returns a correct visible or hidden verdict for a hand-picked point.
- 14:30 milestone: end-to-end plan on the real splat, rough UI.
- 15:30 freeze features, rehearse the heist three times with real people sitting in the judge seats.
- 16:30 record a backup video of a full run.

### Build bottlenecks

1. Splat quality around the judges' table (moving people, glossy laptops). Mitigation: capture at 10:30 before the room fills, from 3 heights, and crop to the front third of the room. Fallback: a pre-captured splat of a mock room set up in a hallway with four chairs.
2. Judges sit somewhere other than expected. Mitigation: the chair clicks happen live during the demo, and the pipeline re-runs from that step in under 60 seconds because the visibility prior is cached per grid cell. Fallback: ask judges to sit in the marked seats ("please sit in the chairs with tape").
3. VL latency on hundreds of renders. Mitigation: geometric prior handles 80 percent of points, VL only on ambiguous ones, batch renders, spread across Super VL and Nano Omni to split rate limits. Fallback: precompute the heat map for the expected seats and only re-run the failing lane live.
4. Jev access. Mitigation: use OpenRouter typesafe/jev-1.14-20261014 with the same request shape. Fallback: local Lightning-based gate with the same API, flagged in the UI as "fallback gate."
5. The live heist fails awkwardly. Mitigation: make failure part of the show by displaying the predicted probability that the plan fails; rehearse with fake judges. Fallback: run the heist against the demo audience's front row instead of judges.

### Self-score

- Technicality: 8 (visibility over a splat, per-observer rendering, timed distraction replanning).
- Live demo: 9 (a real physical heist in front of the judges).
- Creativity: 9.
- Future potential: 6 (adversarial observer modeling transfers to security red-teaming and robot stealth, but the framing is playful).
- Weighted total: 0.40x8 + 0.20x9 + 0.25x9 + 0.15x6 = 3.2 + 1.8 + 2.25 + 0.9 = **8.15**.

---

## Concept 2: Rephotographer

**Hook:** Hand the swarm any picture of a place (a phone snap, a pencil sketch, a 1950s archival photo) and recursive search agents find the exact spot and lens it was taken from inside a splat, then re-shoot it from there.

### Live demo, beat by beat

- 0:00 - The splat of the hackathon room is on screen.
- 0:10 - We ask a judge to take one photo of anything in the room with their own phone and AirDrop it to us (or show it to the laptop webcam).
- 0:20 - The photo appears in a corner; the Surveyor agent reads it ("projector screen, left edge of a window, two round tables, taken low, wide lens").
- 0:30 - The Surveyor partitions the room into 6 volumes and spawns one Searcher per volume; each Searcher renders a coarse fan of candidate views.
- 0:50 - The splat fills with dozens of little camera frustums, colored by Jev match probability; most go grey and die.
- 1:05 - Two Searchers report high probability; each one decides on its own to spawn finer child Searchers around its best pose (smaller radius, finer yaw steps), so the frustum cloud visibly collapses toward one spot in two or three generations.
- 1:20 - A Skeptic agent challenges the leader ("the table count matches but the window is mirrored"), forcing a sibling branch to be re-opened; the tree view shows the recovery.
- 1:35 - Wow moment: a camera frustum lands, the viewer flies to it, and the render cross-fades with the judge's photo almost pixel for pixel; a dot appears on the floor where the judge was standing, and we point at the spot in the real room.
- 1:50 - Second round, harder: a judge draws a 20-second sketch of the room on paper, or we use a pre-shot "historical" photo of a public splat scene, and the same swarm localizes it with a lower but calibrated confidence.
- 2:30 - Close: confidence-vs-generation chart, number of agents spawned, and a "rephotograph" side by side, the classic then-and-now pair.

### Why judges have not seen it

Visual relocalization is a mature field (COLMAP, hloc, ACE, MASt3R) and it beats LLM agents on clean photos of the same scene.
Rephotography (Then-and-Now, Mark Klett's work) is a known art practice but is done by hand.
What is new is a semantic, cross-modal localizer that works on sketches, old photos with changed furniture, and verbal descriptions, where feature matching has nothing to match, and whose search tree is grown by the agents themselves.
Honest risk: a judge may ask "why not just hloc?"; the answer is the sketch round, so the sketch round must work.

### Load-bearing ingredients

- Splat: the search space; agents can only test a hypothesis "the photo was taken from here" by rendering that exact view, which needs a photoreal novel-view renderer.
- Jev: each candidate render vs. the query gets a calibrated "same viewpoint" probability plus criteria scores (layout, objects, height, lens), and those numbers decide which branches spawn and which die, so it is the search's value function.
- Recursion: coarse-to-fine localization is literally recursive subdivision, and each agent chooses its own branching factor and depth based on its confidence and remaining budget.

### Architecture sketch

- Surveyor (Nemotron 3.5 Super VL): reads the query image into a structured description (objects, layout left to right, estimated height, focal length guess, era cues).
- Searcher(volume, depth) (Nemotron 3.5 Lightning 30B for planning, Nano Omni or Super VL for comparing): renders N poses in its volume, sends query plus render pairs to Jev, spawns children around poses above a threshold, returns its best pose and probability to its parent.
- Skeptic (DeepSeek V4): reviews the top-2 hypotheses for contradictions (mirror, wrong wall, wrong table count) and can veto, which re-opens pruned branches.
- Jev gate: state = {query description, render description, both images if using the image-capable Decisions alternative}, questions = noul "same viewpoint", score per criterion, choice "which of these 8 renders is closest".
- Refiner: when the top probability exceeds 0.9, a classical step (ORB features plus PnP between query and render) snaps to sub-degree accuracy for photo queries, and is skipped for sketches.
- Data flow: query -> Surveyor -> volume split -> Searchers -> render batches -> VL compare -> Jev -> spawn or prune -> Skeptic -> winner -> viewer fly-to and cross-fade.

### 6-hour build plan (3 people)

- 10:45-11:15 all: capture the room splat; collect 10 test queries (5 phone photos, 3 sketches, 2 photos with furniture moved) with known positions as an eval set.
- Person A: headless batch renderer, pose sampler per volume, frustum overlay and fly-to plus cross-fade in the viewer.
- Person B: recursive Searcher runtime (spawn, budget, return), Skeptic, tree event stream.
- Person C: Jev client plus fallback, Surveyor prompts, eval harness that scores localization error on the 10 queries every time prompts change.
- 13:00 milestone: one-level search finds the right wall for 3 of 5 photos.
- 14:30 milestone: recursive search under 2 meters error on photos, right region on sketches.
- 15:30 freeze; tune thresholds on the eval set; 16:30 backup video.

### Build bottlenecks

1. Search cost: 6 volumes x 3 generations x 16 renders is about 300 VL comparisons. Mitigation: make the first pass a choice question ("which of these 8 thumbnails is closest") so one call ranks 8 renders, spread across three VL-capable models. Fallback: precompute a coarse grid of 500 renders with CLIP-style embeddings at noon, and let agents start from the top 10 grid hits.
2. Jev takes text, not images. Mitigation: VL model converts each render into the same structured schema as the query, and Jev compares the two structures; or use the OpenAI Decisions alternative, which accepts images. Fallback: local gate with the same API.
3. Sketch round fails. Mitigation: test 10 sketches during the build, give the Surveyor a sketch-specific prompt, and allow a coarse answer ("this wall, this side") to count. Fallback: replace the live sketch with a verbal description from a judge ("where I am sitting, looking at the stage").
4. AirDrop or upload friction on venue Wi-Fi. Mitigation: a laptop webcam capture button as the input path, and a local web server on a phone hotspot. Fallback: a teammate takes the photo where the judge points.
5. Splat region the judge photographs was poorly captured. Mitigation: capture all four walls at two heights and ask the judge to photograph "anything in front of you". Fallback: a public high-quality splat scene for a second round.

### Self-score

- Technicality: 8 (recursive search with a learned value function over a renderer, plus a classical refiner).
- Live demo: 8 (the cross-fade onto the judge's own photo lands, but it is less physical than a heist).
- Creativity: 8.
- Future potential: 8 (agent search as a general localization and verification primitive: archives, journalism geolocation, forensics, robots that are lost).
- Weighted total: 0.40x8 + 0.20x8 + 0.25x8 + 0.15x8 = **8.0**.

---

## Concept 3: Second Unit

**Hook:** An autonomous film crew of agents is handed a logline and a splat of this room, and it scouts, blocks, shoots, reviews dailies, and re-shoots until it delivers a 30-second trailer of the hackathon, with no human touching a camera.

### Live demo, beat by beat

- 0:00 - The judges pick a genre from three cards (noir, nature documentary, heist thriller); an audience member shouts a logline ("the last GPU in San Francisco").
- 0:15 - The Director agent writes a 6-shot list and spawns a Department Head per shot: each one spawns a Location Scout, a DP, and a Continuity agent.
- 0:35 - Location Scouts fly through the splat and pin candidate spots ("the server rack corner reads as a vault"); the viewer shows pins popping onto real places in the room.
- 0:55 - DPs render test frames for each shot from many camera poses and focal lengths; a contact sheet of real renders of this room fills the screen.
- 1:15 - The Dailies Review runs: Jev scores each frame on the shot's named criteria (composition, readability of the subject, genre mood, continuity with the previous shot), and the losers fade out.
- 1:30 - Continuity catches a break ("shot 3 crosses the 180-degree line relative to shot 2"), so the Director sends a Reshoot order and that shot's sub-tree respawns with a constraint; the tree view shows the recovery.
- 1:50 - The Editor agent picks takes, sets durations, and the system renders smooth camera moves (dolly, crane, push-in) as video from the splat, with TTS narration and captions in genre style.
- 2:10 - Wow moment: the trailer plays full screen; it is unmistakably this room, these tables, today, shot like a film, and nobody operated a camera.
- 2:45 - Close: the shot tree with every take tried and rejected, and the Jev score of each chosen take.

### Why judges have not seen it

Virtual cinematography research and game-engine auto-camera systems exist, and Luma, Runway, and Sora generate video from prompts.
Prompt-to-video is generative and hallucinated; this is a crew working inside a real captured space, which is closer to an actual location shoot, and the crew structure (Director, Department Heads, DPs, Continuity) maps naturally onto a recursive agent tree.
The closest thing is "AI storyboard" tools and splat fly-through videos made by hand in Postshot or SuperSplat.

### Load-bearing ingredients

- Splat: the location; every frame and camera move is a real novel-view render of the real room, so without it there is nothing to film.
- Jev: the Dailies Review must rank hundreds of takes on named film criteria fast and cheaply, with calibrated scores that the Editor combines into a cut, which is exactly a typed choice and score API.
- Recursion: Director -> Department Head per shot -> Scout, DP, Continuity, and reshoots spawn new sub-trees only when review fails, so the crew size grows with how hard the trailer is.

### Architecture sketch

- Director (gpt-oss-120b or GLM 5.3): logline and genre -> shot list with intent per shot.
- Department Head per shot (Nemotron 3 Super 120B): owns one shot, spawns and budgets its crew, can escalate to the Director.
- Location Scout (Nemotron 3.5 Super VL): looks at 360 renders of each splat region, proposes places that fit the shot intent.
- DP (Nemotron 3.5 Lightning 30B): emits camera poses, focal length, and a move type as JSON; the renderer turns them into frames and short clips.
- Continuity (DeepSeek V4): checks screen direction, lighting continuity, and repeated framing across adjacent shots using pose math plus VL descriptions.
- Jev gate: state = {shot intent, genre, frame description, pose}, questions = score per criterion, choice "best take of these 6", noul "cuts cleanly with previous shot".
- Editor (Qwen 3.8): orders takes, durations, narration script; ffmpeg assembles the clip with TTS.
- Data flow: logline -> Director -> shot tree -> Scouts pin locations -> DP renders takes -> VL describe -> Jev review -> Continuity -> Editor -> ffmpeg trailer.

### 6-hour build plan (3 people)

- 10:45-11:15 all: capture the room splat, plus one dramatic area (a hallway, the server racks, the stage) at high density.
- Person A: renderer for stills and camera-path clips (catmull-rom pose splines, 24 fps, 720p), ffmpeg assembly, color grade LUT per genre.
- Person B: crew runtime (Director, Department Heads, Scouts, DPs, Continuity), reshoot logic, tree event stream.
- Person C: Jev review client plus fallback, Editor, TTS narration, the demo UI (genre cards, contact sheet, tree).
- 13:00 milestone: one shot end to end, scout to chosen take.
- 14:30 milestone: full 6-shot trailer, ugly.
- 15:30 freeze, tune genre prompts and LUTs; 16:30 backup trailer video in case live render is slow.

### Build bottlenecks

1. Rendering video clips headless is slow. Mitigation: render 720p at 24 fps in a GPU-backed headless Chrome on a Crusoe VM or a teammate's laptop GPU, shots are 3-5 seconds each, render in parallel tabs. Fallback: render stills only and use Ken Burns pans in ffmpeg.
2. Splat artifacts look bad in close-ups. Mitigation: DPs are told the splat's good zones (a quality map from per-region capture density) and Jev has a "free of artifacts" criterion that rejects floaters. Fallback: keep cameras at mid distance and use a film grain plus vignette LUT that hides blur.
3. Taste is subjective, so the trailer may feel flat. Mitigation: hard-code genre grammar (noir: low angles, slow push-ins; documentary: wide establishing then close) as the shot-list template. Fallback: rehearse with the three genre cards so each has a known-good path.
4. The demo is 3 minutes and the full pipeline may take 5. Mitigation: start the run at the top of the demo and narrate the tree while it works; cap reshoots at one per shot. Fallback: start a run 10 minutes before the slot with the audience logline captured in advance, and show the live tree catching up.
5. Rate limits with hundreds of VL calls. Mitigation: spread roles across 6 models, cache renders by pose hash, and use choice questions over batches of 6 takes. Fallback: drop the per-take VL description and score from Jev with pose-derived features only.

### Self-score

- Technicality: 7 (camera-path rendering, recursive crew, continuity reasoning, video assembly).
- Live demo: 8 (a trailer of the room they are sitting in is fun, but render time adds risk).
- Creativity: 8.
- Future potential: 6 (virtual production and previs are real markets, but the autonomy story is softer).
- Weighted total: 0.40x7 + 0.20x8 + 0.25x8 + 0.15x6 = 2.8 + 1.6 + 2.0 + 0.9 = **7.3**.

---

## Concept 4: Blind Walk

**Hook:** A blindfolded teammate crosses the hackathon room to a judge's table guided only by an agent team that rehearsed the route in a splat this morning, and it re-plans live when a judge drops a bag in the way.

### Live demo, beat by beat

- 0:00 - A teammate puts on a blindfold and a chest-mounted phone streaming its camera; a second teammate walks beside them as a safety spotter, hands off.
- 0:10 - A judge names the destination ("bring me the clicker from the stage").
- 0:20 - The Navigator agent plans on the splat floor grid and spawns one Rehearser per route segment; each Rehearser renders the egocentric views a walker would see along its segment at chest height and builds an expected-view strip with landmarks ("pillar on your left at 2 meters").
- 0:45 - The viewer shows the planned route on the splat, with each segment's expected views as thumbnails, and the agent tree on the side.
- 1:00 - The walk starts; the live frame and the expected splat frame sit side by side, and a Localizer agent tracks progress by comparing them.
- 1:20 - Every step the Jev gate answers "is the next 1 meter clear?" with a probability, and the voice changes pace with it ("walk", "slow", "stop").
- 1:35 - Wow moment: a judge (prompted beforehand) drops a backpack or slides a chair into the path; the live frame no longer matches the splat expectation, the Jev "obstacle present" probability spikes, the voice says "stop", and the Navigator spawns a Detour sub-team that re-rehearses a new path in the splat in a few seconds.
- 2:00 - The walker steps around the obstacle, reaches the stage, and picks up the clicker.
- 2:20 - Close: a timeline of probabilities, every "stop", the detour sub-tree, and the line "the splat is the memory, the camera is the present, the agents reconcile the two."

### Why judges have not seen it

Assistive navigation exists: Be My Eyes with GPT vision, Microsoft Seeing AI and Soundscape, Aira human guides, CMU NavCog with beacons, and Apple's door detection.
Those either describe the current frame or need beacons and maps; none rehearse the route in a photoreal prior map and then reason about differences between the remembered room and the live room.
The closest research is visual teach-and-repeat in robotics, which needs a robot to drive the route first, while here the "teach" pass is done by agents walking virtually through a splat.

### Load-bearing ingredients

- Splat: the rehearsal space and the memory; every expected view along the route is a render, and obstacle detection is "live frame differs from what the splat says should be there."
- Jev: the walker's safety rides on calibrated "next meter clear" and "obstacle present" probabilities with thresholds that map to walk, slow, and stop, where prose would be dangerous and unauditable.
- Recursion: the Navigator spawns per-segment Rehearsers, and on any mismatch spawns a Detour sub-team that itself spawns Rehearsers for the new segments, so recovery is a new sub-tree created during the walk.

### Architecture sketch

- Navigator (gpt-oss-120b): goal -> route on the splat floor grid (A* with clearance), segment split, spawn and re-plan decisions.
- Rehearser per segment (Nemotron 3.5 Super VL): renders chest-height views every 0.5 meters, extracts landmarks and hazards, writes the voice script for that segment.
- Localizer (Nemotron 3 Nano Omni on live video frames at 1-2 fps): matches the live frame against the segment's expected strip to estimate progress and heading drift.
- Hazard Watcher (Nano Omni): describes the floor region in the lower third of each live frame.
- Jev gate: state = {expected landmark list, live description, progress estimate, drift}, questions = noul "next meter clear", noul "obstacle not in map", score "localization confidence", choice "walk / slow / stop / turn".
- Detour sub-team: marks the blocked cells, re-plans, spawns new Rehearsers for the changed segments only.
- Voice: short TTS phrases on a laptop speaker or an earpiece, with "stop" as an interrupting priority channel.
- Data flow: splat -> floor grid -> route -> Rehearsers -> expected strips; live camera -> Localizer and Hazard Watcher -> Jev -> voice; mismatch -> Navigator -> Detour sub-tree.

### 6-hour build plan (3 people)

- 10:45-11:15 all: capture the room splat with the floor well covered, align the splat's ground plane and scale using two taped markers measured with a tape.
- Person A: floor grid and A* planner, chest-height render strips, route and live-position overlay in the viewer.
- Person B: phone streaming (WebRTC or a simple websocket of JPEG frames from a phone browser page), Localizer, Hazard Watcher, latency budget.
- Person C: Jev gate plus fallback, Navigator and Detour runtime, TTS with priority interrupt, safety protocol and rehearsal.
- 13:00 milestone: rehearsed route with expected strips for a fixed goal.
- 14:30 milestone: sighted walk with live localization and stop-on-obstacle.
- 15:30 freeze, five blindfolded rehearsals with different obstacles; 16:30 backup video.

### Build bottlenecks

1. End-to-end latency from frame to voice. Mitigation: 1-2 fps frames at 512 px, Nano Omni with thinking off, the Jev call in parallel with the next frame, and a deterministic "stop" whenever no fresh verdict has arrived in 1.5 seconds. Fallback: slow walking pace cued by the system ("one step... one step"), which reads as caution, not lag.
2. Safety and the judges' comfort. Mitigation: a hands-off spotter walking beside the walker, a short route with no stairs, the walker can lift the blindfold at any time, and the system defaults to stop. Fallback: the walker wears the blindfold but the spotter lightly holds the walker's elbow, or a teammate pushes a rolling chair with the phone mounted as the "walker".
3. Localization drift. Mitigation: taped floor markers seen by the camera act as checkpoints, and the Localizer only needs segment-level progress, not centimeters. Fallback: the walker counts steps with a calibrated stride and the agents use dead reckoning between landmark confirmations.
4. Venue Wi-Fi drops the phone stream. Mitigation: phone and laptop on a phone hotspot, only model calls leave the local network. Fallback: tether the phone over USB and read frames locally.
5. Jev access. Mitigation: OpenRouter typesafe/jev-1.14-20261014. Fallback: local gate with the same API, flagged in the UI.

### Self-score

- Technicality: 8 (live video plus a splat prior, mismatch-driven replanning, a real-time safety gate).
- Live demo: 9 (a blindfolded person crossing the room around an obstacle a judge just placed).
- Creativity: 8 (assistive navigation is known, the splat-as-memory reconciliation is not).
- Future potential: 9 (calibrated gating of physical actions and prior-map reconciliation transfer directly to robots, drones, and assistive tech).
- Weighted total: 0.40x8 + 0.20x9 + 0.25x8 + 0.15x9 = 3.2 + 1.8 + 2.0 + 1.35 = **8.35**.

---

## Ranking

| Concept | Tech (40) | Demo (20) | Creativity (25) | Future (15) | Weighted |
|---|---|---|---|---|---|
| Blind Walk | 8 | 9 | 8 | 9 | 8.35 |
| Inside Job | 8 | 9 | 9 | 6 | 8.15 |
| Rephotographer | 8 | 8 | 8 | 8 | 8.0 |
| Second Unit | 7 | 8 | 8 | 6 | 7.3 |

Notes for choosing.
Blind Walk has the strongest story for "Future Potential" but carries the most physical risk, so it lives or dies on rehearsal and the safety protocol.
Inside Job is the most original and the most fun, and its risk is social (the judges must play along), not technical.
Rephotographer is the safest to build and its core (recursive render-and-compare localization) is also the Localizer inside Blind Walk, so it is a good fallback: if Blind Walk's live stream fails by 14:30, the team can pivot to Rephotographer with most of the code reused.
Second Unit is the weakest on technicality and the most exposed to render time.
Rate-limit warning from the repo notes: Crusoe is 30 RPM per model without a card on file and 600 RPM with one, so put a card on file before 11:00 or every concept above will stall.
