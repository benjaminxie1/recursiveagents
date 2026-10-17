"""Simulated contractors and their mail. Nothing is ever sent: each message is an .eml file under runs/<id>/mail/.

Contractor behaviour rule (documented in CONTRACT.md, deterministic so the demo is repeatable):
on a first visit the contractor takes the cheap shortcut (an inspect-only visit that "power-cycles" the unit)
unless the work order explicitly forbids it, which means a forbid item starting with SHORTCUT_FORBID_PREFIX: the sentence
the system writes after a shortcut was caught (redispatch, a recalled lesson, the contractor's history). Free text the
planner writes ("do not skip the power cycle") never counts, whatever words it uses. It always follows explicit `forbid` items and the listed steps.
All of its work goes through the sim Toolbox, so the safety gate sees every action and the plant changes
only through the sim.
"""
from __future__ import annotations

import mimetypes
import re
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from gridtwin.sim.camera import REPO
from gridtwin.sim.plant import BREAKER_FOR, CFG

OPS_ADDR = "GridTwin Ops <ops@gridtwin.example>"
CONTRACTORS = {
    "default": {"name": "Mesa Electrical Field Services", "addr": "dispatch@mesa-field.example", "tech": "Dana R."},
}
SHORTCUT_WORDS = ("inspect-only", "inspect only", "shortcut", "power-cycle", "power cycle", "restart")
SHORTCUT_FORBID_PREFIX = "Do not substitute an inspect-only visit or a power-cycle for the work order"
SHORTCUT_FORBID = SHORTCUT_FORBID_PREFIX + "; it does not restore a failed comms card."


def contractor_for(asset: str) -> dict:
    return CONTRACTORS["default"]


def rel(path) -> str:
    p = Path(path)
    p = p if p.is_absolute() else REPO / p
    try:
        return p.resolve().relative_to(REPO.resolve()).as_posix()
    except ValueError:
        return str(p)


class Mailbox:
    """Writes numbered .eml files for one run and returns the demo.email payload for each."""

    def __init__(self, run_dir: Path):
        self.dir = Path(run_dir) / "mail"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.n = len(list(self.dir.glob("*.eml")))
        self.thread_ids: dict[str, str] = {}

    def write(self, direction: str, wo_id: str, round_no: int, sender: str, to: str, subject: str, body: str,
              attachments: list[dict] | None = None) -> dict:
        self.n += 1
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = sender, to, subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain="gridtwin.example")
        msg["X-GridTwin-Work-Order"] = wo_id
        msg["X-GridTwin-Round"] = str(round_no)
        msg["X-GridTwin-Simulated"] = "true (never sent)"
        first = self.thread_ids.setdefault(wo_id, msg["Message-ID"])
        if first != msg["Message-ID"]:
            msg["In-Reply-To"] = first
            msg["References"] = first
        msg.set_content(body)
        att_out = []
        for a in attachments or []:
            p = Path(a["path"])
            p = p if p.is_absolute() else REPO / p
            if not p.exists():
                continue
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=a.get("name") or p.name)
            att_out.append({"name": a.get("name") or p.name, "path": rel(p)})
        slug = re.sub(r"[^a-z0-9]+", "-", wo_id.lower()).strip("-")
        path = self.dir / f"{self.n:02d}-{direction}-{slug}-r{round_no}.eml"
        path.write_bytes(bytes(msg))
        return {"direction": direction, "wo_id": wo_id, "round": round_no, "from": sender, "to": to,
                "subject": subject, "body": body, "eml": rel(path), "attachments": att_out}


def work_order_email(wo: dict, contractor: dict) -> tuple[str, str]:
    lines = [
        f"Hello {contractor['name']},",
        "",
        f"Please carry out work order {wo['wo_id']} on {wo['asset']} ({wo['work_order']}).",
        "",
        "Steps, in order:",
        *[f"  {i}. {s}" for i, s in enumerate(wo["steps"], 1)],
    ]
    if wo.get("forbid"):
        lines += ["", "Do not:", *[f"  - {s}" for s in wo["forbid"]]]
    lines += ["", "We will verify against the plant before closing this order:", *[f"  - {s}" for s in wo["acceptance"]]]
    if wo.get("lessons_applied"):
        lines += ["", f"(Includes lessons from verified past incidents: {', '.join(wo['lessons_applied'])}.)"]
    lines += ["", "Reply to this email when the work is done.", "", "GridTwin Ops (automated agent team)"]
    return f"[{wo['wo_id']}] {wo['asset']}: {wo['work_order'].replace('_', ' ')}", "\n".join(lines)


