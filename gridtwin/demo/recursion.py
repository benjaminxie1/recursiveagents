"""One layer of recursive sub-agents for diagnosis and for verification.

diagnose_team: a diagnosis lead (depth 0, Nemotron Super) spawns "sensors", "vision", "spatial" and, when memory has
lessons, "history" at depth 1, runs them in parallel and merges their findings. The merge is an LLM call with a
deterministic fallback (the stale sensor wins, else a confidence-weighted vote).

verify_team: a verification lead (depth 0) spawns three deterministic judges (telemetry, visual, jev) and one LLM
meta judge from a different model family than the planner. The rule that decides the verdict lives here, in code:
the meta judge can veto a fix but can never pass one that a deterministic judge failed (done means done), and its veto
counts only when it names a ground from veto_grounds: a failed judge, or a plant reading code found to contradict the fix.
A veto on anything else (a numeric target a sub-agent wrote into the work order) is overruled.

The depth cap is brains.Agent.spawn's (MAX_DEPTH = 1): leads spawn, sub-agents and judges cannot.
brains_mode "stub" gives the same shapes with no network, for tests and offline rehearsal.
on_agent(agent, status, output) is called with status "spawned", "done" or "failed" for every agent, leads included;
agent_event(agent, status, output) turns that into the demo.agent payload (with team and round) and actor_for(agent)
names the actor to emit it as.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from gridtwin.agents.common import tel_latest
from gridtwin.config import ROLES
from gridtwin.demo.brains import Agent, DepthExceeded, _ask

FRESH_MIN = 5         # acceptance: SCADA must be at most this many minutes old (same bar as the pipeline)
STALE_MIN = 10        # diagnosis: SCADA older than this is stale (same bar as the pipeline)
VISUAL_CONF = 0.7     # the visual judge's bar on the Omni check's confidence
META_ROLE = "verifier"  # gpt-oss-120b; Crusoe served no Meta Llama model when this was written (GET /v1/models, 2026-10-17)
RULE = "deterministic"  # model label for agents that are code, not a model call
_DARK = re.compile(r"\b(dark|blank|off|unlit|no display|not lit|black screen|no readings)\b", re.I)
_LIT = re.compile(r"\b(lit|illuminated|showing|readings|glowing|bright|on)\b", re.I)


_PLAIN = str.maketrans({"\u202f": " ", "\u00a0": " ", "\u2009": " ", "\u2011": "-", "\u2013": "-", "\u2014": "-"})


def _plain(v, limit: int = 400) -> str:
    """Model text for the page: plain spaces and hyphens (gpt-oss likes narrow no-break spaces and long dashes)."""
    return str(v or "").translate(_PLAIN)[:limit]


# ---------- agent plumbing ----------

def _lead(id: str, role: str, model_role: str, task: str, team: str, round_no: int) -> Agent:
    lead = Agent(id, role, model_role, 0, task=task)
    lead.team, lead.round = team, round_no
    return lead


def _spawn(lead: Agent, role: str, task: str, model_role: str) -> Agent:
    kid = lead.spawn(role, task, model_role)
    kid.team, kid.round = lead.team, lead.round
    return kid


def agent_event(agent: Agent, status: str, output=None) -> dict:
    """The demo.agent payload for one agent: brains.Agent.event plus the team and round from CONTRACT.md."""
    return {**agent.event(status, output), "team": getattr(agent, "team", None), "round": getattr(agent, "round", 0)}


def actor_for(agent: Agent) -> str:
    """Event actor: the lead speaks as the diagnoser or the verifier, a sub-agent as subagent:<role>."""
    if agent.depth >= 1:
        return f"subagent:{agent.role}"
    return "diagnoser" if getattr(agent, "team", None) == "diagnose" else "verifier"


def _depth_cap_holds(lead: Agent) -> bool:
    """Probe the cap on a real child: a depth-1 agent must not be able to spawn."""
    if not lead.children:
        return False
    try:
        lead.children[0].spawn("probe", "must fail")
    except DepthExceeded:
        return True
    lead.children[0].children.clear()
    return False


def _run(kids: list[tuple[Agent, callable]], on_agent) -> dict:
    """Run sub-agents in parallel; each fn() returns its output. A failure is reported and maps to None."""
    for k, _ in kids:
        on_agent(k, "spawned", None)

    def one(pair):
        k, fn = pair
        try:
            out = fn()
        except Exception as e:  # noqa: BLE001 - a failed sub-agent is reported, the lead falls back
            on_agent(k, "failed", {"error": f"{type(e).__name__}: {e}"[:300]})
            return k.role, None
        on_agent(k, "done", out)
        return k.role, out

    if not kids:
        return {}
    with ThreadPoolExecutor(max_workers=len(kids)) as ex:
        return dict(ex.map(one, kids))


def _is_stub(brains_mode) -> bool:
    if isinstance(brains_mode, str):
        return brains_mode.strip().lower() == "stub"
    return bool(getattr(brains_mode, "stub", brains_mode))


def _conf(v, default=0.0) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default


def _finding(raw: dict, known: set[str], role: str) -> dict:
    """Normalise a sub-agent's JSON to {suspect, confidence, finding, evidence[]}; an unknown asset abstains."""
    suspect = raw.get("suspect") or raw.get("asset")
    suspect = suspect if suspect in known else None
    return {"suspect": suspect, "confidence": _conf(raw.get("confidence")) if suspect else 0.0,
            "finding": _plain(raw.get("finding") or raw.get("summary")),
            "evidence": [_plain(x, 200) for x in (raw.get("evidence") or [])][:5], "by": role}


