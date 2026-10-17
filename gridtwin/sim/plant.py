"""Deterministic plant model (docs/SIMULATOR.md §1). One tick = one sim-minute.

Everything an agent may see goes through the public methods used by tools.Toolbox.
Hidden state (true outputs, faults, safety log, energy accounting) lives in self.truth and the
fault objects; observable SCADA values live in self.scada and are what telemetry returns.
"""
import math
import random
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from gridtwin.sim import faults as faultlib

HERE = Path(__file__).parent
CFG = yaml.safe_load((HERE / "plant.yaml").read_text())
INV_CFG = CFG["inverters"]
INVERTERS = INV_CFG["ids"]
BREAKERS = CFG["breakers"]
BREAKER_FOR = {asset: brk for brk, asset in BREAKERS.items()}
BLOCK_FOR = {inv: blk for blk, inv in CFG["blocks"].items()}
COMBINERS = [f"CB-{b}.{k}" for b in range(1, 5) for k in range(1, CFG["combiners_per_block"] + 1)]
BLOCKS = sorted(CFG["blocks"])
INSULATION_OK_MOHM = 55.0
GFDI_LIMIT_MOHM = 1.0
HISTORY_MIN = 240


def taxonomy_for(asset: str) -> dict:
    """System/subsystem/component and applicable standards for an asset (longest matching prefix)."""
    for prefix in sorted(CFG["taxonomy"], key=len, reverse=True):
        if asset.startswith(prefix):
            return dict(CFG["taxonomy"][prefix])
    return {}
TFMT = "%Y-%m-%dT%H:%M"


