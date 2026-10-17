"""Workers: inspector, diagnoser, researcher, operator.

Each worker is a tool-calling loop on the worker role. Its system prompt and tool list are module
constants (byte-identical on every turn, so Crusoe's prefix cache hits), and its history is
append-only for the whole run. History is saved after every completed step so a crashed worker
can be restarted from where it was (`Team.run_task`).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from gridtwin import llm
from gridtwin.agents.board import BOARD_TOOLS, Board
from gridtwin.agents.common import ActorToolbox, allow_c2_types, clip, extract_json, last_t_sim, minutes_between, read_events
from gridtwin.agents.research import SEARCH_WEB_SCHEMA, ResearchToolbox

LOG = logging.getLogger("gridtwin.workers")

try:  # the real tool surface (workstream 1); the stub is only for development
    from gridtwin.sim.tools import TOOL_SCHEMAS
except ImportError:  # pragma: no cover
    from tests.agents.stubs import TOOL_SCHEMAS

WORKERS = ("inspector", "diagnoser", "researcher", "operator")
STATE_CHANGING = {"operate", "dispatch_tech", "page_human"}

INSPECT_IMAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "inspect_image",
        "description": "Ask the vision model a question about a photo returned by get_camera_view. Pass its image_path exactly.",
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {"type": "string"},
                "question": {"type": "string", "description": "What to look for, e.g. 'Is the display lit? Any warning LEDs?'"},
            },
            "required": ["image_path", "question"],
            "additionalProperties": False,
        },
    },
}

COMMON = """You are the {role} on GridTwin's autonomous operations team for a 5 MWac solar plant: inverters INV-1..INV-4 (1250 kWac each) feed breakers 52-I1..52-I4, a 34.5 kV collector bus, main breaker 52-M and step-up transformer XFMR-1, then the POI-1 revenue meter. There is also a battery (BESS-1), a met station (MET-1), PV blocks B1..B4 and combiner boxes CB-<block>.<n>.

You work through tools on the live plant (SCADA telemetry, alarms, fixed cameras, field diagnostics, control commands, technician dispatch) and share what you learn on the team board. A separate verifier will later check the team's claims against the plant itself, so only evidence counts.

How to work:
- Evidence over assumptions. Device telemetry can be frozen or wrong: compare each reading's last_update_ts with "now", and cross-check what devices report about themselves against independent sources (the POI-1 revenue meter, peers, cameras, diagnostics). A reading you cannot date is not evidence.
- Call read_board before starting a new line of investigation and do not repeat work already posted. Post every finding with post_finding, citing the event seq numbers (the "seq" field of tool results) that back it.
- Tool calls are limited for the whole team (about 60, verification included). Read several points in one read_telemetry call, use short windows, never repeat a call whose answer you already have, and use wait only when something needs time to take effect.
- Safety: every operate and dispatch_tech passes a safety gate. If it refuses, change course; if it asks for confirmation, gather the missing evidence first. Work that requires de-energized equipment needs its feeding breaker opened first (lockout) and closed again once the work is complete. Never close, restart or reset onto a suspected fault.
- When your task is done, reply with a short plain-text report (what you checked, what you found, the seq numbers) and no tool call.

Your role:
{duty}"""

DUTIES = {
    "inspector": "You inspect equipment with the fixed cameras (get_camera_view, rgb or thermal) and read the photos with inspect_image. Describe what you see factually: displays, indicator LEDs, leaks, staining, damage, heat. You do not change plant state.",
    "diagnoser": "You find root causes from SCADA telemetry, alarms and field diagnostics. Compare inverters with each other and with the POI-1 meter, check data freshness, and run a diagnostic when the data alone cannot settle a question. You do not change plant state.",
    "researcher": "You look up OEM manuals, alarm codes and standards with search_web and post what applies, with exact URLs and short exact quotes. Never invent a URL or a quote; if a search finds nothing useful, say so. You do not change plant state.",
    "operator": "You are the only worker allowed to change plant state (operate, dispatch_tech, page_human). Act on the evidence on the board and in your task, choose the least invasive safe action, follow lockout, wait for technician tickets to complete, and confirm the effect with a fresh reading. Curtail to a safe limit when equipment is at risk, and page a human when no safe fix exists, with a brief that says what is wrong, what was done and what is needed.",
}

SYSTEM = {name: COMMON.format(role=name, duty=DUTIES[name]) for name in WORKERS}
TOOLS = {
    "inspector": TOOL_SCHEMAS + BOARD_TOOLS + [INSPECT_IMAGE_SCHEMA],
    "diagnoser": TOOL_SCHEMAS + BOARD_TOOLS,
    "researcher": TOOL_SCHEMAS + BOARD_TOOLS + [SEARCH_WEB_SCHEMA],
    "operator": TOOL_SCHEMAS + BOARD_TOOLS,
}

CLAIM_INSTRUCTION = """ROUND REVIEW for the goal: {goal}

