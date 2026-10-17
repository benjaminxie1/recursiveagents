"""Contract validator for GridTwin demo runs (gridtwin/demo/CONTRACT.md).

`validate_run(path)` returns every problem found in a demo run, as readable strings; an empty list means
the run honours the contract. `path` is a run directory holding `events.jsonl`, or an events file itself.
Paths inside events (images, .eml files, attachments) are repo-relative, because the page serves the repo
root; the repo root is the parent of `runs/` for a run directory, else this repo.

Command line: `python -m tests.demo.contract runs/<id> [...]` prints the problems and exits 1 if any.
"""
from __future__ import annotations

import json
import re
from email import message_from_bytes, policy
from email.utils import getaddresses
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

STAGES = ("diagnose", "plan", "dispatch", "contractor", "verify", "redispatch", "memory", "final")
# Legal successor of each stage (CONTRACT.md "Stages"); redispatch loops back to the contractor.
NEXT_STAGE = {
    None: {"diagnose"},
    "diagnose": {"plan"},
    "plan": {"dispatch"},
    "dispatch": {"contractor"},
    "contractor": {"verify"},
    "verify": {"redispatch", "memory", "final"},
    "redispatch": {"contractor"},
    "memory": {"final"},
    "final": set(),
}
MAX_ROUNDS = 3
# Stages that may carry round 0, meaning "outside the contractor loop"; exempt from the forward-only rule.
ROUNDLESS_STAGES = {"diagnose", "plan", "memory", "final"}
CHECK_KINDS = {"telemetry", "visual", "jev"}  # every passing verdict needs one of each
META_KIND = "meta"  # only a veto: the meta judge failing a fix the plant checks passed
# Acceptance items the plant cannot prove (CONTRACT.md demo.workorder): power, temperature and latency bands.
UNPROVABLE = re.compile(r"(\bk?w\b|\bmw\b|kilowatt|megawatt|power output|output power|°|\bdeg|temperat|heatsink|"
                        r"latency|\bms\b|millisecond)", re.I)
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")