# ---------- diagnosis team ----------

SENSORS_SYS = """You are the sensors sub-agent of a solar plant's diagnosis team. You read only the SCADA feed and field diagnostics.
data_age_min is minutes since SCADA last heard from the device; more than 10 means the values are frozen, however healthy they look.
A failed comms_ping confirms a communications fault. Name the one asset whose data you trust least.
Reply with JSON only: {"suspect": "INV-n", "confidence": 0..1, "finding": "<1 sentence>", "evidence": ["<fact with numbers>"]}"""

VISION_SYS = """You are the vision sub-agent of a solar plant's diagnosis team. You read only camera descriptions written by a vision model.
A healthy inverter has a lit display showing readings; a dark or blank display on a unit that SCADA calls healthy is the anomaly.
Reply with JSON only: {"suspect": "INV-n" or null, "confidence": 0..1, "finding": "<1 sentence>", "evidence": ["<viewpoint: what is seen>"]}"""

HISTORY_SYS = """You are the history sub-agent of a solar plant's diagnosis team. You read verified lessons from past incidents and the
current symptoms. Say which asset the current symptoms match a past lesson for, and which fix worked; null if no lesson matches.
Reply with JSON only: {"suspect": "INV-n" or null, "confidence": 0..1, "finding": "<1 sentence>", "evidence": ["<lesson id: why it matches>"]}"""

MERGE_SYS = """You are the diagnosis lead of an agent team that runs a 20 MW solar plant (inverters INV-1..INV-4).
Your sub-agents each read one source (SCADA sensors, camera views, the Gaussian-splat twin's camera stations, past lessons) and named a suspect.
Merge them into one diagnosis. Telemetry that looks perfect but has not updated is a lie, not a healthy unit, so a stale sensor outweighs
a missing camera view. Reply with JSON only:
{"asset": "INV-n", "fault": "<short name>", "confidence": 0..1, "summary": "<2 sentences>", "evidence": ["<fact with numbers>", ...]}"""


def _assets(evidence: dict) -> set[str]:
    out = {s.get("asset") for s in evidence.get("sensors") or []}
    out |= {c.get("asset") for c in evidence.get("camera") or []}
    out |= set((evidence.get("diagnostics") or {}).keys())
    return {a for a in out if isinstance(a, str) and a}


def _ping_failed(result) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("ok") is False or result.get("error"):
        return True
    txt = json.dumps(result, default=str).lower()
    return any(w in txt for w in ("timeout", "no response", "unreachable", "fail"))