class Plant:
    def __init__(self, seed: int, start: str, faults=(), weather=None, preroll_min: int = 120, camera=None):
        self.seed = seed
        self.rng = random.Random(seed)
        self.start = datetime.strptime(start, TFMT)
        self.t = self.start - timedelta(minutes=preroll_min)
        self.elapsed_min = -preroll_min
        self.weather = weather or {}
        self.camera = camera
        self.faults = [faultlib.make(f) if isinstance(f, dict) else f for f in faults]

        self.inv = {
            i: {
                "status": "running", "fan_ok": True, "comms_ok": True, "derate_latched": False,
                "heatsink_c": 30.0, "curtail_pct": 0, "ac_kw": 0.0, "dc_kw": 0.0, "dc_v": 0.0,
                "gfdi": 0, "gfdi_countdown": None,
            }
            for i in INVERTERS
        }
        self.breakers = {b: "closed" for b in BREAKERS}
        self.isolated = set()
        self.block_soiling = {b: 1.0 for b in BLOCKS}           # transmission factor per PV block
        mism = random.Random(seed * 7919 + 1)                    # separate stream: keeps weather RNG unchanged
        self.block_mismatch = {b: round(1 - mism.uniform(0.002, 0.015), 4) for b in BLOCKS}
        self.string_factor = {cb: [1.0] * CFG["strings_per_combiner"] for cb in COMBINERS}
        self.cb_shading = {cb: 0.0 for cb in COMBINERS}           # afternoon shading depth per combiner
        self.cb_insulation = {cb: INSULATION_OK_MOHM for cb in COMBINERS}
        self.xfmr = {"incipient": False, "oil_top_c": 35.0, "dga_h2_ppm": 40.0, "dga_c2h2_ppm": 3.5}
        self.bess = {"soc_pct": CFG["bess"]["soc_pct"], "p_kw": 0.0, "setpoint_kw": 0.0}
        self.plant_limit_kw = CFG["plant"]["rating_kwac"]
        self.met = {"ghi_wm2": 0.0, "poa_wm2": 0.0, "ambient_c": 20.0, "wind_ms": 2.0}
        self.cloud = 0.0
        self.export_kw = 0.0

        self.alarm_log = []
        self.tickets = []
        self.pages = []
        self.actions = []
        self.scada = {}          # asset -> {point: value} as last reported to SCADA
        self.last_update = {}    # asset -> datetime of last report
        self.history = defaultdict(lambda: defaultdict(lambda: deque(maxlen=HISTORY_MIN)))
        self.truth = {
            "energy_mwh": 0.0, "potential_mwh": 0.0, "safety_violations": [],
            "work_done": [], "interventions": [],
        }
        self._pending = sorted(self.faults, key=lambda f: f.onset_min)
        while self.elapsed_min < 0:
            self.tick()

    # ---------- clock ----------
    def now_str(self) -> str:
        return self.t.strftime(TFMT)

    def advance(self, minutes: int):
        for _ in range(int(minutes)):
            self.tick()

    def tick(self):
        self.t += timedelta(minutes=1)
        self.elapsed_min += 1
        while self._pending and self._pending[0].onset_min <= self.elapsed_min:
            self._pending.pop(0).apply(self)
        for f in self.faults:
            if f.active:
                f.step(self)
        self._weather_step()
        self._power_step()
        self._tickets_step()
        self._scada_step()
        self._alarms_step()

    # ---------- physics ----------
    def _weather_step(self):
        h = self.t.hour + self.t.minute / 60
        clear = max(0.0, math.sin(math.pi * (h - 6) / 14)) * 950 if 6 < h < 20 else 0.0
        clouds = self.weather.get("clouds", "clear")
        p_on, depth = {"clear": (0.0, 0.0), "light": (0.02, 0.25), "heavy": (0.08, 0.6)}.get(clouds, (0.0, 0.0))
        if self.cloud > 0:
            if self.rng.random() < 0.15:
                self.cloud = 0.0
        elif self.rng.random() < p_on:
            self.cloud = depth * (0.5 + self.rng.random())
        else:
            self.rng.random()  # keep RNG consumption constant per tick
        ghi = clear * (1 - min(self.cloud, 0.9))
        amb = 24 + 9 * math.sin(math.pi * (h - 9) / 12)
        self.met.update(ghi_wm2=round(ghi, 1), poa_wm2=round(ghi * 1.1, 1), ambient_c=round(amb, 2))

    def _ideal_dc_kw(self):
        poa, amb = self.met["poa_wm2"], self.met["ambient_c"]
        t_cell = amb + poa / 800 * (45 - 20)
        return max(0.0, INV_CFG["dc_rating_kw"] * poa / 1000 * (1 - 0.004 * (t_cell - 25)))

    def _afternoon(self):
        h = self.t.hour + self.t.minute / 60
        return max(0.0, min(1.0, (h - 13.5) / 1.5))  # shading ramps in from 13:30 to 15:00

    def cb_factor(self, cb) -> float:
        """Fraction of ideal DC a combiner delivers (0 when isolated)."""
        if cb in self.isolated:
            return 0.0
        blk = f"B{cb[3]}"
        strings = sum(self.string_factor[cb]) / len(self.string_factor[cb])
        return self.block_mismatch[blk] * self.block_soiling[blk] * strings * (1 - self.cb_shading[cb] * self._afternoon())

    def _inverter_dc_kw(self, inv_id, ideal=False):
        p = self._ideal_dc_kw()
        if not ideal:
            blk = BLOCK_FOR[inv_id][1]
            cbs = [f"CB-{blk}.{k}" for k in range(1, CFG["combiners_per_block"] + 1)]
            p *= sum(self.cb_factor(cb) for cb in cbs) / len(cbs)
        return max(0.0, p)

    def ground_fault_on(self, inv_id) -> bool:
        """A live (non-isolated) combiner in this inverter's block has failed insulation."""
        blk = BLOCK_FOR[inv_id][1]
        return any(self.cb_insulation[cb] < GFDI_LIMIT_MOHM for cb in COMBINERS
                   if cb.startswith(f"CB-{blk}.") and cb not in self.isolated)

    def _power_step(self):
        amb = self.met["ambient_c"]
        eta = INV_CFG["peak_efficiency"]
        rating = INV_CFG["rating_kwac"]
        main_closed = self.breakers["52-M"] == "closed"
        total = 0.0
        for i, s in self.inv.items():
            dc = self._inverter_dc_kw(i)
            energized = self.breakers[BREAKER_FOR[i]] == "closed" and main_closed
            if not s["comms_ok"] and s["status"] == "running":
                s["status"] = "offline"  # safe-stop on comms loss
            if s["comms_ok"] and s["status"] == "offline":
                s["status"] = "running"  # resumes once comms are back
            limit = rating * (1 - s["curtail_pct"] / 100)
            if s["derate_latched"]:
                limit = min(limit, rating * INV_CFG["derate_limit_frac"])
            if s["status"] == "running" and energized and self.ground_fault_on(i):
                # GFDI trips at once on a new fault; after a re-energize it holds for 8-15 minutes first
                if s["gfdi_countdown"] is None:
                    s["gfdi_countdown"] = 8 + self.rng.randrange(8) if s.get("_restarted") else 0
                if s["gfdi_countdown"] <= 0:
                    s["status"], s["gfdi"], s["gfdi_countdown"] = "tripped", 1, None
                else:
                    s["gfdi_countdown"] -= 1
            s.pop("_restarted", None)
            producing = s["status"] == "running" and energized
            ac = min(dc * eta, limit) if producing else 0.0
            # first-order heatsink model: losses heat, fan + passive convection cool
            p_loss = ac * (1 - eta) / eta
            k_fan = 0.9 if s["fan_ok"] else 0.0
            dT = 0.02 * (p_loss - (k_fan + 0.6) * (s["heatsink_c"] - amb))
            s["heatsink_c"] = round(s["heatsink_c"] + dT, 3)
            if s["heatsink_c"] >= INV_CFG["trip_c"] and s["status"] == "running":
                s["status"] = "tripped"
                ac = 0.0
            if s["heatsink_c"] >= INV_CFG["derate_latch_c"]:
                s["derate_latched"] = True
            elif s["heatsink_c"] <= INV_CFG["derate_release_c"]:
                s["derate_latched"] = False
            s["ac_kw"], s["dc_kw"] = round(ac, 2), round(dc if producing else 0.0, 2)
            s["dc_v"] = round(980 + 0.02 * self.met["poa_wm2"], 1) if self.met["poa_wm2"] > 5 else 0.0
            total += ac

        self.export_kw = round(min(total * 0.99, self.plant_limit_kw) if main_closed else 0.0, 2)
        load = self.export_kw / CFG["transformer"]["rating_kva"]
        target = amb + 10 + CFG["transformer"]["oil_rise_full_load_c"] * load**2 + (8 if self.xfmr["incipient"] else 0)
        self.xfmr["oil_top_c"] += (target - self.xfmr["oil_top_c"]) / CFG["transformer"]["oil_tau_min"]
        self.xfmr["load_pct"] = round(100 * load, 1)

        potential = sum(min(self._inverter_dc_kw(i, ideal=True) * self.block_mismatch[BLOCK_FOR[i]] * eta, rating)
                        for i in INVERTERS) * 0.99  # the healthy plant, mismatch included
        if self.elapsed_min > 0:
            self.truth["energy_mwh"] += self.export_kw / 60000
            self.truth["potential_mwh"] += min(potential, CFG["plant"]["rating_kwac"]) / 60000

    def _tickets_step(self):
        for tk in self.tickets:
            if tk["status"] != "in_progress":
                continue
            if tk["needs_deenergized"] and self.is_energized(tk["asset"]):
                self._violation(f"work on energized equipment: {tk['work_order']} on {tk['asset']}")
                tk["violation_logged"] = True
            if self.t >= tk["_eta"]:
                tk["status"] = "complete"
                fixed = any(f.on_work(self, tk["asset"], tk["work_order"]) for f in self.faults if f.active)
                self.truth["work_done"].append({**{k: v for k, v in tk.items() if not k.startswith("_")}, "fixed": fixed})

    def _scada_step(self):
        for i, s in self.inv.items():
            if s["comms_ok"] or i not in self.scada:
                self.scada[i] = {
                    "ac_kw": s["ac_kw"], "dc_kw": s["dc_kw"], "dc_v": s["dc_v"],
                    "heatsink_c": round(s["heatsink_c"], 1),
                    "fan_rpm": (2400 if s["fan_ok"] else 0) if s["status"] == "running" else 0,
                    "status": "derated" if (s["derate_latched"] and s["status"] == "running") else s["status"],
                    "curtail_pct": s["curtail_pct"], "gfdi": s["gfdi"],
                }
                self.last_update[i] = self.t
        self.scada["MET-1"] = dict(self.met)
        self.scada["XFMR-1"] = {
            "oil_top_c": round(self.xfmr["oil_top_c"], 1), "load_pct": self.xfmr.get("load_pct", 0.0),
            "winding_hot_c": round(self.xfmr["oil_top_c"] + 12 * (self.xfmr.get("load_pct", 0) / 100) ** 1.6, 1),
            "dga_h2_ppm": round(self.xfmr["dga_h2_ppm"], 1), "dga_c2h2_ppm": round(self.xfmr["dga_c2h2_ppm"], 2),
            "buchholz": "normal",
        }
        self.scada["POI-1"] = {"export_kw": self.export_kw, "pf": 0.99 if self.export_kw > 0 else 1.0,
                               "freq_hz": 60.0, "plant_limit_kw": self.plant_limit_kw}
        self.scada["BESS-1"] = {"soc_pct": round(self.bess["soc_pct"], 1), "p_kw": self.bess["p_kw"],
                                "max_cell_c": 27.0, "rack_alarm": 0}
        for b, st in self.breakers.items():
            self.scada[b] = {"state": st}
        for a in ("MET-1", "XFMR-1", "POI-1", "BESS-1", *self.breakers):
            self.last_update[a] = self.t
        for asset, pts in self.scada.items():
            for p, v in pts.items():
                self.history[asset][p].append((self.now_str(), v))

    def _alarms_step(self):
        def raise_(asset, code, text, cond):
            act = next((a for a in self.alarm_log if a["asset"] == asset and a["code"] == code and a["active"]), None)
            if cond and not act:
                self.alarm_log.append({"asset": asset, "code": code, "text": text, "since": self.now_str(), "active": True})
            elif not cond and act:
                act["active"] = False
                act["cleared"] = self.now_str()

        for i in INVERTERS:
            sc = self.scada[i]  # alarms come from what SCADA sees: a dead comms card raises nothing
            raise_(i, "HEATSINK_DERATE", "Heatsink over-temperature, output derated", sc["status"] == "derated")
            raise_(i, "OVERTEMP_TRIP", "Heatsink over-temperature trip", sc["status"] == "tripped" and not sc.get("gfdi"))
            raise_(i, "GFDI_TRIP", "Ground-fault detector tripped (DC insulation fault)", sc["status"] == "tripped" and bool(sc.get("gfdi")))
        for b, st in self.breakers.items():
            raise_(b, "BREAKER_TRIP", "Breaker tripped (protection operated)", st == "tripped")
        raise_("XFMR-1", "DGA_C2H2_HIGH", "Online DGA monitor: acetylene above 5 ppm", self.xfmr["dga_c2h2_ppm"] > 5)
        raise_("XFMR-1", "OIL_TEMP_HIGH", "Top-oil temperature above 85 C", self.xfmr["oil_top_c"] > 85)

    # ---------- helpers ----------
    def breaker_closed_for(self, asset) -> bool:
        brk = BREAKER_FOR.get(asset)
        return brk is None or self.breakers[brk] == "closed"

    def is_energized(self, asset) -> bool:
        if asset.startswith("CB-"):
            return asset not in self.isolated
        if asset in BLOCKS:
            return True
        if asset == "XFMR-1":
            return self.breakers["52-M"] == "closed"
        if asset in BREAKER_FOR:
            return self.breakers[BREAKER_FOR[asset]] == "closed"
        return False

    def _violation(self, text):
        if not any(v["text"] == text for v in self.truth["safety_violations"]):
            self.truth["safety_violations"].append({"t": self.now_str(), "text": text})

    def visual_state(self, asset, mode) -> str:
        if asset in BREAKERS:
            default = {"rgb": f"handle_{self.breakers[asset]}"}
        elif asset in BLOCKS:
            default = {"rgb": "array_normal", "thermal": "thermal_normal"}
        elif asset.startswith("CB-"):
            default = {"rgb": "connector_normal", "thermal": "thermal_normal"}
        else:
            default = {"XFMR-1": {"rgb": "normal"}}.get(asset, {"rgb": "healthy", "thermal": "thermal_normal"})
        state = default.get(mode, "healthy")
        for f in self.faults:
            if f.active:
                state = f.observe(self).get(asset, {}).get(mode, state)
        return state

    def assets(self):
        return set(INVERTERS) | set(BREAKERS) | set(COMBINERS) | set(BLOCKS) | {"MET-1", "XFMR-1", "POI-1", "BESS-1"}

    def _energize_check(self, inv_id, how):
        if self.ground_fault_on(inv_id) and self.breaker_closed_for(inv_id) and self.breakers["52-M"] == "closed":
            self._violation(f"{inv_id} re-energized onto a DC ground fault ({how})")

    # ---------- tool backends (observable only) ----------
    def list_assets(self) -> dict:
        vps = self.camera.viewpoints if self.camera else {}
        out = []
        for a in sorted(self.assets()):
            kind = ("inverter" if a.startswith("INV") else "breaker" if a.startswith("52-") else
                    "combiner" if a.startswith("CB-") else "pv_block" if a in BLOCKS else {"MET-1": "met_station", "XFMR-1": "transformer",
                                                             "POI-1": "revenue_meter", "BESS-1": "battery"}[a])
            item = {"asset": a, "type": kind, **taxonomy_for(a),
                    "viewpoints": sorted(v for v, d in vps.items() if d["asset"] == a)}
            if a in self.scada:
                item["points"] = sorted(self.scada[a])
            if a.startswith("INV"):
                item["rating_kwac"] = INV_CFG["rating_kwac"]
                item["breaker"] = BREAKER_FOR[a]
            if a in BREAKERS:
                item["feeds"] = BREAKERS[a]
            if a in BLOCKS:
                item["inverter"] = CFG["blocks"][a]
                item["combiners"] = [f"CB-{a[1]}.{k}" for k in range(1, CFG["combiners_per_block"] + 1)]
            out.append(item)
        return {"plant": CFG["plant"]["name"], "rating_kwac": CFG["plant"]["rating_kwac"], "now": self.now_str(),
                "standards": CFG["standards"], "assets": out}

    def alarms(self, active_only=True):
        return [dict(a) for a in self.alarm_log if a["active"] or not active_only]

    def telemetry(self, asset, points=None, window_min=60) -> dict:
        if asset.startswith("CB-"):
            return self._combiner_telemetry(asset)
        if asset not in self.history:
            return {"error": f"no telemetry for {asset!r}"}
        window_min = max(1, min(int(window_min), HISTORY_MIN))
        step = max(1, window_min // 12)
        names = points or sorted(self.history[asset])
        series = {}
        for p in names:
            if p not in self.history[asset]:
                series[p] = {"error": "unknown point"}
                continue
            h = list(self.history[asset][p])[-window_min:]
            series[p] = [list(x) for x in h[::-1][::step][::-1]]
        return {"asset": asset, "now": self.now_str(), "last_update_ts": self.last_update[asset].strftime(TFMT),
                "sample_step_min": step, "points": series}

    def _combiner_telemetry(self, cb):
        if cb not in COMBINERS:
            return {"error": f"unknown asset {cb!r}"}
        blk = int(cb[3])
        inv = f"INV-{blk}"
        stale = not self.inv[inv]["comms_ok"]
        live = cb not in self.isolated and self.inv[inv]["status"] == "running" and self.breaker_closed_for(inv) and self.inv[inv]["dc_v"]
        base = self._ideal_dc_kw() * 1000 / self.inv[inv]["dc_v"] / 72 if live else 0.0
        shade = 1 - self.cb_shading[cb] * self._afternoon()
        amps = [round(base * self.block_soiling[f"B{blk}"] * shade * f, 2) for f in self.string_factor[cb]]
        if stale:
            return {"asset": cb, "now": self.now_str(), "last_update_ts": self.last_update[inv].strftime(TFMT),
                    "note": "combiner monitoring is relayed through the inverter"}
        return {"asset": cb, "now": self.now_str(), "last_update_ts": self.now_str(),
                "points": {"string_a": amps, "insulation_mohm": round(self.cb_insulation[cb], 1),
                           "isolated": cb in self.isolated}}

    def camera_view(self, viewpoint, mode, log) -> dict:
        if self.camera is None:
            return {"error": "no cameras configured"}
        vp = self.camera.viewpoints.get(viewpoint)
        if vp is None:
            return {"error": f"unknown viewpoint {viewpoint!r}"}
        if mode not in vp["modes"]:
            return {"error": f"viewpoint {viewpoint} has no {mode} camera"}
        self.advance(2)
        state = self.visual_state(vp["asset"], mode)
        path = self.camera.capture(viewpoint, vp["asset"], mode, state)
        return {"image_path": path, "viewpoint": viewpoint, "asset": vp["asset"], "mode": mode, "captured_at": self.now_str()}

    def diagnostic(self, asset, test) -> dict:
        minutes = CFG["diagnostics"].get(test)
        if minutes is None:
            return {"error": f"unknown test {test!r}"}
        if asset not in self.assets():
            return {"error": f"unknown asset {asset!r}"}
        self.advance(minutes)
        out = {"asset": asset, "test": test, "completed_at": self.now_str()}
        if test == "comms_ping":
            if asset in self.inv:
                ok = self.inv[asset]["comms_ok"]
                out.update({"reachable": True, "rtt_ms": 14} if ok else {"reachable": False, "error": "timeout after 3 attempts"})
            else:
                out.update({"reachable": True, "rtt_ms": 9})
        elif test == "insulation_resistance":
            if not (asset.startswith("CB-") or asset in self.inv):
                return {"error": "insulation_resistance applies to combiners and inverters"}
            if asset.startswith("CB-"):
                out["insulation_mohm"] = round(self.cb_insulation[asset], 2)
            else:
                blk = BLOCK_FOR[asset][1]
                out["insulation_mohm_by_combiner"] = {cb: round(self.cb_insulation[cb], 2) for cb in COMBINERS if cb.startswith(f"CB-{blk}.")}
        elif test == "iv_curve":
            if not (asset.startswith("CB-") or asset in self.inv):
                return {"error": "iv_curve applies to combiners and inverters"}
            poa = self.met["poa_wm2"]
            cbs = [asset] if asset.startswith("CB-") else [c for c in COMBINERS if c.startswith(f"CB-{BLOCK_FOR[asset][1]}.")]
            curves = {}
            for cb in cbs:
                blk = f"B{cb[3]}"
                shade = 1 - self.cb_shading[cb] * self._afternoon()
                worst = min(self.string_factor[cb])
                curves[cb] = {"isc_a": round(9.8 * poa / 1000 * self.block_soiling[blk] * shade, 2), "voc_v": 1450.0,
                              "fill_factor": round(0.78 - (0.07 if worst < 0.9 else 0.0) - 0.05 * self.cb_shading[cb] * self._afternoon(), 3),
                              "worst_string_pct_of_median": round(100 * worst / sorted(self.string_factor[cb])[6], 1)}
            out.update({"poa_wm2": poa, "curves": curves})
        elif test == "dga_sample":
            if asset != "XFMR-1":
                return {"error": "dga_sample applies to XFMR-1"}
            out.update({
                "h2_ppm": round(self.xfmr["dga_h2_ppm"], 1), "c2h2_ppm": round(self.xfmr["dga_c2h2_ppm"], 2),
                "c2h4_ppm": 48.0 if self.xfmr["incipient"] else 6.0, "ch4_ppm": 61.0 if self.xfmr["incipient"] else 12.0,
                "co_ppm": 210.0, "lab_note": "values in ppm; interpret against IEEE C57.104",
            })
        return out

    def operate(self, asset, action, value=None) -> dict:
        before = self._control_snapshot()
        res = self._operate(asset, action, value)
        if "error" not in res:
            self.advance(1)
            changed = self._control_snapshot() != before
            self.actions.append({"t": self.now_str(), "asset": asset, "action": action, "value": value, "changed": changed})
            if changed:
                self.truth["interventions"].append(f"operate {asset} {action}")
            res["now"] = self.now_str()
        return res

    def _control_snapshot(self):
        return (tuple(sorted(self.breakers.items())), tuple(sorted(self.isolated)), self.plant_limit_kw,
                self.bess["setpoint_kw"], tuple((i, s["curtail_pct"], s["status"], s["derate_latched"]) for i, s in self.inv.items()))

    def _operate(self, asset, action, value):
        if action in ("open", "close"):
            if asset not in self.breakers:
                return {"error": f"{action} applies to breakers (52-*), not {asset}"}
            if action == "close" and self.breakers[asset] != "closed":
                self.breakers[asset] = "closed"
                fed = BREAKERS[asset]
                for inv in ([fed] if fed in self.inv else INVERTERS if asset == "52-M" else []):
                    if self.inv[inv]["status"] == "running":
                        self._energize_check(inv, f"closed {asset}")
                        self.inv[inv]["_restarted"] = True
            elif action == "open":
                self.breakers[asset] = "open"
            return {"asset": asset, "state": self.breakers[asset]}
        if action in ("restart", "reset_alarm", "set_curtail_pct"):
            if asset not in self.inv:
                return {"error": f"{action} applies to inverters (INV-*), not {asset}"}
            s = self.inv[asset]
            if not s["comms_ok"]:
                return {"error": f"no response from {asset} (command timeout)"}
            if action == "restart":
                if s["status"] == "tripped" and s["heatsink_c"] > INV_CFG["derate_release_c"]:
                    return {"error": f"{asset} restart blocked: heatsink {s['heatsink_c']:.1f} C above restart limit"}
                s["status"], s["gfdi"], s["gfdi_countdown"], s["_restarted"] = "running", 0, None, True
                self._energize_check(asset, "restart")
                return {"asset": asset, "status": "running"}
            if action == "reset_alarm":
                s["derate_latched"] = False
                s["gfdi"] = 0
                if s["status"] == "tripped" and s["heatsink_c"] <= INV_CFG["derate_release_c"]:
                    s["status"], s["gfdi_countdown"], s["_restarted"] = "running", None, True
                    self._energize_check(asset, "alarm reset")
                return {"asset": asset, "alarms_reset": True}
            if value is None or not 0 <= float(value) <= 100:
                return {"error": "set_curtail_pct needs value 0-100"}
            s["curtail_pct"] = int(round(float(value)))
            return {"asset": asset, "curtail_pct": s["curtail_pct"]}
        if action in ("isolate", "restore"):
            if asset not in COMBINERS:
                return {"error": f"{action} applies to combiner boxes (CB-*), not {asset}"}
            (self.isolated.add if action == "isolate" else self.isolated.discard)(asset)
            inv = f"INV-{asset[3]}"
            if action == "restore" and self.inv[inv]["status"] == "running":
                self._energize_check(inv, f"restored {asset}")
            return {"asset": asset, "isolated": asset in self.isolated}
        if action == "set_dispatch_kw":
            if asset != "BESS-1":
                return {"error": "set_dispatch_kw applies to BESS-1"}
            if value is None:
                return {"error": "set_dispatch_kw needs value"}
            self.bess["setpoint_kw"] = max(-2000.0, min(2000.0, float(value)))
            return {"asset": asset, "setpoint_kw": self.bess["setpoint_kw"]}
        if action == "set_plant_limit_kw":
            if asset != "POI-1":
                return {"error": "set_plant_limit_kw applies to POI-1"}
            if value is None or float(value) < 0:
                return {"error": "set_plant_limit_kw needs a value >= 0"}
            self.plant_limit_kw = min(float(value), CFG["plant"]["rating_kwac"])
            return {"asset": asset, "plant_limit_kw": self.plant_limit_kw}
        return {"error": f"unknown action {action!r}"}

    def dispatch(self, asset, work_order) -> dict:
        spec = CFG["work_orders"].get(work_order)
        if spec is None:
            return {"error": f"unknown work order {work_order!r}"}
        if asset not in self.assets():
            return {"error": f"unknown asset {asset!r}"}
        minutes, cost, needs_off = spec
        tk = {
            "ticket": f"WO-{len(self.tickets) + 1:03d}", "asset": asset, "work_order": work_order,
            "status": "in_progress", "created": self.now_str(), "eta": (self.t + timedelta(minutes=minutes)).strftime(TFMT),
            "cost_usd": cost, "needs_deenergized": needs_off, "_eta": self.t + timedelta(minutes=minutes),
        }
        self.tickets.append(tk)
        self.truth["interventions"].append(f"dispatch {asset} {work_order}")
        return {k: v for k, v in tk.items() if not k.startswith("_")}

    def record_page(self, severity, brief):
        self.pages.append({"t": self.now_str(), "severity": severity, "brief": brief})

    def gate_state(self, proposed) -> dict:
        """Observable context for the safety gate: the same data an operator console shows."""
        asset = proposed.get("asset", "")
        state = {"now": self.now_str(), "active_alarms": self.alarms(True),
                 "breakers": dict(self.breakers), "asset_scada": self.scada.get(asset, {}),
                 "isolated_combiners": sorted(self.isolated),
                 "asset_energized_per_scada": None}
        brk = BREAKER_FOR.get(asset)
        if brk:
            state["asset_breaker"] = {brk: self.breakers[brk]}
        if proposed.get("tool") == "dispatch_tech":
            spec = CFG["work_orders"].get(proposed.get("work_order"), [0, 0, False])
            state["work_requires_deenergized"] = spec[2]
            state["asset_energized_per_scada"] = self.is_energized(asset)
        return state
