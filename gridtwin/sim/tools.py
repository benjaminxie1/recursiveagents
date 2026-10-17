"""The tool surface agents get (contract C1).

TOOL_SCHEMAS is a module constant: it must stay byte-identical across calls for prefix caching.
Toolbox.call never raises; errors come back as {"error": ...}. Nothing here returns sim.truth.
"""
import json

WORK_ORDERS = [
    "inspect",
    "replace_fan_filter",
    "reseat_comms_card",
    "replace_mc4_connector",
    "clean_modules",
    "mow_vegetation",
    "replace_module",
    "transformer_oil_service",
]
ACTIONS = [
    "open", "close",                      # breakers 52-*
    "restart", "reset_alarm", "set_curtail_pct",  # inverters INV-*
    "isolate", "restore",                 # combiner boxes CB-*
    "set_dispatch_kw",                    # BESS-1
    "set_plant_limit_kw",                 # POI-1
]
DIAGNOSTICS = ["comms_ping", "insulation_resistance", "iv_curve", "dga_sample"]


def _fn(name, description, properties, required=()):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


_ASSET = {"type": "string", "description": "Asset id, e.g. INV-3, 52-I4, XFMR-1, POI-1, CB-2.3"}

TOOL_SCHEMAS = [
    _fn("list_assets", "List every plant asset with its type, rating and available telemetry points and viewpoints.", {}),
    _fn(
        "list_requirements",
        "List the inspection requirements (codes and standards x components) that apply to an asset or to the whole plant: id, title, cited standards and how each is verified. Status is not included; it must be proven with evidence.",
        {"asset": {"type": "string", "description": "Asset id; omit for every requirement in the plant."}},
    ),
    _fn(
        "list_alarms",
        "List SCADA alarms.",
        {"active_only": {"type": "boolean", "description": "Only alarms that are still active (default true)."}},
    ),
    _fn(
        "read_telemetry",
        "Read SCADA telemetry for one asset: recent samples per point plus last_update_ts (when SCADA last heard from the device).",
        {
            "asset": _ASSET,
            "points": {"type": "array", "items": {"type": "string"}, "description": "Point names; omit for all."},
            "window_min": {"type": "integer", "description": "Minutes of history, 1-240 (default 60)."},
        },
        ["asset"],
    ),
    _fn(
        "get_camera_view",
        "Take a photo from a fixed site camera. Returns an image path to inspect. Costs 2 sim-minutes.",
        {
            "viewpoint": {"type": "string", "description": "Viewpoint id from list_assets, e.g. inv3_cabinet."},
            "mode": {"type": "string", "enum": ["rgb", "thermal"], "description": "Default rgb."},
        },
        ["viewpoint"],
    ),
    _fn(
        "run_diagnostic",
        "Run a field diagnostic. comms_ping (10 min), insulation_resistance (20 min), iv_curve (30 min), dga_sample (30 min).",
        {"asset": _ASSET, "test": {"type": "string", "enum": DIAGNOSTICS}},
        ["asset", "test"],
    ),
    _fn(
        "operate",
        "Send a control command. Every command passes a safety gate first and may be refused. Costs 1 sim-minute.",
        {
            "asset": _ASSET,
            "action": {"type": "string", "enum": ACTIONS},
            "value": {"type": "number", "description": "For set_curtail_pct (0-100), set_dispatch_kw, set_plant_limit_kw."},
        },
        ["asset", "action"],
    ),
    _fn(
        "dispatch_tech",
        "Dispatch a field technician with a work order. Returns a ticket; the work completes after its duration (call wait). Costs money. Passes the safety gate.",
        {"asset": _ASSET, "work_order": {"type": "string", "enum": WORK_ORDERS}},
        ["asset", "work_order"],
    ),
    _fn(
        "wait",
        "Let sim time pass so actions and tickets take effect.",
        {"minutes": {"type": "integer", "description": "1-240."}},
        ["minutes"],
    ),
    _fn(
        "page_human",
        "Page the on-call human engineer. Use only when the problem needs a person; the brief must say what is wrong, what was done and what is needed.",
        {
            "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
            "brief": {"type": "string"},
        },
        ["severity", "brief"],
    ),
]
TOOL_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS]
GATED = {"operate", "dispatch_tech"}