Team board:
{board}
{feedback}
Decide the team's status now. You may make at most a few tool calls first, only to finish work already under way (for example close a breaker after completed work, confirm with one fresh reading, or page a human). Then reply with only this JSON:
{{"status": "DONE" | "ESCALATED" | "NO_ACTION" | "CONTINUE", "summary": "..."}}
DONE = the problem is fixed and the plant is healthy now. ESCALATED = a human has been paged because no available command or work order can safely fix the confirmed cause; the problem is not resolved. A cause that an available work order or command fixes is not an escalation: CONTINUE so it gets fixed. NO_ACTION = nothing is wrong and nothing was changed. CONTINUE = more work is needed (the planner will assign it); say in the summary what is missing."""


class WorkerKilled(RuntimeError):
    """Raised by --chaos to simulate a worker process dying mid-task."""


class Chaos:
    """Kill one worker once, at a given step of its N-th task."""

    def __init__(self, worker: str = "diagnoser", at_step: int = 1):
        self.worker, self.at_step, self.fired = worker, at_step, False

    def maybe_kill(self, worker: str, step: int):
        if not self.fired and worker == self.worker and step >= self.at_step:
            self.fired = True
            raise WorkerKilled(f"chaos: worker {worker} killed at step {step}")


def _vision_describe(image_path: str, question: str) -> str:
    try:
        from gridtwin.vision import describe  # workstream 3
    except ImportError:
        from gridtwin.agents.vision_fallback import describe
    return describe(image_path, question)


READ_ONLY = {"list_assets", "list_alarms", "read_telemetry"}


def annotate(result):
    """Add data_age_min (now - last_update_ts) to a telemetry result: arithmetic on two observed fields,
    so a frozen-but-plausible reading is as visible to a worker as it is to the verifier."""
    if isinstance(result, dict) and result.get("last_update_ts") and result.get("now"):
        age = minutes_between(result["last_update_ts"], result["now"])
        if age is not None:
            result = dict(result, data_age_min=round(age, 1))
    return result


class ReadCache:
    """Team-wide read cache. Within one sim minute, telemetry for an asset is read once (all points, 60-min
    window) and every later read of that asset is answered from it; identical alarm/registry reads are too.
    Cache hits cost no tool budget. Nothing is cached across sim time."""

    def __init__(self, log, registry: dict | None = None):
        self.log, self.registry, self.hits = log, registry, 0
        self.seen: dict[str, tuple] = {}
        self.snaps: dict[str, dict] = {}

    @staticmethod
    def _points(args) -> list | None:
        p = args.get("points")
        if isinstance(p, str):
            p = [x.strip() for x in p.split(",") if x.strip()]
        return sorted(p) if p else None

    @staticmethod
    def _window(args) -> int:
        try:
            return max(1, min(240, int(args.get("window_min") or 60)))
        except (TypeError, ValueError):
            return 60

    def key(self, name: str, args: dict) -> str:
        a = dict(args)
        if name == "list_alarms":
            a = {} if a.get("active_only", True) in (True, "true", None) else {"active_only": False}
        return name + json.dumps(a, sort_keys=True, default=str)

    def fetch_args(self, name: str, args: dict) -> dict:
        if name == "read_telemetry" and args.get("asset"):
            return {"asset": args["asset"], "window_min": max(60, self._window(args))}
        return args

    def view(self, result: dict, args: dict) -> dict:
        """What the worker asked for, cut from a full read."""
        if not isinstance(result, dict) or not isinstance(result.get("points"), dict):
            return result
        want, win, now = self._points(args), self._window(args), result.get("now")
        pts = {}
        for p, series in result["points"].items():
            if want and p not in want:
                continue
            if isinstance(series, list):
                series = [x for x in series if not (isinstance(x, list) and x and (minutes_between(x[0], now) or 0) > win)]
            pts[p] = series
        for p in want or []:
            pts.setdefault(p, {"error": "unknown point"})
        return dict(result, points=pts)

    def get(self, name: str, args: dict):
        now = last_t_sim(self.log)
        if name == "list_assets" and self.registry:
            self.hits += 1
            return {"seq": None, "result": {"note": "the registry is in your briefing; no tool call spent"}}
        if name == "read_telemetry":
            snap = self.snaps.get(str(args.get("asset")))
            if snap and snap["t"] == now and self._window(args) <= snap["window"]:
                self.hits += 1
                return {"seq": snap["seq"], "result": annotate(self.view(snap["result"], args)),
                        "note": f"from the team's read of this asset at seq {snap['seq']} (same sim minute); no tool call spent"}
            return None
        if name in READ_ONLY:
            hit = self.seen.get(self.key(name, args))
            if hit and hit[0] == now:
                self.hits += 1
                return {"seq": hit[1], "result": hit[2], "note": f"same as seq {hit[1]} (same sim minute); no tool call spent"}
        return None

    def put(self, name: str, args: dict, seq, result):
        if not (isinstance(result, dict) and "error" not in result):
            return
        if name == "read_telemetry" and args.get("asset"):
            self.snaps[str(args["asset"])] = {"t": last_t_sim(self.log), "seq": seq, "result": result,
                                              "window": self._window(args)}
        elif name in READ_ONLY:
            self.seen[self.key(name, args)] = (last_t_sim(self.log), seq, result)


class Worker:
    def __init__(self, name: str, toolbox, log, board: Board, kb_dir, run_dir: Path, chaos: Chaos | None = None,
                 cache: ReadCache | None = None):
        assert name in WORKERS, name
        self.name, self.actor = name, f"worker:{name}"
        self.log, self.board, self.chaos = log, board, chaos
        self.tools = TOOLS[name]
        self.messages = [{"role": "system", "content": SYSTEM[name]}]
        if name == "researcher":
            self.tb = ResearchToolbox(toolbox, log, kb_dir, self.actor)
        else:
            self.tb = ActorToolbox(toolbox, self.actor)
        self.path = Path(run_dir) / "workers" / f"{name}.json"
        self.actions: list[tuple[str, str]] = []  # (tool, canonical args) for loop detection
        self.tool_calls = 0
        self.cache = cache or ReadCache(log)
        self.task_calls, self.task_cap = 0, 99

    # ----- persistence -----
    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"messages": self.messages, "actions": self.actions, "tool_calls": self.tool_calls}, default=str))
        tmp.replace(self.path)

    def load(self) -> bool:
        if not self.path.is_file():
            return False
        d = json.loads(self.path.read_text())
        self.messages, self.tool_calls = d["messages"], d.get("tool_calls", 0)
        self.actions = [tuple(a) for a in d.get("actions", [])]
        return True

    def brief(self, text: str):
        """One-time context (goal, plant registry, lessons) appended right after the system prompt."""
        self.messages.append({"role": "user", "content": text})
        self.save()

    # ----- tools -----
    def _dispatch(self, name: str, args: dict) -> dict:
        board_result = self.board.handle(self.name, name, args)
        if board_result is not None:
            return board_result
        if name == "inspect_image":
            if self.name != "inspector":
                return {"error": "inspect_image is the inspector's tool"}
            try:
                return {"answer": _vision_describe(args.get("image_path", ""), args.get("question", "Describe the equipment state."))}
            except Exception as e:
                return {"error": f"vision failed: {e}"}
        if name in STATE_CHANGING and self.name != "operator":
            return {"error": f"only the operator changes plant state; post a finding recommending '{name}' instead"}
        cached = self.cache.get(name, args)
        if name not in READ_ONLY or cached is not None:  # loop detection: actions and repeated identical reads
            self.actions.append((name, json.dumps(args, sort_keys=True)))
        if cached is not None:
            return cached
        if self.task_calls >= self.task_cap:
            return {"error": f"tool-call cap for this task ({self.task_cap}) reached; post your findings and report now"}
        self.task_calls += 1
        self.tool_calls += 1
        fetch = self.cache.fetch_args(name, args)
        result = self.tb.call(name, fetch)
        seq = None
        evs = read_events(self.log)
        for e in reversed(evs[-4:]):
            if e.get("type") == "tool_result":
                seq = e.get("seq")
                break
        self.cache.put(name, fetch, seq, result)
        if fetch is not args:
            result = self.cache.view(result, args)
        return {"seq": seq, "result": annotate(result)}

    # ----- loop -----
    def step(self) -> str | None:
        """One model turn. Returns the final text when the model stops calling tools, else None."""
        resp = llm.chat("worker", self.messages, max_tokens=4096, tools=self.tools, log=self.log, actor=self.actor)
        msg = resp.choices[0].message
        calls = list(msg.tool_calls or [])
        assistant = {"role": "assistant", "content": msg.content or ""}
        if calls:
            assistant["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                for c in calls
            ]
        new = [assistant]
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            result = self._dispatch(c.function.name, args)
            new.append({"role": "tool", "tool_call_id": c.id, "content": clip(result, 2600)})
        self.messages.extend(new)
        self.save()
        if not calls:
            return msg.content or ""
        return None

    def run_task(self, instruction: str, max_steps: int = 8, resume: bool = False, max_calls: int = 6) -> str:
        if not resume:
            self.task_calls = 0
        self.task_cap = max(0, max_calls)
        if not resume:
            self.messages.append({"role": "user", "content": f"TASK: {instruction}"})
            self.save()
            start = 0
        else:  # count steps already taken for this task
            start = 0
            for m in reversed(self.messages):
                if m["role"] == "user":
                    break
                if m["role"] == "assistant":
                    start += 1
        for i in range(start, max_steps):
            if self.chaos:
                self.chaos.maybe_kill(self.name, i)
            out = self.step()
            if out is not None:
                return out
        # step cap: ask for a report without executing anything else
        self.messages.append({"role": "user", "content": "Step limit for this task reached. Reply now with your report; do not call tools."})
        resp = llm.chat("worker", self.messages, max_tokens=2048, tools=self.tools, log=self.log, actor=self.actor)
        msg = resp.choices[0].message
        calls = list(msg.tool_calls or [])
        assistant = {"role": "assistant", "content": msg.content or ""}
        if calls:
            assistant["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                for c in calls
            ]
        self.messages.append(assistant)
        for c in calls:
            self.messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps({"error": "step limit reached; not executed"})})
        self.save()
        return msg.content or "(step limit reached without a report)"


class Team:
    """The four workers for one run, with restart-from-history recovery."""

    def __init__(self, toolbox, log, kb_dir, run_dir: Path, briefing: str, chaos: Chaos | None = None, max_restarts: int = 2,
                 registry: dict | None = None):
        self.toolbox, self.log, self.kb_dir, self.run_dir = toolbox, log, kb_dir, Path(run_dir)
        self.cache = ReadCache(log, registry)
        allow_c2_types(log)
        self.board = Board(log)
        self.briefing, self.chaos, self.max_restarts = briefing, chaos, max_restarts
        self.workers: dict[str, Worker] = {}
        self.restarts = 0

    def worker(self, name: str) -> Worker:
        if name not in self.workers:
            w = Worker(name, self.toolbox, self.log, self.board, self.kb_dir, self.run_dir, self.chaos, self.cache)
            w.brief(self.briefing)
            self.workers[name] = w
        return self.workers[name]

    def run_task(self, worker: str, instruction: str, max_steps: int = 6, max_calls: int = 6) -> str:
        worker = worker if worker in WORKERS else "diagnoser"
        w = self.worker(worker)
        resume = False
        for attempt in range(self.max_restarts + 1):
            try:
                return w.run_task(instruction, max_steps=max_steps, resume=resume, max_calls=max_calls)
            except Exception as e:  # crash, timeout, 429 storm: restart from saved history
                if attempt >= self.max_restarts:
                    self.log.emit("supervisor", "supervisor", {"decision": "worker_failed", "reason": f"{worker}: {type(e).__name__}: {e}"})
                    return f"(worker {worker} failed: {type(e).__name__})"
                self.restarts += 1
                self.log.emit("supervisor", "supervisor", {
                    "decision": "restart", "reason": f"worker {worker} crashed ({type(e).__name__}: {e}); restarting from saved history",
                })
                fresh = Worker(worker, self.toolbox, self.log, self.board, self.kb_dir, self.run_dir, self.chaos, self.cache)
                fresh.load()
                fresh.task_calls = w.task_calls
                self.workers[worker] = w = fresh
                resume = True
        return ""

    def claim(self, goal: str, feedback: str = "", max_steps: int = 3, max_calls: int = 2) -> dict:
        posts = self.board.read()
        board = "\n".join(f"- [{p['worker']}] {p['text']} (seqs {p.get('evidence')})" for p in posts) or "(no posts)"
        text = self.run_task("operator", CLAIM_INSTRUCTION.format(goal=goal, board=board, feedback=feedback), max_steps=max_steps,
                             max_calls=max_calls)
        d = extract_json(text) if text else None
        if not isinstance(d, dict):
            d = {"status": "CONTINUE", "summary": (text or "")[:500], "next_tasks": []}
        status = str(d.get("status", "CONTINUE")).upper().replace(" ", "_")
        if status not in ("DONE", "ESCALATED", "NO_ACTION", "CONTINUE"):
            status = "CONTINUE"
        tasks = [t for t in d.get("next_tasks") or [] if isinstance(t, dict) and t.get("instruction")]
        return {"status": status, "summary": str(d.get("summary", ""))[:1500], "next_tasks": tasks}

    def actions(self) -> list[tuple[str, str]]:
        out = []
        for w in self.workers.values():
            out.extend(w.actions)
        return out

    def tool_calls(self) -> int:
        return sum(w.tool_calls for w in self.workers.values())
