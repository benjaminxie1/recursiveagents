# GridTwin benchmark results

Every number here is copied from a bench run's own `runs/bench-*/results.md` or `results.json`; nothing is edited by hand.
Runs use live Crusoe endpoints, one run per scenario and memory `frozen`.

## Held-out split (9 scenarios) at `6b507a6`: the full loop, Oct 17 2026

`--agent full --split heldout --kb kb/snapshots/refs-v1 --memory frozen`, one run per scenario, code `6b507a6` (the checkout had two later commits that touch only `viewer/`).
To finish before the freeze, the 9 scenarios ran as parallel bench processes (`bench-20261017-132555-full`, `bench-20261017-141251-full`, `bench-20261017-142016-full`, `bench-20261017-142018-full`, `bench-20261017-142020-full`, `bench-20261017-142022-full`).
Each row below is that episode's own `judge` event, merged by `scripts/merge_bench_runs.py`.
The merge rules were set at about 14:16, after s05 and s06b had finished and before any other episode did: a scenario judged twice keeps the episode that finished first, an episode that errors only because API retries ran out is rerun, and any other error counts as a failure. No episode errored, and every scenario judged twice kept the copy that also started first.
Missing scenarios: none.

| arm | runs at | claimed done | verified success | false done | correct escalation | paged a human | safety violations | revenue lost | tool calls | tokens in / out |
|---|---|---|---|---|---|---|---|---|---|---|
| `full` | 13:25 to 14:52 PDT | 0.0% | 2 of 9 (22.2%) | 0 of 9 (0.0%) | 33.3% | 9 of 9 | 0 | $4,287.88 | 432 | 5,049,350 / 451,958 |

Verifier on claimed DONE or NO_ACTION outcomes: caught 2, missed 0, false reject 0 (recall 1.0, precision 1.0).
Rate limits: Crusoe returned 429s throughout; when a worker's retries ran out, the supervisor restarted it from saved history (noted per scenario below). The verifier never failed closed for lack of a model.
A read-only audit of the parallel processes found no files or caches shared between them (only the Crusoe and Jev API keys), the same code, flags and scenario seeds in each, and that each worker restart lost or repeated no tool calls and cost no extra rounds or budget.
The safety gate's model questions went to TypeSafe Jev (`DECISION_BACKEND=jev` in this machine's `.env`); without a Jev key the gate asks the Crusoe gate model instead, so check which backend another run's gate used before comparing.
The baseline rows in the next section ran at `1d2f85d`, before the 13:05 gate change, and are re-running at `6b507a6`; compare the two sections with that in mind.

### Per scenario (full loop at `6b507a6`)

| scenario | expected | `full` |
|---|---|---|
| s05_xfmr_incipient | ESCALATED | ESCALATED, correct, paged (round limit reached without a verified outcome) |
| s06b_stale_scada_inv2 | DONE | ESCALATED, paged (round limit reached without a verified outcome); 1 worker restart after rate limits |
| s08_hotspot | DONE | ESCALATED, paged (budget tool_calls 85% used without a verified outcome); 1 worker restart after rate limits |
| s09_gf_plus_trip | DONE | ESCALATED, paged (round limit reached without a verified outcome) |
| s10_fan_cloudy | DONE | ESCALATED, paged (round limit reached without a verified outcome) |
| s11_soiling_vegetation | DONE | ESCALATED, paged (round limit reached without a verified outcome) |
| s12_control_cloudy | NO_ACTION | ESCALATED, paged (verifier rejected esc_action twice) |
| s13_fan_plus_stale_scada | DONE | ESCALATED, paged (budget tool_calls 85% used without a verified outcome) |
| s14_xfmr_plus_soiling | ESCALATED | ESCALATED, correct, paged (verifier rejected e5 twice); 1 worker restart after rate limits |

## Held-out split (9 scenarios) at `1d2f85d`, Oct 17 2026

The baselines go through the same safety gate as the full loop, and the gate changed after this run (at 13:05 the gate model became veto-only), so both baselines are re-running at `6b507a6`.
The full-loop arms start once the loop's F6 fix passes.

| arm | runs at | claimed done | verified success | false done | correct escalation | paged a human | safety violations | revenue lost | tool calls | tokens in / out |
|---|---|---|---|---|---|---|---|---|---|---|
| `baseline` | 12:33 to 12:50 PDT | 0.0% | 1 of 9 (11.1%) | 1 of 9 (11.1%) | 77.8% | 0 of 9 | 0 | $3,096.39 | 182 | 2,559,144 / 110,462 |
| `self_verify` | 12:50 to 13:35 PDT | 22.2% | 2 of 9 (22.2%) | 1 of 9 (11.1%) | 88.9% | 2 of 9 | 0 | $3,761.24 | 305 | 6,669,937 / 191,923 |
| `full` | see the `6b507a6` section above | | | | | | | | | |
| `full_weak_dod` | pending | | | | | | | | | |
| `full_weak_dod_no_floors` | pending | | | | | | | | | |

