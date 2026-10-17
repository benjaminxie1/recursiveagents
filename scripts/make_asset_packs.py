"""Build static per-asset data packs for the twin's asset panel: sensors, camera, infrared and a status flag for every asset.

Use case: the 3-minute demo. The agent diagnoses a fault, the twin flies to the asset and opens it, and the panel shows that
asset's sensor history, a camera frame and an infrared frame with the hot spot boxed and labelled in deg C, plus a flag
(ok / hot / fault) with a short reason and a call to action. Everything is precomputed here, so the page loads files and
makes no live calls.

For each scenario the sim runs to the scenario's start (the moment the agent gets its goal, gridtwin.sim, no model calls),
recording every minute of every asset. Then, per asset in assets/splats/scene.json:

- sensors: per-minute series as SCADA reported them (a dead comms card shows as a flat line), plus per-combiner string
  currents and per-block DC output, which SCADA does not log but the sim knows;
- camera: the splat render from the asset's station in its current visual state (scripts/render_splat_views.py), else the
  asset's own splat view (scripts/render_anchor_views.py), else the photo bank; s06's inverters reuse Will's display
  frames from assets/demo/s06_case;
- infrared: modelled on that camera frame from the sim's true temperatures (heatsink, top oil, cell, a hot string), ironbow
  palette, span locked per scenario, box Bx1 on the asset with its max in deg C; s06's inverters reuse Will's frames;
- flag: from the sim's hidden faults and visual state: status, reason, CTA (the scenario's expected fix) and requirement ids.

Run: python -m scripts.make_asset_packs [--scenarios s03_fan_relapse,s06_stale_scada] [--out assets/demo/asset_packs]
"""
import argparse
import csv
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from gridtwin.sim import episode
from gridtwin.sim.camera import PHOTOS, REPO
from gridtwin.sim.plant import BLOCK_FOR, BREAKER_FOR, BREAKERS, CFG, COMBINERS, INVERTERS, Plant, taxonomy_for

OUT = REPO / "assets" / "demo" / "asset_packs"
SCENE = REPO / "assets" / "splats" / "scene.json"
RENDERS = REPO / "assets" / "splats" / "renders"
VIEWS = OUT / "_views"
S06_CASE = REPO / "assets" / "demo" / "s06_case"
SCENARIOS = ["s03_fan_relapse", "s06_stale_scada", "s06b_stale_scada_inv2", "s08_hotspot"]
CAM = (800, 600)
IRONBOW = [(0.00, (0, 0, 10)), (0.10, (25, 0, 70)), (0.25, (85, 0, 140)), (0.40, (150, 10, 150)), (0.52, (205, 40, 90)),
           (0.64, (235, 85, 20)), (0.76, (250, 140, 0)), (0.88, (255, 200, 30)), (0.96, (255, 240, 130)),
           (1.00, (255, 255, 235))]
UNITS = {"ac_kw": "kW", "dc_kw": "kW", "dc_v": "V", "heatsink_c": "°C", "fan_rpm": "rpm", "curtail_pct": "%",
         "ghi_wm2": "W/m²", "poa_wm2": "W/m²", "ambient_c": "°C", "wind_ms": "m/s", "oil_top_c": "°C", "load_pct": "%",
         "winding_hot_c": "°C", "dga_h2_ppm": "ppm", "dga_c2h2_ppm": "ppm", "export_kw": "kW", "freq_hz": "Hz",
         "plant_limit_kw": "kW", "soc_pct": "%", "p_kw": "kW", "max_cell_c": "°C", "string_a_total": "A",
         "string_a_min": "A", "string_a_max": "A", "insulation_mohm": "MΩ", "module_c": "°C", "yield_pct_of_peers": "%"}
KIND = {"INV": ("central_inverter", "central inverter"), "52-": ("breaker", "breaker"), "CB-": ("combiner", "combiner box"),
        "B": ("pv_block", "PV block"), "XFMR-": ("transformer", "step-up transformer"), "BESS-": ("battery", "battery system"),
        "POI-": ("revenue_meter", "revenue meter"), "MET-": ("met_station", "met station")}
