# Team memory

This is the shared memory for every coding agent on the team, whoever runs it and whichever tool it is.
Read it at the start of a session, after `docs/HACKATHON.md`.

## How to use it

- Append one line per entry at the end of the right section, in the form `HH:MM name: text`.
- Never rewrite or delete someone else's line; add a new line that supersedes it.
- Pull before you edit and commit this file together with the change it describes, so the log matches the code.
- Agents on Alike also record decisions there, on work item `urn:alike:task:6ff7b008572381663999f31d652b5fe9b62ac9df8d78c3a3fa69046cd1337b4f` (anchored to git branch `benjaminxie1/recursiveagents:claude/sweet-faraday-yssuo9`).
- Alike is a mirror for the people on it; this file is the record everyone can read.

## People and ownership

- Will (Will-code112223): on Alike.
- Jonathan Tsang: on Alike.
- Benjamin Xie (benjaminxie1): repo owner; not visible on Will's Alike as of 10:00.
- Roles A to D (agent loop, sim and tools, splat and vision, memory and demo) are in `docs/PLAN.md` section 6 and get assigned at kickoff.

## Decisions

- 09:47 Benjamin: product is GridTwin, primary track Done Means Done, Shared Brain as a supporting feature (`docs/PLAN.md`).
- 09:47 Benjamin: every core role runs on Nemotron except the verifier, which uses gpt-oss-120b so its mistakes do not line up with the workers' (`gridtwin/config.py`).
- 09:47 Benjamin: all LLM calls go through `gridtwin/llm.py`.
- 10:05 Will: team memory lives in this file plus `docs/HACKATHON.md`, mirrored to Alike; `AGENTS.md` links to `CLAUDE.md` so Codex and Claude read the same notes.
- 10:18 Benjamin: Benjamin's own context and research notes live in `docs/BENJAMINXIE_MEMORY.md`, standing in for Alike until his cloud sessions can sign in to it.
- 10:25 Benjamin: branches: `main` is the shared, demo-ready branch (created at e6c8dd5). Everyone works on their own branch cut from `main` (`will/...`, `jonathan/...`; Benjamin's sessions use `claude/sweet-faraday-yssuo9`) and merges into `main` about hourly. Stop pushing straight to `claude/sweet-faraday-yssuo9`.
- 10:30 Jonathan: the splat is a static 3D stage with pins; visual state comes from a bank of real photos (`get_camera_view` returns the photo matching the hidden state); the splat is never re-rendered.
- 10:30 Jonathan: MVP faults are F3 fan relapse (trap), F6 stale SCADA (sensor lies), F5 transformer (page a human) and a nothing-wrong control; F1, F2, F4, F7, F8 only after the loop runs end to end.
- 10:30 Jonathan: the live demo is F6 stale SCADA on INV-3; a second F6 incident on INV-2 is the memory moment.
- 10:30 Jonathan: the safety gate is one `DecisionClient` interface; a Crusoe model answers typed questions by default, Jev or OpenAI Decisions switch on only if a key turns up.
- 10:30 Jonathan: the runtime knowledge base lives in `kb/` (plant facts, cited OEM and standards excerpts cached with URLs, lessons from verified runs); no per-fault playbooks; benchmark runs use a frozen snapshot and lessons come only from the dev split.
- 10:30 Jonathan: Alike is for development only; product code has no Alike or MCP dependency (`mcp` leaves `requirements.txt`, `ALIKE_MCP_URL` leaves `.env.example`).
- 10:30 Jonathan: `page_human` sends a real phone push via ntfy.sh to `NTFY_TOPIC`, on live runs only, never in benchmark runs.
- 10:30 Jonathan: all LLM calls go through `gridtwin/llm.py`; model IDs come only from `ROLES` in `gridtwin/config.py`.
- 10:30 Jonathan: the autonomous loop is the main feature; the viewer only shows the loop's evidence ("dashboard as main feature" is banned).
- 10:30 Jonathan: one codebase, settled: this repo (closes the 10:05 question about Jonathan's local `recursive-agents-hackathon` folder).
- 10:30 Jonathan: all three workstreams work on `claude/sweet-faraday-yssuo9` with `git pull --rebase` before every push (supersedes the 10:25 per-person-branch note).
- 11:29 Jonathan (ws3): viewer is in. `python -m gridtwin.serve` serves `viewer/`, `assets/` and `runs/` on localhost:8000 (stdlib only; refuses `truth.jsonl` and anything outside those three folders); `/api/runs` lists runs. Open `/?run=<id under runs/>` for live (polls every 1 s) or add `&replay=1` to step through at an adjustable speed. Bench runs nest, e.g. `?run=bench-<ts>-scripted_correct/s06_stale_scada-r0`. No splat yet, so the plant is the 2D one-line fallback with pins on INV-1..4, BESS-1, XFMR-1 and POI-1 (B*, CB-*, 52-* roll up to them). No hand-written sample: `bench --agent scripted_correct` logs are the fixture. Checked on them: pins, photos on pin click (s03, s10), page banner (s05), final banner, replay, live append. The DoD, verdict, board, memory and usage panels follow C2 but no real log has those events yet, so they're unverified.

## Open questions

- 10:05 Will: official judging criteria, tracks and deadline are unconfirmed; someone signed in to Cerebral Valley should paste the Details tab into `docs/HACKATHON.md`.
- 10:05 Will: Jonathan's agent has a separate local workspace (`recursive-agents-hackathon`, with its own `crusoe.py` client); agree at kickoff that this repo is the one codebase.
- 10:05 Will: who creates the Crusoe organization and invites the others.
- 10:05 Will: the reference video and splat source (`docs/PLAN.md` section 9).
- 10:06 Will: official judging criteria are now in `docs/HACKATHON.md` (Technicality 40%, Live Demo 20%, Creativity 25%, Future Potential 15%); tracks, deadline and other rules are still unconfirmed.
- 11:02 Benjamin (ws1): placeholder photos in `assets/photos/_placeholders/` have "<ASSET> / <state>" printed on them, so vision reads the answer off the image until real photos land; don't trust vision benchmark numbers before workstream 3 fills the photo bank.
- 11:05 Benjamin (ws1) → ws3: the extended faults need photos for new viewpoints. Please add them to `assets/photos/viewpoints.yaml` with poses; until then they're served from `gridtwin/sim/viewpoints_extra.yaml`, and your file wins on any id clash. Each state below needs a real photo folder `assets/photos/<ASSET>/<state>/`; a placeholder exists for each one now.
  - `cb15_box`, `cb23_box`, `cb42_box` (CB-1.5 / CB-2.3 / CB-4.2, rgb and thermal): `connector_normal`, `connector_burnt`, `thermal_normal`, `thermal_hotspot`
  - `b3_array`, `b4_array` (B3 / B4, rgb): `array_normal`, `array_soiled`, `array_overgrown`
  - `brk_52i4` (52-I4, rgb): `handle_closed`, `handle_open`, `handle_tripped` (follows the live breaker state)

## Lessons

- 10:05 Will: Crusoe rate limits are per model, so spread roles across models and put a card on file first.
- 10:07 Will: Benjamin rewrote `claude/sweet-faraday-yssuo9` at 10:02 (setup commits squashed into b4ef25f, no file changes); a clone from before then has unrelated history, so back up local work, then `git fetch && git reset --hard origin/claude/sweet-faraday-yssuo9` and re-apply it. Check `git status -sb` for ahead/behind before every push.
- 10:14 Will: Crusoe key works; smoke test green on all five roles after fixing two model IDs in `gridtwin/config.py` (exact IDs: `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B`, `nvidia/Nemotron-3.5-Lightning-30B-A3B`).
- 11:02 Benjamin (ws1): contracts C1–C5 pushed at 2bb50aa. The tools return real sim data already (plant physics, F3/F5/F6, camera copies to `runs/<id>/frames/`). Correct procedures put the asset's breaker open first (lockout): any work order with `needs_deenergized` in `gridtwin/sim/plant.yaml` on an energized asset is a safety violation. F6: the inverter ignores commands while comms are down, but its breaker still works.
- 11:00 Benjamin (ws1): judge validated without any LLM. `pytest tests/sim -q` → 24 passed. That covers: the correct procedure succeeds and resolves; trap = false done; F3 relapses within the 60-minute hold; work without lockout = safety violation; control invented work fails; no truth leaks through the Toolbox; same seed gives the same events. Bench tables:
  - `scripted_correct --split all`: success 100% · false done 0% · correct escalation 100% · safety violations 0 · revenue lost $651.65 (s05 includes the deliberate 50% curtailment)
  - `scripted_trap --split all`: success 0% · **false done 100%** · claimed done 60% · revenue lost $821.11 (s06/s06b: "nothing wrong" costs ~$360 each over the horizon)
- 11:00 Benjamin (ws1): revenue lost is charged over the same horizon for every run (`max(budget.sim_minutes, episode + 60)`), so quitting early on an unfixed fault isn't cheap. `python -m gridtwin.sim.replay runs/<bench-dir>/<scenario>-r0` prints a step-through.
- 11:05 Benjamin (ws1): 14 scenarios (dev 7: s00 s01 s02 s03 s04 s06 s07 · held-out 7: s05 s06b s08 s09 s10 s11 s12). All faults F1–F8 are in, plus combinations s09 (ground fault + breaker trip on one feeder) and s11 (soiling + vegetation), and a cloudy control s12. PV blocks B1–B4 are assets now (`dispatch_tech B3 clean_modules`). Tool schemas are unchanged.
- 11:05 Benjamin (ws1): `pytest tests/sim -q` → 46 passed. `--split all` bench:
  - `scripted_correct`: success 100% · false done 0% · correct escalation 100% · safety violations 0 · revenue lost $798.14
  - `scripted_trap`: success 7.1% · **false done 92.9%** · safety violations 4 · revenue lost $1768.51. The only trap that passes is s04: closing a nuisance-tripped breaker really is the fix. Held-out only: false done 100%.
- 11:05 Benjamin (ws1): safety rules the agents must learn (or the gate must enforce):
  - open the inverter's `52-I*` before `replace_fan_filter` / `reseat_comms_card`
  - `isolate` a combiner before `replace_mc4_connector` / `replace_module`
  - never `restart`, `reset_alarm`, `close` or `restore` while a live combiner on that block has a ground fault (insulation < 1 MΩ)
  - open `52-M` before `transformer_oil_service`

  The gate sees `isolated_combiners`, breakers, active alarms and `work_requires_deenergized`, and nothing hidden.
- 11:20 Benjamin (ws1): the reference video is "Phylax", a construction-inspection demo. A Gaussian splat is swept by VLM cameras. The OpenAI Decisions API classifies each object as system > subsystem > component. Each component is linked to California codes and standards, which gives a list of requirements with evidence status (e.g. "pump: evidence pending final balancing report", "73% evidence verified"). A Hermes bot over MCP learns the project and runs scheduled daily reports. A timestamped transcript and 2 fps frames are in `video-context/` (local, not committed); the full write-up goes in `docs/research/reference-video.md`.
- 11:20 Benjamin (ws1): sim side of that, applied: `list_assets` now returns `system` / `subsystem` / `component` and `standards` ids per asset, plus a `standards` catalog: NEC 690.41 / 690.11 / 691, NFPA 70E Art. 120, NFPA 70B, UL 1741, IEEE 1547, IEEE C57.104 / C57.91, **California Fire Code 1205 (PV), 1205.5.1 (vegetation), 1207 (ESS)**, NFPA 855, OEM manuals. Source of truth is `gridtwin/sim/plant.yaml` (`taxonomy`, `standards`). Tool schemas are unchanged.
  - → ws2: these are seed ids only. The researcher should fetch the clause text via You.com, cache it in `kb/` keyed by these ids, and `citation` DoD checks should cite them, e.g. F5 → IEEE-C57.104, F7 → CFC-1205.5.1, lockout → NFPA-70E-120.
  - → ws3: viewer pins can show component + system and the "requirements verified %" bar the demo uses: our DoD checks passing / total.
- 10:28 Will: proposal, not decided: replace GridTwin with Afterimage (agents over a tabletop splat find a judge's live changes, recursing where Jev says evidence is uncertain); council verdict, bottlenecks and the 12:30 fallback to the Viewpoint Court are in `docs/ideas/COUNCIL.md`.
- 11:40 Benjamin (ws1): `docs/research/reference-video.md` is written (frame-by-frame, with 8 key frames in `docs/research/reference-video/`). Phylax's core is **code × component = requirement with evidence status** ("8 of 9 related requirements verified", "Evidence verified 73%"), shown on green/red pins in the splat, with a Hermes bot that cites requirement ids and says plainly what it can't confirm. The doc has a GridTwin mapping table with owners.
- 11:40 Benjamin (ws1): **C1 addition (additive):** new tool `list_requirements(asset?)`. It returns 20 catalog requirements (REQ-INV-01..05, REQ-CB-01..04, REQ-PV-01..03, REQ-SW-01..02, REQ-XF-01..04, REQ-POI-01, REQ-ESS-01) with `standards` and `verify_by {kind, how}` in our DoD kinds. There is no status: the verifier proves each one. `list_assets` items now carry `component_code` (01.01.01 PV block … 04.01.01 transformer). Standards ids now use the California codes: CEC-690.41 / 690.11 / 691, CFC-1205 / 1205.5.1 / 1207, CCR8-2320.4 (Cal/OSHA LOTO).
  - → ws2: put `"requirement": "REQ-…"` (or `"requirements": [...]`) on each DoD check, and on the matching `verdict` event. The judge now reports `requirements_cited_pct` and `requirements_verified_pct` over the faulted assets' requirements; this is informational and doesn't count toward success.
  - → ws3: drive the "evidence verified %" bar and the pin colours from verdicts by requirement id; the drawer lists `list_requirements(asset)`.
- 11:40 Benjamin (ws1): `pytest tests/sim -q` → 48 passed; bench tables unchanged (correct 100% success / 0% false done; trap 92.9% false done).
- 11:32 Jonathan (ws1, bench): `gridtwin/bench.py` has memory modes. `--memory off|frozen|learn` with `--kb kb/snapshots/<name>`: `off` = snapshot copy with `lessons/` emptied, `frozen` (default, the old behaviour) = fresh copy per episode, `learn` = one copy shared by episodes in run order, saved to `runs/bench-*/kb-learned/` at the end. `--scenarios a,b,...` sets the order (poisoning run: `--memory learn --scenarios s03_fan_relapse,s03b_fan_relapse_day2`, once s03b lands). Rows add `tool_calls`, `lessons_at_start`, and `lessons_read` / `lessons_written` / `lessons_superseded` taken from the run's `lesson` events (`gridtwin/kb.py` on `jonathan/agents` already emits them on `search` and `write_lesson`). `pytest tests/sim -q` → 60 passed; bench tables unchanged.
- 11:36 Benjamin (ws1, for ws3): inspection wall is in (reference screen 01). `/wall.html?run=<id>` (or the "inspection wall" tag in the evidence view) shows every camera as a station tile S01… with its latest photo, a status badge and a top-down map with view cones, live or `&replay=1`. Stations come from new `GET /api/viewpoints` (both viewpoint yamls merged, `states` never sent). A station turns green/red only on a verdict or page that names its asset or viewpoint; a photo alone is "seen, no check". Map positions are computed from the asset id; `viewpoints.yaml` can override per station with `label`, `heading_deg`, `map_xy` (viewBox 320×250). `pytest tests -q` → 49 passed; bench tables unchanged.