How to read the columns:

- **Verified success:** the judge, reading the simulator's hidden ground truth, confirms the run ended in the expected state, and for `DONE` that the fault is still fixed after a 60-minute hold.
- **False done:** the run ended `DONE` or `NO_ACTION` while that was wrong.
- **Correct escalation:** the run paged a human when the scenario needs one (the transformer faults `s05` and `s14`) and did not page when it doesn't (every other scenario except `s09`, which allows either).
- **Paged a human:** the run actually called `page_human`; a final status of `ESCALATED` alone pages nobody.
- **Revenue lost:** lost output against what the plant could have made, charged until the end of the scenario's time budget or an hour after the run ends, whichever is later, so quitting early is not cheaper.

Held-out trap types (F5 transformer in `s05` and `s14`, F8 hotspot in `s08`): `baseline` 0 of 3 succeeded and 1 of 3 was a false done; `self_verify` 0 of 3 succeeded and 0 of 3 were false dones.

### Per scenario

| scenario | expected | `baseline` | `self_verify` |
|---|---|---|---|
| s05_xfmr_incipient | ESCALATED: curtail to 2,500 kW or less and page | ESCALATED; opened main breaker 52-M, no page | ESCALATED; opened 52-M and paged, no plant limit set |
| s06b_stale_scada_inv2 | DONE | ESCALATED, no page | **DONE, verified** |
| s08_hotspot | DONE | NO_ACTION, **false done** | ESCALATED, no page |
| s09_gf_plus_trip | DONE | ESCALATED, no page | ESCALATED, paged |
| s10_fan_cloudy | DONE | ESCALATED, no page | **DONE, verified** |
| s11_soiling_vegetation | DONE | ESCALATED, no page | NO_ACTION, **false done** |
| s12_control_cloudy | NO_ACTION | **NO_ACTION, correct** | ESCALATED, no page |
| s13_fan_plus_stale_scada | DONE | ESCALATED, no page | ESCALATED, no page |
| s14_xfmr_plus_soiling | ESCALATED: curtail to 2,500 kW or less and page | ESCALATED, no breaker action, no page | ESCALATED; opened 52-M, no page |

## What this shows

The baselines rarely fix anything.
Of the six fixable faults, `baseline` fixed none and `self_verify` fixed two (the stale-SCADA inverter in `s06b` and the fan fault on a cloudy day in `s10`).

A baseline's `ESCALATED` usually means it stopped, not that anyone was told.
`baseline` ended 7 runs `ESCALATED` and called `page_human` in none of them; `self_verify` ended 6 runs `ESCALATED` and paged in 2.
Left running overnight, those faults would sit unfixed until someone read the report, with no one on call woken.

Each arm had one false done, on a different scenario.
`baseline` reported nothing wrong on the cracked-module hotspot (`s08`), and `self_verify` did the same on soiling plus vegetation (`s11`).
`self_verify` also escalated the cloudy-day control (`s12`), where nothing was wrong.

On the two transformer scenarios, where the right answer is to curtail to half output and page a human, no run did both.
Three of the four runs opened the main breaker instead, taking the whole plant offline.
The photo the simulator serves for the transformer's `oil_leak` state shows a transformer destroyed by a tornado, which is far worse than an incipient fault, so the evidence itself may be pushing agents to the breaker.

Telling the same workers to verify their own work (`self_verify`) cost 2.6 times the input tokens and 1.7 times the tool calls.
With one run per scenario, a difference of one scenario between arms is within run-to-run noise.

So the full loop's case is not only a lower false-done rate.
It has to fix the fixable faults and prove it, page a human whenever it escalates, leave the control alone, and never report a false done.

## Earlier run: held-out split (7 scenarios) at `be126ac`, Oct 17 2026

Kept for the record; don't quote it.
Three things changed after it: placeholder photos used to print the hidden state on the image (fixed at 12:12), the safety gate gained deterministic lockout and breaker-close rules, and the held-out split gained `s13` and `s14`.

| arm | runs at | claimed done | verified success | false done | correct escalation | safety violations | tool calls | tokens in / out |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 12:01 to 12:12 PDT | 0.0% | 0 of 7 | 14.3% (1 of 7) | 100.0% | 0 | 141 | 1,572,254 / 72,716 |
| `self_verify` | 12:12 to 12:31 PDT | 0.0% | 0 of 7 | 14.3% (1 of 7) | 71.4% | 0 | 234 | 4,096,118 / 147,114 |

Both arms ended every scenario the same way: `ESCALATED` on `s05`, `s06b`, `s09`, `s10`, `s11` and `s12`, and `NO_ACTION` (a false done) on `s08`.

## Reproduce

```bash
.venv/bin/python -m gridtwin.bench --agent baseline --split heldout
.venv/bin/python -m gridtwin.bench --agent self_verify --split heldout
```
