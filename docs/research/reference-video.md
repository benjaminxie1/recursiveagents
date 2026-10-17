# Reference video: "Phylax" (Speed Hacks demo, Sun 10.11.26)

Source: `gaussian_refernece(1).mp4`, 5:14.8, 1920×1080 @ 30 fps, with narration by two presenters (Kaushik, then Charlie).
Method: frames at 2 fps (630 total, in the local `video-context/` package), contact sheets every 3 s, full-resolution crops of eight key screens, and a faster-whisper transcript.
Key frames are in [`reference-video/`](reference-video/). Quotes come from the transcript ([`reference-video/transcript.md`](reference-video/transcript.md), with timestamps), and UI text was read off the frames.

## Summary

Phylax helps construction **owners** confirm that a building (here, a plant room) is built to code before opening. Its pipeline:

1. A **3D Gaussian splat** of the room is captured, repeatedly across construction stages.
2. A **VLM sweeps camera stations** through the splat and analyzes each view.
3. The **OpenAI Decisions API** classifies objects into **system > subsystem > component**. Object detection is **Grounding DINO hosted on Modal**.
4. Components are linked to a **library of California codes and standards** scraped from the web. Each code × component pair becomes a **requirement** that needs **evidence**.
5. An owner walks the splat "like an inspector" and sees, live, which requirements aren't met. The example: a pump with "evidence pending: final balancing report". The pitch is that one day of delay on a data center costs "$100–200k".
6. A **Hermes bot connected over MCP** keeps learning about the project, runs **scheduled jobs** (what changed, inspection prep, what I learned) and answers questions with citations.

## Timeline

| Time | Screen | What happens |
|---|---|---|
| 0:00 | Title card "Speed Hacks · Charlie Robison · Phylax" | Intro: "today we built Phylax" |
| 0:03–1:18 | **Plant room inspection wall** ([01](reference-video/01-camera-sweep-grid-t0m04s.jpg)) | A 4×3 grid of camera stations S01–S12, each labelled with a location ("Pumps and pipework", "Overhead services", "Exit lighting wall"…) and a heading (180°/225°). Each tile has an `Analyzing`/`Failed` badge and a row of dots for progress through its angles. Header: `Run wall-202610112347 · 54 of 108 views`, with a Stop button. A **top-down map** on the right shows the station positions and view cones. Narration explains the splat → VLM → Decisions pipeline |
| 1:21 | **Home** ([02](reference-video/02-home-t1m21s.jpg)) | "Explore building systems, source publications, and inspection findings". Three entry cards: Explore the plant room (3D, keyboard), Browse codes & standards, Open plant room inspection tour (automatic walkthrough). System status cards: Database `Connected`, Claude (Claude Agent SDK, OAuth) |
| 1:24–1:42 | **Codes & standards → Source library (112)** ([03](reference-video/03-codes-source-library-t1m30s.jpg)) | Collections: San Francisco 27, California codebooks 10, California references 50, Referenced standards 7, Los Angeles City 14, Los Angeles County 4. Publications include the 2025 California Administrative Code Title 24 Part 1, California Green Building Standards Code Title 24 Part 11, and SF ordinances / CBSC filings. An embedded PDF viewer opens the selected publication (1/382) |
| 1:45–1:54 | **Systems & components (4)** ([04](reference-video/04-systems-components-t1m45s.jpg)) | Taxonomy with numbered codes: Mechanical 01, with subsystems such as Pipe Support 01.02, Heat Generation and Rejection 01.03 and Ventilation 01.04; Electrical 02, with Cable Management 02.02, Lighting 02.03 and Fire Alarm 02.04; Plumbing 03 (Fire Suppression); Architectural 04. Each component type shows a count |
| 1:57–2:10 | **Requirements (148)** ([05](reference-video/05-requirements-t2m00s.jpg)) | A progress bar ("108 verified … 40 pending"), search, and a discipline filter. Each row has an action, citations (`CEC 110.26(B) · SFEC 110.26(B) · 8 CCR 2340.16(a),(c)`), a type (SUPPORTING CRITERION · SPACE / COMPONENT), and `Evidence verified` or `Evidence pending`. Detail view for REQ-EL-01-01 "Unobstructed electrical working space": discipline, action type, target, **Conditions**, **Building attributes**, **Evidence**, **Cited sources** (CEC 110.26(B), 2025 California Electrical Code, revision date, errata status) |
| 2:18–4:00 | **Visual workspace** (3D splat) ([06](reference-video/06-visual-workspace-pump-t2m40s.jpg), [07](reference-video/07-visual-workspace-isolator-t3m30s.jpg)) | WASD flying through the splat. Markers are green when verified and red when pending. Toolbar: Components 618 · Markers · Outlines · Codes & standards 112/148. Left panel, "Plant room survey": Components 618 (594 mapped), **Evidence verified 73%** (450 verified / 168 pending), discipline chips (Architectural 100%, Electrical 66%, Mechanical 75%, Plumbing 70%), "In line of sight · 243 matches", and a list (`#921 Pump · Mechanical / Hydronic Distribution · Pending · 100% match`). Clicking a component outlines it with a label (`#921 Pump Pending`) and opens a **detail panel**: component code 01.01.01, identification 100%, observations, and "Evidence pending: 8 of 9 related requirements verified". Each related requirement has an ID (`REQ-PL-014-01 · subsystem · Final balancing report · Pending`) and there's an "Explore codes & standards" link. Second example: #1146 Local Isolator (Electrical / Power Distribution, identified 82%, 5 of 9 verified), with requirements such as "Guard energized parts" and "Protect electrical equipment from impact" |
| 4:00–5:14 | **Hermes desktop app** ([08](reference-video/08-hermes-bot-t4m10s.jpg)) | Bots sidebar (Construction domain experts, Construction research, Phylax). The user asks "from the last walkthrough what codes are ready for inspection?". The answer ("Inspection prep for run-202610112150…") is grouped by system: Fire alarm (manual call points 20, sounders 2, strobe 1) and Sprinklers. Each item has a requirement ID and citations (`REQ-F15-01: SFFC 907.8.4, annual`; `REQ-F14-01: CFC 901.6, 901.6.1; Title 19 904(a)(1); SFFD AB 4.10`) and says what to have ready. It's explicit about its limits: "I can't tell you which of these are ready. The record shows what will be inspected and what to prepare, not outcomes." Scheduled jobs: "What changed on…" every 30 min, "Inspection prep" `30 7 * * 1`, "What I learned this…" `0 17 * * 5`. Model shown: Sonnet 5.5 |

