# Judge Q&A prep

Questions came from a blind subagent that saw only the pitch. Answers are ours. Items marked **To do** must exist before the demo.

1. **"You built the sim, traps and checks. Isn't the benchmark rigged?"** Dev on 8 scenarios, report on 7 held out (`docs/SIMULATOR.md` §2), plus two "nothing wrong" controls. Three arms: naive baseline, a self-verify baseline (same workers told to verify, same token budget), full loop. Show per-scenario results, not just a rate. **To do:** self-verify arm.
2. **"A weak planner check passes a bad fix."** The verifier also enforces fixed floors the planner can't weaken: minimum soak time, data freshness, safety invariants. **To do:** ablation that feeds a deliberately weak DoD and shows the floors still catch the trap.
3. **"Verifier false-reject rate?"** Reported next to false-done rate, with human pages caused.
4. **"Take the splat out, what breaks?"** The splat is the shared spatial map, and the verifier renders its own viewpoint, so workers can't cherry-pick evidence. In the sim the fault state is painted into the splat; we say so. The protocol is the claim. In a real plant the splat is refreshed by a nightly drone capture.
5. **"Where do novel viewpoints come from in reality?"** Drone recapture and pan-tilt cameras update the splat; between captures, fixed cameras plus telemetry carry the state.
6. **Sun ray-tracing for shading vs soiling:** dropped. pvlib and IV curves already do it.
7. **"What's new vs Reflexion / AutoGen / LLM-as-judge?"** Agents write their own runnable definition of done, and a verifier that never sees their narrative proves it against hidden ground truth, with traps built to fool self-checks. Prior work grades with the model's own judgment or with human-written tests.
8. **"Is the safety gate an LLM?"** No. Hard-coded interlocks have the final say (like protection relays). The decision API is a second gate that can only refuse. Agents propose; interlocks decide.
9. **"You only catch traps you designed for."** Freshness is a generic check on every stream. **To do:** hold out one trap type during development and report whether it's caught.
10. **Cost and scale:** report cost and wall-clock time per verified fix. Agents wake on anomalies, so cost grows with incidents, not inverter count.
11. **Memory poisoning:** write only verified outcomes that still hold at the end of the run. Report the speedup on a held-out incident with different equipment, not a replay.
12. **Live demo:** live Crusoe endpoints, pre-warmed cache, a scenario under 3 minutes, recorded backup. Judges pick one of 3–4 pre-tested scenarios.