# type -> (allowed actors, {key: kind}). Kinds: str, int, num, bool, dict, list, list[str], list[dict],
# "<kind>|null" for nullable, "any" for present with any value, or a set of allowed string values.
SCHEMA = {
    "demo.stage": ({"orchestrator"}, {"stage": set(STAGES), "status": {"start", "end"}, "round": "int"}),
    "demo.twin": ({"diagnoser"}, {"asset": "str", "action": {"open"}}),
    "demo.sensor": ({"diagnoser"}, {
        "asset": "str", "data_age_min": "num|null", "last_update_ts": "any", "latest": "dict", "stale": "bool"}),
    "demo.camera": ({"diagnoser", "verifier"}, {
        "viewpoint": "str", "asset": "str", "mode": "str", "image": "str", "description": "str", "round": "int"}),
    "demo.diagnosis": ({"planner"}, {
        "asset": "str", "fault": "str", "confidence": "num", "summary": "str", "evidence": "list[str]"}),
    "demo.agent": ({"planner", "diagnoser", "verifier", "subagent:*"}, {
        "id": "str", "parent": "str|null", "depth": "int", "role": "str", "model": "str", "task": "str",
        "status": {"spawned", "done", "failed"}, "output": "any", "team": {"diagnose", "plan", "verify"},
        "round": "int"}),
    "demo.workorder": ({"planner"}, {
        "wo_id": "str", "asset": "str", "work_order": "any", "steps": "list[str]", "forbid": "list[str]",
        "acceptance": "list[str]", "lessons_applied": "list", "round": "int"}),
    "demo.email": ({"planner", "verifier", "contractor"}, {
        "direction": {"out", "in"}, "wo_id": "str", "round": "int", "from": "str", "to": "str",
        "subject": "str", "body": "str", "eml": "str", "attachments": "list[dict]"}),
    "demo.contractor": ({"contractor"}, {
        "wo_id": "str", "round": "int", "actions": "list[dict]", "shortcut": "bool", "note": "str"}),
    "demo.check": ({"verifier"}, {
        "wo_id": "str", "round": "int", "kind": CHECK_KINDS | {META_KIND}, "name": "str", "pass": "bool",
        "detail": "any", "evidence": "any"}),
    "demo.jev": ({"verifier"}, {"wo_id": "str", "round": "int", "input": "dict", "output": "dict"}),
    "demo.diffusion": ({"verifier"}, {
        "wo_id": "str", "round": "int", "image": "str", "backend": {"diffusion", "photo_bank"},
        "prompt": "str", "source": "any", "seen": "str|null"}),
    "demo.verdict": ({"verifier"}, {"wo_id": "str", "round": "int", "pass": "bool", "failed": "list[str]"}),
    "demo.memory": ({"memory"}, {"op": {"recall", "write", "score", "stats"}, "lessons": "list[dict]", "effect": "any"}),
    "demo.final": ({"orchestrator"}, {
        "status": {"VERIFIED", "ESCALATED", "FAILED"}, "rounds": "int", "summary": "str", "usd": "num|null",
        "wall_s": "num"}),
}
SUBSCHEMA = {  # (event type, list or dict field) -> spec for each item
    ("demo.jev", "output"): {
        "verdict": {"verified", "not_verified", "uncertain"}, "p_verified": "num", "uncertainty": "num",
        "backend": {"jev", "local"}, "reason": "str"},
    ("demo.contractor", "actions"): {"tool": "str", "args": "any", "ok": "bool"},
    ("demo.memory", "lessons"): {"id": "str", "text": "str"},
    ("demo.email", "attachments"): {"name": "str", "path": "str"},
}
# Stages an agent of each team may appear in (a plan agent may also re-plan during redispatch).
TEAM_STAGES = {"diagnose": {"diagnose"}, "plan": {"plan", "redispatch"}, "verify": {"verify"}}
MIN_PLAN_SUBAGENTS = 2


def _verifier_bars(asset: str) -> list[str] | None:
    """The verifier's own acceptance bars (gridtwin/demo/pipeline.py verifier_acceptance), if importable."""
    try:
        from gridtwin.demo.pipeline import verifier_acceptance
    except Exception:  # noqa: BLE001 - the validator still runs without the product package
        return None
    return list(verifier_acceptance(asset))


def _subagent_roles() -> set[str] | None:
    """Sub-agent roles the contract allows (gridtwin/demo/events.py ALL_SUBAGENT_ROLES), if importable."""
    try:
        from gridtwin.demo.events import ALL_SUBAGENT_ROLES
    except Exception:  # noqa: BLE001 - the validator still runs without the product package
        return None
    return set(ALL_SUBAGENT_ROLES)


# Stages an event may appear in (CONTRACT.md "Stages"). Email and memory are keyed by direction and op.
PLACEMENT = {
    "demo.twin": {"diagnose"},
    "demo.sensor": {"diagnose", "verify"},
    "demo.camera": {"diagnose", "verify"},
    "demo.diagnosis": {"diagnose"},
    "demo.agent": {"diagnose", "plan", "verify", "redispatch"},
    "demo.workorder": {"plan", "dispatch", "redispatch"},
    "demo.email:out": {"dispatch", "redispatch"},
    "demo.email:in": {"contractor"},
    "demo.contractor": {"contractor"},
    "demo.check": {"verify"},
    "demo.jev": {"verify"},
    "demo.verdict": {"verify"},
    "demo.diffusion": {"verify", "redispatch"},
    "demo.memory:recall": {"diagnose", "plan"},
    "demo.memory:write": {"memory"},
    "demo.memory:score": {"memory"},
    "demo.memory:stats": {"memory"},
    "demo.final": {"final"},
}