def sensors_rule(evidence: dict) -> dict:
    """Deterministic read of the SCADA feed: the stalest stale sensor, more confident when its ping also failed."""
    sensors = [s for s in evidence.get("sensors") or [] if s.get("asset")]
    diags = evidence.get("diagnostics") or {}
    stale = sorted((s for s in sensors if s.get("stale") or (s.get("data_age_min") or 0) > STALE_MIN),
                   key=lambda s: -(s.get("data_age_min") or 1e9))
    if not stale:
        return {"suspect": None, "confidence": 0.0, "finding": "every SCADA feed is fresh", "evidence": [], "by": "sensors"}
    s = stale[0]
    ping = _ping_failed(diags.get(s["asset"]))
    ev = [f"{s['asset']} data_age_min {s.get('data_age_min')} (stale over {STALE_MIN})",
          f"{s['asset']} latest {json.dumps(s.get('latest') or {}, default=str)[:120]}"]
    if s["asset"] in diags:
        ev.append(f"{s['asset']} comms_ping {'failed' if ping else 'answered'}")
    return {"suspect": s["asset"], "confidence": 0.95 if ping else 0.85,
            "finding": f"{s['asset']} SCADA has not updated for {s.get('data_age_min')} min while showing normal values",
            "evidence": ev, "by": "sensors"}


def vision_rule(evidence: dict) -> dict:
    """Deterministic read of the camera descriptions: a dark display is the anomaly."""
    dark, lit = [], []
    for c in evidence.get("camera") or []:
        d = str(c.get("description") or "")
        if _DARK.search(d):
            dark.append(c)
        elif _LIT.search(d):
            lit.append(c)
    if not dark:
        return {"suspect": None, "confidence": 0.0, "finding": "no camera view shows a dark display", "by": "vision",
                "evidence": [f"{c.get('viewpoint')}: {str(c.get('description'))[:80]}" for c in lit][:3]}
    c = dark[0]
    return {"suspect": c.get("asset"), "confidence": 0.8, "finding": f"{c.get('asset')} display is dark in {c.get('viewpoint')}",
            "evidence": [f"{c.get('viewpoint')}: {str(c.get('description'))[:120]}"], "by": "vision"}


def spatial_rule(evidence: dict, suspects: list[str]) -> dict:
    """Which registered camera stations see which asset (splat twin registration if given, else the camera list).
    The spatial agent does not vote; it tells the verifier which independent views it can use."""
    seen: dict[str, list[str]] = defaultdict(list)
    twin = evidence.get("splat_twin")
    views = twin.get("views") if isinstance(twin, dict) else None
    stations = twin.get("stations") if isinstance(twin, dict) else None
    if isinstance(stations, list):
        for st in stations:
            if st.get("sees_asset", True) and st.get("asset"):
                seen[st["asset"]].append(str(st.get("viewpoint")))
    elif isinstance(views, dict):
        for vp, v in views.items():
            if isinstance(v, dict) and v.get("asset"):
                seen[v["asset"]].append(vp)
    for c in evidence.get("camera") or []:
        if c.get("asset") and c.get("viewpoint") and c["viewpoint"] not in seen[c["asset"]]:
            seen[c["asset"]].append(c["viewpoint"])
    ev = [f"{a} seen from {', '.join(vps)}" for a, vps in sorted(seen.items())]
    cov = {a: len(seen.get(a, [])) for a in suspects}
    return {"suspect": None, "confidence": 0.0, "by": "spatial", "coverage": dict(seen),
            "finding": "; ".join(f"{a}: {n} registered view(s)" for a, n in cov.items()) or "no registered views",
            "evidence": ev[:5]}


def history_rule(lessons: list[dict], stale_assets: list[str]) -> dict:
    for l in lessons:
        txt = json.dumps(l, default=str)
        for a in stale_assets:
            if a in txt:
                return {"suspect": a, "confidence": 0.6, "by": "history", "finding": f"lesson {l.get('id')} matches {a}: {l.get('fix')}",
                        "evidence": [f"{l.get('id')}: {str(l.get('fix'))[:100]}"]}
    return {"suspect": None, "confidence": 0.0, "finding": "no past lesson matches", "evidence": [], "by": "history"}


