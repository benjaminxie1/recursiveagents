# Past hackathon winners - research for the Recursive Agents Hackathon

Compiled on Sat Oct 17 2026, the morning of the event, for Will, Jonathan and Benjamin.
Every claim has a URL.
Anything not confirmed from a primary source (organizer, sponsor, or the winners themselves) is marked **unconfirmed**.
Search engines have thin coverage of events from the last few months, so this list favors fewer, confirmed entries over many guessed ones.

## 1. This event: what is public

### 1.1 Official event page

Source: https://cerebralvalley.ai/e/crusoe-recursive-agents-hackathon (fetched Oct 17 2026).

What the page states:
- Schedule: "Sat, Oct 17 at 9:00 AM - 10:00 PM (PDT)" in San Francisco, fully in person, attendees must apply and be approved.
- An optional Crusoe Field Day ran on Fri Oct 16, 1-5 PM, with limited spots.
- Prizes: "$25,000 in cash prizes" plus an NVIDIA RTX 5090.
- Team size: "Teams may have up to four members."

The page's embedded event data (raw HTML, fetched with curl on Oct 17 2026) also contains `"hackathonSubmissionDueDate":"2026-10-18 00:00:00"` and `"submissionDueDateGracePeriodHours":1`.
If that timestamp is UTC, the deadline is 5:00 PM PDT Oct 17 with a grace hour to 6:00 PM; the time zone is not stated, so treat 5:00 PM as hard.
The same data has `"showHackathonGallery":false` and `"hackathonPublicVotingEnabled":false`, so there is no public gallery or crowd vote; judges decide.

What the page does **not** state, as fetched: tracks, judging criteria or weights, judges, banned project types, or submission requirements.
Those sit on the Details tab, https://cerebralvalley.ai/e/crusoe-recursive-agents-hackathon/details, which renders only "Loading..." without a Cerebral Valley login, and its HTML does not contain the strings "Done Means Done", "Shared Brain", "Technicality", "judging criteria", "Best Use of Nemotron" or "dashboard" (checked Oct 17 2026).
The team's beliefs about tracks ("Done Means Done", "Shared Brain"), the 40% technicality weight, the "dashboard as main feature" ban, and the README rule are therefore **unconfirmed from any public source I could reach**.
They most likely come from the in-room kickoff, the participant Notion, or Discord, which are not indexed.
Treat whatever the organizers said at kickoff as the authority over anything in this file.

### 1.2 Crusoe developer hub skill file for this event

Source: https://github.com/crusoecloud/crusoe-developer-hub/blob/main/examples/hackathons/crusoe-cv-recursive-agents-hackathon/crusoe-cv-recursive-agents-hackathon-skill.md

This file is Crusoe's official technical guide for this hackathon.
It explicitly says tracks, judging criteria, judges and rules are not in the file and points participants to the organizers.
It lists the models on Crusoe Managed Inference, including nvidia/Nemotron-3-Super-120B-A12B, nvidia/Nemotron-3-Nano-30B-A3B, nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B, nvidia/nemotron-3.5-lightning-30b-a3b and nvidia/Nemotron-3-VoiceChat, plus non-Nemotron open models (DeepSeek V4, Gemma 4, gpt-oss-120b, Qwen3.8, GLM-5.3).
It notes that Nemotron 3 Ultra 550B appears on Crusoe's pricing page but not in the docs catalog, so its availability is unverified.

### 1.3 Searches that found nothing

I searched for the event name with "judging criteria", "tracks", "judges", "Done Means Done", "Shared Brain", and for Luma, X, Notion and GitHub mentions.
None returned this event's criteria (searches on Oct 17 2026, standard web search).
The phrase "Shared Brain" does appear as the tagline of an unrelated XTrace "Mind Games AI Hackathon" in Berkeley: https://luma.com/xtrace-e47u - not this event.

## 2. Cerebral Valley winners (confirmed from primary sources)

Cerebral Valley (CV) runs the logistics and the submission platform, while the sponsor's own team usually judges.
The best-documented CV events are the Anthropic "Built with Opus" Claude Code hackathons, because Anthropic publishes the winners and CV published the rubric.

### 2.1 Built with Opus 4.6 (Anthropic x Cerebral Valley, virtual, one week, Feb 2026)