WHY = {  # fault type -> status, reason template, CTA verb
    "F3_fan_failure": ("hot", "Cooling fan dead: {fan_rpm} rpm, heatsink {heatsink_c} °C, output derated"),
    "F6_stale_scada": ("fault", "Comms card dead: SCADA frozen since {last_update}, display dark, cabinet cold on IR"),
    "F8_hotspot": ("hot", "Hot spot on string {string}: {hot_c} °C on IR, string current {pct}% of median"),
    "F1_ground_fault": ("fault", "Burnt MC4 connector, insulation {insulation_mohm} MΩ, GFDI tripped"),
    "F2_soiling": ("fault", "Modules soiled: block yield {yield_pct_of_peers}% of peers"),
    "F7_vegetation": ("fault", "Vegetation shading the front-row strings in the afternoon"),
    "F5_xfmr_incipient": ("hot", "Incipient fault: C2H2 {dga_c2h2_ppm} ppm, top oil {oil_top_c} °C"),
    "F4_breaker_trip": ("fault", "Breaker tripped, feeder down"),
}


def kind_of(a):
    for p, k in KIND.items():
        if a.startswith(p):
            return k
    return ("asset", "asset")


def font(size, bold=False):
    for name in (("DejaVuSans-Bold.ttf",) if bold else ("DejaVuSans.ttf",)):
        for d in ("/usr/share/fonts/truetype/dejavu", "/Library/Fonts", "/System/Library/Fonts/Supplemental"):
            try:
                return ImageFont.truetype(f"{d}/{name}", size)
            except OSError:
                continue
    for path in ("/System/Library/Fonts/Helvetica.ttc",):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


# ---------------------------------------------------------------- the sim

def simulate(scenario):
    """Run a scenario to its start and record every minute: SCADA history plus what SCADA does not log (combiners, blocks)."""
    extra, tick = [], Plant.tick

    def rec(sim):
        tick(sim)
        row = {"t": sim.now_str()}
        poa = sim.met["poa_wm2"]
        t_cell = sim.met["ambient_c"] + poa / 800 * 25
        for cb in COMBINERS:
            tel = sim._combiner_telemetry(cb).get("points")
            if tel:
                amps = tel["string_a"]
                row[cb] = {"string_a_total": round(sum(amps), 1), "string_a_min": min(amps), "string_a_max": max(amps),
                           "insulation_mohm": tel["insulation_mohm"]}
            else:
                row[cb] = None
        dc = {b: sim.inv[inv]["dc_kw"] for b, inv in CFG["blocks"].items()}
        mean = sum(dc.values()) / len(dc) or 1.0
        for b, inv in CFG["blocks"].items():
            row[b] = {"dc_kw": dc[b], "module_c": round(t_cell, 1),
                      "yield_pct_of_peers": round(100 * dc[b] / mean, 1) if mean > 1 else 100.0}
        extra.append(row)

    Plant.tick = rec
    try:
        with tempfile.TemporaryDirectory() as tmp:
            sim, tb, _log, sc = episode.start(scenario, run_id="pack", runs_dir=Path(tmp))
            alarms = tb.call("list_alarms", {"active_only": False})["alarms"]
    finally:
        Plant.tick = tick
    return sim, sc, extra, alarms


def series_for(sim, extra, asset):
    """{"t": [...], "series": {point: [...]}} at one-minute steps over the recorded window."""
    if asset in sim.history:
        hist = sim.history[asset]
        pts = [p for p in hist if p != "gfdi"]
        return {"t": [x[0] for x in hist[pts[0]]], "series": {p: [x[1] for x in hist[p]] for p in pts}}
    rows = [r for r in extra if asset in r][-240:]
    pts = sorted({k for r in rows if r[asset] for k in r[asset]})
    return {"t": [r["t"] for r in rows],
            "series": {p: [(r[asset] or {}).get(p) for r in rows] for p in pts}}


# ---------------------------------------------------------------- truth -> flag

def fault_by_asset(sim):
    return {f.asset: f for f in sim.faults if f.active and f.asset}


