"""Build the s06 demo data pack: what each modality showed while INV-3's comms card was dead, then fixed.

A scripted agent (no model calls, the same steps as bench's scripted_correct plus the camera and comms
evidence) works s06_stale_scada through the real toolbox, and every sim minute is recorded twice: as SCADA
reported it (what the dashboard and the agents see) and as it really was (hidden truth, never shown to agents).
This is simulated plant data from a scripted run, not the agent team's verified run (runs/demo/s06-verified on the
demo machine, not in git yet).

Run: python -m scripts.make_s06_pack [out_dir]   (default assets/demo/s06_pack)
"""
import csv
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from gridtwin.sim import episode
from gridtwin.sim.camera import REPO, Camera
from gridtwin.sim.plant import INVERTERS, TFMT, Plant

OUT = REPO / "assets" / "demo" / "s06_pack"
SCENARIO = "s06_stale_scada"
HOLD_MIN = 30  # the supervisor's hold window before DONE is accepted
SHOTS = (  # (result key, file name, caption), in the order _run takes them
    ("cam_inv3_before", "1_inv3_display_before_fix.jpg", "INV-3 display while its telemetry still looks perfect: dark."),
    ("cam_inv2_reference", "2_inv2_display_reference.jpg", "INV-2 display two minutes later, for comparison: lit."),
    ("cam_inv3_after", "3_inv3_after_reseat.jpg", "INV-3 after the reseat, as the sim served it: lit, but a different "
     "photo-bank image (other angle, low sun), so use photos 1 and 4 for a side-by-side."),
)
STAND_IN = " AI-generated stand-in; the numbers on its screen are not sim values."


def _record(minutes, sources):
    """Patch Plant.tick and Camera.source_for so the preroll and every photo pick are captured too."""
    tick, source_for = Plant.tick, Camera.source_for

    def rec_tick(sim):
        tick(sim)
        row = {"t": sim.now_str(), "poi_export_kw": sim.export_kw, "poa_wm2": sim.met["poa_wm2"]}
        for i in INVERTERS:
            row[f"{i}_scada_kw"] = sim.scada[i]["ac_kw"]
            row[f"{i}_scada_status"] = sim.scada[i]["status"]
            row[f"{i}_last_update"] = sim.last_update[i].strftime(TFMT)
            row[f"{i}_true_kw"] = sim.inv[i]["ac_kw"]
            row[f"{i}_true_status"] = sim.inv[i]["status"]
            row[f"{i}_comms_ok"] = sim.inv[i]["comms_ok"]
        minutes.append(row)

    def rec_source(cam, viewpoint, asset, state, count):
        src = source_for(cam, viewpoint, asset, state, count)
        sources.append({"viewpoint": viewpoint, "asset": asset, "state": state, "source": str(src.relative_to(REPO))})
        return src

    Plant.tick, Camera.source_for = rec_tick, rec_source
    return lambda: (setattr(Plant, "tick", tick), setattr(Camera, "source_for", source_for))


