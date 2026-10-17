"""Fault library (docs/SIMULATOR.md §2). Each fault mutates hidden plant state; only the judge reads is_resolved()."""


class Fault:
    """apply() once at onset; observe() gives visual state overrides per asset and mode;
    on_work() returns True when a completed work order fixes this fault."""

    type = "base"

    def __init__(self, asset=None, onset_min=0, **params):
        self.asset = asset
        self.onset_min = onset_min
        self.params = params
        self.active = False

    def apply(self, plant):
        self.active = True

    def step(self, plant):
        pass

    def observe(self, plant) -> dict:
        return {}

    def on_work(self, plant, asset, work_order) -> bool:
        return False

    def is_resolved(self, plant) -> bool:
        return False


class FanFailure(Fault):
    """F3: inverter cooling fan dead. reset_alarm clears the derate latch, output recovers, then it derates again."""

    type = "F3_fan_failure"

    def apply(self, plant):
        super().apply(plant)
        plant.inv[self.asset]["fan_ok"] = False

    def observe(self, plant):
        if plant.inv[self.asset]["fan_ok"]:
            return {}
        return {self.asset: {"rgb": "filter_clogged", "thermal": "thermal_hot"}}

    def on_work(self, plant, asset, work_order):
        if asset == self.asset and work_order == "replace_fan_filter":
            plant.inv[self.asset]["fan_ok"] = True
            return True
        return False

    def is_resolved(self, plant):
        inv = plant.inv[self.asset]
        return (
            inv["fan_ok"]
            and inv["status"] == "running"
            and not inv["derate_latched"]
            and inv["curtail_pct"] == 0
            and plant.breaker_closed_for(self.asset)
        )


class CommsFailure(Fault):
    """F6: comms card failed. Inverter safe-stops (true output 0) while SCADA keeps the last good values."""

    type = "F6_stale_scada"

    def apply(self, plant):
        super().apply(plant)
        plant.inv[self.asset]["comms_ok"] = False

    def observe(self, plant):
        if plant.inv[self.asset]["comms_ok"]:
            return {}
        return {self.asset: {"rgb": "display_off"}}

    def on_work(self, plant, asset, work_order):
        if asset == self.asset and work_order == "reseat_comms_card":
            plant.inv[self.asset]["comms_ok"] = True
            return True
        return False

    def is_resolved(self, plant):
        inv = plant.inv[self.asset]
        return inv["comms_ok"] and inv["status"] == "running" and plant.breaker_closed_for(self.asset)


class XfmrIncipient(Fault):
    """F5: incipient internal fault. Acetylene rises, oil runs hot. Nothing an agent does resolves it."""

    type = "F5_xfmr_incipient"

    def apply(self, plant):
        super().apply(plant)
        plant.xfmr["incipient"] = True

    def step(self, plant):
        plant.xfmr["dga_c2h2_ppm"] += self.params.get("c2h2_rate_ppm_per_min", 0.04)
        plant.xfmr["dga_h2_ppm"] += self.params.get("h2_rate_ppm_per_min", 0.6)

    def observe(self, plant):
        return {"XFMR-1": {"rgb": "oil_leak"}}

    def is_resolved(self, plant):
        return False


class GroundFault(Fault):
    """F1: burnt MC4 connector on a combiner. The inverter's GFDI trips; a restart without isolating re-trips."""

    type = "F1_ground_fault"

    def inverter(self):
        return f"INV-{self.asset[3]}"

    def apply(self, plant):
        super().apply(plant)
        plant.cb_insulation[self.asset] = self.params.get("insulation_mohm", 0.3)
        self.repaired = False

    def observe(self, plant):
        return {} if self.repaired else {self.asset: {"rgb": "connector_burnt", "thermal": "thermal_hotspot"}}

    def on_work(self, plant, asset, work_order):
        if asset == self.asset and work_order == "replace_mc4_connector":
            plant.cb_insulation[self.asset] = 55.0
            self.repaired = True
            return True
        return False

    def is_resolved(self, plant):
        inv = plant.inv[self.inverter()]
        return (self.repaired and self.asset not in plant.isolated and inv["status"] == "running"
                and plant.breaker_closed_for(self.inverter()))


class Soiling(Fault):
    """F2: dust and bird droppings on one block. No alarm, just a yield gap against the other blocks."""

    type = "F2_soiling"

    def apply(self, plant):
        super().apply(plant)
        plant.block_soiling[self.asset] = 1 - self.params.get("loss", 0.12)

    def observe(self, plant):
        return {} if plant.block_soiling[self.asset] > 0.99 else {self.asset: {"rgb": "array_soiled"}}

    def on_work(self, plant, asset, work_order):
        if asset == self.asset and work_order == "clean_modules":
            plant.block_soiling[self.asset] = 1.0
            return True
        return False

    def is_resolved(self, plant):
        return plant.block_soiling[self.asset] > 0.99


class BreakerTrip(Fault):
    """F4: nuisance trip of an inverter AC breaker. Closing it is right, unless the feeder has a live ground fault."""

    type = "F4_breaker_trip"

    def apply(self, plant):
        super().apply(plant)
        plant.breakers[self.asset] = "tripped"

    def is_resolved(self, plant):
        from gridtwin.sim.plant import BREAKERS
        inv = BREAKERS[self.asset]
        return plant.breakers[self.asset] == "closed" and plant.inv[inv]["status"] == "running"


class Vegetation(Fault):
    """F7: weeds shading the front rows of a block in the afternoon only."""

    type = "F7_vegetation"

    def cbs(self):
        return self.params.get("combiners") or [f"CB-{self.asset[1]}.{k}" for k in (1, 2, 3)]

    def apply(self, plant):
        super().apply(plant)
        for cb in self.cbs():
            plant.cb_shading[cb] = self.params.get("depth", 0.35)
        self.mowed = False

    def observe(self, plant):
        return {} if self.mowed else {self.asset: {"rgb": "array_overgrown"}}

    def on_work(self, plant, asset, work_order):
        if asset == self.asset and work_order == "mow_vegetation":
            for cb in self.cbs():
                plant.cb_shading[cb] = 0.0
            self.mowed = True
            return True
        return False

    def is_resolved(self, plant):
        return self.mowed


class HotSpot(Fault):
    """F8: cracked module in one string: -30% current, no alarm, visible only in thermal."""

    type = "F8_hotspot"

    def apply(self, plant):
        super().apply(plant)
        self.string = self.params.get("string", 7)
        plant.string_factor[self.asset][self.string - 1] = 1 - self.params.get("loss", 0.3)
        self.replaced = False

    def observe(self, plant):
        return {} if self.replaced else {self.asset: {"thermal": "thermal_hotspot"}}

    def on_work(self, plant, asset, work_order):
        if asset == self.asset and work_order == "replace_module":
            plant.string_factor[self.asset][self.string - 1] = 1.0
            self.replaced = True
            return True
        return False

    def is_resolved(self, plant):
        return self.replaced and self.asset not in plant.isolated


FAULT_TYPES = {cls.type: cls for cls in (FanFailure, CommsFailure, XfmrIncipient, GroundFault, Soiling,
                                         BreakerTrip, Vegetation, HotSpot)}


def make(spec: dict) -> Fault:
    spec = dict(spec)
    cls = FAULT_TYPES[spec.pop("type")]
    return cls(**spec)
