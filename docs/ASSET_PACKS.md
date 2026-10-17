# Per-asset data packs for the twin (simulated, precomputed)

When the agent diagnoses a fault, the twin flies to the asset and opens it. The asset panel needs that asset's sensor
history, a camera frame, an infrared frame with the hot spot boxed in deg C, and a flag with a reason and a call to action.
All of it is precomputed in `assets/demo/asset_packs/`, so the page only fetches static files and makes no live calls.
Everything here is simulated, so label it "simulated" if asked.

Regenerate: `python -m scripts.make_asset_packs` (all scenarios, a few minutes, no network, no model calls);
`--scenarios s03_fan_relapse` for one. The camera views of assets with no station come from
`python -m scripts.render_anchor_views` (Chromium); without them those assets get a plain card.
Test: `tests/sim/test_asset_packs.py`.

## Scenarios

| Scenario | As of | Focus asset | Status | Use |
|---|---|---|---|---|
| `s06_stale_scada` | 2026-07-14 11:00 | INV-3 | fault | end-to-end demo, golden run 1 (`docs/DEMO-E2E.md`) |
| `s06b_stale_scada_inv2` | 2026-07-15 10:45 | INV-2 | fault | end-to-end demo, golden run 2 |
| `s03_fan_relapse` | 2026-07-14 12:00 | INV-1 | hot | dashboard demo (fan failure, heatsink hot) |
| `s08_hotspot` | 2026-07-14 11:30 | CB-1.5 | hot | the most striking infrared hot spot |

Every pack covers all 42 anchors in `assets/splats/scene.json`. Exactly one asset per scenario is flagged; the rest are `ok`.

## Files

```
assets/demo/asset_packs/
  index.json                          {"schema": "docs/ASSET_PACKS.md", "scenarios": {<id>: {index, as_of, focus, goal, counts}}}
  _views/<ASSET>.jpg, _views/index.json   splat camera view per anchor (scripts/render_anchor_views.py)
  <scenario>/index.json               the scenario index below
  <scenario>/<ASSET>/sensors.json     {"asset", "step_min": 1, "units": {point: unit}, "t": [...], "series": {point: [...]}}
  <scenario>/<ASSET>/sensors.csv      the same, one row per minute
  <scenario>/<ASSET>/camera.jpg       800x600 (s06 inverters: Will's 1024x768 display frames)
  <scenario>/<ASSET>/infrared.png     892x600 ironbow frame with Bx1 box, max label, HOT SPOT / NO HEAT tag, deg C scale bar
```

All paths inside the JSON are repo-relative (`assets/demo/asset_packs/...`), so the page prefixes its static root.

## `<scenario>/index.json`

```jsonc
{
  "scenario": "s03_fan_relapse", "goal": "...", "as_of": "2026-07-14T12:00", "plant": "GridTwin Demo Solar",
  "focus": "INV-1",                       // the asset the agent jumps to and opens
  "faults": [{"type": "F3_fan_failure", "asset": "INV-1"}],
  "ambient_c": 31.4, "poa_wm2": 1012.0, "counts": {"ok": 41, "hot": 1, "fault": 0}, "simulated": true,
  "assets": {
    "INV-1": {
      "kind": "central_inverter", "label": "INV-1 central inverter", "anchor": [150, 1.45, 30],   // scene.json xyz
      "taxonomy": {"system": "...", "subsystem": "...", "component": "..."},
      "status": "hot",                    // ok | hot | fault
      "reason": "Cooling fan dead: 0 rpm, heatsink 53.5 °C, output derated",
      "fault": "F3_fan_failure",          // null when ok
      "cta": {                            // null when ok
        "label": "Dispatch replace_fan_filter",
        "steps": [{"tool": "dispatch_tech", "asset": "INV-1", "work_order": "replace_fan_filter"}],
        "work_order": "replace_fan_filter", "minutes": 60, "usd": 1800, "needs_deenergized": true,
        "isolate_first": "52-I1"          // only for inverters that need de-energizing
      },
      "requirements": ["REQ-INV-01", ...], "failing_requirements": ["REQ-INV-02", "REQ-INV-03"],  // ids in plant.yaml / codes.html
      "latest": {"ac_kw": 750.0, "heatsink_c": 53.5, "fan_rpm": 0, "status": "derated",
                 "last_update": "2026-07-14T12:00", "data_age_min": 0, ...},
      "alarms": [{"asset": "INV-1", "code": "HEATSINK_DERATE", "text": "...", "since": "...", "active": true}],
      "sensors": {"json": ".../sensors.json", "csv": ".../sensors.csv", "points": ["ac_kw", ...], "from": "...", "to": "..."},
      "camera": {"file": ".../camera.jpg", "captured": "...",
                 "source": {"kind": "splat_render|splat_view|photo_bank|s06_case|placeholder", "station": "...", "state": "..."}},
      "infrared": {"file": ".../infrared.png", "captured": "...", "span_c": [22, 75],
                   "Bx1": {"label": "INV-1 body", "box": [x0, y0, x1, y1], "max_c": 52.8, "min_c": 37.1, "mean_c": 44.4,
                           "max_xy": [402, 300]},       // box and max_xy are pixels in the 800x600 thermal area
                   "frame": {"max_c": ..., "min_c": ...}}
    }
  }
}
```

For `s06_stale_scada` the inverters' camera and infrared are Will's s06 case frames (`assets/demo/s06_case`): there
`Bx1.box` is in his 320x240 radiometric grid (`"grid": [320, 240]`), `Sp1` (louvre) and `Sp2` (display) are spot readings,
and `radiometric` points at his CSV.

## Points per asset kind

- Inverters `INV-n`: `ac_kw dc_kw dc_v heatsink_c fan_rpm status curtail_pct` as SCADA reported them (a dead comms card is a flat line; `latest.data_age_min` shows how stale).
- Transformer `XFMR-1`: `oil_top_c load_pct winding_hot_c dga_h2_ppm dga_c2h2_ppm buchholz`.
- Meter `POI-1`: `export_kw pf freq_hz plant_limit_kw`. Met `MET-1`: `ghi_wm2 poa_wm2 ambient_c wind_ms`. `BESS-1`: `soc_pct p_kw max_cell_c rack_alarm`.
- Breakers `52-*`: `state`.
- Combiners `CB-b.k`: `string_a_total string_a_min string_a_max insulation_mohm` (null while the inverter's comms are down).
- Blocks `B1..B4`: `dc_kw module_c yield_pct_of_peers`.

## How the data is made

- Sensors: `gridtwin.sim`, the scenario's seed, run to its start time, up to 240 minutes of history.
- Status and CTA: the sim's hidden active faults; the CTA is the scenario's expected fix (`expected.resolved_by`) with the work order's time, cost and de-energize flag from `plant.yaml`.
- Camera: the splat re-rendered from the asset's station in its current visual state (`assets/splats/renders`), else the asset's own splat view, else the photo bank.
- Infrared: modelled on the camera frame from the sim's true temperatures (inverter heatsink, transformer top oil, module cell temperature, a hot string at about 99 °C), not from a thermal camera.
