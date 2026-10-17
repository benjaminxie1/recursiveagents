# Benjamin's memory

Working memory for Benjamin's agent sessions (benjaminxie1). It stands in for Alike until Alike works in Benjamin's cloud sessions.
Team-wide decisions still go in `docs/MEMORY.md`. This file holds Benjamin's own context, research, and to-dos.
Same rules as `docs/MEMORY.md`: append `HH:MM name: text` lines (Pacific time), and never rewrite old lines.

## Preferences

- 10:18 Benjamin: no Claude authorship on commits (no Co-Authored-By or session trailers). Commits are authored as `benjaminxie1 <87211469+benjaminxie1@users.noreply.github.com>`.
- 10:18 Benjamin: ask before pushing. Never force-push over a teammate's commits without checking them first and keeping their authorship.
- 10:18 Benjamin: keep replies short.

## Research (done, don't redo)

- 10:18 Benjamin: decision API = TypeSafe **Jev** `POST https://api.typesafe.ai/v1/systemone`, model `jev-latest`, questions keyed by name, types `choice` / `score` / `noul`. Signups were reportedly paused on Sep 22, and it's also on OpenRouter as `typesafe/jev-1.14-20261014`. Or **OpenAI Decisions** `POST /v1/decisions`, model `gpt-6-luna`, questions as an array, types `predicate` / `choice` / `score`, public beta since Oct 6, accepts images. Details in `docs/SIMULATOR.md` §3.
- 10:18 Benjamin: **Alike** = a16z Speedrun SR006, docs at alike.help (index: alike.help/llms.txt). MCP `https://alike.network/api-general/mcp`, Streamable HTTP, browser OAuth only (no API key). Tools: `resolve_work_item`, `record_decision`, `search_entities`, `search_environment`, `message_agent_session`. Mapping is in `docs/PLAN.md` §8.
- 10:18 Benjamin: Crusoe reference = the skill file in crusoecloud/crusoe-developer-hub `examples/hackathons/crusoe-cv-recursive-agents-hackathon/`. Key points are in `CLAUDE.md`.
- 10:27 Benjamin: Jev works with our key (served `jev-1.13.0`, ~430 input tokens). Response shape: `answers.<name>` = `{type:"choice", choice, confidence, probabilities:{...}}` or `{type:"noul", noul: <prob>}`. Test (close a breaker with a ground-fault alarm): `reject` 0.98, unsafe 0.91.

## Setup state

- 10:18 Benjamin: Alike is connected on Benjamin's laptop (`claude mcp list` shows ✔). It's added as a claude.ai connector, but cloud sessions say "needs sign-in again": re-authorize at claude.ai/customize/connectors. Benjamin isn't in Will's Alike workspace yet.
- 10:18 Benjamin: Crusoe key works (Will, 10:14). Cloud sessions still need `CRUSOE_API_KEY` in the environment settings.
- 10:18 Benjamin: the reference video `gaussian_refernece.mp4` is only on Benjamin's Mac. To have a session analyze it frame by frame, upload it to Google Drive.

## To do

- 10:18 Benjamin: get keys for Jev and/or OpenAI Decisions, plus You.com (`YDC_API_KEY`).
- 10:18 Benjamin: get invited to Will's Alike workspace.
- 10:18 Benjamin: make the repo public before 5:00 PM.
- 10:18 Benjamin: choose the splat source (`docs/SIMULATOR.md` §4). The visual state comes from a bank of real-world photos.
- 10:18 Benjamin: feature work starts at 10:45. Build order is in `docs/SIMULATOR.md` §6.
- 10:25 Benjamin: my working branch is `claude/sweet-faraday-yssuo9`; `main` is shared. Commit signing is off in this repo (`commit.gpgsign false`) so commits stay under my name.
- 11:29 Claude: commit signing is now on in Benjamin's sessions (supersedes the 10:25 note). The SSH key comes from env `GIT_SIGNING_KEY` (base64) and is written to `~/.ssh/git_signing_key`. The repo-local config sets `gpg.format ssh`, `commit.gpgsign true` and `gpg.ssh.program ssh-keygen`, which overrides the container's `/tmp/code-sign`. Key fingerprint: SHA256:r5+SCC2MC9wmJIlNByrQLOH4tccLZf7qVCaDWOazEgo.