def _kind_ok(value, kind) -> bool:
    if isinstance(kind, set):
        return isinstance(value, str) and value in kind
    if kind.endswith("|null"):
        return value is None or _kind_ok(value, kind[:-5])
    if kind == "any":
        return True
    if kind == "str":
        return isinstance(value, str)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "num":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "dict":
        return isinstance(value, dict)
    if kind == "list":
        return isinstance(value, list)
    if kind == "list[str]":
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    if kind == "list[dict]":
        return isinstance(value, list) and all(isinstance(v, dict) for v in value)
    raise ValueError(f"unknown kind {kind!r}")


def _schema_problems(data: dict, spec: dict, where: str) -> list[str]:
    out = []
    for key, kind in spec.items():
        if key not in data:
            out.append(f"{where}: missing key {key!r}")
        elif not _kind_ok(data[key], kind):
            want = sorted(kind) if isinstance(kind, set) else kind
            out.append(f"{where}: {key}={data[key]!r} is not {want}")
    return out


def _actor_ok(actor, allowed: set) -> bool:
    if not isinstance(actor, str):
        return False
    return actor in allowed or ("subagent:*" in allowed and actor.startswith("subagent:") and actor != "subagent:")


def _addresses(value: str) -> set[str]:
    return {addr.lower() for _, addr in getaddresses([value]) if addr}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _data(event: dict) -> dict:
    d = event.get("data")
    return d if isinstance(d, dict) else {}