def merge_rule(evidence: dict, findings: dict) -> dict:
    """Deterministic merge: the stale sensor wins; else a confidence-weighted vote of the sub-agents."""
    known = _assets(evidence)
    sens = sensors_rule(evidence)
    votes: dict[str, float] = defaultdict(float)
    for f in findings.values():
        if f and f.get("suspect") in known:
            votes[f["suspect"]] += _conf(f.get("confidence"))
    if sens["suspect"]:
        asset, why = sens["suspect"], "stale SCADA outranks every other source"
    elif votes:
        asset, why = max(sorted(votes), key=lambda a: votes[a]), "confidence-weighted vote of the sub-agents"
    else:
        asset, why = (sorted(known) or ["INV-1"])[0], "no sub-agent named a suspect"
    agree = [r for r, f in findings.items() if f and f.get("suspect") == asset]
    ev = [e for r in ("sensors", "vision", "history", "spatial") for e in ((findings.get(r) or {}).get("evidence") or [])]
    stale = bool(sens["suspect"])
    return {"asset": asset,
            "fault": "comms card failure (stale SCADA)" if stale else "unknown fault",
            "confidence": round(min(0.95, 0.55 + 0.15 * len(agree)), 2) if stale else round(min(0.8, 0.4 + 0.1 * len(agree)), 2),
            "summary": f"{asset} is the suspect ({why}; agreeing: {', '.join(agree) or 'none'}). "
                       + (f"{sens['finding']}." if stale else "No SCADA feed is stale."),
            "evidence": ev[:6] or sens["evidence"]}


def diagnose_team(evidence: dict, lessons: list[dict] | None, brains_mode, on_agent, log=None) -> dict:
    """Diagnosis by a lead and its depth-1 sub-agents. Returns the CONTRACT diagnosis keys plus findings and merge."""
    lessons = lessons or []
    stub = _is_stub(brains_mode)
    known = _assets(evidence)
    sens_view = {"sensors": evidence.get("sensors"), "diagnostics": evidence.get("diagnostics")}
    cam_view = {"camera": evidence.get("camera")}
    stale_assets = [s["asset"] for s in evidence.get("sensors") or [] if s.get("stale")]
    lead = _lead("diagnosis-lead", "diagnosis_lead", "planner", "Find the faulty asset and the fault from four independent reads",
                 "diagnose", 0)
    on_agent(lead, "spawned", None)

    def llm_or_rule(role, system, payload, rule):
        if stub:
            return lambda: rule()
        return lambda: _finding(_ask("worker", system, json.dumps(payload, default=str), log, f"subagent:{role}",
                                     max_tokens=2048), known, role)

    kids = [
        (_spawn(lead, "sensors", "Read the SCADA feed: which asset's values are frozen (data_age_min) or failed comms_ping", "worker"),
         llm_or_rule("sensors", SENSORS_SYS, sens_view, lambda: sensors_rule(evidence))),
        (_spawn(lead, "vision", "Read the Omni camera descriptions: whose display is dark", "worker"),
         llm_or_rule("vision", VISION_SYS, cam_view, lambda: vision_rule(evidence))),
        (_spawn(lead, "spatial", "Map the splat twin's registered camera stations to the assets they see", RULE),
         lambda: spatial_rule(evidence, sorted(set(stale_assets) | {c.get("asset") for c in evidence.get("camera") or []} - {None}))),
    ]
    if lessons:
        brief = [{k: l.get(k) for k in ("id", "symptoms", "fix", "forbid") if l.get(k)} for l in lessons]
        kids.append((_spawn(lead, "history", f"Match the symptoms against {len(lessons)} verified lesson(s)", "worker"),
                     llm_or_rule("history", HISTORY_SYS, {"lessons": brief, "current": sens_view},
                                 lambda: history_rule(lessons, stale_assets))))
    findings = _run(kids, on_agent)
    capped = _depth_cap_holds(lead)

    fallback = merge_rule(evidence, findings)
    merged, how = fallback, "rule"
    if not stub:
        try:
            raw = _ask("planner", MERGE_SYS, json.dumps({"findings": findings, "sensors": evidence.get("sensors"),
                                                         "diagnostics": evidence.get("diagnostics")}, default=str),
                       log, "diagnoser", max_tokens=4096)
            if raw.get("asset") in known:
                merged = {"asset": raw["asset"], "fault": _plain(raw.get("fault"), 80) or fallback["fault"],
                          "confidence": _conf(raw.get("confidence"), fallback["confidence"]),
                          "summary": _plain(raw.get("summary"), 600) or fallback["summary"],
                          "evidence": [_plain(x, 200) for x in (raw.get("evidence") or [])][:6] or fallback["evidence"]}
                how = "llm"
            else:
                how = f"rule (lead named {raw.get('asset')!r}, not a known asset)"
        except Exception as e:  # noqa: BLE001 - the deterministic merge stands in
            how = f"rule (lead call failed: {type(e).__name__})"
    dx = {**merged, "findings": findings, "merge": how,
          "model": "stub" if stub else (ROLES["planner"] if how == "llm" else RULE)}
    on_agent(lead, "done", {"asset": dx["asset"], "fault": dx["fault"], "confidence": dx["confidence"], "merge": how,
                            "votes": {r: (f or {}).get("suspect") for r, f in findings.items()}, "depth_cap_enforced": capped})
    return dx