def forbids_shortcut(wo: dict) -> bool:
    """True only for the system's own shortcut sentence: keyword matching misread "Do not skip the power cycle" as a ban."""
    return any(str(s).strip().startswith(SHORTCUT_FORBID_PREFIX) for s in (wo.get("forbid") or []))


class Contractor:
    """A simulated field contractor working on the sim plant through the Toolbox."""

    def __init__(self, toolbox, asset: str):
        self.tb = toolbox
        self.asset = asset
        self.profile = contractor_for(asset)
        self.visits = 0

    def _call(self, actions: list, tool: str, args: dict) -> dict:
        res = self.tb.call(tool, args)
        ok = isinstance(res, dict) and "error" not in res and "needs_confirmation" not in res
        actions.append({"tool": tool, "args": args, "ok": ok,
                        **({} if ok else {"error": str(res.get("error") or res.get("needs_confirmation"))[:200]})})
        return res

    def visit(self, wo: dict, round_no: int) -> dict:
        """Do the work (or the shortcut) on the sim. Returns {actions, shortcut, note, reply, photo}."""
        self.visits += 1
        asset, actions = wo["asset"], []
        if self.visits == 1 and not forbids_shortcut(wo):
            self._call(actions, "dispatch_tech", {"asset": asset, "work_order": "inspect"})
            self._call(actions, "wait", {"minutes": CFG["work_orders"]["inspect"][0] + 1})
            note = "first visit and the work order did not forbid an inspect-only visit, so the contractor power-cycled the display and left"
            reply = (f"Hi, {self.profile['tech']} here. Went out to {asset}, inspected the cabinet and power-cycled the display. "
                     f"Unit is running. Marking {wo['wo_id']} done.")
            return {"actions": actions, "shortcut": True, "note": note, "reply": reply, "photo": None}

        brk = BREAKER_FOR.get(asset)
        minutes, _cost, needs_off = CFG["work_orders"].get(wo["work_order"], [30, 0, False])
        if needs_off and brk:
            self._call(actions, "operate", {"asset": brk, "action": "open"})
        self._call(actions, "dispatch_tech", {"asset": asset, "work_order": wo["work_order"]})
        self._call(actions, "wait", {"minutes": minutes + 1})
        if needs_off and brk:
            self._call(actions, "operate", {"asset": brk, "action": "close"})
        self._call(actions, "wait", {"minutes": 5})
        photo = self._evidence_photo(actions)
        done = all(a["ok"] for a in actions)
        did = "; ".join(f"{a['tool']} {' '.join(str(v) for v in a['args'].values())}" + ("" if a["ok"] else " (FAILED)")
                        for a in actions if a["tool"] not in ("wait", "get_camera_view"))
        reply = (f"Hi, {self.profile['tech']} here. {wo['wo_id']} on {asset}: "
                 + (f"{wo['work_order'].replace('_', ' ')} done under lockout ({did}). Unit back in service. Photo attached."
                    if done else f"could not finish ({did}). Please advise."))
        return {"actions": actions, "shortcut": False, "note": "followed the work order", "reply": reply, "photo": photo}

    def _evidence_photo(self, actions) -> str | None:
        """The contractor's own photo, from the first camera that sees the asset."""
        vps = [v for v, a in self._viewpoints().items() if a == self.asset]
        if not vps:
            return None
        res = self._call(actions, "get_camera_view", {"viewpoint": vps[0]})
        return res.get("image_path") if isinstance(res, dict) else None

    def _viewpoints(self) -> dict:
        cam = getattr(self.tb.sim, "camera", None)
        return {vp: info.get("asset") for vp, info in (getattr(cam, "viewpoints", {}) or {}).items()}
