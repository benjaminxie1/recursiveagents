# GridTwin: pitch and demo script

Owner: Will.
This replaces the demo script in `docs/PLAN.md` section 4, which predates the 12:04 decisions (static splat stage, photo bank as visual state).
Judge questions and answers live in `docs/JUDGE_QA.md`.

## The pitch in one breath

AI data centers are adding demand faster than the US can build plants, so every existing plant has to stay available, and the costliest failure is a fix that reports done while the fault is still there.
GridTwin is a team of agents that works a plant fault from a vague symptom to a verified fix, and won't say done until a verifier from a different model family has proven it against the plant itself.
We measure the one number that matters for walking away: the false-done rate.

## Three-minute live demo

Scenario: `s06_stale_scada`.
Inverter 3's comms card has failed, so its telemetry is frozen at perfect-looking values, and only the data timestamps and the inverter's dark display give it away.

1. **0:00 Hook (20 s).**
   "Data centers are adding load faster than the US can build plants. Turbines are sold out to 2031, transformers take three years, and PJM is paying record prices and still coming up short. So every plant we have has to stay up. The worst failure isn't a fault. It's a fault someone marked fixed. In Winter Storm Elliott, plants that said they were ready and weren't paid 1.8 billion dollars in penalties."
2. **0:20 Give the goal and walk away (10 s).**
   Type the goal: "Plant output looks fine on the dashboard but the owner says revenue is down. Find out why and fix it."
   Step back from the keyboard and say so.
3. **0:30 The definition of done (30 s).**
   Show the planner's checks in the evidence view: telemetry checks that require fresh data, a visual check, safety invariants.
   Line: "Before anyone touches the plant, the team writes down what done means, as checks a machine can run."
4. **1:00 The team works (30 s).**
   Show the inspection wall: workers posting findings to the team board, camera stations lighting up, the safety gate passing or holding each action.
   Line: "Every action on the plant goes through a safety gate first, and the simulator fails any run that breaks a safety rule."
5. **1:30 The catch (40 s).**
   The trap: every telemetry number on inverter 3 looks perfect.
   The verifier, which never reads the workers' reasoning, re-runs the checks, finds the data stale, and picks a camera viewpoint no worker used: the display is dark.
   The team dispatches a technician to reseat the comms card, the supervisor holds for 30 minutes, and the verifier re-runs everything before it accepts DONE.
   Line: "The data said fine. The plant wasn't. A team that grades its own homework stops here."
6. **2:10 Memory (20 s).**
   Run `s06b_stale_scada_inv2`, the same failure on a different inverter.
   Show it reading the verified lesson and finishing in fewer steps.
   Line: "Only verified outcomes become memory, so the team learns the fix and never the fake fix."
7. **2:30 The number (20 s).**
   Show the held-out results table from `results.md`: baseline, self-verify baseline and the full loop, with verified success, false-done rate, needless pages, safety violations and cost per run.
   The baselines' failure is under-acting, not over-claiming: they escalate fixable faults to a human (4 of 5 on held-out) and call a cracked module "nothing wrong".
   Line, only if the full arm's numbers support it: "Without a definition of done, agents either quit to a human or say nothing's wrong. With one, they fix it and prove it."
   Use only numbers from `results.md`.
8. **2:50 Close (10 s).**
   "Done means done: proven by a different model, against the plant, on Nemotron on Crusoe."

If judges pick the scenario, offer these pre-tested ones: `s06_stale_scada` (sensor lies), `s03_fan_relapse` (the fix that looks fine for ten minutes), `s05_xfmr_incipient` (the right answer is curtail and page a human) and `s00_control` (nothing is wrong, and the team must not invent work).

## Before going on stage

- Warm the prefix cache: `.venv/bin/python -m gridtwin.run --warm`.
- Start the viewer: `.venv/bin/python -m gridtwin.serve --port 8000`, then open `http://localhost:8000/?run=<id>` and `http://localhost:8000/wall.html?run=<id>`.
- Have a finished run of each pre-tested scenario ready to show with `&replay=1` if the live run stalls.
- Keep the results table open in a second tab.

## Backup recording (4:00)

Record one full `s06_stale_scada` run with the evidence view and inspection wall visible, using QuickTime screen recording, with the voice-over from the script above.
Cut a one-minute version for the submission: hook, goal, the catch, the number, close.
Keep the video files out of git and attach them to the submission; save the run itself (its `runs/<id>/` folder) as `runs/demo-<date>/`, which is the one runs folder git keeps, so the replay works offline.

## What not to claim

- Don't say the splat is re-rendered; the splat is a static stage with pins, and photos carry the visual state.
- Don't say the verifier picks a viewpoint no worker used until Jonathan's viewpoint rule has landed; until then say it checks the camera itself.
- Don't say agents spawn sub-agents unless the recursion work has landed and passed its tests by the 3:30 freeze.
- Don't show any number that didn't come from a bench run.
