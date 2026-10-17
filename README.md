# GridTwin

A team of agents for power-plant operations that won't say "done" until an independent verifier proves it.
You give it a one-line goal like "Revenue is down, find out why and fix it" and walk away.
It writes its own definition of done as checks it can run, works the problem on a simulated 5 MW solar plant with a battery and a substation, and ends every run in one of three states: `DONE` (verified), `ESCALATED` (a human was paged and the fault is explicitly not fixed) or `NO_ACTION` (nothing was wrong).

Built for the [Recursive Agents Hackathon](https://cerebralvalley.ai/e/crusoe-recursive-agents-hackathon) (Crusoe × Cerebral Valley, Oct 17 2026), **Done Means Done** track.
Every model runs on Crusoe Managed Inference, with NVIDIA Nemotron in the core roles.

## Why this matters now

AI data centers are adding electricity demand faster than the US can build power plants.
Data centers used 4.4% of US electricity in 2023 and could use 6.7% to 12% by 2028 ([LBNL/DOE](https://www.energy.gov/articles/doe-releases-new-report-evaluating-increase-electricity-demand-data-centers)).
New gas turbines ordered today ship around 2031, generator step-up transformers take more than three years, and PJM's 2027/28 capacity auction cleared at its price cap and still came up 6,623 MW short.
So the plants that exist, and the solar and storage that can be built quickly (about 79% of 2026's planned additions, per [EIA](https://www.eia.gov/todayinenergy/detail.php?id=67205)), have to stay available.

The costliest failure is a plant or a repair that reports "ready" or "fixed" when it isn't.
In Winter Storm Elliott, 90,500 MW of generation failed unexpectedly, and PJM assessed about $1.8 billion in penalties on plants that did not deliver.
Solar sites lost 5.77% of their output to equipment problems in 2024, up from 5.00% ([Raptor Maps](https://pv-magazine-usa.com/2026/02/25/u-s-solar-assets-lose-average-5070-per-mw-from-power-losses/)), and the technicians and remote analysts who chase those faults are scarce.

GridTwin targets that gap: the work between "something is off" and "provably fixed", done by agents that a remote operations analyst can leave running overnight.
The headline metric is the **false-done rate**: how often a team claims success while the fault is still there.

## How it works

1. **Planner** (Nemotron Super) reads the knowledge base and writes the definition of done as JSON checks of four kinds: `telemetry` (with required data freshness), `visual`, `citation` and `safety`.
2. **Workers** (Nemotron Lightning, with Nemotron Omni reading photos) use the plant tools.
   Every `operate` and `dispatch_tech` call passes a safety gate first: a decision model (a Crusoe-hosted Nemotron by default) checks the action against the plant safety policy and can hold or refuse it, failing closed when unsure.
   The simulator scores any safety violation as a failed run, even if output recovers.
3. **Verifier** (gpt-oss-120b, a different model family on purpose) takes over when a worker claims done.
   It re-runs every check itself with raw tool access and never reads what the workers wrote.
   Two floors sit outside the planner's control: an audit that adds checks the plan is missing, and a 30-minute hold after which every check is re-run to catch relapses.
4. **Supervisor** chooses continue, replan, stop or page a human, watching for repeated actions, stalled progress, budget and goal drift.
5. **Memory:** only verified outcomes become lessons, so the next similar incident starts from what actually worked.

The plant simulator holds hidden ground truth that only the benchmark judge reads.
Its fault library includes traps built to fool a team that does not really check: a fan fault whose alarm clears for ten minutes before the inverter overheats again (F3), and a comms failure that freezes telemetry at perfect-looking values (F6).
A real photo bank carries each piece of equipment's visual state, so vision reads real images while the true answer stays known.

## Results

Measured on Crusoe against the held-out scenarios (faults and combinations the prompts were not tuned on), comparing a baseline that decides on its own when it is done, a self-verify baseline with the same budget, and the full loop.
Every row comes from `python -m gridtwin.bench`, and each run writes its table to `runs/bench-*/results.md`.

<!-- RESULTS: filled in from the Crusoe bench runs; never edit numbers by hand. -->
*Runs in progress.*

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                                         # set CRUSOE_API_KEY (CRUSOE_RPM=600 once a card is on file)
.venv/bin/python -m scripts.smoke_test                       # every role answers
.venv/bin/python -m gridtwin.run --warm                      # pre-warm the prefix cache
.venv/bin/python -m gridtwin.run --scenario s06_stale_scada  # one incident, end to end
.venv/bin/python -m gridtwin.serve --port 8000               # evidence viewer and inspection wall
```

Benchmark arms: `scripted_correct`, `scripted_trap`, `baseline`, `self_verify`, `full`, `full_weak_dod` and `full_weak_dod_no_floors`.

```bash
.venv/bin/python -m gridtwin.bench --agent full --split heldout
.venv/bin/pytest tests -q
```

## Repository map

- `gridtwin/sim/`: plant model, fault library, scenarios, tool surface, event log and judge.
- `gridtwin/agents/`: planner, workers, verifier, supervisor, definition-of-done checks, safety gate and the baseline arms.
- `gridtwin/bench.py`, `gridtwin/run.py`, `gridtwin/serve.py`: benchmark, single-run CLI and evidence-viewer server.
- `viewer/`: evidence viewer and inspection wall (plain HTML and JavaScript).
- `kb/`: the agents' runtime knowledge base (plant profile, cached references, verified lessons).
- `docs/`: plan, simulator spec, pitch and demo script (`docs/DEMO.md`), judge Q&A prep, team decision log and research.

## Built during the hackathon

Everything in this repository was written on Oct 17 2026, the day of the event.
Before hacking started at 10:45 AM PDT there was only planning and setup: this README, `docs/PLAN.md`, `docs/SIMULATOR.md`, `CLAUDE.md`, `.env.example`, `requirements.txt`, the team notes in `docs/HACKATHON.md` and `docs/MEMORY.md`, the past-winners research, and a Crusoe client wrapper (`gridtwin/config.py`, `gridtwin/llm.py`) with a connectivity smoke test.
Everything else is in the commit history after 10:45: the plant simulator and fault library, the judge and benchmark, the agent loop, the safety gate, the evidence viewer and inspection wall, the photo bank and the tests.

Third-party pieces we use but did not build:

- **Model serving:** Crusoe Managed Inference.
- **Models:** NVIDIA Nemotron 3 Super, Nemotron 3.5 Lightning, Nemotron 3 Nano Omni and Nemotron 3 Nano; OpenAI gpt-oss-120b; Google Gemma 4 31B (vision fallback).
- **Optional decision backends:** TypeSafe Jev and the OpenAI Decisions API (the default safety gate uses a Crusoe-hosted model).
- **Search:** You.com Search API for OEM and standards references (cached in `kb/refs/`).
- **Notifications:** ntfy.sh for the live `page_human` push.
- **Python libraries:** openai, httpx, python-dotenv, PyYAML, Pillow and pytest.
- **Photos:** 17 photos from Wikimedia Commons under CC BY-SA 4.0, CC BY-SA 3.0, CC BY-SA 2.0, CC BY 3.0 and CC0, each listed with its source, author and license in [`assets/photos/LICENSES.csv`](assets/photos/LICENSES.csv).
  Placeholder images in `assets/photos/_placeholders/` are our own.
- **Development only (not in the product):** Alike, used by the team's coding agents for shared memory.
<!-- Add the splat capture tool and the registration libraries (opencv, numpy) when the splat registration lands. -->