## Feature inventory

| # | Feature | Where | How it works on screen |
|---|---|---|---|
| 1 | Gaussian-splat site model | 2:18–4:00 | A full 3D splat of the plant room. WASD/QE flight, Shift for speed, Reset view, full-screen |
| 2 | Automated camera sweep (VLM inspection run) | 0:03–1:18 | 12 named stations, each with a heading, scanned through several angles (dots). Per-station status, run id, a view counter (54/108), a top-down map with view cones, and Stop |
| 3 | Object detection and classification | narrated 3:41–3:50 | Grounding DINO on Modal detects. The Decisions API classifies into system/subsystem/component, with identification confidence (100%, 82%) and match % per instance |
| 4 | Component taxonomy with codes | 1:45 | Hierarchical numeric codes (01.01.01). Counts per type, 4 top-level systems |
| 5 | Custom codes and standards library | 1:24 | 112 scraped California and local publications in collections (SF, California codebooks, LA), with an in-app PDF viewer and publisher links |
| 6 | Requirements engine | 1:57 | Code × component gives 148 requirements. Each has an ID, citations, a type, conditions, attributes, evidence needed, and a verified/pending status, plus an overall verified bar |
| 7 | Evidence status on 3D pins | 2:18+ | Pins are green (verified) or red (pending). Selecting one outlines the object and shows its label |
| 8 | Component detail panel | 2:39+ | Code, identification %, observations, X of Y requirements verified, a list of related requirements with IDs and status, and a deep link into the codes |
| 9 | Discipline progress | 2:24+ | Chips with % verified per discipline. "In line of sight" filters to what the camera can see |
| 10 | Project memory bot (Hermes over MCP) | 4:00+ | A chat that answers from the last walkthrough with requirement IDs and citations, and states plainly what it can't know |
| 11 | Scheduled agent jobs | 4:00+ | Cron jobs: a 30-minute change digest, weekly inspection prep, and a weekly "what I learned" |
| 12 | Repeated scans over time | narrated 0:34, 4:33 | Splats captured at construction stages or daily, with a run id per walkthrough |

## UI anatomy

- **Two visual languages.** The library pages (home, codes, requirements) are **light**: white background, black type, grey borders, green "verified" and red "pending" pills, and a thin green/red progress bar. The **3D workspace and the inspection wall are dark**: near-black panels over the splat, a green accent (verified, active chips) and a red accent (pending, failed).
- **Typography:** clean sans-serif (Inter-like) for UI text, monospace for IDs (`REQ-PL-014-01`, `#921`, component codes, run ids).
- **Layout (workspace):**
  - top-left: breadcrumb "← Phylax / Visual workspace · Plant room"
  - top-right: toggle chips (Components n · Markers · Outlines · Codes & standards n/m)
  - left: a collapsible survey panel
  - right: the detail drawer
  - bottom-left: keyboard hints
  - bottom-right: Reset view
- **Pins:** small dark circles with a coloured ring (green or red). The selected object gets a translucent red box outline and a dark label tag.
- **Camera:** first-person flight, no auto fly-to visible. The sweep is a separate page, not an animation inside the splat.

## GridTwin equivalents

These mappings are our suggestions, not something the video shows. Workstreams: 1 = sim, 2 = agent loop and KB, 3 = viewer, vision and demo.