def _run(out):
    minutes, sources, marks = [], [], {}
    restore = _record(minutes, sources)
    try:
        sim, tb, log, sc = episode.start(SCENARIO, run_id="run", runs_dir=out)
        log.emit("operator", "goal", {"text": sc["goal"], "scenario_id": SCENARIO})
        r = {}
        r["requirements"] = tb.call("list_requirements", {"asset": "INV-3"})
        r["alarms_before"] = tb.call("list_alarms", {})
        r["inv3_before"] = tb.call("read_telemetry", {"asset": "INV-3", "points": ["ac_kw", "status"], "window_min": 120})
        r["poi_before"] = tb.call("read_telemetry", {"asset": "POI-1", "points": ["export_kw"], "window_min": 120})
        r["ping_inv3"] = tb.call("run_diagnostic", {"asset": "INV-3", "test": "comms_ping"})
        r["ping_inv2"] = tb.call("run_diagnostic", {"asset": "INV-2", "test": "comms_ping"})
        r["cam_inv3_before"] = tb.call("get_camera_view", {"viewpoint": "inv3_display", "mode": "rgb"})
        r["cam_inv2_reference"] = tb.call("get_camera_view", {"viewpoint": "inv2_display", "mode": "rgb"})
        marks["lockout"] = sim.now_str()  # bench._lockout_work's steps, with the close time kept
        tb.call("operate", {"asset": "52-I3", "action": "open"})
        r["work_order"] = tb.call("dispatch_tech", {"asset": "INV-3", "work_order": "reseat_comms_card"})
        tb.call("wait", {"minutes": 46})
        tb.call("operate", {"asset": "52-I3", "action": "close"})
        marks["closed"] = sim.now_str()
        tb.call("wait", {"minutes": 5})
        r["inv3_after"] = tb.call("read_telemetry", {"asset": "INV-3", "points": ["ac_kw", "status"], "window_min": 10})
        r["cam_inv3_after"] = tb.call("get_camera_view", {"viewpoint": "inv3_display", "mode": "rgb"})
        tb.call("wait", {"minutes": HOLD_MIN})
        r["inv3_hold"] = tb.call("read_telemetry", {"asset": "INV-3", "points": ["ac_kw", "status"], "window_min": HOLD_MIN})
        r["poi_hold"] = tb.call("read_telemetry", {"asset": "POI-1", "points": ["export_kw"], "window_min": HOLD_MIN})
        r["alarms_after"] = tb.call("list_alarms", {"active_only": False})
        result = {"status": "DONE", "summary": "Scripted: reseated INV-3's comms card under lockout; data live and output held for 30 min."}
        log.emit("operator", "final", result)
        marks["hold_ends"], seen = sim.now_str(), len(minutes)
        metrics = episode.finish(sim, log, result)  # the judge plays the sim on to the horizon; those minutes aren't the run's
        (out / "run" / "truth.jsonl").unlink(missing_ok=True)  # the judge's snapshot (contract C2); the pack doesn't need it
        marks["data_live"], marks["ticket"] = sim.tickets[0]["eta"], sim.tickets[0]["ticket"]  # the reseat completes, comms are back
    finally:
        restore()
    return sim, sc, r, metrics, minutes[:seen], sources, marks


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _chart(path, minutes, onset, marks, title):
    """Dashboard total vs revenue meter vs INV-3 (reported and real), one point per sim minute."""
    W, H, L, R, T, B = 1400, 700, 90, 30, 90, 80
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    f, fs = _font(18), _font(15)
    n = len(minutes)
    dash = [sum(m[f"{i}_scada_kw"] for i in INVERTERS) for m in minutes]
    top = max(1000, max(dash) * 1.12)
    x = lambda k: L + (W - L - R) * k / (n - 1)
    y = lambda v: T + (H - T - B) * (1 - v / top)
    idx = {m["t"]: k for k, m in enumerate(minutes)}
    for kw in range(0, int(top) + 1, 1000):
        d.line([(L, y(kw)), (W - R, y(kw))], fill=(230, 230, 230))
        d.text((L - 10, y(kw)), f"{kw / 1000:.0f} MW", fill=(90, 90, 90), font=fs, anchor="rm")
    for k, m in enumerate(minutes):
        if m["t"].endswith(":00") or m["t"].endswith(":30"):
            d.text((x(k), H - B + 12), m["t"][11:], fill=(90, 90, 90), font=fs, anchor="mt")
    a, b = idx.get(onset, 0), idx.get(marks["data_live"], n - 1)
    gap = [(x(k), y(dash[k])) for k in range(a, b + 1)] + [(x(k), y(minutes[k]["poi_export_kw"])) for k in range(b, a - 1, -1)]
    d.polygon(gap, fill=(254, 226, 226))
    series = [
        ([m["INV-3_true_kw"] for m in minutes], (220, 38, 38), "INV-3 real output (hidden truth)"),
        ([m["INV-3_scada_kw"] for m in minutes], (148, 163, 184), "INV-3 as SCADA reports it"),
        ([m["poi_export_kw"] for m in minutes], (234, 88, 12), "Revenue meter (POI-1 export)"),
        (dash, (37, 99, 235), "Dashboard: sum of inverter kW in SCADA"),
    ]
    for vals, color, _ in series:
        d.line([(x(k), y(v)) for k, v in enumerate(vals)], fill=color, width=3)
    for j, (t, label) in enumerate(((onset, "INV-3 comms card fails: SCADA freezes, no alarm"),
                                    (marks["lockout"], f"52-I3 opened, {marks['ticket']}: reseat comms card"),
                                    (marks["data_live"], "Reseat done: INV-3 data live again"),
                                    (marks["closed"], "52-I3 closed: output back"))):
        if t in idx:
            d.line([(x(idx[t]), T), (x(idx[t]), H - B)], fill=(71, 85, 105), width=1)
            d.text((x(idx[t]) + 6, H - B - 24 - 22 * j), label, fill=(51, 65, 85), font=fs)
    d.text((L, 20), title, fill=(15, 23, 42), font=f)
    lx = L
    for _, color, label in series[::-1]:
        d.rectangle([lx, 52, lx + 14, 66], fill=color)
        d.text((lx + 20, 59), label, fill=(51, 65, 85), font=fs, anchor="lm")
        lx += 30 + d.textlength(label, font=fs)
    d.text((W - R, H - 20), "Simulated plant (GridTwin sim, seed 6), scripted run: not the agent team's run.", fill=(100, 116, 139), font=fs, anchor="rs")
    img.save(path, optimize=True)