Rubric source: the official rules page, archived inside a winner's repo at https://huggingface.co/datasets/introvoyz041/postvisit/blob/main/data/docs/hackathon-rules.md (the file says it was pasted from the official CV / Anthropic event page on 2026-02-11).
This is the only full CV rubric I found, and it is the best available guess at how CV structures judging.

Rubric, verbatim weights and questions:
- Demo (30%): "Is this a working, impressive demo? Does it hold up live? Is it genuinely cool to watch?"
- Impact (25%): "What's the real-world potential here? Who benefits, and how much does it matter?"
- Opus 4.6 Use (25%): "How creatively did this team use Opus 4.6? Did they go beyond a basic integration?"
- Depth & Execution (20%): "Did the team push past their first idea? Is the engineering sound and thoughtfully refined?"

Process: Stage 1 was async, with six Anthropic judges scoring independently.
Stage 2 was live, where the top 6 teams played pre-recorded 3-minute demos and judges deliberated.
Rules: open source, "started from scratch during the hackathon", 3-minute max demo video, public repo, 100-200 word written summary, submitted on the CV platform.
Side prizes: "Most Creative Opus 4.6 Exploration" and a "Keep Thinking" prize for teams that "pushed past the obvious, iterated relentlessly."

Winners, from Anthropic's post at https://claude.com/blog/meet-the-winners-of-our-built-with-opus-4-6-claude-code-hackathon (Apr 20 2026):

| Place | Project | Builder | What it did |
|---|---|---|---|
| 1st | CrossBeam | Mike Brown, a lawyer | California housing-permit helper: builders upload blueprints and correction letters, parallel sub-agents parse documents and take one correction each, and it outputs an action plan in about 20 minutes. |
| 2nd ($30K credits) | Elisa | Jon McBee, software engineer | Block-based visual IDE for kids: a meta-planner turns a visual spec into a task graph, AI writes the code, and a teaching engine explains it. Built in 30 hours, 76 commits, 39K+ lines, 1,500+ tests. |
| 3rd | PostVisit.ai | Michal Nedoszytko, cardiologist | Post-appointment tools that explain diagnoses in plain language and surface clinical evidence, with physician oversight. |
| Keep Thinking | TARA | Kyeyune Kazibwe | Dashcam road footage to an investment appraisal (surface condition, NPV, sensitivity, equity score), tested on a real road in Uganda. |
| Creative Exploration | Conductr | Asep Bagja Priandana | Browser virtual bandmate that listens to MIDI chords and generates drums, bass and melody in real time via a C engine in WebAssembly. |

The post gives no explicit reason for any win.
What it does highlight: a real user or adopter (Buena Park city "considering adopting" CrossBeam), a before/after number (permit plan in 20 minutes vs a six-month delay; TARA's report in about five hours vs feasibility studies costing $1-4 million and taking 9-14 months), and visible engineering depth (Elisa's test count).
The post notes "four out of five winners were not professional developers", which suggests domain insight and a sharp problem beat raw engineering at that event.

### 2.2 Built with Opus 4.7 (Anthropic x Cerebral Valley, virtual, one week, spring 2026)

Source: https://www.claude.com/blog/meet-the-winners-of-built-with-opus-4-7-claude-code-hackathon (Jun 15 2026).
CV's role is confirmed by its newsletter: https://cerebralvalley.beehiiv.com/p/cerebral-valley-week-of-april-20th.
The post gives no rubric or reasons; the rubric was likely similar to 4.6 (**unconfirmed**).

| Place | Project | What it did, and the evidence the post cites |
|---|---|---|
| 1st | Medkit (Bedirhan Keskin) | Simulated patient encounters for junior doctors, with an agentic grader that scores each encounter against published clinical guidelines. Built with Claude Managed Agents across four parallel Claude Code sessions. Three medical faculties and a pharma company planned pilots. |
| 2nd | Wrench Board (Alexis Chapellier) | Electronics repair diagnostic agent: builds an electrical graph from a schematic, tells you which pads to probe, reads your measurements, and revises its hypotheses until it reaches a diagnosis. |
| 3rd | Maieutic (Paula Vasquez-Henriquez) | Python IDE that locks the editor until the student writes a clear spec, then an "Intent-Diff Review" classifies each spec-vs-code divergence as drift, revision, or bug. |
| Most Creative | Virtual Puppet Theater (Rene Hangstrup Moller) | Webcam and voice drive a real-time 3D puppet; spatial reasoning refined with a screenshot feedback loop. |
| Keep Thinking | MaestrIA (Benjamin Torralbo) | Home-repair diagnosis from photos. A knowledge file from interviews with a master carpenter raised eval agreement with the human expert from 74% to 81%, measured on a nine-dimension eval over 12 real cases with ground truth. |
| Best Use of Managed Agents | ARIA (Idriss Benguezzou, Adam Hnaien) | Factory predictive maintenance: five agents monitor live signals and write work orders (component, failure mode, urgency, parts, window). "One day of planning let us spend the rest of the week executing, not improvising." |

