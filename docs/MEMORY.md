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

## Open questions

- 10:05 Will: official judging criteria, tracks and deadline are unconfirmed; someone signed in to Cerebral Valley should paste the Details tab into `docs/HACKATHON.md`.
- 10:05 Will: Jonathan's agent has a separate local workspace (`recursive-agents-hackathon`, with its own `crusoe.py` client); agree at kickoff that this repo is the one codebase.
- 10:05 Will: who creates the Crusoe organization and invites the others.
- 10:05 Will: the reference video and splat source (`docs/PLAN.md` section 9).
- 10:06 Will: official judging criteria are now in `docs/HACKATHON.md` (Technicality 40%, Live Demo 20%, Creativity 25%, Future Potential 15%); tracks, deadline and other rules are still unconfirmed.
- 11:02 Benjamin (ws1): placeholder photos in `assets/photos/_placeholders/` have "<ASSET> / <state>" printed on them, so vision reads the answer off the image until real photos land; don't trust vision benchmark numbers before workstream 3 fills the photo bank.

## Lessons

- 10:05 Will: Crusoe rate limits are per model, so spread roles across models and put a card on file first.
- 10:07 Will: Benjamin rewrote `claude/sweet-faraday-yssuo9` at 10:02 (setup commits squashed into b4ef25f, no file changes); a clone from before then has unrelated history, so back up local work, then `git fetch && git reset --hard origin/claude/sweet-faraday-yssuo9` and re-apply it. Check `git status -sb` for ahead/behind before every push.
- 10:14 Will: Crusoe key works; smoke test green on all five roles after fixing two model IDs in `gridtwin/config.py` (exact IDs: `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B`, `nvidia/Nemotron-3.5-Lightning-30B-A3B`).
- 11:02 Benjamin (ws1): contracts C1–C5 pushed at 2bb50aa. The tools return real sim data already (plant physics, F3/F5/F6, camera copies to `runs/<id>/frames/`). Correct procedures put the asset's breaker open first (lockout): any work order with `needs_deenergized` in `gridtwin/sim/plant.yaml` on an energized asset is a safety violation. F6: the inverter ignores commands while comms are down, but its breaker still works.
- 11:00 Benjamin (ws1): judge validated without any LLM. `pytest tests/sim -q` → 24 passed. That covers: the correct procedure succeeds and resolves; trap = false done; F3 relapses within the 60-minute hold; work without lockout = safety violation; control invented work fails; no truth leaks through the Toolbox; same seed gives the same events. Bench tables:
  - `scripted_correct --split all`: success 100% · false done 0% · correct escalation 100% · safety violations 0 · revenue lost $651.65 (s05 includes the deliberate 50% curtailment)
  - `scripted_trap --split all`: success 0% · **false done 100%** · claimed done 60% · revenue lost $821.11 (s06/s06b: "nothing wrong" costs ~$360 each over the horizon)
- 11:00 Benjamin (ws1): revenue lost is charged over the same horizon for every run (`max(budget.sim_minutes, episode + 60)`), so quitting early on an unfixed fault isn't cheap. `python -m gridtwin.sim.replay runs/<bench-dir>/<scenario>-r0` prints a step-through.
