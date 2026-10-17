# GridTwin end-to-end demo: contract between the pieces

Owner: Will (agent-build session). Scenario: `s06_stale_scada` (INV-3 comms card failed; telemetry frozen at good-looking values; display dark).
Run: `.venv/bin/python -m gridtwin.demo.run --scenario s06_stale_scada [--memory runs/e2e-kb] [--stub]`.
Page: `.venv/bin/python -m gridtwin.demo.serve --port 8010`, then `http://localhost:8010/demo.html` (Run button) or `demo.html?run=<run_id>` (live or `&replay=1`).

## Stages (one run)

1. **diagnose**: read the sensor feed (`read_telemetry` per inverter, `data_age_min`), take camera views, describe them with Nemotron Omni (`gridtwin.vision`), link the splat twin (`twin.html?scene=<scene>&run=<run_id>`), and a Nemotron diagnosis.
2. **plan**: the planner (Nemotron Super, depth 0) spawns 3 sub-agents (Nemotron Lightning, depth 1): `safety` (lockout steps), `repair` (work order), `acceptance` (proof the verifier will demand). Sub-agents cannot spawn (depth cap 1, enforced in code). The planner merges their output into a work order. Verified lessons from memory are applied here.
3. **dispatch**: each work order is an email to a simulated contractor, written as `runs/<id>/mail/<NN>-out-<wo_id>.eml` (never sent).
4. **contractor**: the simulated contractor acts on the sim through the Toolbox (`operate`, `dispatch_tech`, `wait`) and replies "done" as `runs/<id>/mail/<NN>-in-<wo_id>.eml`, with an evidence photo when it has one.
   Behaviour rule (documented, deterministic): on a first visit the contractor takes the cheap shortcut (`restart` the inverter) unless the work order explicitly forbids it. It always follows explicit `forbid` and `steps`.
5. **verify**: a verifier from a different model family (gpt-oss-120b) runs multimodal checks against the plant, never the contractor's word: telemetry freshness, a camera view checked by Nemotron Omni, and the JEV step.
   JEV step: `gridtwin.demo.jev.interpret(...)` turns xyz data (registered camera stations and asset positions in the splat frame, from `assets/splats/<scene>.registration.json`) plus sensor and visual results into one JSON state; `gridtwin.demo.jev.decide(state)` asks TypeSafe Jev (or the local Crusoe fallback when `JEV_API_KEY` is empty) whether the fix is verified.
6. **redispatch** (when verification fails): email the contractor back with the failed checks and an "expected state" image from `gridtwin.demo.diffusion.expected_state_image(...)` (diffusion when available, else the healthy photo from the photo bank). Loop to stage 4, max 3 rounds.
7. **memory**: on a verified pass, write a lesson (symptoms, fix, the shortcut that failed, the `forbid` and acceptance items) to the demo KB; the next run recalls it in stage 2 and the contractor gets it right first time.

## Event log

All events go through the existing EventLog (`log.emit(actor, type, data)`) into `runs/<id>/events.jsonl`, so the existing viewers keep working. New types, all prefixed `demo.`:

| type | actor | data |
|---|---|---|
| `demo.stage` | `orchestrator` | `{stage, status: "start"|"end", round}`; stage in `diagnose, plan, dispatch, contractor, verify, redispatch, memory, final` |
| `demo.sensor` | `diagnoser` | `{asset, data_age_min, last_update_ts, latest: {point: value}, stale: bool}` |
| `demo.camera` | `diagnoser` or `verifier` | `{viewpoint, asset, mode, image (path under repo, served as /<path>), description, round}` |
| `demo.diagnosis` | `planner` | `{asset, fault, confidence, summary, evidence: [str]}` |
| `demo.agent` | `planner` or `subagent:<role>` | `{id, parent (null at depth 0), depth, role, model, task, status: "spawned"|"done"|"failed", output}` |
| `demo.workorder` | `planner` | `{wo_id, asset, work_order, steps: [str], forbid: [str], acceptance: [str], lessons_applied: [lesson id], round}` |
| `demo.email` | `planner`, `verifier` or `contractor` | `{direction: "out"|"in", wo_id, round, from, to, subject, body, eml (path), attachments: [{name, path}]}` |
| `demo.contractor` | `contractor` | `{wo_id, round, actions: [{tool, args, ok}], shortcut: bool, note}` |
| `demo.check` | `verifier` | `{wo_id, round, kind: "telemetry"|"visual"|"jev", name, pass, detail, evidence}` |
| `demo.jev` | `verifier` | `{wo_id, round, input (the interpreted JSON), output: {verdict, p_verified, uncertainty, backend, reason}}` |
| `demo.diffusion` | `verifier` | `{wo_id, round, image, backend: "diffusion"|"photo_bank", prompt, source}` |
| `demo.verdict` | `verifier` | `{wo_id, round, pass, failed: [check name]}` |
| `demo.memory` | `memory` | `{op: "recall"|"write", lessons: [{id, text}], effect}` |
| `demo.final` | `orchestrator` | `{status: "VERIFIED"|"ESCALATED", rounds, summary, usd, wall_s}` |

Image paths are repo-relative (e.g. `assets/photos/INV-3/display_off/01_ai_generated.jpg` or `runs/<id>/img/expected-r1.png`); the server serves the repo root.

## Module interfaces

- `gridtwin/demo/jev.py` (jev session):
  - `interpret(registration: dict | str | Path, asset: str, telemetry: dict, visual: list[dict], viewpoints: list[str] | None = None) -> dict`: pure, no network. Output JSON keys: `asset`, `asset_xyz`, `stations: [{viewpoint, xyz, target_xyz, distance_m, angle_to_asset_deg, sees_asset}]`, `sensors: {data_age_min, stale, latest}`, `visual: [{viewpoint, claim, pass, confidence}]`, `coverage: {stations_seeing_asset, independent_views}`, `units`.
  - `decide(state: dict, log=None) -> {"verdict": "verified"|"not_verified"|"uncertain", "p_verified": float, "uncertainty": float, "backend": "jev"|"local", "reason": str}`; never raises.
- `gridtwin/demo/diffusion.py` (diffusion session): `expected_state_image(asset: str, src_image: str | Path, prompt: str, out_path: str | Path) -> {"image": str, "backend": "diffusion"|"photo_bank", "prompt": str, "source": str}`; never raises; photo-bank fallback picks `assets/photos/<asset>/healthy/*`.
- `gridtwin/demo/pipeline.py`, `contractors.py`, `memory.py`, `run.py`, `serve.py` (agent-build session).
- `viewer/demo.html`, `viewer/demo.js`, `viewer/demo.css` (frontend session). The page polls `/runs/<run_id>/events.jsonl`; `POST /api/demo/run` with `{"scenario": "s06_stale_scada", "memory": true}` returns `{"run_id"}`; `GET /api/demo/runs` lists demo runs newest first.