class _Run:
    """One pass over a run's demo events; each check method appends to self.problems."""

    def __init__(self, events: list, repo_root: Path):
        self.repo_root = repo_root
        self.problems: list[str] = []
        self.all_events = [e for e in events if isinstance(e, dict)]
        self.events = [e for e in self.all_events if str(e.get("type", "")).startswith("demo.")]
        self.stage_of: dict[int, str | None] = {}  # index in self.events -> enclosing stage
        finals = [_data(e) for e in self.events if e.get("type") == "demo.final"]
        # A crashed run (demo.final FAILED) stops anywhere: what happened must still be right, but nothing has to finish.
        self.crashed = bool(finals) and finals[-1].get("status") == "FAILED"

    def bad(self, i: int | None, msg: str):
        if i is None:
            self.problems.append(msg)
        else:
            e = self.events[i]
            self.problems.append(f"seq {e.get('seq')} {e.get('type')}: {msg}")

    def of(self, type_: str, **match) -> list[tuple[int, dict]]:
        """(index, data) of every event of this type whose data matches all `match` items."""
        return [(i, _data(e)) for i, e in enumerate(self.events)
                if e.get("type") == type_ and all(_data(e).get(k) == v for k, v in match.items())]

    def resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else self.repo_root / path

    def exists(self, p) -> bool:
        return isinstance(p, str) and bool(p) and self.resolve(p).is_file()

    # checks ------------------------------------------------------------------

    def envelope_and_schema(self):
        last_seq = None
        for e in self.all_events:
            seq = e.get("seq")
            if isinstance(seq, int):
                if last_seq is not None and seq <= last_seq:
                    self.problems.append(f"seq {seq}: not after seq {last_seq} (log out of order)")
                last_seq = seq
        for i, e in enumerate(self.events):
            type_, data = e.get("type"), e.get("data")
            if not isinstance(e.get("seq"), int):
                self.bad(i, "missing integer seq")
            if type_ not in SCHEMA:
                self.bad(i, "unknown demo event type")
                continue
            if not isinstance(data, dict):
                self.bad(i, "data is not an object")
                continue
            actors, spec = SCHEMA[type_]
            actor = e.get("actor")
            if not _actor_ok(actor, actors):
                self.bad(i, f"actor {actor!r} not in {sorted(actors)}")
            for p in _schema_problems(data, spec, "data"):
                self.bad(i, p)
            for (t, field), sub in SUBSCHEMA.items():
                if t != type_:
                    continue
                items = data.get(field)
                items = [items] if isinstance(items, dict) else items if isinstance(items, list) else []
                for item in items:
                    if isinstance(item, dict):
                        for p in _schema_problems(item, sub, field):
                            self.bad(i, p)
            if type_ == "demo.email":
                inbound = data.get("direction") == "in"
                if inbound != (actor == "contractor"):
                    self.bad(i, f"{data.get('direction')!r} email emitted by actor {actor!r}")
            if type_ == "demo.agent" and str(actor).startswith("subagent:") and actor != f"subagent:{data.get('role')}":
                self.bad(i, f"actor {actor!r} does not match role {data.get('role')!r}")

    def stages(self):
        """Legal order, no nesting, start/end pairs, rounds that only move forward."""
        open_stage = None  # (name, round, index)
        last_name, last_round = None, None
        self.contractor_rounds: list[int] = []
        for i, e in enumerate(self.events):
            if e.get("type") != "demo.stage":
                self.stage_of[i] = open_stage[0] if open_stage else None
                continue
            d = _data(e)
            name, status, rnd = d.get("stage"), d.get("status"), d.get("round")
            self.stage_of[i] = name
            if status == "start":
                if open_stage:
                    self.bad(i, f"stage {name!r} starts while {open_stage[0]!r} is still open")
                legal = NEXT_STAGE.get(last_name, set())
                if name not in legal:
                    self.bad(i, f"stage {name!r} cannot follow {last_name!r} (legal: {sorted(legal)})")
                roundless = rnd == 0 and name in ROUNDLESS_STAGES
                if isinstance(rnd, int) and isinstance(last_round, int) and rnd < last_round and not roundless:
                    self.bad(i, f"round went back from {last_round} to {rnd}")
                if name == "contractor" and isinstance(rnd, int):
                    if self.contractor_rounds and rnd != self.contractor_rounds[-1] + 1:
                        self.bad(i, f"contractor round {rnd} does not follow round {self.contractor_rounds[-1]}")
                    self.contractor_rounds.append(rnd)
                open_stage, last_name = (name, rnd, i), name
                if isinstance(rnd, int) and not roundless:
                    last_round = rnd
            elif status == "end":
                if not open_stage or open_stage[0] != name:
                    self.bad(i, f"stage {name!r} ends but {open_stage[0] if open_stage else None!r} is open")
                elif open_stage[1] != rnd:
                    self.bad(i, f"stage {name!r} ends in round {rnd}, started in round {open_stage[1]}")
                open_stage = None
        if self.crashed:
            return
        if open_stage:
            self.bad(open_stage[2], f"stage {open_stage[0]!r} never ends")
        if last_name != "final":
            self.bad(None, f"run does not reach the final stage (last stage {last_name!r})")
        if len(self.contractor_rounds) > MAX_ROUNDS:
            self.bad(None, f"{len(self.contractor_rounds)} contractor rounds, more than the {MAX_ROUNDS} allowed")

    def placement(self):
        for i, e in enumerate(self.events):
            type_, d = e.get("type"), _data(e)
            key = {"demo.email": f"demo.email:{d.get('direction')}",
                   "demo.memory": f"demo.memory:{d.get('op')}"}.get(type_, type_)
            allowed = PLACEMENT.get(key)
            if self.crashed and type_ == "demo.final":
                continue
            if allowed and self.stage_of.get(i) not in allowed:
                self.bad(i, f"emitted in stage {self.stage_of.get(i)!r}, expected one of {sorted(allowed)}")

    def agents(self):
        agents: dict[str, dict] = {}
        for i, d in self.of("demo.agent"):
            aid = d.get("id")
            if not isinstance(aid, str):
                continue
            a = agents.setdefault(aid, {"first": i, "depth": d.get("depth"), "parent": d.get("parent"),
                                        "role": d.get("role"), "team": d.get("team"), "statuses": []})
            team_stages = TEAM_STAGES.get(d.get("team"))
            if team_stages and self.stage_of.get(i) not in team_stages:
                self.bad(i, f"agent {aid!r} of team {d.get('team')!r} acts in stage {self.stage_of.get(i)!r}")
            for k in ("depth", "parent", "role", "team"):
                if d.get(k) != a[k]:
                    self.bad(i, f"agent {aid!r} changes {k} from {a[k]!r} to {d.get(k)!r}")
            a["statuses"].append(d.get("status"))
        roles = _subagent_roles()
        for aid, a in agents.items():
            i, depth, parent = a["first"], a["depth"], a["parent"]
            if depth == 1 and roles is not None and a["role"] not in roles:
                self.bad(i, f"sub-agent {aid!r} has role {a['role']!r}, not one of ALL_SUBAGENT_ROLES")
            if a["statuses"][0] != "spawned":
                self.bad(i, f"agent {aid!r} first appears as {a['statuses'][0]!r}, not 'spawned'")
            if a["statuses"][-1] not in ("done", "failed"):
                self.bad(i, f"agent {aid!r} never finishes (last status {a['statuses'][-1]!r})")
            if depth not in (0, 1):
                self.bad(i, f"agent {aid!r} at depth {depth!r}; the depth cap is 1")
            if depth == 0 and parent is not None:
                self.bad(i, f"depth-0 agent {aid!r} has parent {parent!r}")
            if depth == 1 and parent is None:
                self.bad(i, f"depth-1 agent {aid!r} has no parent")
            if depth != 0 and parent is not None:
                p = agents.get(parent)
                if p is None:
                    self.bad(i, f"agent {aid!r} has parent {parent!r}, which is not a known agent")
                    continue
                if p["depth"] != 0:
                    self.bad(i, f"agent {aid!r} was spawned by {parent!r} at depth {p['depth']!r}; "
                                "sub-agents cannot spawn (no depth 2)")
                if p["first"] > i:
                    self.bad(i, f"agent {aid!r} appears before its parent {parent!r}")
                if p["team"] != a["team"]:
                    self.bad(i, f"agent {aid!r} is in team {a['team']!r} but its parent {parent!r} is in {p['team']!r}")
        if not any(a["depth"] == 0 and a["team"] == "plan" for a in agents.values()):
            self.bad(None, "no depth-0 planner agent in the plan team")
        subs = [aid for aid, a in agents.items() if a["depth"] == 1 and a["team"] == "plan"]
        if len(subs) < MIN_PLAN_SUBAGENTS:
            self.bad(None, f"only {len(subs)} sub-agent(s) in the plan team; the planner must spawn at least "
                           f"{MIN_PLAN_SUBAGENTS}")

    def emails(self):
        for i, d in self.of("demo.email"):
            eml = d.get("eml")
            if not isinstance(eml, str) or not eml:
                self.bad(i, "no eml path")
                continue
            path = self.resolve(eml)
            # mail/<NN>-<direction>-<wo_id>.eml; the pipeline lowercases the id and may add -r<round>
            m = re.fullmatch(r"(\d+)-(out|in)-(.+?)(?:-r(\d+))?\.eml", path.name, flags=re.IGNORECASE)
            if (not m or m.group(2) != d.get("direction") or m.group(3).lower() != str(d.get("wo_id")).lower()
                    or path.parent.name != "mail"):
                self.bad(i, f"eml {eml!r} is not mail/<NN>-{d.get('direction')}-{d.get('wo_id')}.eml")
            elif m.group(4) is not None and int(m.group(4)) != d.get("round"):
                self.bad(i, f"eml {eml!r} is named for round {m.group(4)} but the event is round {d.get('round')}")
            if not path.is_file():
                self.bad(i, f"eml {eml!r} does not exist")
                continue
            try:
                msg = message_from_bytes(path.read_bytes(), policy=policy.default)
                headers = {h: str(msg.get(h, "")) for h in ("From", "To", "Subject")}
                names = {part.get_filename() for part in msg.iter_attachments()}
            except Exception as exc:  # any parse failure is a contract problem, not a crash
                self.bad(i, f"eml {eml!r} does not parse: {exc}")
                continue
            for h, v in headers.items():
                if not v:
                    self.bad(i, f"eml {eml!r} has no {h} header")
            if " ".join(str(d.get("subject", "")).split()) != " ".join(headers["Subject"].split()):
                self.bad(i, f"eml Subject {headers['Subject']!r} differs from the event's {d.get('subject')!r}")
            for h, k in (("From", "from"), ("To", "to")):
                if isinstance(d.get(k), str) and d[k] and not _addresses(d[k]) <= _addresses(headers[h]):
                    self.bad(i, f"eml {h} {headers[h]!r} does not match the event's {d[k]!r}")
            for att in d.get("attachments") or []:
                if not isinstance(att, dict):
                    continue
                if not self.exists(att.get("path")):
                    self.bad(i, f"attachment {att.get('name')!r} path {att.get('path')!r} does not exist")
                if att.get("name") not in names:
                    self.bad(i, f"attachment {att.get('name')!r} is not in the .eml "
                                f"(it has {sorted(n for n in names if n)})")

        for i, d in self.of("demo.workorder") if not self.crashed else []:
            if not [j for j, _ in self.of("demo.email", direction="out", wo_id=d.get("wo_id"), round=d.get("round"))
                    if j > i]:
                self.bad(i, f"work order {d.get('wo_id')!r} round {d.get('round')} has no outbound email after it")
        for i, d in self.of("demo.email", direction="in"):
            if not [j for j, _ in self.of("demo.email", direction="out", wo_id=d.get("wo_id"), round=d.get("round"))
                    if j < i]:
                self.bad(i, f"reply for {d.get('wo_id')!r} round {d.get('round')} has no outbound email before it")
        for i, d in self.of("demo.contractor") if not self.crashed else []:
            if not self.of("demo.email", direction="in", wo_id=d.get("wo_id"), round=d.get("round")):
                self.bad(i, f"contractor round {d.get('round')} for {d.get('wo_id')!r} has no inbound email")

    def verdicts(self):
        verdicts = self.of("demo.verdict")
        seen = set()
        for i, d in verdicts:
            key = (d.get("wo_id"), d.get("round"))
            if key in seen:
                self.bad(i, f"second verdict for {key}")
            seen.add(key)
        for i, d in self.of("demo.email", direction="in") if not self.crashed else []:
            if not [j for j, _ in self.of("demo.verdict", wo_id=d.get("wo_id"), round=d.get("round")) if j > i]:
                self.bad(i, f"reply for {d.get('wo_id')!r} round {d.get('round')} has no verdict after it")

        for i, v in verdicts:
            wo, rnd = v.get("wo_id"), v.get("round")
            replies = [j for j, _ in self.of("demo.email", direction="in", wo_id=wo, round=rnd)]
            if not replies:
                self.bad(i, f"verdict for {wo!r} round {rnd} has no contractor reply")
            reply_at = min(replies) if replies else -1
            checks = [(j, c) for j, c in self.of("demo.check", wo_id=wo, round=rnd) if j < i]
            for j, _ in checks:
                if j < reply_at:
                    self.bad(j, "check runs before the contractor's reply, so it cannot be reading the fixed plant")
            failing = {c.get("name") for _, c in checks if c.get("pass") is not True}
            plant_failed = any(c.get("pass") is not True for _, c in checks if c.get("kind") != META_KIND)
            for j, c in checks:
                if c.get("kind") == META_KIND and c.get("pass") is not False:
                    self.bad(j, "meta check that passes; meta appears only as a veto")
                if c.get("kind") == META_KIND and plant_failed:
                    self.bad(j, "meta veto although a plant check already failed; meta only vetoes a passing fix")
            should_pass = bool(checks) and not failing
            if v.get("pass") is not should_pass:
                self.bad(i, f"pass={v.get('pass')!r} but its {len(checks)} check(s) say pass={should_pass}")
            if isinstance(v.get("failed"), list) and set(v["failed"]) != failing:
                self.bad(i, f"failed={sorted(map(str, v['failed']))} but the failing checks are "
                            f"{sorted(map(str, failing))}")
            jevs = [x for j, x in self.of("demo.jev", wo_id=wo, round=rnd) if j < i]
            for j, c in checks:
                if c.get("kind") != "jev":
                    continue
                if not jevs:
                    self.bad(j, "jev check without a demo.jev event")
                for x in jevs:
                    said = _data({"data": x.get("output")}).get("verdict")
                    if c.get("pass") is not (said == "verified"):
                        self.bad(j, f"jev check pass={c.get('pass')!r} but JEV said {said!r}")
            if v.get("pass") is True:
                kinds = {c.get("kind") for _, c in checks}
                if not CHECK_KINDS <= kinds:
                    self.bad(i, f"passing verdict without a {sorted(CHECK_KINDS - kinds)} check")
                if not any(_data({"data": x.get("output")}).get("verdict") == "verified" for x in jevs):
                    self.bad(i, "passing verdict without a JEV 'verified' decision")
                if not [j for j, _ in self.of("demo.camera", round=rnd)
                        if self.events[j].get("actor") == "verifier" and reply_at < j < i]:
                    self.bad(i, "passing verdict without a verifier camera view taken after the reply")

        # what follows each verdict: fail -> redispatch with an image attachment, or (memory scoring, then) ESCALATED;
        # pass -> no redispatch
        starts = [(j, _data(e).get("stage")) for j, e in enumerate(self.events)
                  if e.get("type") == "demo.stage" and _data(e).get("status") == "start"]
        for i, v in verdicts:
            nxt = next((s for j, s in starts if j > i), None)
            if v.get("pass") is True:
                if nxt == "redispatch":
                    self.bad(i, "passing verdict followed by a redispatch")
                continue
            if self.crashed and nxt is None:
                continue
            if nxt not in ("redispatch", "memory", "final"):
                self.bad(i, f"failed verdict followed by stage {nxt!r}, not redispatch, memory or final")
            if nxt != "redispatch":
                continue
            rnd = v.get("round")
            nxt_round = rnd + 1 if isinstance(rnd, int) else None
            outs = [(j, d) for j, d in self.of("demo.email", direction="out", wo_id=v.get("wo_id"), round=nxt_round)
                    if j > i]
            if not outs:
                self.bad(i, f"failed verdict redispatched without an outbound email for round {nxt_round}")
                continue
            j, d = outs[0]
            if not [a for a in d.get("attachments") or [] if isinstance(a, dict)
                    and str(a.get("path", "")).lower().endswith(IMAGE_SUFFIXES) and self.exists(a.get("path"))]:
                self.bad(j, "redispatch email has no attached expected-state image that exists")

    def final(self):
        finals = self.of("demo.final")
        if len(finals) != 1:
            self.bad(None, f"{len(finals)} demo.final events, expected exactly 1")
        if not finals:
            return
        i, f = finals[-1]
        later = [e for e in self.events[i + 1:]
                 if not (e.get("type") == "demo.stage" and _data(e).get("stage") == "final")]
        if later:
            self.bad(i, f"{len(later)} demo event(s) after demo.final")
        verdicts = self.of("demo.verdict")
        last = verdicts[-1][1] if verdicts else None
        if f.get("status") == "VERIFIED" and (last is None or last.get("pass") is not True):
            self.bad(i, "VERIFIED but the last verdict did not pass (done means done)")
        if f.get("status") == "ESCALATED" and last is not None and last.get("pass") is True:
            self.bad(i, "ESCALATED although the last verdict passed")
        if f.get("status") == "FAILED":
            if not isinstance(f.get("error"), str) or not f["error"]:
                self.bad(i, "FAILED without an error saying why")
            return
        rounds = len({(d.get("wo_id"), d.get("round")) for _, d in verdicts})
        if f.get("rounds") != rounds:
            self.bad(i, f"rounds={f.get('rounds')!r} but {rounds} round(s) have a verdict")

    def acceptance(self):
        for i, d in self.of("demo.workorder"):
            items = d.get("acceptance") if isinstance(d.get("acceptance"), list) else []
            bars = _verifier_bars(str(d.get("asset")))
            if bars is not None and items[:len(bars)] != bars:
                self.bad(i, f"acceptance does not start with the verifier's {len(bars)} bars")
            extra = items[len(bars):] if bars is not None else items
            for item in extra:
                if isinstance(item, str) and UNPROVABLE.search(item):
                    self.bad(i, f"acceptance item the plant cannot prove (power, temperature or latency): {item[:80]!r}")

    def memory(self):
        recalled = set()
        for i, e in enumerate(self.events):
            type_, d = e.get("type"), _data(e)
            if type_ == "demo.memory" and d.get("op") == "recall":
                recalled |= {lesson.get("id") for lesson in d.get("lessons") or [] if isinstance(lesson, dict)}
            elif type_ == "demo.workorder":
                for lid in d.get("lessons_applied") or []:
                    if lid not in recalled:
                        self.bad(i, f"applies lesson {lid!r}, which no earlier demo.memory recall returned")
            elif type_ == "demo.memory" and d.get("op") == "write":
                prior = [v for j, v in self.of("demo.verdict") if j < i]
                if not prior or prior[-1].get("pass") is not True:
                    self.bad(i, "memory write without a passing verdict before it")
                if not d.get("lessons"):
                    self.bad(i, "memory write with no lessons")

    def images(self):
        for type_ in ("demo.camera", "demo.diffusion"):
            for i, d in self.of(type_):
                p = d.get("image")
                if isinstance(p, str) and Path(p).is_absolute():
                    self.bad(i, f"image {p!r} is absolute; the page serves repo-relative paths as /<path>")
                if not self.exists(p):
                    self.bad(i, f"image {p!r} does not exist")
        for i, e in enumerate(self.events):
            if e.get("type") in ("demo.camera", "demo.diffusion"):
                continue
            for s in _strings(e.get("data")):
                if ("/" in s and "://" not in s and not any(c.isspace() for c in s)
                        and s.lower().endswith(IMAGE_SUFFIXES) and not self.exists(s)):
                    self.bad(i, f"image path {s!r} does not exist")

    def run(self) -> list[str]:
        if not self.events:
            return self.problems + ["no demo.* events"]
        for step in (self.envelope_and_schema, self.stages, self.placement, self.agents, self.emails,
                     self.verdicts, self.final, self.acceptance, self.memory, self.images):
            step()
        return self.problems