def flag_for(sim, sc, asset, latest, hot_c):
    faults = fault_by_asset(sim)
    reqs = [r["id"] for r in CFG["requirements"] if asset.startswith(r["applies_to"])]
    f = faults.get(asset)
    if f is None:
        return {"status": "ok", "reason": "Within normal range", "cta": None, "requirements": reqs}
    status, tmpl = WHY.get(f.type, ("fault", f.type))
    vals = dict(latest)
    vals.update(hot_c=hot_c, string=getattr(f, "string", "?"))
    if asset.startswith("CB-") and "string_a_min" in latest:
        med = latest["string_a_total"] / CFG["strings_per_combiner"]
        vals["pct"] = round(100 * latest["string_a_min"] / med) if med else 0
    try:
        reason = tmpl.format(**vals)
    except (KeyError, IndexError):
        reason = tmpl.split(":")[0]
    steps = []
    for step in (sc.get("expected") or {}).get("resolved_by", []):
        (tool, args), = step.items()
        steps.append({"tool": tool, **args})
    work = next((s["work_order"] for s in steps if s.get("work_order")), None)
    wo = CFG["work_orders"].get(work) if work else None
    label = {"dispatch_tech": "Dispatch", "operate": "Operate"}
    cta = {"label": " → ".join(f"{label.get(s['tool'], s['tool'])} {s.get('work_order') or s.get('action')}" for s in steps)
           or "Page an engineer", "steps": steps}
    if wo:
        cta.update(work_order=work, minutes=wo[0], usd=wo[1], needs_deenergized=wo[2])
        if wo[2] and asset in BREAKER_FOR:
            cta["isolate_first"] = BREAKER_FOR[asset]
    fail = {"F3_fan_failure": ["REQ-INV-02", "REQ-INV-03"], "F6_stale_scada": ["REQ-INV-04", "REQ-INV-01"],
            "F8_hotspot": ["REQ-CB-02", "REQ-CB-03"], "F1_ground_fault": ["REQ-CB-01", "REQ-CB-03"],
            "F5_xfmr_incipient": ["REQ-XF-01", "REQ-XF-04"]}.get(f.type, [])
    return {"status": status, "reason": reason, "fault": f.type, "cta": cta, "requirements": reqs, "failing": fail}


def true_surface_c(sim, asset):
    """Hottest surface the thermal camera would see on the asset, from the sim's true state (deg C)."""
    amb, poa = sim.met["ambient_c"], sim.met["poa_wm2"]
    sun = poa / 1000 * 6
    if asset in INVERTERS:
        s = sim.inv[asset]
        if s["status"] != "running" or s["ac_kw"] <= 0:
            return amb + 1.2 + sun * 0.3
        return s["heatsink_c"] - 0.8
    if asset.startswith("CB-"):
        f = fault_by_asset(sim).get(asset)
        if f is not None and f.type in ("F8_hotspot", "F1_ground_fault"):
            return amb + poa / 800 * 25 + 38
        return amb + sun + 4
    if asset in CFG["blocks"]:
        return amb + poa / 800 * 25
    if asset == "XFMR-1":
        return sim.xfmr["oil_top_c"] + 1.5
    if asset in BREAKERS:
        return amb + sun + (3 if sim.breakers[asset] == "closed" else 0.5)
    if asset == "BESS-1":
        return 27.0 + sun * 0.5
    if asset == "POI-1":
        return amb + sun + 2
    return amb + sun


# ---------------------------------------------------------------- images