| Reference feature | GridTwin version (5 MW solar plant) | MVP? | Owner |
|---|---|---|---|
| Splat of the plant room | Splat of the plant (inverter pads, combiner rows, transformer yard) with an asset pin per viewpoint | MVP | 3 |
| Camera sweep wall (stations S01–S12, headings, top-down map) | The inspector agent's `get_camera_view` calls shown as a live grid of stations (inv1_cabinet, xfmr1_yard, cb23_box…). Each tile shows Analyzing → verdict, and a top-down map shows viewpoint positions | MVP (grid from `events.jsonl`) | 3 (needs `heading_deg`/`map_xy` in `viewpoints.yaml`) |
| Decisions API classification | **Jev** answers typed questions: the safety gate before every action, plus the verifier's choice/score on photos | MVP | 2 |
| System > subsystem > component codes | **Done (ws1):** `list_assets` returns system/subsystem/component and `component_code` (01.01.01 PV block, 01.02.01 combiner, 02.01.01 inverter, 03.01.01 breaker, 04.01.01 transformer, 05.01.01 BESS, 06.01.01 meter) | MVP | 1 ✅ |
| California codes library (Title 24 parts, SF/LA ordinances) | California codes for PV and storage. **CEC** (Title 24 Part 3) 690.41 / 690.11 / 691, **CFC** (Title 24 Part 9) 1205 / 1205.5.1 (vegetation) / 1207 (ESS), **Cal/OSHA 8 CCR 2320.4** (LOTO), plus IEEE C57.104 / C57.91, IEEE 1547, UL 1741, NFPA 70E / 70B / 855 and OEM manuals. Clause text fetched through You.com and cached in `kb/` with URLs | MVP | 1 ✅ (ids), 2 (text) |
| Requirements engine (code × component, conditions, evidence, cited sources) | **Done (ws1):** the new `list_requirements(asset)` tool returns 20 requirements (REQ-INV-01…, REQ-CB-…, REQ-XF-04 "abnormal DGA escalated"…). Each has its standards and a `verify_by` {kind, how} using our DoD kinds. There's no status; **the verifier proves each one**. The planner should cite requirement ids in DoD checks (`"requirement": "REQ-XF-01"`) | MVP | 1 ✅, 2 (use it) |
| "Evidence verified 73%" bar and discipline chips | DoD checks passing out of total for the run, and per system (power_conversion, pv_generation, substation…). The judge also reports `requirements_cited_pct` and `requirements_verified_pct` | MVP | 3, with 1 ✅ |
| Green/red pins plus a detail drawer | Pin colour comes from the latest verdict for that asset. The drawer shows the component code, the photo the agent saw, related requirements with PASS/FAIL/pending, and cited clauses | MVP | 3 |
| "8 of 9 requirements verified" honesty | Our core claim: **DONE only when the verifier passes every check.** Otherwise `ESCALATED`, stated plainly ("not fixed") | MVP | 2 |
| Hermes bot over MCP that learns the project | Lessons from verified runs go into `kb/lessons`, and the next incident retrieves them (the s06 → s06b memory demo). Alike is development-only, per the 10:30 decision | MVP | 2 |
| Scheduled jobs (digest, inspection prep, what I learned) | A "shift report" run after each episode (what changed, what's pending, what was learned), and a phone push with ntfy on escalations | Stretch | 2 |
| Repeated scans over time | Day-over-day scenarios (s06 → s06b on the next day), and a replay of `events.jsonl` | MVP | 1 ✅ / 3 |
| Bot honesty: "I can't tell you which are ready" | The verifier's evidence rule: never infer an outcome from a plan, and say what's unverified | MVP | 2 |
| Grounding DINO detection | Out of scope. We use a photo bank and Nemotron Omni checks of claims against images | No | — |

## Copy-this list (for the viewer, workstream 3)

1. **Dark 3D workspace**: left survey panel with an overall "evidence verified %" bar and per-system chips, right detail drawer, toolbar chips (Assets n · Markers · Outlines · Requirements n/m), keyboard hints, Reset view.
2. **Green/red pins with a selected outline and an ID label** (`INV-3 · Pending`). Monospace IDs.
3. **Detail drawer**: component code, related requirements with REQ ids and status, the evidence photo, and cited clause links.
4. **Inspection wall**: a grid of camera stations with live status badges plus a top-down map. In our version it is driven live by the agent loop. This is the "agents at work" shot.
5. **Light requirements page**: list, progress bar, and a detail view with Conditions / Evidence / Cited sources.
6. **Agent report panel**: grouped by system, requirement IDs with citations, and an explicit "not verified" section.

## Unknowns

- How the Decisions API calls were phrased (no request or response is shown).
- Whether the 3D pins are placed by projecting detections into the splat or by hand.
- How "Evidence verified" is decided: by the VLM, by documents, or manually. The bot says the record shows "what will be inspected… not outcomes".
- What the inspection tour mode (home card 3) looks like; it isn't shown.
- The splat capture tool used isn't named.
