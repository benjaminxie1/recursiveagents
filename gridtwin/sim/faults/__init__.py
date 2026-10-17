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


FAULT_TYPES = {cls.type: cls for cls in (FanFailure, CommsFailure, XfmrIncipient)}


def make(spec: dict) -> Fault:
    spec = dict(spec)
    cls = FAULT_TYPES[spec.pop("type")]
    return cls(**spec)
