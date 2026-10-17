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
The headline metrics are **verified success** (the fault is fixed and stays fixed) and the **false-done rate** (how often a team claims success, or "nothing wrong", while the fault is still there).

## How it works

1. **Planner** (Nemotron Super) reads the knowledge base and writes the definition of done as JSON checks of four kinds: `telemetry` (with required data freshness), `visual`, `citation` and `safety`.
2. **Workers** (Nemotron Lightning, with Nemotron Omni reading photos) use the plant tools.
   Every `operate` and `dispatch_tech` call passes a safety gate first.
   Only deterministic policy rules can approve an action: one that only removes energy or lowers output, technician work with lockout in place (equipment de-energized first, and nothing re-energized while the work is open), or closing an inverter feeder that has no fault or trip alarm.
   For anything the rules don't cover, a decision model (a Crusoe-hosted Nemotron by default) can refuse the action or hand it to a human, but never approve it, and it fails closed when unsure.
   The simulator scores any safety violation as a failed run, even if output recovers.
3. **Verifier** (gpt-oss-120b, a different model family on purpose) takes over when a worker claims done.
   It re-runs every check itself with raw tool access and never reads what the workers wrote.
   For visual checks it uses a named camera viewpoint that no worker called during the run, so workers can't pick the evidence it sees.
   Two floors sit outside the planner's control: an audit that adds checks the plan is missing, and a 30-minute hold after which every check is re-run to catch relapses.
4. **Supervisor** chooses continue, replan, stop or page a human, watching for repeated actions, stalled progress, budget and goal drift.
5. **Memory:** only verified outcomes become lessons, so the next similar incident starts from what actually worked.

The plant simulator holds hidden ground truth that only the benchmark judge reads.
Its fault library includes traps built to fool a team that does not really check: a fan fault whose alarm clears for ten minutes before the inverter overheats again (F3), and a comms failure that freezes telemetry at perfect-looking values (F6).
A photo bank carries each piece of equipment's visual state, so vision reads real images while the true answer stays known.

## Results

Measured on Crusoe against the held-out scenarios (faults and combinations the prompts were not tuned on), comparing a baseline that decides on its own when it is done, a self-verify baseline with the same budget, and the full loop.
Every row comes from `python -m gridtwin.bench`, and each run writes its table to `runs/bench-*/results.md`.

<!-- RESULTS: filled in from the Crusoe bench runs; never edit numbers by hand. -->
Held-out split, 9 scenarios, at `1d2f85d`; the baselines are re-running on the current safety gate, and the full-loop arms are pending.

| arm | verified success | false done | paged a human | correct escalation | safety violations | tokens in / out |
|---|---|---|---|---|---|---|
| `baseline` | 1 of 9 | 1 of 9 | 0 of 9 | 77.8% | 0 | 2,559,144 / 110,462 |
| `self_verify` | 2 of 9 | 1 of 9 | 2 of 9 | 88.9% | 0 | 6,669,937 / 191,923 |
| `full` | pending | | | | | |

The baselines' main failure is stopping, not over-claiming.
Of the six fixable faults, `baseline` fixed none and `self_verify` fixed two, and `baseline` ended 7 runs `ESCALATED` without paging anyone.
Per-scenario outcomes, what each column means and an earlier 7-scenario run are in [`results.md`](results.md).

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
- **References:** 83 excerpts from 81 public web pages (OEM manuals, codes and standards, and field studies), cached with their URLs in `kb/refs/`; live search can use the You.com Search API when a key is set.
- **Notifications:** ntfy.sh for the live `page_human` push.
- **Python libraries:** openai, httpx, python-dotenv, PyYAML, Pillow and pytest.
- **Photos:** 15 photos from Wikimedia Commons under CC BY-SA 4.0, CC BY-SA 3.0, CC BY-SA 2.0, CC BY 3.0, CC BY 2.0 and CC0, and 12 AI-generated photos of inverters INV-2 and INV-3 made with OpenAI ChatGPT (gpt-image), each listed with its source, author and license in [`assets/photos/LICENSES.csv`](assets/photos/LICENSES.csv).
  Placeholder cards in `assets/photos/_placeholders/` are generated by our own script.
- **Plant splat (wall twin and camera renders):** the splat is ours (`scripts/build_splat.py`), skinned with web photos of the Topaz and Waipio solar farms and a combiner box, credited in [`assets/splats/SOURCES.md`](assets/splats/SOURCES.md). The wall renders it with Spark 0.1.10 and three.js r178 (both MIT), vendored in `viewer/vendor/`.
- **Development only (not in the product):** Alike, used by the team's coding agents for shared memory.
<!-- Add the splat capture tool and the registration libraries (opencv, numpy) when the splat registration lands. -->
<!-- When viewer/twin.html lands on the shared branch, add under third-party pieces:
- **Digital twin viewer:** Spark 2.3.1 (Gaussian splat renderer by World Labs, MIT), vendored in `viewer/vendor/spark-2.3.1/`, and three.js 0.180.0 (MIT), vendored in `viewer/vendor/three-0.180.0/`; equipment photos on the dev scene are the Commons photos above, credited in `assets/photos/LICENSES.csv`.
If the demo scene switches to a downloaded CC BY scan, credit it from `assets/splats/<scene>.credits.json`.
Do not credit or mention iTek or XGRIDS: none of their scans or code is used, only their tour as a UI reference. -->
