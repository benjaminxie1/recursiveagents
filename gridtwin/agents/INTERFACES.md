# Workstream 2 interfaces (agent loop)

Internal contract for `gridtwin/agents/**`, `gridtwin/kb.py`, `gridtwin/llm.py`, `gridtwin/notify.py`, `gridtwin/run.py`.
The cross-workstream contracts C1–C7 live in the delegation prompts and `docs/MEMORY.md`; this file pins the shapes inside workstream 2.

Times are sim-time ISO strings `YYYY-MM-DDTHH:MM` (parse with `datetime.fromisoformat`).
No module here ever opens `runs/*/truth.jsonl` or imports `gridtwin.sim.plant`/`judge`.

## `gridtwin/agents/common.py`

- `read_events(log) -> list[dict]`: every event emitted so far (tries `log.events`, `log.read()`, then `<run_dir>/events.jsonl`).
- `parse_ts(s) -> datetime | None`, `minutes_between(a, b) -> float | None`.
- `tel_series(result, point) -> list[(ts, value)]`, `tel_latest(result, point) -> (ts, value) | None`,
  `tel_last_update(result) -> str | None`, `result_now(result) -> str | None`. One place adapts to the real `read_telemetry` shape.
- `ok(result) -> bool`: no `error` and no `needs_confirmation`.
- `ActorToolbox(toolbox, actor)`: `.call(name, args)` attributes calls to `actor`.
- `extract_json(text) -> dict | list | None`: tolerant JSON from LLM output.
- `clip(obj, n=1800) -> str`: deterministic JSON dump truncated to `n` chars, for tool results in chat history.

Assumed `read_telemetry` result (the stub uses it; the normalizers also accept a few variants):
```json
{"asset": "INV-3", "now": "2026-07-14T09:42", "last_update_ts": "2026-07-14T08:10",
 "points": {"ac_kw": [["2026-07-14T09:41", 980.0], ["2026-07-14T09:42", 981.2]]}}
```

## DoD (`gridtwin/agents/dod.py`)

```json
{"version": 1,
 "branches": {"DONE": [<check>...], "ESCALATED": [<check>...], "NO_ACTION": [<check>...]}}
```
The branch verified is the one matching the claimed status. Every check has `id` (unique in the DoD), `kind`, optional `desc`.

| kind | fields |
|---|---|
| `telemetry` op `threshold` | `asset`, `point`, `cmp` (`>=` `<=` `>` `<` `==` `!=`), `value` (number, or `{"rated": "<asset or glob>", "factor": f}`, or `{"point": {"asset", "point"}, "factor": f}`), `max_age_min` (required), `hold_min` (optional, 0–30) |
| `telemetry` op `balance` | `lhs: {"assets": "<glob or list>", "point"}` (summed), `rhs: {"asset", "point"}`, `tol_pct`, `max_age_min` |
| `telemetry` op `peer` | `assets` (glob or list), `point`, `min_ratio` (each ≥ ratio × group median), `max_age_min` |
| `telemetry` op `fresh` | `assets` (glob or list), `max_age_min` |
| `telemetry` op `no_alarms` | `assets` (optional glob), `max_age_min` |
| `visual` | `viewpoint`, `mode` (`rgb`/`thermal`), `claim` |
| `citation` | `action`, `url`, `quote` |
| `safety` | `rule` in `no_action_after_reject`, `no_close_on_fault`, `page_sent`, `no_dispatch`, `no_state_change`, `action_taken`; `params` (dict, rule-specific) |

`action_taken` params: `tool` (`operate`/`dispatch_tech`), optional `asset`, `action`, `work_order`, `value_max`, `value_min`.

API:
- `validate(dod) -> list[str]` (empty = valid); `validate_check(check) -> list[str]`.
- `evaluate(checks, toolbox, log, kb_dir=None, visual_fn=None, citation_fn=None, actor="verifier", emit=True) -> list[verdict]`
  where `verdict = {"check_id", "kind", "pass": bool, "evidence": dict}`; emits one `verdict` event per check.
- `visual_fn(image_path, claim) -> {"pass", "confidence", "evidence"}`; pass needs `confidence >= 0.7`.
- `citation_fn(check, kb_dir) -> {"pass", "evidence"}`; default: URL in cached refs and normalized quote in cached text.

## Knowledge base (`gridtwin/kb.py`)

Layout: `kb/plant/profile.md`, `kb/refs/*.json`, `kb/lessons/*.json`, `kb/snapshots/<name>/`.
Ref file (one per query): `{"query", "fetched_at", "results": [{"url", "title", "snippet", "query", "fetched_at"}]}`.
Lesson:
```json
{"id": "L-<run_id>", "symptoms": ["..."], "checks": [<check>...], "fix": "...", "steps": ["..."],
 "outcome": "DONE|ESCALATED|NO_ACTION", "run_id": "...", "verdict_seqs": [12, 13],
 "status": "active|superseded", "supersedes": "<id>|null", "superseded_by": "<id>|null",
 "verified": true, "created_at": "<wall iso>"}
```
- `KB(kb_dir, log=None)`: `.search(query, k=5, kinds=("plant","refs","lessons"), actor="planner") -> [{"kind","id","title","text","score"}]` (BM25; superseded lessons never returned; one `lesson` event `op=read` per lesson returned),
  `.plant_profile()`, `.lessons(include_superseded=False)`, `.write_lesson(lesson, actor) -> lesson` (emits `op=write`, and `op=supersede` for each lesson it supersedes; only verified lessons supersede), `.refs()`, `.find_ref(url)`, `.add_ref(query, results)`.
- `snapshot(kb_dir, name) -> Path`, `build_plant_profile(plant_yaml, out_md)`.

## Research (`gridtwin/agents/research.py`)

- `SEARCH_WEB_SCHEMA` (function tool), `search_web(query, kb_dir, count=5) -> {"query", "results", "cached", "offline"}`.
- `ResearchToolbox(toolbox, log, kb_dir, actor)`: `search_web` handled here (logged as `tool_call`/`tool_result`), everything else delegated.

## Gate (`gridtwin/agents/gate.py`), board (`board.py`), notify (`gridtwin/notify.py`)

- `make_gate(backend=None, log=None) -> gate(proposed, state) -> {"route": "proceed|confirm|reject", "unsafe": float, "reason": str}`; backends `local` (gate role), `jev`, `openai`; falls back to `local` when the key is missing; never raises (errors → `confirm`). Only policy rules approve; the model can only veto (its `proceed` becomes `confirm`).
- `BOARD_TOOLS` (two function tools), `Board(log)`: `.post(worker, text, evidence) -> post`, `.read() -> [post]`, `.handle(worker, name, args) -> dict | None`.
- `make_notifier() -> notifier(severity, brief) | None` (None unless `NTFY_TOPIC` is set).