# ---------- verification team ----------

META_SYS = """You are the meta judge of a solar plant's verification team, from a different model family than the planner.
Three deterministic judges checked the plant itself: SCADA telemetry, a camera view read by a vision model, and the JEV spatial verdict.
plant_readings holds the other readings the verifier took from the plant (comms_ping, the latest SCADA values).
The contractor's claim is not evidence: only the plant's own readings and images count.
The bar for done is fixed and the judges already apply it: SCADA data_age_min at most 5 with status running, the display lit
in a camera view (confidence 0.7 or more), and JEV verified. Numeric targets (ping latency, temperature, power ranges) are not part
of the bar: normal plant variation in them never fails a fix.
veto_grounds lists, by key, every failed judge and every plant reading that code found to contradict the fix.
You may say "fail" only by naming one of those keys as "grounds"; with no grounds, say "pass". A veto that names anything else is overruled.
Reply with JSON only: {"verdict": "pass" | "fail", "grounds": "<a veto_grounds key>" or null, "reason": "<1-2 sentences citing the numbers>"}"""

GROUNDED = "grounded"  # meta["veto"] value for a veto code accepted


def _num(v):
    try:
        return None if v is None or isinstance(v, bool) else float(v)
    except (TypeError, ValueError):
        return None


def veto_grounds(evidence: dict, checks: list[dict], asset: str | None = None) -> dict[str, str]:
    """The only things the meta judge may veto on, found in code: a failed judge, or a plant reading that contradicts the fix.
    The catalogue is closed on purpose: a numeric target a sub-agent wrote into the work order is never a ground."""
    grounds = {c["name"]: f"judge failed: {c['detail']}"[:200] for c in checks if not c["pass"]}
    pr = evidence.get("plant_readings") if isinstance(evidence.get("plant_readings"), dict) else {}
    tel = evidence.get("telemetry") if isinstance(evidence.get("telemetry"), dict) else {}
    ping = pr.get("comms_ping")
    if isinstance(ping, dict) and (ping.get("reachable") is False or ping.get("ok") is False or ping.get("error")):
        grounds["comms_ping_unreachable"] = f"comms_ping: {ping.get('error') or 'unreachable'}"
    latest = pr.get("scada_latest") if isinstance(pr.get("scada_latest"), dict) else (tel.get("latest") or {})
    status = latest.get("status")
    if status is not None and str(status).lower() != "running":
        grounds["scada_status_not_running"] = f"SCADA status {status}"
    ac = _num(latest.get("ac_kw"))
    if ac is not None and ac <= 0 and str(status).lower() == "running":
        grounds["running_with_no_output"] = f"SCADA status running but ac_kw {ac:g}"
    age = _num(pr.get("data_age_min", tel.get("data_age_min")))
    if age is None or age > FRESH_MIN:
        grounds["scada_stale"] = f"data_age_min {age} (bar {FRESH_MIN})"
    for a in pr.get("alarms") or []:
        if isinstance(a, dict) and a.get("active", True) and a.get("asset") in (None, asset):
            grounds["active_alarm"] = f"active alarm {a.get('code') or a.get('name') or a}"[:200]
            break
    return grounds


def telemetry_judge(tel: dict) -> dict:
    tel = tel or {}
    age = tel.get("data_age_min")
    latest = tel.get("latest") if isinstance(tel.get("latest"), dict) else {}
    status = latest.get("status")
    if status is None:
        try:
            v = tel_latest(tel, "status")
            status = v[1] if isinstance(v, (list, tuple)) and len(v) == 2 else v
        except Exception:  # noqa: BLE001
            status = None
    try:
        fresh = age is not None and float(age) <= FRESH_MIN
    except (TypeError, ValueError):
        fresh = False
    running = str(status or "").lower() == "running"
    return {"kind": "telemetry", "name": "scada_fresh_and_running", "pass": fresh and running,
            "detail": f"data_age_min {age} ({'fresh' if fresh else f'stale, over {FRESH_MIN}'}), status {status}, ac_kw {latest.get('ac_kw')}",
            "evidence": {"data_age_min": age, "latest": latest, "last_update_ts": tel.get("last_update_ts")}}


