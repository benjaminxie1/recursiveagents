"""The end-to-end demo run (CONTRACT.md): diagnose -> plan (one level of sub-agents) -> email the contractor ->
contractor works on the sim -> verify against the plant (telemetry, camera + Omni, JEV) -> email back and loop ->
write the verified lesson. Every stage is a demo.* event in runs/<id>/events.jsonl, which viewer/demo.html renders.

The plant, the contractor and email delivery are simulated. The model calls are real (Crusoe) unless stub=True.
The verifier never reads the contractor's email: every check reads the plant through the Toolbox.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gridtwin import llm
from gridtwin.agents.common import minutes_between, tel_latest
from gridtwin.demo import brains as B
from gridtwin.demo import memory as M
from gridtwin.demo import recursion as R
from gridtwin.demo.contractors import OPS_ADDR, Contractor, Mailbox, contractor_for, rel, work_order_email
from gridtwin.demo.events import SUBAGENT_ROLES, LockedLog
from gridtwin.sim import episode
from gridtwin.sim.camera import REPO
from gridtwin.sim.plant import BREAKER_FOR, INVERTERS
from gridtwin.sim.tools import WORK_ORDERS

SCENARIO = "s06_stale_scada"
STALE_MIN = 10          # SCADA older than this is stale (diagnosis)
FRESH_MIN = 5           # acceptance: SCADA must be fresher than this
VISUAL_CONF = 0.7       # the verifier's bar for a camera check (same as the full loop)
MAX_ROUNDS = 3
POINTS = ["ac_kw", "dc_kw", "status", "heatsink_c"]
HEALTHY_CLAIM = "The inverter's front display or touchscreen is lit and showing live readings; it is not dark or blank."
SHORTCUT_FORBID = "Do not substitute an inspect-only visit or a power-cycle for the work order; it does not restore a failed comms card."
TWIN_SCENE = os.environ.get("GRIDTWIN_TWIN_SCENE", "_dev")


# ---------- optional pieces (built in parallel; each has a fallback so the demo never depends on it) ----------

def _registration(scene: str):
    p = REPO / "assets" / "splats" / f"{scene}.registration.json"
    if not p.exists():
        alt = Path.home() / "claude" / "recursiveagents" / "assets" / "splats" / p.name
        p = alt if alt.exists() else p
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {"views": {}}


def jev_step(registration, asset, telemetry, visual, log=None, stub=False) -> tuple[dict, dict]:
    """(interpreted JSON state, decision). Uses gridtwin.demo.jev when present; stub mode never calls out."""
    try:
        from gridtwin.demo import jev
        state = jev.interpret(registration, asset, telemetry, visual)
        if not stub:
            return state, jev.decide(state, log=log)
        ask_model = jev._ask_model  # stub: keep decide() (and its vetoes) but never call a model

        def offline(*_a, **_k):
            raise RuntimeError("stub mode: no model call")
        jev._ask_model = offline
        try:
            return state, jev.decide(state, log=log)
        finally:
            jev._ask_model = ask_model
    except Exception as e:  # noqa: BLE001 - fallback keeps the demo running
        age = _age(telemetry)
        state = {"asset": asset, "sensors": {"data_age_min": age, "stale": age is None or age > FRESH_MIN},
                 "visual": visual, "note": f"minimal interpreter ({type(e).__name__}: {e})"[:200]}
        good = (not state["sensors"]["stale"]) and visual and all(v.get("pass") for v in visual)
        return state, {"verdict": "verified" if good else "not_verified", "p_verified": 0.9 if good else 0.1,
                       "uncertainty": 0.2, "backend": "local", "reason": "rule: deterministic fallback (jev module unavailable)"}


def expected_image(asset, src, prompt, out_path) -> dict:
    try:
        from gridtwin.demo import diffusion
        return diffusion.expected_state_image(asset, src, prompt, out_path)
    except Exception as e:  # noqa: BLE001
        import shutil
        bank = sorted((REPO / "assets" / "photos" / asset / "healthy").glob("*.jpg"))
        if not bank:
            return {"image": None, "backend": "none", "prompt": prompt, "source": f"{type(e).__name__}"}
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        dst = Path(out_path).with_suffix(".jpg")
        shutil.copy(bank[0], dst)
        return {"image": rel(dst), "backend": "photo_bank", "prompt": prompt, "source": rel(bank[0])}


# ---------- vision (live: Nemotron Omni via gridtwin.vision; stub: the sim's own visual state) ----------

class Eyes:
    def __init__(self, sim, stub: bool):
        self.sim, self.stub = sim, stub

    def describe(self, image, asset, question) -> str:
        if self.stub:
            return f"(stub) {asset} looks {self.sim.visual_state(asset, 'rgb').replace('_', ' ')}"
        from gridtwin import vision
        return vision.describe(image, question)

    def check(self, image, asset, claim) -> dict:
        if self.stub:
            good = self.sim.visual_state(asset, "rgb") == "healthy"
            return {"pass": good, "confidence": 0.95, "evidence": f"(stub) display {'lit' if good else 'dark'}", "model": "stub"}
        from gridtwin import vision
        return vision.check_detail(image, claim)


def _age(tel) -> float | None:
    if not isinstance(tel, dict):
        return None
    a = minutes_between(tel.get("last_update_ts"), tel.get("now"))
    return round(a, 1) if a is not None else None


def _latest(tel) -> dict:
    out = {}
    for p in POINTS:
        try:
            v = tel_latest(tel, p)
        except Exception:  # noqa: BLE001
            v = None
        if isinstance(v, (list, tuple)) and len(v) == 2:  # (ts, value)
            v = v[1]
        if v is not None:
            out[p] = v
    return out


class Demo:
    def __init__(self, scenario=SCENARIO, memory_dir=None, stub=False, run_id=None, max_rounds=MAX_ROUNDS, runs_dir=None):
        from gridtwin.agents.gate import make_gate

        self.t0 = time.time()
        self.gate = make_gate()
        self.sim, self.tb, raw, self.sc = episode.start(scenario, run_id=run_id, gate=self.gate, runs_dir=runs_dir)
        if hasattr(self.gate, "client"):
            self.gate.client.log = raw  # the gate's lockout rule reads open tickets from this run's log
        self.raw_log, self.log = raw, LockedLog(raw)
        self.run_dir = Path(raw.run_dir)
        self.run_id = self.run_dir.name
        self.brains = B.StubBrains(self.log) if stub else B.LiveBrains(self.log)
        self.eyes = Eyes(self.sim, stub)
        self.memory = M.Memory(memory_dir)
        self.mail = Mailbox(self.run_dir)
        self.max_rounds = max_rounds
        self.stub = stub
        if stub:
            os.environ["GRIDTWIN_NO_DIFFUSION"] = "1"  # offline rehearsal: photo bank, no model load
        self.viewpoints = {vp: info.get("asset") for vp, info in self.sim.camera.viewpoints.items()
                           if "rgb" in (info.get("modes") or ["rgb"])}
        self.used_viewpoints: set[str] = set()
        self.twin_url = f"twin.html?scene={TWIN_SCENE}&run={self.run_id}"

    # ----- helpers -----
    def emit(self, actor, type_, data):
        return self.log.emit(actor, type_, data)

    def on_agent(self, agent, status, output):
        """demo.agent for the diagnosis and verification teams (gridtwin.demo.recursion)."""
        self.emit(R.actor_for(agent), "demo.agent", R.agent_event(agent, status, output))

    def stage(self, name, status, rnd=0, **extra):
        self.emit("orchestrator", "demo.stage", {"stage": name, "status": status, "round": rnd, **extra})

    def vps_for(self, asset) -> list[str]:
        return [v for v, a in self.viewpoints.items() if a == asset]

    def photo(self, viewpoint, actor, rnd, question=None, asset=None) -> dict:
        res = self.tb.call("get_camera_view", {"viewpoint": viewpoint})
        self.used_viewpoints.add(viewpoint)
        return {"viewpoint": viewpoint, "asset": asset or res.get("asset"), "mode": "rgb", "image": res.get("image_path"),
                "round": rnd, "_q": question, "_actor": actor, "error": res.get("error")}

    # ----- stages -----
    def diagnose(self) -> dict:
        self.stage("diagnose", "start", twin_url=self.twin_url)
        self.tb.call("list_alarms", {"active_only": True})
        sensors = []
        for inv in INVERTERS:
            tel = self.tb.call("read_telemetry", {"asset": inv, "points": POINTS, "window_min": 30})
            age = _age(tel)
            s = {"asset": inv, "data_age_min": age, "last_update_ts": tel.get("last_update_ts"), "latest": _latest(tel),
                 "stale": age is None or age > STALE_MIN}
            sensors.append(s)
            self.emit("diagnoser", "demo.sensor", s)
        suspects = [s["asset"] for s in sensors if s["stale"]] or [INVERTERS[0]]
        diags = {a: self.tb.call("run_diagnostic", {"asset": a, "test": "comms_ping"}) for a in suspects}
        healthy = next((s["asset"] for s in sensors if not s["stale"]), None)
        shots = [self.photo(self.vps_for(a)[0], "diagnoser", 0, asset=a) for a in suspects if self.vps_for(a)]
        if healthy and self.vps_for(healthy):
            shots.append(self.photo(self.vps_for(healthy)[0], "diagnoser", 0, asset=healthy))
        q = "Is the inverter's display or touchscreen lit and showing readings? Describe what you see."
        with ThreadPoolExecutor(max_workers=max(1, len(shots))) as ex:
            descs = list(ex.map(lambda s: self.eyes.describe(s["image"], s["asset"], q) if s["image"] else s["error"], shots))
        for s, d in zip(shots, descs):
            s["description"] = d
            self.emit("diagnoser", "demo.camera", {k: v for k, v in s.items() if not k.startswith("_") and k != "error"})
        evidence = {"sensors": sensors, "diagnostics": diags,
                    "camera": [{"asset": s["asset"], "viewpoint": s["viewpoint"], "description": s["description"]} for s in shots],
                    "splat_twin": "camera stations registered in the Gaussian-splat twin; see twin_url"}
        stale = [s["asset"] for s in sensors if s["stale"]]
        self.lessons = self.memory.recall(f"{' '.join(stale)} stale scada frozen telemetry data_age_min "
                                          + " ".join(str(s.get("description", "")) for s in shots), role="diagnosis")
        try:
            dx = R.diagnose_team(evidence, self.lessons, "stub" if self.stub else "live", self.on_agent, log=self.log)
        except Exception as e:  # noqa: BLE001 - the flat diagnosis keeps the demo running
            try:
                dx = self.brains.diagnose(evidence)
            except Exception:  # noqa: BLE001
                dx = B.StubBrains().diagnose(evidence)
            dx["summary"] = f"{dx['summary']} (diagnosis team failed: {type(e).__name__})"
        dx = {"asset": dx.get("asset") if dx.get("asset") in INVERTERS else suspects[0], "fault": str(dx.get("fault", "unknown")),
              "confidence": float(dx.get("confidence") or 0.5), "summary": str(dx.get("summary", "")),
              "evidence": [str(x) for x in (dx.get("evidence") or [])][:6], "twin_url": self.twin_url,
              "model": B.ROLES["planner"] if not self.stub else "stub"}
        self.emit("planner", "demo.diagnosis", dx)
        self.stage("diagnose", "end")
        return dx

    def plan(self, dx: dict) -> dict:
        self.stage("plan", "start")
        lessons = self.memory.recall(f"{dx['asset']} {dx['fault']} {dx['summary']} {' '.join(dx['evidence'])}", role="planner")
        self.applied = [l["id"] for l in lessons]
        forbid_mem = [f for l in lessons for f in (l.get("forbid") or [])]
        notes = self.memory.contractor_notes(contractor_for(dx["asset"])["name"])
        self.emit("memory", "demo.memory", {
            "op": "recall", "lessons": [{"id": l["id"], "text": M.summary(l)} for l in lessons],
            "hints": {r: M.lesson_hints(lessons, r) for r in ("diagnosis", "repair", "verify", "contractor")},
            "contractor_notes": notes,
            "effect": (f"work order will forbid: {'; '.join(dict.fromkeys(forbid_mem))}" if forbid_mem
                       else "no verified lesson for this symptom yet") if lessons else
                      ("memory is empty for this symptom" + (f"; contractor history: {notes[-1]}" if notes else ""))})
        root = B.Agent("planner", "planner", "planner", 0, task=f"Plan the repair of {dx['asset']} ({dx['fault']})")
        self.emit("planner", "demo.agent", root.event("spawned"))
        try:
            plan = self.brains.plan(dx, lessons)
        except Exception as e:  # noqa: BLE001
            plan = B.StubBrains().plan(dx, lessons)
            plan["rationale"] = f"fallback briefs (planner call failed: {type(e).__name__})"
        briefs = plan.get("briefs") if isinstance(plan.get("briefs"), dict) else {}

        def on_event(agent, status, output):
            self.emit(f"subagent:{agent.role}", "demo.agent", agent.event(status, output))

        outs = B.run_subagents(root, self.brains, briefs, dx, lessons, on_event)
        try:  # the cap is enforced in code: a depth-1 agent cannot spawn
            root.children[0].spawn("probe", "must fail")
            capped = False
        except B.DepthExceeded:
            capped = True
        wo = self.work_order(dx, outs, lessons, forbid_mem)
        try:  # memory flywheel: lessons' forbid/acceptance items and this contractor's history
            wo, _ = self.memory.apply_to_work_order(wo, lessons, contractor_for(dx["asset"])["name"])
        except Exception:  # noqa: BLE001 - the work order above already carries the lessons' forbid items
            pass
        self.emit("planner", "demo.agent", root.event("done", {"briefs": briefs, "rationale": plan.get("rationale"),
                                                              "depth_cap_enforced": capped, "work_order": wo["wo_id"],
                                                              "override": self.override}))
        self.emit("planner", "demo.workorder", wo)
        self.stage("plan", "end")
        return wo

    def work_order(self, dx, outs, lessons, forbid_mem) -> dict:
        outs = json.loads(json.dumps(outs, default=str, ensure_ascii=False).replace("\u2014", " - ").replace("\u2013", "-"))
        asset = dx["asset"]
        n = asset.split("-")[-1]
        safety = outs.get("safety") or {}
        repair = outs.get("repair") or {}
        acc = outs.get("acceptance") or {}
        wo_type = repair.get("work_order")
        expected = B.expected_work_order(f"{dx['fault']} {dx['summary']}")
        self.override = None
        if wo_type not in WORK_ORDERS or wo_type == "inspect" or (expected and wo_type != expected):
            # the planner checks its sub-agent against the diagnosis instead of trusting it
            self.override = f"repair sub-agent chose {wo_type!r}, which does not fix {dx['fault']!r}; planner set {expected or 'inspect'}"
            wo_type = expected or "inspect"
            steps_repair = [f"{wo_type.replace('_', ' ')} on {asset}"]
        else:
            steps_repair = None
        brk = BREAKER_FOR.get(asset, f"52-I{n}")
        before = [str(s) for s in (safety.get("before") or [f"Open feeder breaker {brk} and apply lockout/tagout"])]
        after = [str(s) for s in (safety.get("after") or [f"Close {brk} after the work is complete and confirm the unit restarts"])]
        steps = before + (steps_repair or [str(s) for s in (repair.get("steps") or [wo_type.replace("_", " ")])]) + after
        forbid = list(dict.fromkeys([str(s) for s in (repair.get("do_not") or [])] + forbid_mem))
        acceptance = [str(s) for s in (acc.get("acceptance") or [])] or [
            f"{asset} SCADA data_age_min under {FRESH_MIN}", f"{asset} display lit in a camera view the verifier picks"]
        return {"wo_id": "WO-1", "asset": asset, "work_order": wo_type, "steps": steps, "forbid": forbid,
                "acceptance": acceptance, "lessons_applied": [l["id"] for l in lessons], "round": 1}

    def verify(self, wo, rnd, contractor_vp, claim: str = "") -> dict:
        self.stage("verify", "start", rnd)
        asset, checks = wo["asset"], []
        tel = self.tb.call("read_telemetry", {"asset": asset, "points": POINTS, "window_min": 30})
        age, latest = _age(tel), _latest(tel)
        t_ok = age is not None and age <= FRESH_MIN and str(latest.get("status", "")).lower() == "running"
        checks.append({"kind": "telemetry", "name": "scada_fresh_and_running", "pass": t_ok,
                       "detail": f"data_age_min {age}, status {latest.get('status')}, ac_kw {latest.get('ac_kw')}",
                       "evidence": {"data_age_min": age, "latest": latest, "last_update_ts": tel.get("last_update_ts")}})
        # the verifier picks its own camera: one the contractor's photo did not come from
        vps = self.vps_for(asset)
        vp = next((v for v in vps if v != contractor_vp), vps[0] if vps else None)
        visual = []
        if vp:
            shot = self.photo(vp, "verifier", rnd, asset=asset)
            res = self.eyes.check(shot["image"], asset, HEALTHY_CLAIM) if shot["image"] else {"pass": False, "confidence": 0, "evidence": shot["error"]}
            v_ok = bool(res.get("pass")) and float(res.get("confidence") or 0) >= VISUAL_CONF
            self.emit("verifier", "demo.camera", {"viewpoint": vp, "asset": asset, "mode": "rgb", "image": shot["image"],
                                                  "description": res.get("evidence", ""), "round": rnd})
            checks.append({"kind": "visual", "name": f"display_lit@{vp}", "pass": v_ok,
                           "detail": f"{res.get('evidence', '')} (confidence {res.get('confidence')})"[:400],
                           "evidence": {"image": shot["image"], "viewpoint": vp, "model": res.get("model")}})
            visual.append({"viewpoint": vp, "claim": HEALTHY_CLAIM, "pass": v_ok, "confidence": res.get("confidence")})
            self._last_check = res
            self._last_image = shot["image"]
        state, decision = jev_step(_registration(TWIN_SCENE), asset, tel, visual, log=self.log, stub=self.stub)
        self.emit("verifier", "demo.jev", {"wo_id": wo["wo_id"], "round": rnd, "input": state, "output": decision})
        checks.append({"kind": "jev", "name": "jev_fix_verified", "pass": decision.get("verdict") == "verified",
                       "detail": f"{decision.get('verdict')} p={decision.get('p_verified')} via {decision.get('backend')}: "
                                 f"{decision.get('reason', '')}"[:400],
                       "evidence": {"backend": decision.get("backend")}})
        meta = None
        try:  # the verification team: three deterministic judges plus a different-family meta judge, one level deep
            ping = self.tb.call("run_diagnostic", {"asset": asset, "test": "comms_ping"})
            team = R.verify_team(wo, rnd, {"telemetry": {**tel, "data_age_min": age, "latest": latest},
                                           "plant_readings": {"comms_ping": ping, "scada_latest": latest, "data_age_min": age},
                                           "visual": [{"viewpoint": v["viewpoint"], "image": getattr(self, "_last_image", None),
                                                       "check": self._last_check} for v in visual],
                                           "jev": {"input": state, "output": decision}, "contractor_claim": claim},
                                 getattr(self, "lessons", []), "stub" if self.stub else "live", self.on_agent, log=self.log)
            checks, meta = team["checks"], team["meta"]
            if team.get("failed") and "meta_judge" in team["failed"]:
                checks = checks + [{"kind": "meta", "name": "meta_judge", "pass": False, "detail": meta.get("reason", ""),
                                    "evidence": {"model": meta.get("model")}}]
        except Exception as e:  # noqa: BLE001 - the flat checks above decide
            meta = {"verdict": "abstain", "reason": f"verification team failed ({type(e).__name__}); flat checks decide"}
        for c in checks:
            self.emit("verifier", "demo.check", {"wo_id": wo["wo_id"], "round": rnd, **c})
        failed = [c for c in checks if not c["pass"]]
        verdict = {"wo_id": wo["wo_id"], "round": rnd, "pass": not failed, "failed": [c["name"] for c in failed], "meta": meta}
        ev = self.emit("verifier", "demo.verdict", verdict)
        verdict["seq"] = ev.get("seq") if isinstance(ev, dict) else None
        verdict["checks"] = checks
        self.stage("verify", "end", rnd)
        return verdict

    def redispatch(self, wo, rnd, verdict, visit) -> dict:
        self.stage("redispatch", "start", rnd)
        failed = [c for c in verdict["checks"] if not c["pass"]]
        new = dict(wo)
        new["round"] = rnd + 1
        if visit.get("shortcut") and SHORTCUT_FORBID not in new["forbid"]:
            new["forbid"] = new["forbid"] + [SHORTCUT_FORBID]
        elif not visit.get("shortcut"):
            # the contractor did exactly what we asked and the plant is still broken: our work order was wrong
            alt = B.expected_work_order(f"{self.dx['fault']} {self.dx['summary']} {' '.join(self.dx['evidence'])}")
            tried = {wo["work_order"], *getattr(self, "tried", set())}
            self.tried = tried
            if alt and alt not in tried:
                new["work_order"] = alt
                new["steps"] = [s if wo["work_order"].replace("_", " ") not in s.lower() else f"{alt.replace('_', ' ')} on {wo['asset']}"
                                for s in wo["steps"]]
                if not any(alt.replace("_", " ") in s for s in new["steps"]):
                    new["steps"] = new["steps"][:1] + [f"{alt.replace('_', ' ')} on {wo['asset']}"] + new["steps"][1:]
                new["forbid"] = new["forbid"] + [f"Do not repeat {wo['work_order']}; it did not fix the fault."]
                self.emit("planner", "demo.agent", {"id": f"planner.replan.r{rnd}", "parent": None, "depth": 0, "role": "planner",
                                                    "model": "rule", "task": "Replan after a faithful visit failed verification",
                                                    "status": "done", "team": "plan", "round": rnd,
                                                    "output": {"from": wo["work_order"], "to": alt}})
        src = getattr(self, "_last_image", None)
        prompt = f"the same {wo['asset']} inverter cabinet, repaired: its display lit and showing live readings, photo-realistic"
        img = expected_image(wo["asset"], src, prompt, self.run_dir / "img" / f"expected-r{rnd}.png")
        self.emit("verifier", "demo.diffusion", {"wo_id": wo["wo_id"], "round": rnd, **img})
        try:
            body = self.brains.email_back(new, [{k: c[k] for k in ("name", "detail")} for c in failed])
        except Exception as e:  # noqa: BLE001
            body = ""
        if not body:
            body = "Our checks against the plant failed:\n" + "\n".join(f"- {c['name']}: {c['detail']}" for c in failed)
        body += ("\n\nRequired now:\n" + "\n".join(f"  {i}. {s}" for i, s in enumerate(new["steps"], 1))
                 + "\n\nDo not:\n" + "\n".join(f"  - {s}" for s in new["forbid"])
                 + "\n\nAttached: what the unit must look like when it is fixed, and our failing photo.")
        atts = [{"name": "expected-state" + Path(img["image"]).suffix, "path": img["image"]}] if img.get("image") else []
        if src:
            atts.append({"name": "verifier-photo.jpg", "path": src})
        prof = contractor_for(wo["asset"])
        self.emit("planner", "demo.workorder", new)  # the revised order first, then the email that carries it
        mail = self.mail.write("out", wo["wo_id"], rnd + 1, OPS_ADDR, f"{prof['name']} <{prof['addr']}>",
                               f"Re: [{wo['wo_id']}] not verified, please return ({', '.join(verdict['failed'])})", body, atts)
        self.emit("verifier", "demo.email", mail)
        self.stage("redispatch", "end", rnd)
        return new

    def learn(self, dx, wo, verdicts, visits) -> dict | None:
        """Score the lessons this run applied, write the new lesson (verified runs only), show the flywheel."""
        self.stage("memory", "start")
        verified = bool(verdicts and verdicts[-1]["pass"])
        shortcut_failed = any(v.get("shortcut") for v in visits)
        saved = None
        if verified:
            saved = self.memory.learn(self.run_id, dx, wo, verdicts, visits)
            effect = ("next time the first work order forbids the shortcut, so the first visit is the real fix"
                      if shortcut_failed else "confirmed: the recalled lesson made the first visit pass")
            self.emit("memory", "demo.memory", {"op": "write", "lessons": [{"id": saved["id"], "text": M.summary(saved)}],
                                                "effect": effect})
        scored = self.memory.record_outcome(getattr(self, "applied", []), self.run_id, len(verdicts), verified,
                                            contractor_for(wo["asset"])["name"], visits)
        if scored:
            self.emit("memory", "demo.memory", {"op": "score", "lessons": [
                {"id": x["id"], "text": f"score {x['score']} ({'+' if x['delta'] >= 0 else ''}{x['delta']})"
                 + (", flagged" if x["flagged"] else "")} for x in scored],
                "effect": "lessons that led to a first-round pass gain weight; lessons that failed are flagged"})
        self.emit("memory", "demo.memory", self.memory.stats_event())
        self.stage("memory", "end")
        return saved

    def run(self, goal=None) -> dict:
        goal = goal or self.sc["goal"]
        self.emit("operator", "goal", {"text": goal, "scenario_id": self.sc["id"]})
        dx = self.diagnose()
        self.dx = dx
        wo = self.plan(dx)
        contractor = Contractor(self.tb, wo["asset"])
        prof = contractor.profile
        self.stage("dispatch", "start", 1)
        subj, body = work_order_email(wo, prof)
        self.emit("planner", "demo.email", self.mail.write("out", wo["wo_id"], 1, OPS_ADDR, f"{prof['name']} <{prof['addr']}>", subj, body))
        self.stage("dispatch", "end", 1)
        verdicts, visits = [], []
        for rnd in range(1, self.max_rounds + 1):
            self.stage("contractor", "start", rnd)
            visit = contractor.visit(wo, rnd)
            visits.append(visit)
            self.emit("contractor", "demo.contractor", {"wo_id": wo["wo_id"], "round": rnd, "actions": visit["actions"],
                                                        "shortcut": visit["shortcut"], "note": visit["note"]})
            atts = [{"name": "after-repair.jpg", "path": visit["photo"]}] if visit.get("photo") else []
            self.emit("contractor", "demo.email", self.mail.write(
                "in", wo["wo_id"], rnd, f"{prof['tech']} <{prof['addr']}>", OPS_ADDR, f"Re: [{wo['wo_id']}] done", visit["reply"], atts))
            self.stage("contractor", "end", rnd)
            contractor_vp = next((a["args"].get("viewpoint") for a in visit["actions"] if a["tool"] == "get_camera_view"), None)
            v = self.verify(wo, rnd, contractor_vp, visit.get("reply", ""))
            verdicts.append(v)
            if v["pass"]:
                break
            if rnd < self.max_rounds:
                wo = self.redispatch(wo, rnd, v, visit)
        verified = bool(verdicts and verdicts[-1]["pass"])
        lesson = self.learn(dx, wo, verdicts, visits)
        self.stage("final", "start")
        status = "VERIFIED" if verified else "ESCALATED"
        if not verified:
            self.tb.call("page_human", {"severity": "warning", "brief": f"{wo['asset']}: {wo['work_order']} not verified after {len(verdicts)} rounds; failed {verdicts[-1]['failed']}"})
        metrics = episode.finish(self.sim, self.raw_log, {"status": "DONE" if verified else "ESCALATED",
                                                          "summary": f"{status} in {len(verdicts)} round(s)"})
        usage = llm.USAGE.snapshot().get("total", {})
        final = {"status": status, "rounds": len(verdicts),
                 "summary": (f"{wo['asset']} {wo['work_order'].replace('_', ' ')} verified against the plant in round {len(verdicts)}"
                             if verified else f"not verified after {len(verdicts)} rounds; a human was paged"),
                 "usd": round(float(usage.get("usd", 0) or 0), 4), "wall_s": round(time.time() - self.t0, 1),
                 "judge": {k: metrics.get(k) for k in ("success", "false_done", "safety_violations", "claimed_done", "resolved") if k in metrics},
                 "lesson": lesson["id"] if lesson else None, "stub": self.stub}
        self.emit("orchestrator", "demo.final", final)
        self.stage("final", "end")
        (self.run_dir / "result.json").write_text(json.dumps({"run_id": self.run_id, **final, "metrics": metrics}, indent=2, default=str))
        return {"run_id": self.run_id, **final}


def run_demo(scenario=SCENARIO, memory_dir=None, stub=False, run_id=None, max_rounds=MAX_ROUNDS, goal=None, runs_dir=None) -> dict:
    return Demo(scenario, memory_dir, stub, run_id, max_rounds, runs_dir).run(goal)