def repo_root_for(path: Path) -> Path:
    """`<repo>/runs/<id>` resolves event paths against `<repo>`; anything else against this repo."""
    run_dir = path if path.is_dir() else path.parent
    return run_dir.parent.parent if run_dir.parent.name == "runs" else REPO_ROOT


def load_events(path) -> tuple[list, list[str]]:
    """Events of a run directory or events.jsonl, plus a problem per line that is not JSON."""
    path = Path(path)
    events_path = path / "events.jsonl" if path.is_dir() else path
    events, problems = [], []
    for n, line in enumerate(events_path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            problems.append(f"line {n}: not JSON ({exc})")
    return events, problems


def validate_run(run_dir, repo_root=None) -> list[str]:
    """Problems with a demo run (empty list = valid). `run_dir` is a run directory or an events.jsonl file."""
    path = Path(run_dir)
    events_path = path / "events.jsonl" if path.is_dir() else path
    if not events_path.is_file():
        return [f"{events_path} does not exist"]
    events, problems = load_events(path)
    return problems + _Run(events, Path(repo_root) if repo_root else repo_root_for(path)).run()


def has_demo_events(events_path) -> bool:
    try:
        return '"demo.' in Path(events_path).read_text()
    except OSError:
        return False


if __name__ == "__main__":
    import sys

    failed = False
    for arg in sys.argv[1:]:
        found = validate_run(arg)
        print(f"{arg}: {'OK' if not found else f'{len(found)} problem(s)'}")
        for p in found:
            print(f"  - {p}")
        failed = failed or bool(found)
    sys.exit(1 if failed else 0)
