# GridTwin: game plan

**Pitch (one line):** Give the agent team a vague goal like "get Unit 2 back to full output safely". It writes its own definition of done as checks it can run, works the problem on a Gaussian-splat digital twin of the plant, has an independent verifier prove each claim, and only pages a human when it truly has to.

**Primary track: Done Means Done.** Shared Brain (Alike MCP) is a supporting feature, not the headline. We pick one track and win it outright instead of spreading thin across three.

**Side prize:** Best Use of Nemotron (RTX 5090). Every core role runs on Nemotron except the verifier. The verifier uses a different model family on purpose, and that choice is part of the story.

---

## 1. What we're copying from the reference video

> ⚠️ The video (`gaussian_refernece.mp4`) is on Ben's laptop, and the cloud session couldn't open it. The flow below is my best guess at a construction-site splat demo. **Correct it at kickoff** and update this section.

| Reference demo (construction) | Our version (energy plant) |
|---|---|
| Phone or drone walkthrough → Gaussian splat of the site | Walkthrough of a "plant" (solar array, inverter cabinet, transformer yard, or a tabletop mock) → splat |
| CV spots progress and defects on the splat | Nemotron Omni spots faults in footage and renders: hot spots, soiling, broken panels, open cabinets, leaks, gauge readings |
| Agents compare against the schedule or plan | Agents compare against plant telemetry, the expected state, and OEM specs (found with You.com, with citations) |
| A decision gets made (reorder, flag, assign) | A decision gets made (curtail, dispatch a tech, isolate, restart) through the decision API, and the verifier checks the result |
| 3D view with annotations | 3D splat viewer with pins where the agents found something. Click a pin to see evidence and verifier verdicts |

## 2. Architecture

```
            one-line goal
                 │
        ┌────────▼─────────┐   reads/writes   ┌───────────────────┐
        │  PLANNER         │◄────────────────►│ SHARED BRAIN      │
        │  Nemotron Super  │                  │ Alike MCP         │
        │  writes DoD as   │                  │ (plant facts,     │
        │  runnable checks │                  │  past incidents,  │
        └────────┬─────────┘                  │  what worked)     │
                 │ tasks                      └───────────────────┘
   ┌─────────────┼──────────────┬───────────────┐
   ▼             ▼              ▼               ▼
INSPECTOR     DIAGNOSER      RESEARCHER      OPERATOR
Nemotron Omni Lightning      Lightning +     Lightning →
(video/img of (telemetry +   You.com (OEM    decision API /
 splat views) sim queries)   manuals, cites) plant sim actions
   └─────────────┴──────┬───────┴───────────────┘
                        ▼ claims + evidence
               ┌──────────────────┐
               │ VERIFIER         │ gpt-oss-120b, a different family
               │ runs DoD checks  │ re-renders the splat view, re-queries the sim,
               │ independently    │ never trusts worker prose
               └────────┬─────────┘
                        ▼
               ┌──────────────────┐
               │ SUPERVISOR       │ drift/loop detector:
               │ continue | replan│ same action repeated, checks not improving,
               │ | stop | page    │ budget burn, goal drift (embedding distance)
               └──────────────────┘
```

### Key pieces

1. **Plant simulator (`gridtwin/sim.py`).** A deterministic Python model of a small plant: PV strings → inverters → transformer → grid meter, plus a cooling loop. It supports fault injection (string ground fault, inverter overtemp, soiling, transformer oil temp, tripped breaker). This is what makes "done" *checkable*. We need it no matter what, because there's no real plant to hand.
2. **Definition-of-done compiler.** The planner emits a JSON list of checks. Each check has a type from a small, safe DSL rather than free code:
   - `telemetry`: e.g. `inverter_2.output_kw >= 0.95 * rated for 3 consecutive ticks`
   - `visual`: e.g. take a photo from named viewpoint `cabinet_B` and have vision confirm the "door closed, no smoke, no warning LED"
   - `citation`: the action is backed by an OEM or standards source (You.com URL) that the verifier fetched
   - `safety`: an invariant that must hold the whole time (no breaker closed onto a fault, no overtemp)