Pattern worth noting for us: the top three are all closed loops with a checker inside them.
Medkit grades against an external standard, Wrench Board loops hypothesis, measure, revise, and Maieutic diffs intent against output.
That is close to a "Done Means Done" story.

### 2.3 Claude Opus 4.8 Build Day (Anthropic, in person, one day, San Francisco, Jun 13 2026)

Source: https://claude.com/resources/articles/meet-the-winners-of-our-claude-opus-4-8-build-day-hackathon (Jun 17 2026).
This is the closest format match to today: in person, 12 hours, 310 participants out of 1,500+ applicants.
The post does not name Cerebral Valley as co-host, so CV involvement is **unconfirmed**.
No rubric or reasons given.

| Place | Project | What it did |
|---|---|---|
| 1st | Tekton (Holly Tang, Austin Burgess) | 3D reconstruction of historic buildings (Tang Dynasty architecture, the Notre-Dame spire) in 339 construction states, with each component linked to its historical evidence. |
| 2nd | Sim Francisco (Tanmayi Priya Dasari, Tejas Prabhune) | Digital twin of 10,000 synthetic SF residents from Census data. Backtested against the 2024 presidential vote and March 2024 Prop A, and batching into about 300 personas cut inference cost 10-100x. |
| 3rd | Custom Universe (Jake Stevens, Mauricio Pereira) | Phone photo to an editable photoreal 3D object for robotics synthetic data, on open-source models. |

Patterns: visual output that is striking on a projector, claims tied to evidence (Tekton cites a source per component), and a backtest against reality (Sim Francisco).

### 2.4 Other Cerebral Valley events (weak or secondhand sources)

- Cerebral Valley x Llama Lounge Agentic Hackathon: a team claims a win with an AI DJ that turned a CrewAI agent into a character in a game world that generates music from context.
  Source is the builder's own LinkedIn profile, https://ua.linkedin.com/in/oleksiisemko, not fetched (LinkedIn blocks it), place and date **unconfirmed**.
- OpenAI x Cerebral Valley Voice Hack Night: aggregator pages relay an OpenAI Developers post naming "Agentic OS for a Phone" 1st, Wagner 2nd, Surgical Triage 3rd, Curo special award.
  Source: https://www.traeai.com/glossary/cerebral_valley, secondhand, date **unconfirmed**.
- CV also ran Mistral (SF Mar 2024, Paris May 2024, London), Gemini (London, Asia tour, Google I/O) and Vercel x DeepMind "Zero to Agent" hackathons, but I found no winner lists for them from primary sources.
  Example event page: https://cerebralvalley.notion.site/Mistral-AI-a16z-London-Hackathon-Event-Details-Hackers-62cdf3e742a745aa9f4c31d20a8882af (one winner per track).

## 3. Crusoe-hosted or Crusoe-sponsored hackathons

Crusoe's developer hub lists only one hackathon folder, this event, so this appears to be the first hackathon Crusoe hosts as a main co-host: https://github.com/crusoecloud/crusoe-developer-hub/tree/main/examples/hackathons (checked Oct 17 2026).
Crusoe has sponsored tracks at other events.

### 3.1 DevNetwork [AI + ML] Hackathon at AI DevSummit, San Francisco (finals May 27-28 2026), Crusoe track