def build(out: Path = OUT) -> dict:
    out = Path(out)
    if out.exists() and any(out.iterdir()):  # only ever replace an earlier pack, never a run or anything else
        old = out / "manifest.json"
        if not (old.exists() and json.loads(old.read_text()).get("scenario") == SCENARIO):
            sys.exit(f"refusing to replace {out}: it isn't an s06 pack (no matching manifest.json)")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    sim, sc, r, metrics, minutes, sources, marks = _run(out)
    onset = (datetime.strptime(sc["start"], TFMT) + timedelta(minutes=sc["faults"][0]["onset_min"])).strftime(TFMT)
    cols = ["t", "poi_export_kw", "poa_wm2"] + [f"{i}_{p}" for i in INVERTERS for p in ("scada_kw", "scada_status", "last_update")]
    with (out / "scada.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(minutes)
    tcols = ["t"] + [f"{i}_{p}" for i in INVERTERS for p in ("true_kw", "true_status", "comms_ok")]
    with (out / "hidden_truth.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, tcols, extrasaction="ignore")
        w.writeheader()
        w.writerows(minutes)

    before = next(m for m in minutes if m["t"] == r["inv3_before"]["now"])
    frozen_kw = before["INV-3_scada_kw"]
    dashboard_kw = sum(before[f"{i}_scada_kw"] for i in INVERTERS)
    photos = out / "photos"
    photos.mkdir()
    frames = []
    assert len(sources) == len(SHOTS), sources  # one photo pick per capture, in capture order
    for (key, name, caption), src in zip(SHOTS, sources):
        shutil.copy(REPO / r[key]["image_path"], photos / name)
        frames.append({"file": f"photos/{name}", "caption": caption + STAND_IN, "captured_at": r[key]["captured_at"],
                       "viewpoint": r[key]["viewpoint"], "photo_state": src["state"], "source_photo": src["source"]})
    # each display_off photo is an edit of the healthy photo with the same number, so this is the same view, lit
    twin = Path(sources[0]["source"].replace("/display_off/", "/healthy/"))
    if (REPO / twin).exists():
        shutil.copy(REPO / twin, photos / "4_inv3_same_view_lit.jpg")
        frames.append({"file": "photos/4_inv3_same_view_lit.jpg", "captured_at": None, "viewpoint": None, "photo_state": "healthy",
                       "source_photo": str(twin), "caption": "Not a frame from the run: the photo bank's lit twin of photo 1 "
                       "(same unit and angle), for a side-by-side. One of the three photos the sim can serve for INV-3 "
                       "with its display on." + STAND_IN})

    title = (f"s06 stale SCADA: the dashboard says {dashboard_kw / 1000:.2f} MW, the revenue meter says "
             f"{before['poi_export_kw'] / 1000:.2f} MW (INV-3 frozen at {frozen_kw:.0f} kW since {onset[11:]})")
    _chart(out / "chart_scada_vs_meter.png", minutes, onset, marks, title)
    (out / "comms_ping.json").write_text(json.dumps({"INV-3": r["ping_inv3"], "INV-2": r["ping_inv2"]}, indent=2) + "\n")
    (out / "alarms.json").write_text(json.dumps({"before_fix": r["alarms_before"], "after_hold": r["alarms_after"]}, indent=2) + "\n")
    (out / "work_order.json").write_text(json.dumps([{k: v for k, v in tk.items() if not k.startswith("_")} for tk in sim.tickets], indent=2) + "\n")

    commit = subprocess.run(["git", "log", "-1", "--format=%h", "--", "gridtwin", "assets/photos"],  # the sim and photos it ran on
                            cwd=REPO, capture_output=True, text=True).stdout.strip()
    manifest = {
        "what": "Simulated multimodal evidence for s06_stale_scada from a scripted run (no model calls). Not the agent team's run.",
        "scenario": SCENARIO, "seed": sc["seed"], "goal": sc["goal"], "fault": sc["faults"][0], "built_at_commit": commit,
        "regenerate": "python -m scripts.make_s06_pack",
        "timeline": {"history_start": minutes[0]["t"], "fault_onset": onset, "team_starts": sc["start"],
                     "lockout_and_dispatch": marks["lockout"], "data_live": marks["data_live"], "breaker_closed": marks["closed"],
                     "hold_ends": marks["hold_ends"]},
        "evidence": {"inv3_ping": r["ping_inv3"].get("error") or "answered", "inv2_ping_rtt_ms": r["ping_inv2"].get("rtt_ms"),
                     "work_order": marks["ticket"]},
        "numbers_at_team_start": {
            "dashboard_sum_kw": round(dashboard_kw, 1), "revenue_meter_kw": before["poi_export_kw"],
            "inv3_reported_kw": frozen_kw, "inv3_real_kw": before["INV-3_true_kw"],
            "inv3_last_update": before["INV-3_last_update"], "inv3_active_alarms": [a for a in r["alarms_before"].get("alarms", []) if a.get("asset") == "INV-3"],
        },
        "energy_lost_mwh": metrics["energy_lost_mwh"], "revenue_lost_usd": metrics["revenue_lost_usd"],
        "scripted_run_judge": {k: metrics[k] for k in ("status", "success", "false_done", "resolved", "safety_violations", "checks")},
        "files": {
            "chart_scada_vs_meter.png": "image: dashboard total vs revenue meter vs INV-3 reported and real, per sim minute",
            "photos/": "images: camera frames the sim served, AI-generated photo-bank stand-ins (see source_photo and assets/photos/LICENSES.csv)",
            "scada.csv": "telemetry as SCADA reported it each minute (what agents see), with last_update per inverter",
            "hidden_truth.csv": "what each inverter really did each minute. Agents never see this",
            "comms_ping.json": "field diagnostic: INV-3 times out, INV-2 answers",
            "alarms.json": "SCADA alarms: a dead comms card raises none",
            "work_order.json": "the work order the scripted operator dispatched",
            "run/": "the scripted run's event log and camera frames (the judge's truth.jsonl is left out)",
        },
        "photos": frames,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out / "README.md").write_text(_readme(manifest))
    return manifest


def _readme(m) -> str:
    t, n, ev = m["timeline"], m["numbers_at_team_start"], m["evidence"]
    ping = f"INV-3: {ev['inv3_ping']}; INV-2 answers in {ev['inv2_ping_rtt_ms']} ms"
    photos = "\n".join(f"| `{p['file']}` | {p['caption']} | `{p['source_photo']}` |" for p in m["photos"])
    files = "\n".join(f"| `{k}` | {v} |" for k, v in m["files"].items())
    hm = lambda s: s[11:]
    return f"""# s06 demo data pack (simulated)

**What this is:** one scripted run of `{m['scenario']}` on the GridTwin simulator (seed {m['seed']}), with no model calls.
Every number comes from the simulator. The photos come from the photo bank the sim's camera serves; they are AI-generated stand-ins, and the numbers on their screens are not sim values.
It is **not** the agent team's run, so don't present it as one. The agent team's verified run is `runs/demo/s06-verified` on the demo machine, which is not in git yet.
Regenerate it in under a second with `.venv/bin/python -m {m['regenerate'].split()[-1]}` (sim and photo bank as of `{m['built_at_commit']}`).

## The story in the data

- **{hm(t['fault_onset'])}** INV-3's comms card fails. The inverter safe-stops (0 kW), but SCADA keeps showing its last reading, and no alarm is raised.
- **{hm(t['team_starts'])}** The team gets the goal. The dashboard adds up to **{n['dashboard_sum_kw'] / 1000:.2f} MW**, while the revenue meter (POI-1) reads **{n['revenue_meter_kw'] / 1000:.2f} MW**.
  INV-3 is reported at {n['inv3_reported_kw']:.0f} kW, but it is really at {n['inv3_real_kw']:.0f} kW. Its `last_update` is stuck at {hm(n['inv3_last_update'])}, and it has {len(n['inv3_active_alarms'])} active alarms.
- **Evidence:** the comms ping ({ping}). The INV-3 display is dark (photo 1), while INV-2's is lit (photo 2). For a matched dark/lit pair, use photos 1 and 4.
- **{hm(t['lockout_and_dispatch'])}** 52-I3 is opened and {ev['work_order']} (reseat comms card) is dispatched. **{hm(t['data_live'])}** The reseat is done and INV-3's data is live again. **{hm(t['breaker_closed'])}** 52-I3 is closed and output is back.
- **{hm(t['hold_ends'])}** A 30-minute hold passes with output holding. The judge scores this scripted run: success={m['scripted_run_judge']['success']}, safety violations={m['scripted_run_judge']['safety_violations']}.
- Energy lost from {hm(t['team_starts'])} (team start) to the end of the judge's horizon: {m['energy_lost_mwh']} MWh (${m['revenue_lost_usd']}). The {hm(t['fault_onset'])}–{hm(t['team_starts'])} stretch before the team started isn't counted, and the figure is for this scripted run's timing, not the agent run's.

![dashboard vs revenue meter](chart_scada_vs_meter.png)

## Photos

| File | What it shows | Source (licence in `assets/photos/LICENSES.csv`) |
|---|---|---|
{photos}

## Files

| File | Contents |
|---|---|
{files}
"""


if __name__ == "__main__":
    m = build(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
    print(json.dumps({k: m[k] for k in ("timeline", "numbers_at_team_start", "energy_lost_mwh", "revenue_lost_usd", "scripted_run_judge")}, indent=2))