def camera_frame(sim, asset, scene):
    """(image, anchor_px, source) for the asset's current visual state."""
    state = sim.visual_state(asset, "rgb")
    stations = [s for s, d in scene["stations"].items() if d["asset"] == asset]
    rindex = json.loads((RENDERS / "index.json").read_text())
    if state not in ("healthy", "connector_normal", "array_normal", "normal", "handle_closed"):
        stations.sort(key=lambda s: 0 if state in rindex.get(s, {}) else 1)
    for st in stations:
        e = rindex.get(st, {}).get(state)
        if e and (REPO / e["file"]).exists():
            return Image.open(REPO / e["file"]).convert("RGB"), e["anchor_px"], {"kind": "splat_render", "station": st,
                                                                                  "state": state, "file": e["file"]}
    vindex = json.loads((VIEWS / "index.json").read_text()) if (VIEWS / "index.json").exists() else {}
    e = vindex.get(asset)
    if e and (REPO / e["file"]).exists():
        return Image.open(REPO / e["file"]).convert("RGB"), e["anchor_px"], {"kind": "splat_view", "state": state,
                                                                              "file": e["file"]}
    folder = PHOTOS / asset / state
    files = sorted(folder.glob("*.jp*g")) if folder.is_dir() else []
    if files:
        return Image.open(files[0]).convert("RGB"), None, {"kind": "photo_bank", "state": state,
                                                           "file": str(files[0].relative_to(REPO))}
    img = Image.new("RGB", CAM, (70, 74, 80))
    ImageDraw.Draw(img).text((24, 280), f"{asset}", fill=(230, 230, 230), font=font(28, True))
    return img, None, {"kind": "placeholder", "state": state}


def apparent_c(rgb, amb, poa):
    """Background apparent temperature from the rgb frame: sky cold, dark sunlit surfaces warm."""
    a = np.asarray(rgb.filter(ImageFilter.GaussianBlur(1.5)), float) / 255
    lum = a @ np.array([0.3, 0.55, 0.15])
    t = amb + 2.0 + poa / 1000 * 10.0 * (1 - lum)
    sky = (a[..., 2] > a[..., 0] + 0.06) & (lum > 0.45)
    sky = np.cumprod(sky, axis=0).astype(bool)
    return np.where(sky, amb - 8 + 2.0 * (1 - lum), t)


def colorize(t, span):
    x = np.clip((t - span[0]) / (span[1] - span[0]), 0, 1)
    xs = np.array([p[0] for p in IRONBOW])
    cols = np.array([p[1] for p in IRONBOW], float)
    return np.stack([np.interp(x, xs, cols[:, c]) for c in range(3)], -1).astype(np.uint8)


def infrared(rgb, anchor_px, surface_c, amb, poa, span, status, asset, rng):
    """Ironbow thermal frame with Bx1 on the asset and its max in deg C. Returns (image, readings)."""
    rgb = rgb.resize(CAM)
    w, h = CAM
    t = apparent_c(rgb, amb, poa)
    x, y = anchor_px if anchor_px else (w / 2, h / 2)
    x, y = float(np.clip(x, 40, w - 40)), float(np.clip(y, 40, h - 40))
    hot = status == "hot"
    r = 0.025 * w if hot else 0.07 * w
    yy, xx = np.mgrid[0:h, 0:w]
    blob = np.exp(-(((xx - x) ** 2 + ((yy - y) * 1.15) ** 2) / (2 * r * r)))
    body = np.exp(-(((xx - x) ** 2 + (yy - y) ** 2) / (2 * (0.09 * w) ** 2)))
    base = t[int(y), int(x)]
    lift = max(0.0, surface_c - base)
    t = t + body * (min(lift * 0.4, 12.0) if hot else lift * 0.85)  # a hot spot sits on a merely warm body
    t = t + blob * max(0.0, surface_c - t[int(y), int(x)]) * (1.0 if hot else 0.9)
    t = t + rng.normal(0, 0.05, t.shape)
    bw = int(0.19 * w) if not hot else int(0.12 * w)
    box = [int(max(0, x - bw)), int(max(0, y - bw * 0.8)), int(min(w, x + bw)), int(min(h, y + bw * 0.8))]
    roi = t[box[1]:box[3], box[0]:box[2]]
    iy, ix = np.unravel_index(np.argmax(roi), roi.shape)
    readings = {"Bx1": {"label": f"{asset} body", "box": box, "max_c": round(float(roi.max()), 1),
                        "min_c": round(float(roi.min()), 1), "mean_c": round(float(roi.mean()), 1),
                        "max_xy": [int(box[0] + ix), int(box[1] + iy)]},
                "frame": {"max_c": round(float(t.max()), 1), "min_c": round(float(t.min()), 1)}}
    img = Image.fromarray(colorize(t, span))
    # MSX-style edges from the rgb frame
    edges = np.asarray(rgb.convert("L").filter(ImageFilter.FIND_EDGES), float)
    arr = np.asarray(img, float) + (edges[..., None] > 40) * 35
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    col = {"hot": (255, 40, 40), "fault": (80, 200, 255)}.get(status, (255, 255, 255))
    d.rectangle(box, outline=col, width=3 if status != "ok" else 2)
    mx, my = readings["Bx1"]["max_xy"]
    d.line([(mx - 9, my), (mx + 9, my)], fill=(255, 255, 255), width=2)
    d.line([(mx, my - 9), (mx, my + 9)], fill=(255, 255, 255), width=2)
    label = f"Bx1 max {readings['Bx1']['max_c']:.1f} °C"
    f = font(20, True)
    tw = d.textlength(label, font=f)
    lx, ly = box[0], max(0, box[1] - 30)
    d.rectangle([lx, ly, lx + tw + 12, ly + 27], fill=col)
    d.text((lx + 6, ly + 3), label, fill=(0, 0, 0) if status == "ok" else (255, 255, 255) if status == "hot" else (0, 0, 0),
           font=f)
    if status != "ok":
        tag = {"hot": "HOT SPOT", "fault": "NO HEAT"}[status]
        tf = font(18, True)
        d.rectangle([12, 12, 24 + d.textlength(tag, font=tf), 40], fill=col)
        d.text((18, 15), tag, fill=(255, 255, 255) if status == "hot" else (0, 0, 0), font=tf)
    # scale bar
    bar = 92
    canvas = Image.new("RGB", (w + bar, h), (16, 16, 18))
    canvas.paste(img, (0, 0))
    d = ImageDraw.Draw(canvas)
    top, bot = 24, h - 24
    ramp = colorize(np.linspace(span[1], span[0], bot - top)[:, None].repeat(16, 1), span)
    canvas.paste(Image.fromarray(ramp), (w + 12, top))
    sf = font(13)
    for v in np.linspace(span[0], span[1], 7):
        yv = bot - (v - span[0]) / (span[1] - span[0]) * (bot - top)
        d.line([(w + 28, yv), (w + 33, yv)], fill=(230, 230, 230))
        d.text((w + 36, yv - 8), f"{v:.0f} °C", fill=(230, 230, 230), font=sf)
    return canvas, readings


