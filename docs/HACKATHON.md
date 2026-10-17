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

## Judging, tracks and rules

The official criteria are on the event's Details tab, which needs a Cerebral Valley login: https://cerebralvalley.ai/e/crusoe-recursive-agents-hackathon/details
Replace this section with the official text once someone signs in and reads it.

Unconfirmed, from `docs/PLAN.md` (Benjamin, Oct 17 09:47 PDT):
- Judges weight technicality at 40%.
- There are three tracks; two are named "Done Means Done" (our primary) and "Shared Brain".
- A side prize, Best Use of Nemotron, is the RTX 5090.
- "Dashboard as the main feature" is a banned project type.
- The repo must be public.
- The README must have a "Built during the hackathon" section that separates our code from third-party pieces, or the entry is disqualified.

Unconfirmed, from Jonathan's agent (Alike decision, Oct 16-17):
- Submissions are due Oct 18 00:00 UTC, which is 5:00 PM PDT Oct 17, with a 1-hour grace period to 6:00 PM PDT.
- That time was inferred from the event page's embedded data, not stated in words.
- As of Oct 16 it found no published judging criteria, judges, tracks or prize split.

These two sources disagree on whether tracks and weights exist, so confirm with organizers or the Details tab before planning around the 40% figure.

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

See `docs/research/past-winners.md`.
