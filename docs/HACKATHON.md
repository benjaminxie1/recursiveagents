# Recursive Agents Hackathon: rules, scoring, logistics

Every coding agent on this team reads this file at the start of a session.
Each fact says where it came from.
"Confirmed" means read on an official page; "unconfirmed" means a teammate's note that still needs checking against the official details page.

## Event (confirmed, public event page, read Oct 17 2026 09:55 PDT)

- The Recursive Agents Hackathon, hosted by Cerebral Valley and Crusoe: https://cerebralvalley.ai/e/crusoe-recursive-agents-hackathon
- Saturday Oct 17 2026, 9:00 AM to 10:00 PM PDT, San Francisco, in person only, during the PyTorch Conference.
- Teams of up to four. Ours: Will (Will-code112223), Jonathan Tsang, Benjamin Xie (benjaminxie1, repo owner).
- Prizes: $25,000 in cash and an NVIDIA RTX 5090.
- Must build on Crusoe Managed Inference with open models, including NVIDIA Nemotron.
- Theme: "Build a team of agents you can walk away from."
- The page asks for agents that take a goal, plan, spin up sub-agents, delegate, talk to each other, check their own progress, recover when things break, and keep going.
- It names persistent loops that rarely need a human, agent teams and delegation, memory, and long-running inference as the target, not one-shot agents.

## Judging criteria (official, pasted by Will from the organizers, Oct 17 2026)

- **Technicality (40%):** How difficult is this project to recreate? Does this involve technicality beyond your average hackathon project? Are you impressed with what they built in such a short time?
- **Live Demo (20%):** How is the actual demo of the project? Does it impress you? Is it well-engineered and working?
- **Creativity and Originality (25%):** How creative is the project? Is this something you have never seen before?
- **Future Potential and AI Impact (15%):** Does this project point toward an interesting future for AI? Does it advance how we think about AI capabilities, autonomy, learning, evaluation, or agentic systems?

The weights add up to 100%.
The demo is judged live, so a recorded run is only a fallback.

## Tracks and other rules

The official Details tab needs a Cerebral Valley login: https://cerebralvalley.ai/e/crusoe-recursive-agents-hackathon/details

Unconfirmed, from `docs/PLAN.md` (Benjamin, Oct 17 09:47 PDT):
- There are three tracks; two are named "Done Means Done" (our primary) and "Shared Brain".
- A side prize, Best Use of Nemotron, is the RTX 5090.
- "Dashboard as the main feature" is a banned project type.
- The repo must be public.
- The README must have a "Built during the hackathon" section that separates our code from third-party pieces, or the entry is disqualified.

Unconfirmed, from Jonathan's agent (Alike decision, Oct 16-17):
- Submissions are due Oct 18 00:00 UTC, which is 5:00 PM PDT Oct 17, with a 1-hour grace period to 6:00 PM PDT.
- That time was inferred from the event page's embedded data, not stated in words.
- As of Oct 16 it found no published judging criteria, judges, tracks or prize split.

The judging weights are now confirmed; tracks, the dashboard ban, the README rule and the deadline still need checking with organizers.

## Schedule we are working to

- Hacking starts 10:45 AM (from `docs/PLAN.md`).
- Feature freeze 3:30 PM, backup demo recorded by 4:15 PM, submit by 4:50 PM.
- Treat 5:00 PM as the hard deadline until organizers confirm otherwise.

## Credits and accounts

- Crusoe gives $50 in API credits per participant through this form: https://forms.gle/JdEvMGSb4TURH1Tq8
- The form asks for name, Crusoe organization ID (Admin, bottom left of the console) and the email on the Crusoe account.
- A credit card must be on file to claim the credits, and usage past $50 is charged to it.
- A card on file also raises rate limits from 30 to 600 requests per minute per model.
- Crusoe recommends one teammate creates the organization and invites the others, so everyone makes their own key in the same org.
- Sign up: https://console.crusoecloud.com/request-foundry
- Set each key to expire Oct 18 2026.
- Keys live only in `.env`, which is gitignored; keys can contain `$`, so single-quote them in a shell.

## Resources

- Crusoe hackathon guide: https://github.com/crusoecloud/crusoe-developer-hub/tree/main/examples/hackathons/crusoe-cv-recursive-agents-hackathon
- Crusoe API gotchas for agents are in `CLAUDE.md`; the full reference is the skill file in that guide.
- NVIDIA Nemotron: https://developer.nvidia.com/topics/ai/nemotron
- NVIDIA NemoClaw (sandboxed always-on agents on OpenShell): https://www.nvidia.com/en-us/ai/nemoclaw
- NemoClaw needs Docker or Colima, Node 22.19+, 20 GB disk and about 8 GB RAM, and supports a custom OpenAI-compatible provider such as Crusoe.
- It is alpha and "supported with limitations" on Apple Silicon, so it is not on the critical path unless the team decides to run the loop inside a sandbox.

## What past winners did

Full report with sources: `docs/research/past-winners.md` (researched Oct 17 2026).
The only full Cerebral Valley rubric on record (Built with Opus 4.6) was Demo 30%, Impact 25%, model use 25%, Depth and Execution 20%, and finalists played pre-recorded 3-minute videos.
Here technicality is 40%, so engineering counts about twice as much, and the demo is live rather than recorded.

What won, across Cerebral Valley, Crusoe, NVIDIA and other agent hackathons:
- A measured before-and-after number beat more polished demos (Nemotron Seoul grand prize).
- A checker inside the loop: winners graded their output against something outside the model, or used a second model to check the first.
- Crusoe's earlier track winners (Argus, Leavitt) were ops agents that kept working through failures.
- One concrete problem with a named user, done end to end, beat broad platforms.
- Deep use of the sponsor stack, with each model's role explained, beat a basic integration.

What that means for GridTwin, by criterion:
- Technicality (40%): the hard-to-recreate parts are the definition-of-done compiler, the verifier that re-runs checks on its own, the supervisor's loop detection, and visual checks rendered from the splat twin; show all of them working, not on slides.
- Live Demo (20%): the live run has to work on venue Wi-Fi, so cache the splat and footage locally, pre-warm the prefix cache, and keep the recorded run only as a fallback.
- Creativity (25%): a separate verifier on its own is not new, because past winners did it; the new part is agents writing their own runnable definition of done and checking it against a 3D twin of the plant, so put that up front.
- Future Potential (15%): pitch the false-done rate as an evaluation result for autonomous agents in general, not only for power plants.

What won elsewhere, applied to GridTwin:
- Lead with the false-done rate from real runs, baseline against our loop, over several runs.
- Show the verifier rejecting a "done" claim and the team recovering, live.
- Inject a failure on stage (kill a sub-agent, force a 429) and let judges watch the supervisor route around it.
- Start the goal at the top of the pitch, walk away, and come back to a verified result and a run log.
- Keep Nemotron in the core roles, say why each model was picked, and report cost or tokens per finished task.
- Show memory as fewer steps on a second, similar incident.
- Record a clean backup run early, and make the README carry the architecture, results table and "Built during the hackathon" section in case judging is async.