def reuse_s06_case(asset, out_dir, pack_dir):
    """s06 inverters: Will's display frame and radiometric infrared frame (assets/demo/s06_case)."""
    objs = json.loads((S06_CASE / "agent" / "objects.json").read_text())["objects"]
    o = objs.get(asset)
    if not o:
        return None
    shutil.copy(S06_CASE / o["camera"]["file"], out_dir / "camera.jpg")
    shutil.copy(S06_CASE / o["infrared"]["file"], out_dir / "infrared.png")
    rd = o["infrared"]["readings"]
    return ({"kind": "s06_case", "file": f"assets/demo/s06_case/{o['camera']['file']}"},
            {"Bx1": {"label": rd["Bx1"]["label"], "box": rd["Bx1"]["box"], "max_c": rd["Bx1"]["max_c"],
                     "min_c": rd["Bx1"]["min_c"], "mean_c": rd["Bx1"]["mean_c"],
                     "Sp1": rd["Sp1"]["temp_c"], "Sp2": rd["Sp2"]["temp_c"]},
             "grid": [320, 240], "radiometric": f"assets/demo/s06_case/{o['infrared']['radiometric']}"},
            o["camera"]["captured"], o["infrared"]["captured"])


# ---------------------------------------------------------------- pack

def build(scenario, out_root):
    scene = json.loads(SCENE.read_text())
    sim, sc, extra, alarms = simulate(scenario)
    rng = np.random.default_rng(sc["seed"])
    pack = out_root / scenario
    if pack.exists():
        shutil.rmtree(pack)
    pack.mkdir(parents=True)
    amb, poa = sim.met["ambient_c"], sim.met["poa_wm2"]
    surface = {a: true_surface_c(sim, a) for a in scene["anchors"]}
    span = [round(amb - 8), max(50, int(np.ceil(max(surface.values()) / 5) * 5 + 5))]
    assets, focus = {}, None
    for asset in sorted(scene["anchors"]):
        d = pack / asset
        d.mkdir()
        ser = series_for(sim, extra, asset)
        latest = {p: v[-1] for p, v in ser["series"].items() if v and v[-1] is not None}
        if asset in sim.last_update:
            latest["last_update"] = sim.last_update[asset].strftime("%Y-%m-%dT%H:%M")
            latest["data_age_min"] = int((sim.t - sim.last_update[asset]).total_seconds() // 60)
        (d / "sensors.json").write_text(json.dumps({"asset": asset, "step_min": 1, "units": {p: UNITS.get(p, "") for p in
                                                                                            ser["series"]}, **ser}))
        with (d / "sensors.csv").open("w", newline="") as fh:
            wr = csv.writer(fh)
            wr.writerow(["t", *ser["series"]])
            for k, t in enumerate(ser["t"]):
                wr.writerow([t, *(v[k] for v in ser["series"].values())])
        status_guess = flag_for(sim, sc, asset, latest, round(surface[asset], 1))["status"]
        reused = reuse_s06_case(asset, d, pack) if scenario == "s06_stale_scada" else None
        if reused:
            cam_src, ir, cam_t, ir_t = reused
        else:
            img, anchor_px, cam_src = camera_frame(sim, asset, scene)
            img = img.resize(CAM)
            img.save(d / "camera.jpg", "JPEG", quality=85)
            ir_img, ir = infrared(img, anchor_px, surface[asset], amb, poa, span, status_guess, asset, rng)
            ir_img.save(d / "infrared.png", optimize=True)
            ir["span_c"] = span
            cam_t = ir_t = sim.now_str()
        hot_c = ir["Bx1"]["max_c"]
        flag = flag_for(sim, sc, asset, latest, hot_c)
        if flag["status"] != "ok" and focus is None:
            focus = asset
        kind, label = kind_of(asset)
        rel = f"assets/demo/asset_packs/{scenario}/{asset}"
        assets[asset] = {
            "kind": kind, "label": f"{asset} {label}", "anchor": scene["anchors"][asset],
            "taxonomy": {k: v for k, v in taxonomy_for(asset).items() if k in ("system", "subsystem", "component")},
            "status": flag["status"], "reason": flag["reason"], "cta": flag["cta"], "fault": flag.get("fault"),
            "requirements": flag["requirements"], "failing_requirements": flag.get("failing", []),
            "latest": latest,
            "alarms": [a for a in alarms if a["asset"] == asset],
            "sensors": {"json": f"{rel}/sensors.json", "csv": f"{rel}/sensors.csv", "points": list(ser["series"]),
                        "from": ser["t"][0] if ser["t"] else None, "to": ser["t"][-1] if ser["t"] else None},
            "camera": {"file": f"{rel}/camera.jpg", "captured": cam_t, "source": cam_src},
            "infrared": {"file": f"{rel}/infrared.png", "captured": ir_t, **ir},
        }
    faults = [{"type": f.type, "asset": f.asset} for f in sim.faults if f.active]
    index = {"scenario": scenario, "goal": sc["goal"], "as_of": sim.now_str(), "plant": CFG["plant"]["name"],
             "focus": focus, "faults": faults, "ambient_c": amb, "poa_wm2": poa,
             "counts": {s: sum(1 for a in assets.values() if a["status"] == s) for s in ("ok", "hot", "fault")},
             "simulated": True, "assets": assets}
    (pack / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))
    print(f"{scenario}: {len(assets)} assets, focus {focus}, {index['counts']}")
    return index


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    top = {}
    for s in a.scenarios.split(","):
        ix = build(s, out)
        top[s] = {"index": f"assets/demo/asset_packs/{s}/index.json", "as_of": ix["as_of"], "focus": ix["focus"],
                  "goal": ix["goal"], "counts": ix["counts"]}
    old = json.loads((out / "index.json").read_text()) if (out / "index.json").exists() else {}
    old.get("scenarios", {}).update(top)
    (out / "index.json").write_text(json.dumps({"schema": "docs/ASSET_PACKS.md", "scenarios": {**old.get("scenarios", {}),
                                                                                               **top}}, indent=1))


if __name__ == "__main__":
    main()
