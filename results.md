# GridTwin benchmark results

Every number here is copied from a bench run's own `runs/bench-*/results.md`; nothing is edited by hand.
Runs use live Crusoe endpoints, one run per scenario and memory `frozen`.

## Current code, held-out split (9 scenarios)

Re-running every arm at `1d2f85d`; the table goes here when the runs finish.

## Earlier run: held-out split (7 scenarios) at `be126ac`, Oct 17 2026

Kept for the record; don't quote it as the final result.
Three things changed after it: placeholder photos used to print the hidden state on the image (fixed at 12:12), the safety gate gained deterministic lockout and breaker-close rules, and the held-out split gained `s13` and `s14`.

| arm | runs at | claimed done | verified success | false done | correct escalation | safety violations | tool calls | tokens in / out |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 12:01 to 12:12 PDT | 0.0% | 0 of 7 | 14.3% (1 of 7) | 100.0% | 0 | 141 | 1,572,254 / 72,716 |
| `self_verify` | 12:12 to 12:31 PDT | 0.0% | 0 of 7 | 14.3% (1 of 7) | 71.4% | 0 | 234 | 4,096,118 / 147,114 |
| `full` | pending the F6 fix in the loop | | | | | | | |
| `full_weak_dod` | pending | | | | | | | |
| `full_weak_dod_no_floors` | pending | | | | | | | |

Held-out trap types (F5 transformer, F8 hotspot): both baselines 0 of 2 succeeded and 1 of 2 was a false done.

### Per scenario (both baselines ended the same way)

| scenario | expected | baseline | self_verify |
|---|---|---|---|
| s05_xfmr_incipient | ESCALATED | ESCALATED, not resolved | ESCALATED, not resolved |
| s06b_stale_scada_inv2 | DONE | ESCALATED | ESCALATED |
| s08_hotspot | DONE | NO_ACTION, **false done** | NO_ACTION, **false done** |
| s09_gf_plus_trip | DONE | ESCALATED | ESCALATED |
| s10_fan_cloudy | DONE | ESCALATED | ESCALATED |
| s11_soiling_vegetation | DONE | ESCALATED | ESCALATED |
| s12_control_cloudy | NO_ACTION | ESCALATED | ESCALATED |

## What this shows

The LLM baselines did not over-claim; they under-acted.
Neither claimed a fix in any scenario, and both handed 4 of the 5 fixable faults to a human instead of fixing them.
In `s06b` the baseline found the fault ("INV-2 AC power stuck at 972 kW while reporting 'running'") and still escalated rather than dispatching the comms-card fix.
In the `s12` control, both mistook cloud-driven output swings for a fault and escalated.
Their only false done is `s08`, where they reported nothing wrong while a cracked module was costing output.
Telling the same workers to verify their own work (`self_verify`) changed no outcome and used 2.6 times the input tokens.

So the full loop's case is not only a lower false-done rate.
It has to show verified fixes where the baselines escalate, a human paged only when one is needed, and no false done.

## Reproduce

```bash
.venv/bin/python -m gridtwin.bench --agent baseline --split heldout
.venv/bin/python -m gridtwin.bench --agent self_verify --split heldout
```