def visual_judge(visual: list[dict]) -> dict:
    views = [v for v in visual or [] if isinstance(v, dict)]
    if not views:
        return {"kind": "visual", "name": "display_lit", "pass": False, "detail": "no camera view of the asset", "evidence": {}}
    rows = []
    for v in views:
        chk = v.get("check") or {}
        conf = _conf(chk.get("confidence"))
        rows.append((v, chk, conf, bool(chk.get("pass")) and conf >= VISUAL_CONF))
    ok = all(r[3] for r in rows)
    v0, chk0, conf0, _ = next((r for r in rows if not r[3]), rows[0])
    vps = ",".join(str(r[0].get("viewpoint")) for r in rows)
    return {"kind": "visual", "name": f"display_lit@{vps}", "pass": ok,
            "detail": f"{chk0.get('evidence', '')} (confidence {conf0}, bar {VISUAL_CONF})"[:400],
            "evidence": {"image": v0.get("image"), "viewpoint": v0.get("viewpoint"), "model": chk0.get("model"),
                         "views": [{"viewpoint": r[0].get("viewpoint"), "pass": r[3], "confidence": r[2]} for r in rows]}}


def jev_judge(jev: dict) -> dict:
    out = (jev or {}).get("output") or {}
    ok = out.get("verdict") == "verified"
    return {"kind": "jev", "name": "jev_fix_verified", "pass": ok,
            "detail": f"{out.get('verdict')} p={out.get('p_verified')} via {out.get('backend')}: {out.get('reason', '')}"[:400],
            "evidence": {"backend": out.get("backend"), "p_verified": out.get("p_verified")}}


def _meta_stub(checks: list[dict], grounds: dict[str, str]) -> dict:
    if grounds:
        g = next(iter(grounds))
        return {"verdict": "fail", "grounds": g, "reason": f"(stub) {g}: {grounds[g]}; the contractor's claim does not change that"}
    return {"verdict": "pass", "grounds": None, "reason": "(stub) all three plant checks pass and no plant reading contradicts the fix"}


def ground_meta(raw: dict, grounds: dict[str, str], model: str) -> dict:
    """The meta judge's answer under the rule: a "fail" is a veto only when it names a key of veto_grounds; else it is overruled."""
    verdict = str(raw.get("verdict", "")).strip().lower()
    named = raw.get("grounds")
    named = [str(g).strip() for g in (named if isinstance(named, list) else [named]) if g not in (None, "", "null")]
    reason = _plain(raw.get("reason"), 500) or "no reason given"
    meta = {"verdict": verdict, "grounds": None, "reason": reason, "model": model}
    if verdict == "pass":
        return meta
    if verdict == "fail":
        hit = next((g for g in named if g in grounds), None)
        if hit:
            return {**meta, "grounds": hit, "veto": GROUNDED, "ground_detail": grounds[hit]}
        return {**meta, "verdict": "overruled", "model_verdict": "fail", "claimed_grounds": named or None,
                "veto": f"overruled: named {', '.join(named) or 'no ground'}, not a failed judge or a contradicting plant reading"}
    return {**meta, "verdict": "abstain", "reason": f"unreadable verdict {raw.get('verdict')!r}; the plant checks decide. {reason}"}


def decide(checks: list[dict], meta: dict) -> bool:
    """Done means done: pass only if every deterministic judge passed and the meta judge did not veto (an overruled veto is no veto)."""
    return bool(checks) and all(c["pass"] for c in checks) and meta.get("verdict") != "fail"