class Toolbox:
    def __init__(self, sim, log, gate=None, notifier=None, budget=None):
        self.sim = sim
        self.log = log
        self.gate = gate
        self.notifier = notifier
        self.budget = budget or {}
        self.calls = 0

    def _exhausted(self) -> bool:
        if self.budget.get("tool_calls") is not None and self.calls >= self.budget["tool_calls"]:
            return True
        if self.budget.get("sim_minutes") is not None and self.sim.elapsed_min >= self.budget["sim_minutes"]:
            return True
        return False

    def call(self, name: str, args: dict | None = None) -> dict:
        args = dict(args or {})
        by = getattr(self, "actor", None)  # set by agents.common.ActorToolbox; lets viewers attribute calls (additive, C2)
        self.log.emit("sim", "tool_call", {"name": name, "args": args, **({"by": by} if isinstance(by, str) else {})})
        if self._exhausted():
            result = {"error": "budget_exhausted"}
        else:
            self.calls += 1
            try:
                result = self._dispatch(name, args)
            except Exception as e:  # never raise into the agent loop
                result = {"error": f"{type(e).__name__}: {e}"}
        result = json.loads(json.dumps(result, default=str))
        self.log.emit("sim", "tool_result", {"name": name, "result": result})
        return result

    def _dispatch(self, name, args):
        sim = self.sim
        if name not in TOOL_NAMES:
            return {"error": f"unknown tool {name!r}"}
        if name in GATED:
            refusal = self._gate(name, args)
            if refusal is not None:
                return refusal
        if name == "list_assets":
            return sim.list_assets()
        if name == "list_requirements":
            return sim.requirements(args.get("asset"))
        if name == "list_alarms":
            return {"alarms": sim.alarms(args.get("active_only", True))}
        if name == "read_telemetry":
            return sim.telemetry(args["asset"], args.get("points"), int(args.get("window_min", 60)))
        if name == "get_camera_view":
            return sim.camera_view(args["viewpoint"], args.get("mode", "rgb"), self.log)
        if name == "run_diagnostic":
            return sim.diagnostic(args["asset"], args["test"])
        if name == "operate":
            return sim.operate(args["asset"], args["action"], args.get("value"))
        if name == "dispatch_tech":
            return sim.dispatch(args["asset"], args["work_order"])
        if name == "wait":
            minutes = int(args["minutes"])
            if not 1 <= minutes <= 240:
                return {"error": "minutes must be 1-240"}
            sim.advance(minutes)
            return {"now": sim.now_str(), "waited_min": minutes}
        if name == "page_human":
            sev, brief = args["severity"], str(args["brief"])
            sim.record_page(sev, brief)
            self.log.emit("sim", "page", {"severity": sev, "brief": brief})
            if self.notifier is not None:
                try:
                    self.notifier(sev, brief)
                except Exception as e:
                    return {"paged": True, "notify_error": str(e)}
            return {"paged": True, "at": sim.now_str()}

    def _gate(self, name, args):
        if self.gate is None:
            return None
        proposed = {"tool": name, **args}
        state = self.sim.gate_state(proposed)
        try:
            d = self.gate(proposed, state)
        except Exception as e:
            d = {"route": "confirm", "unsafe": 0.0, "reason": f"gate error: {e}"}
        route, unsafe, reason = d.get("route", "confirm"), float(d.get("unsafe", 0.0)), d.get("reason", "")
        # Which part decided, additive (C2): "policy" for the hard-coded interlock rules, else the decision model's backend.
        src = "policy" if str(reason).startswith("policy rule") else getattr(self.gate, "backend", None)
        self.log.emit("gate", "gate", {"proposed": proposed, "route": route, "unsafe": unsafe, "reason": reason,
                                       **({"source": src} if isinstance(src, str) else {})})
        if route == "reject" or unsafe > 0.5:
            return {"error": f"refused by safety gate: {reason}", "route": route, "unsafe": unsafe}
        if route == "confirm":
            return {"needs_confirmation": reason}
        return None