Track brief (secondhand from search snippets of a LinkedIn post by a Crusoe developer-relations person, https://il.linkedin.com/in/nirlevy98, **unconfirmed** wording): build a Nous Research Hermes or NemoClaw agent running NVIDIA Nemotron on Crusoe Managed Inference.
Reported scale: 200+ projects overall and 33 in the Crusoe track (same source, **unconfirmed**).
Event listing with dates: https://internshala.com/competitions/devnetwork-ai-ml-hackathon-2026/.

Winners:
- Argus, by Aditya Rawat - won the Crusoe Challenge (prize: an NVIDIA DGX Spark).
  The builder describes it as "a dual-cognition autonomous SRE agent" that investigates live incidents: a primary Claude reasoner plus "a shadow NVIDIA Nemotron model on Crusoe Managed Inference", run in parallel so that if one degrades mid-investigation the other continues.
  Source: the builder's LinkedIn, https://linkedin.com/in/aditya1rawat (first-person, primary for the claim).
  A second post by Emmanuel Acheampong on the same page says Argus took "first place in Crusoe for Developers' hackathon track at AI DevSummit".
- Leavitt, by Adam Rahman - "on-call AI agent that survives infra chaos", named as a Crusoe-track winner.
  Source: search snippet of the Crusoe-side LinkedIn post above, **unconfirmed** placing.

What this tells us about Crusoe judges: both winners are ops agents that keep working through failure, which is resilience and recovery, the same thing this event's theme asks for ("recover when things break, keep going").
Argus won even though its primary reasoner was a closed model, with Nemotron as the backup brain.
For a "Best Use of Nemotron" prize, though, Nemotron should carry the core roles.

### 3.2 NVIDIA Nemotron hackathons (relevant to the RTX 5090 side prize)

- NVIDIA Nemotron Hackathon, Nemotron Developer Days Seoul, Apr 21-22 2026.
  Grand Prize and Track C first place: Team Nota AI with a synthetic-data pipeline for quantizing Mixture-of-Experts models.
  Source: the winners' own blog, https://blog.nota.ai/insights/nemotron-hackathon-winner.
  The post says many rival demos were more polished, but this team showed measured gains over a baseline, and judges praised "a team that demonstrated not only qualitative but also quantitative evaluation".
  Peer review was worth 20 points, and each finalist had 5 minutes on stage.
- NVIDIA NeMo Agent Toolkit Hackathon (two weeks, results posted Jul 17 2025).
  Criteria: "real-world applicability, technical execution, and how effectively they demonstrated agentic AI in action".
  Winners: 1st Route Optimization Agent (multi-agent, NVIDIA cuOpt for routing, Omniverse simulation, deployed to Jetson robots), 2nd OpenCodeReview, 3rd an AI travel planner with fallback to a local database during API outages, honorable mention a macOS cyber agent with specialist sub-agents sharing findings.
  Source: https://developer.nvidia.com/blog/hackathon-winners-bring-agentic-ai-to-life-with-the-nvidia-nemo-agent-toolkit/.

The common thread for NVIDIA-judged prizes: use several pieces of the NVIDIA stack deeply (the 1st place used cuOpt, Omniverse and Jetson together), and show numbers.

## 4. Other recent agent hackathons and their published rubrics

### 4.1 Berkeley RDI LLM Agents Hackathon (Oct 2024 - Feb 2025)

Source: https://rdi.berkeley.edu/llm-agents-hackathon (official page; winners announced Feb 12 2025).
Tracks were Applications, Benchmarks, Fundamentals (memory, planning, reasoning, tool use), Safety, and Decentralized and Multi-Agents.
Rubric language, verbatim openers per track:
- Applications: "Strong submissions will demonstrate novel use cases addressing real-world problems."
- Benchmarks: "Strong submissions will provide comprehensive, standardized benchmarks with clear evaluation criteria."
- Fundamentals: "Strong submissions will aim to enhance current LLM agent capabilities."
- Safety: "Strong submissions will thoroughly define and address high-impact safety risks."
- Decentralized and Multi-Agents: "Strong submissions will expand on limitations of existing frameworks."

Selected winners relevant to us:
- Applications 1st: Smooth Operator (Team Eyecognito), a multi-agent system that picks movers and negotiates prices, including by voice call.
- Applications 3rd: fightforms, an agent that phones the user to complete financial forms and follows up by email or message if the call drops (recovery from interruption).
- Fundamentals 3rd: SallCo, a multi-agent system built around memory, observation and evaluation functions, tested on the ALFWorld benchmark.
- Safety 2nd (tie): a scanner that found control-flow hijacking risks in LangChain and llama-index, reporting 41% of tested integrations vulnerable (a headline number).
- Multi-Agents 1st (tie): DAMCS, decentralized agents with knowledge-graph memory and structured communication, evaluated in a multi-agent Crafter environment.
- Multi-Agents 3rd: Trust Issues, a reputation system that adjusts trust in each agent from its past performance.

Takeaway: on the technical tracks, winners were judged against a benchmark or environment, not just a demo.

### 4.2 Google Cloud ADK Hackathon (May 12 - Jun 23 2025, online)

Winners source: https://cloud.google.com/blog/products/ai-machine-learning/adk-hackathon-results-winners-and-highlights (Sep 2 2025).
Scale: 10,400+ participants, 477 projects.
The blog does not publish the rubric, and Devpost blocked my fetch, so the weights are **unconfirmed**.
- Grand Prize: SalesShortcut, a multi-agent sales-development system covering lead generation, research, proposals, and outreach end to end.
- Regional winners: Energy Agent AI (multi-agent energy customer management), Edu.AI (agents that grade essays and build study plans), GreenOps ("an AI team that continuously audits, forecasts, and optimizes cloud infrastructure"), Nexora-AI (education).
- Honorable mention: Particle Physics Agent, which converts natural language into Feynman diagrams that are validated against physical laws.

Takeaway: the grand prize went to a complete end-to-end business workflow run by a team of agents, not to a clever component.

### 4.3 Microsoft AI Agents Hackathon (Apr 8-30 2025, online)

Source: https://microsoft.github.io/AI_Agents_Hackathon/winners/ (official).
Criteria, verbatim: "innovation, impact, usability, solution quality, and category alignment."
- Best Overall: RiskWise, agents that scan supply-chain data and trade news and flag risks such as port delays.
- Best C# Agent: Apollo, a deep-research meta agent that coordinates sub-agents and uses "self-reflective retrieval" to find and fill gaps in its own knowledge.
- Best JS/TS Agent: ModelProof, which runs two models on each query, compares the answers and audits for hallucination; the page says it showed "how to make AI outputs more trustworthy and transparent."

Takeaway: self-checking (Apollo) and cross-model checking (ModelProof) were explicitly rewarded.

### 4.4 Summary of published rubrics

| Event | Technical or depth weight | Demo weight | Sponsor-tech weight | Source |
|---|---|---|---|---|
| Built with Opus 4.6 (CV) | 20% Depth & Execution | 30% | 25% | https://huggingface.co/datasets/introvoyz041/postvisit/blob/main/data/docs/hackathon-rules.md |
| NeMo Agent Toolkit (NVIDIA) | "technical execution", unweighted | "demonstrated agentic AI in action" | implicit | https://developer.nvidia.com/blog/hackathon-winners-bring-agentic-ai-to-life-with-the-nvidia-nemo-agent-toolkit/ |
| Nemotron Hackathon Seoul (NVIDIA) | quantitative evaluation praised | 5-minute stage pitch | NVIDIA stack | https://blog.nota.ai/insights/nemotron-hackathon-winner |
| Microsoft AI Agents | "solution quality" | "usability" | "category alignment" | https://microsoft.github.io/AI_Agents_Hackathon/winners/ |
| This event | 40% technicality per team notes, **unconfirmed** | unknown | Crusoe and Nemotron required | Details tab, login needed |

If the 40% technicality figure is right, this event weights engineering twice as heavily as the only CV rubric on record.
That rewards a measured result and a visible architecture over polish.

## 5. Patterns across winners

These are my reading of the sources above, not stated by any organizer, unless a quote says otherwise.

1. **A number beats polish.** Nota AI won the Nemotron grand prize over more polished demos, and both the judges' comments and the team credit its measured gains over a baseline (https://blog.nota.ai/insights/nemotron-hackathon-winner).
   MaestrIA won "Keep Thinking" with 74% to 81% agreement against a human expert on 12 ground-truth cases.
   Sim Francisco backtested against real elections.
   CrossBeam and TARA framed their value as hours versus months.
2. **A checker inside the loop.** The Opus 4.7 top three all close a loop against something external: Medkit grades against published guidelines, Wrench Board revises its hypotheses from real measurements, and Maieutic diffs intent against code.
   ModelProof and Argus both use a second model to check or back up the first.
   This is the "Done Means Done" story told by people who won.
3. **One sharp, concrete problem with a real user.** Permit corrections, road appraisal, electronics repair, home repair, factory maintenance.
   Several winners name a person or institution who wants it (Buena Park city, three medical faculties, a carpenter's company).
   Four of five Opus 4.6 winners were domain experts, not professional developers.
4. **End-to-end beats a clever piece.** The ADK grand prize (SalesShortcut) and the NeMo Agent Toolkit 1st place (simulation through to robots) ran the whole workflow.
   Judges see the goal go in and the finished artifact come out.
5. **Deep, specific use of the sponsor's stack.** CV's rubric gave 25% to model use with the question "Did they go beyond a basic integration?".
   NVIDIA's top picks combined several NVIDIA pieces.
   Argus, the Crusoe track winner, ran Nemotron on Crusoe as its backup brain, a working part of the system rather than a footnote.
6. **Resilience is what Crusoe has rewarded.** Both known Crusoe-track winners (Argus, Leavitt) are ops agents that survive failure.
   That matches this event's "recover when things break, keep going."
7. **Visual output on a projector.** Tekton, Custom Universe, Virtual Puppet Theater and Conductr are striking to watch.
   CV's rubric asks "Is it genuinely cool to watch?"
8. **Demo format.** At the CV-run Opus 4.6 event, finalists played pre-recorded 3-minute videos and judges scored async first.
   Today's format is unknown, so be ready to show both a live run and a recorded backup.

## 6. What loses (inference, weakly sourced)

No source lists losing projects, so this section is inference from what the rubrics and winners posts reward.
- A polished UI with no measured result.
  The Nota post says the more polished demos lost to the one with numbers.
- A thin wrapper around one model call.
  CV's rubric asks whether the team went "beyond a basic integration."
- Sticking with the first idea.
  CV's Depth & Execution question is "Did the team push past their first idea?"
- A broad platform with no specific user.
  Winners name a concrete problem and a concrete person.
- Projects that break the rules.
  CV disqualifies work not "started from scratch during the hackathon" or using code or assets without rights (Opus 4.6 rules).
  That is why the README should split hackathon work from third-party pieces.
- A dashboard as the product.
  This event reportedly bans it (**unconfirmed**).
  A screen that only shows data, without the loop doing work, also fails the theme "agents you can walk away from."

## 7. Advice for an agent-team project judged on technicality

1. **Lead with one before-and-after number from a run you actually did.** For GridTwin that is the false-done rate: the baseline agent's "done" claims checked against ground truth, versus the team with its verifier.
   Show N runs, not one.
   Never invent numbers.
   One honest table beats a slide of claims.
2. **Make the verifier visible and separate.** Show the claim, the check the agent wrote, the check running, and pass or fail.
   Show at least one case where the verifier rejects "done" and the team recovers.
   That moment is the track.
3. **Show recovery live.** Inject a fault on purpose during the demo (kill a sub-agent, return a 429, corrupt a reading) and let the judges watch the supervisor notice and route around it.
   This is the theme sentence acted out, and it is what Crusoe's past track winners did.
4. **Show "walk away".** Start the goal at the beginning of the pitch, talk over it, and come back to a finished, verified result with a run log.
   Show the human paged only once, with a short brief.
5. **Put Nemotron in the core roles and explain each choice in one line.** Which model plans, which one checks, and why (speed, reasoning, vision).
   Show calls going to the Crusoe endpoint.
   Report tokens, latency or cost per finished task; that is a technical number judges can trust.
6. **Show memory as a measured gain.** Run a second, similar task and show it finish in fewer steps because it read the shared memory.
   Print the step counts.
7. **Keep the UI as evidence, not the product.** The viewer exists to show the loop's evidence: plan, delegation, checks, recoveries.
   Say that in the pitch.
8. **Be ready for a recorded 3-minute format.** Record a clean backup run early.
   If judging is async, the video and the README are the whole entry: architecture diagram, the results table, how to reproduce it, and the "Built during the hackathon" section.
9. **Scope to one domain scenario done fully, not three done halfway.** Every winner above did one thing all the way through.
10. **Confirm the rules before planning around them.** Someone should sign in to the Details tab, or ask an organizer, and paste the official criteria into `docs/HACKATHON.md`.