def verify_team(wo: dict, round_no: int, evidence: dict, lessons: list[dict] | None, brains_mode, on_agent, log=None) -> dict:
    """Verification by a lead, three deterministic judges and one meta judge, all against evidence read from the plant."""
    stub = _is_stub(brains_mode)
    asset = (wo or {}).get("asset")
    claim = str(evidence.get("contractor_claim") or "")[:2000]
    lead = _lead(f"verify-lead.r{round_no}", "verify_lead", META_ROLE,
                 f"Prove or refute the fix of {asset} ({(wo or {}).get('work_order')}) from the plant, not the contractor's word",
                 "verify", round_no)
    on_agent(lead, "spawned", None)
    judges = [
        (_spawn(lead, "telemetry_judge", f"SCADA for {asset}: data_age_min at most {FRESH_MIN} and status running", RULE),
         lambda: telemetry_judge(evidence.get("telemetry"))),
        (_spawn(lead, "visual_judge", f"Omni camera check of {asset}: pass with confidence at least {VISUAL_CONF}", RULE),
         lambda: visual_judge(evidence.get("visual"))),
        (_spawn(lead, "jev_judge", f"JEV spatial verdict for {asset} must be 'verified'", RULE),
         lambda: jev_judge(evidence.get("jev"))),
    ]
    outs = _run(judges, on_agent)
    checks = []
    for (k, _), fallback in zip(judges, ("scada_fresh_and_running", "display_lit", "jev_fix_verified")):
        c = outs.get(k.role)
        checks.append(c or {"kind": k.role.replace("_judge", ""), "name": fallback, "pass": False,
                            "detail": f"{k.role} failed to run", "evidence": {}})

    meta_agent = _spawn(lead, "meta_judge", "Read the judges and the contractor's claim: does the evidence prove the fix?",
                        "stub" if stub else META_ROLE)
    on_agent(meta_agent, "spawned", None)
    grounds = veto_grounds(evidence, checks, asset)
    # The work order's acceptance items are not sent: sub-agents write numeric targets (ping latency, heatsink and power ranges)
    # the plant never promised, and a judge that reads them vetoes a fixed plant (runs e2e-tt3-1, e2e-wfa-1, e2e-s06-...-144420).
    user = json.dumps({"work_order": {k: (wo or {}).get(k) for k in ("wo_id", "asset", "work_order")},
                       "round": round_no, "judges": [{k: c[k] for k in ("kind", "name", "pass", "detail")} for c in checks],
                       "plant_readings": evidence.get("plant_readings") or {},
                       "veto_grounds": grounds,
                       "contractor_claim": claim,
                       "lessons": [{k: l.get(k) for k in ("id", "fix", "forbid") if l.get(k)} for l in lessons or []]},
                      default=str)
    try:
        raw = _meta_stub(checks, grounds) if stub else _ask(META_ROLE, META_SYS, user, log, "subagent:meta_judge", max_tokens=2048)
        meta = ground_meta(raw, grounds, meta_agent.model)
        on_agent(meta_agent, "done", meta)
    except Exception as e:  # noqa: BLE001 - the deterministic judges still decide; the meta judge only abstains
        meta = {"verdict": "abstain", "reason": f"meta judge call failed ({type(e).__name__}); the plant checks decide",
                "model": meta_agent.model}
        on_agent(meta_agent, "failed", {"error": f"{type(e).__name__}: {e}"[:300]})

    ok = decide(checks, meta)
    det_ok = all(c["pass"] for c in checks)
    if det_ok and not ok:
        rule = f"meta judge vetoed a fix the plant checks passed, on {meta.get('grounds')}: {meta.get('ground_detail')}"
    elif not det_ok and meta["verdict"] == "pass":
        rule = "meta judge said pass but a plant check failed; done means done, so the fix fails"
    elif meta["verdict"] == "overruled":
        rule = ("meta judge's veto overruled: it named no failed judge and no plant reading that contradicts the fix; "
                + ("the plant checks pass" if det_ok else "a plant check failed, so the fix fails anyway"))
    else:
        rule = "plant checks and meta judge agree" if meta["verdict"] != "abstain" else "plant checks decide; meta judge abstained"
    meta["rule"] = rule
    failed = [c["name"] for c in checks if not c["pass"]] + (["meta_judge"] if meta["verdict"] == "fail" and det_ok else [])
    capped = _depth_cap_holds(lead)
    on_agent(lead, "done", {"pass": ok, "failed": failed, "meta": meta["verdict"], "rule": rule, "depth_cap_enforced": capped})
    return {"pass": ok, "checks": checks, "meta": meta, "failed": failed}
