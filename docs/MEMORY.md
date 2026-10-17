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

## Open questions

- 10:05 Will: official judging criteria, tracks and deadline are unconfirmed; someone signed in to Cerebral Valley should paste the Details tab into `docs/HACKATHON.md`.
- 10:05 Will: Jonathan's agent has a separate local workspace (`recursive-agents-hackathon`, with its own `crusoe.py` client); agree at kickoff that this repo is the one codebase.
- 10:05 Will: who creates the Crusoe organization and invites the others.
- 10:05 Will: the reference video and splat source (`docs/PLAN.md` section 9).
- 10:06 Will: official judging criteria are now in `docs/HACKATHON.md` (Technicality 40%, Live Demo 20%, Creativity 25%, Future Potential 15%); tracks, deadline and other rules are still unconfirmed.

## Lessons

- 10:05 Will: Crusoe rate limits are per model, so spread roles across models and put a card on file first.
- 10:07 Will: Benjamin rewrote `claude/sweet-faraday-yssuo9` at 10:02 (setup commits squashed into b4ef25f, no file changes); a clone from before then has unrelated history, so back up local work, then `git fetch && git reset --hard origin/claude/sweet-faraday-yssuo9` and re-apply it. Check `git status -sb` for ahead/behind before every push.