3. **Verifier.** It gets only the checks and raw tool access. It never sees the worker's narrative, so it can't be talked into a pass.
4. **Supervisor.** Hard rules plus a small LLM judgment: loop detection (action hash repeats), progress (fraction of checks passing over time), budget, and escalation. "Ping a human" is a real channel, e.g. a push notification or Slack, with a one-paragraph brief.
5. **Shared Brain (Alike MCP).** Each agent reads the plant profile and relevant past incidents before acting, then writes back a lesson after the verifier's verdict. Only *verified* outcomes get written, which keeps memory lean and free of conflicts.
6. **Splat twin and viewer.** A web viewer (Spark or `@mkkellogg/gaussian-splats-3d`) loads the plant splat. Named camera viewpoints map to equipment IDs. Agent findings show up as 3D pins, and the verifier's visual checks use the same named viewpoints, so "visual" checks are reproducible.
   - **Static stage, named viewpoints (Will 12:04, built by Jonathan 12:16; supersedes Benjamin's 11:44 note from commit 71e865e, struck through below).** The splat is a static 3D stage with pins, and nothing renders it today. `get_camera_view` returns a photo from the bank that matches the asset's hidden state, so the photos carry the visual state. Each demo asset has three named viewpoints (`assets/photos/viewpoints.yaml`). For each `visual` check the verifier picks one of the asset's viewpoints that no worker called during the run, so workers can't cherry-pick evidence, and the verdict records which one it used. No C1 change. In a real plant the splat is refreshed by a nightly drone capture, with pan-tilt and fixed cameras carrying the state in between (`docs/JUDGE_QA.md` Q4–Q5).
   - ~~**Re-rendered, not static (Benjamin, 11:44; supersedes the 10:30 static-stage decision).** `get_camera_view` renders the splat from the viewpoint's camera pose, and the verifier picks its own viewpoints, including ones the workers never looked from, so workers can't cherry-pick evidence. In the sim the hidden fault state is painted into the render, and the pitch says so.~~ Superseded at 12:04 (`docs/MEMORY.md`); kept for the record.

## 3. The number that wins: claimed done vs. actually done

Judges weight **Technicality at 40%**. The most convincing technical result is a measured one:

- **Benchmark:** 10 to 15 fault scenarios in the sim, each with ground truth. Include tricky ones: two simultaneous faults, a fault that *looks* fixed in telemetry but is still visible on camera, and one where the right answer is "stop and page a human".
- **Baseline:** the same workers with no DoD and no verifier. The agent just says when it's done.
- **Ours:** the full loop.
- **Report:** `claimed_success`, `true_success`, **false-done rate** (claimed but not true), human pings, tokens and cost.
- Target headline: *"Baseline claimed done 90% of the time but was right 40%. GridTwin claimed 70% and was right 68%: a false-done rate of 3% vs 56%."* Use whatever the real numbers turn out to be. Never fake them.

This also gives the demo a "before and after" moment, and it answers the track's exact question.

## 4. Demo script (3 minutes live, plus a 1-minute video)

1. **(0:00) Hook.** "Agents say 'done' when they aren't. In a power plant, that's how you start a fire." Show the 3D splat of the plant.
2. **(0:20) Give the goal.** Type `Unit 2 is underperforming, get it back to full output safely.` and walk away from the keyboard.
3. **(0:30) Watch it work.** The planner shows its definition of done (a list of checks). Inspector pins appear on the splat. The researcher cites the OEM manual through You.com. The operator acts.
4. **(1:30) The catch.** The worker claims done. The verifier re-renders the cabinet view, sees the warning LED is still on, and rejects it. The supervisor replans, and the second fix passes all checks.
5. **(2:10) Memory.** Run a second, similar incident. It reads the Shared Brain and finishes in fewer steps. Show the step count.
6. **(2:30) The number.** Show the benchmark table: false-done rate, baseline vs. ours.
7. **(2:50) Close.** "Done means done: verified, cited, and on Nemotron on Crusoe."

**Have a recorded backup run** in case Wi-Fi or rate limits fail live. Pre-warm the prefix cache before going on stage.

## 5. Timeline (hacking 10:45 → submissions 5:00)

| Time | Milestone |
|---|---|
| 10:45–11:15 | Keys working (`python -m scripts.smoke_test` green). Confirm model IDs. Capture plant footage and start splat processing. Resolve the open questions below |
| 11:15–12:30 | Sim and fault injection. Agent loop skeleton: planner → workers → verifier → supervisor, running end-to-end in the terminal |
| 12:30–1:30 | DoD check DSL and the verifier. You.com researcher. Alike MCP read/write |
| 1:30–2:30 | Splat viewer with viewpoints and pins. Vision checks against renders and footage |
| 2:30–3:30 | Benchmark harness. Run baseline vs. ours. Fix whatever the numbers expose |
| 3:30–4:15 | **Feature freeze.** Polish the demo path only. Record the backup run |
| 4:15–4:50 | 1-minute video, README provenance section, confirm the repo is public, submit |
| 4:50 | Submitted, with a 10-minute buffer |

## 6. Team split (up to 4)

- **A: Agent loop.** Planner, verifier, supervisor, DoD DSL, benchmark.
- **B: Sim and tools.** Plant sim, fault scenarios, decision API / operator tools, You.com researcher.
- **C: Splat and vision.** Capture, splat training/export, viewer, viewpoint renders, Nemotron Omni prompts.
- **D: Memory and demo.** Alike MCP integration, demo script, video, README, submission.

## 7. Judging checklist

- [ ] Repo is **public**
- [ ] README has a **"Built during the hackathon"** section listing what we wrote, versus third-party pieces (splat viewer lib, Alike, You.com, the capture app). Missing this means disqualification
- [ ] The main feature is the **autonomous loop**, not a dashboard. The viewer exists to *show the loop's evidence*. Pitch it that way, because "dashboard as main feature" is a banned project type
- [ ] Nemotron is in every core role, and we can say why each model was picked
- [ ] A live demo works offline from cached splat and footage, with only inference over the network
- [ ] Have a 1–2 minute answer ready for "how do you know it's actually done?" and "what stops it from looping?"
- [ ] Adding a payment method raises Crusoe limits from 30 to 600 RPM per model. **Do this first**, or parallel agents will hit 429s

## 8. Integrations (researched)

**Decision API: Jev (TypeSafe) or OpenAI Decisions.** Both are "System One" decision APIs. You send a state and typed questions, and get back probabilities, not prose. This is the operator's safety gate and the verifier's typed completion check. Details in `docs/SIMULATOR.md` §3.
- Jev: `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer $JEV_API_KEY`, model `jev-latest`, with questions keyed by name and types `choice` / `score` / `noul`. Docs at docs.typesafe.ai. Also on OpenRouter as `typesafe/jev-1.14-20261014`. *Signups were reportedly paused on Sep 22, so get a key early or use OpenRouter.*
- OpenAI Decisions: `POST https://api.openai.com/v1/decisions`, model `gpt-6-luna`, with questions as an array and types `predicate` / `choice` / `score`. Public beta since Oct 6, and it **accepts image input**, which our visual checks can use.
- Source for the request shapes: the agentgateway post comparing the two (Oct 12). Confirm field names against the official docs at kickoff.

**Shared Brain: Alike MCP** (a16z Speedrun SR006, alike.dev / docs at alike.help).
- MCP server `https://alike.network/api-general/mcp`, Streamable HTTP, **OAuth in the browser (no API key)**. Each client signs in once. You need an account on alike.network.
- Alike is built for *coding agents* and organized around **work items**: branch, PR, or Linear issue. Our mapping:
  - one plant incident = one work item (anchor: a git branch like `incident/s06-inv3-comms`)
  - a verified lesson = `record_decision` (rationale plus anchors)
  - before work: `resolve_work_item` or `search_entities` / `search_environment`, so the agent starts with past decisions
  - agent ↔ agent: `message_agent_session` (`tell` / `ask` / `propose`)
- Our Python agents connect with the `mcp` SDK's Streamable HTTP client and OAuth provider. The token is cached locally after one browser approval on the demo laptop. **Do the OAuth step first thing.** It can't be done in a headless cloud box.
- Shared event Wi-Fi means a shared Alike rate limit (`Rate limit exceeded. Retry after N`). Batch the writes. A local JSON mirror is a fallback if Alike is down.

## 9. Still open

1. **Reference video**: not reachable from the cloud session (see the chat). The flow in section 1 is still a guess.
2. **Splat source**: see `docs/SIMULATOR.md` §4 for the options (licensed capture, image-to-3D from a real photo, or our own stand-in capture).
