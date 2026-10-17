# Data in this folder

All times are plant local time on 2026-07-14.

## sensors/

`scada_1min.csv` holds SCADA as the plant dashboard received it, one row per minute from 09:01 to 11:00.

| Column | Unit | Meaning |
|---|---|---|
| `t` | | minute |
| `poi_export_kw` | kW | revenue meter POI-1, power exported to the grid |
| `poa_wm2` | W/m2 | plane-of-array irradiance, met station |
| `ambient_c` | C | air temperature, met station |
| `wind_ms` | m/s | wind speed, met station |
| `INV-n_ac_kw` | kW | inverter AC output |
| `INV-n_dc_kw` | kW | inverter DC input |
| `INV-n_dc_v` | V | inverter DC bus voltage |
| `INV-n_heatsink_c` | C | inverter heatsink temperature |
| `INV-n_fan_rpm` | rpm | inverter cooling fan speed |
| `INV-n_status` | | running, derated, tripped or stopped |
| `INV-n_last_update` | | when SCADA last received a report from the inverter |

`alarms.json` lists the active SCADA alarms at 11:00 and every alarm raised since 09:00.

## camera/

One frame per inverter from the fixed camera facing its cabinet door and display, taken at 11:00:04.
`index.json` gives each frame's camera id, asset and capture time.

## infrared/

One frame per inverter from the fixed thermal camera, taken at 11:00:20.
The thermal camera is a dual-sensor unit mounted with the display camera, and its frames are registered to the display camera's view.

- `INV-n_ir.png`: false-colour image (ironbow), rendered at 640x480 from the 320x240 radiometric grid, with visible-light edges blended in (MSX). The span is locked at 20 to 50 C for every frame, so frames compare directly. Sp1 is a spot meter on the upper louvre of the left door, Sp2 a spot meter on the display, Bx1 a box over the cabinet body.
- `INV-n_ir.csv`: the radiometric data, 240 rows by 320 columns of apparent temperature in C (row 0 is the top of the image), at emissivity 0.95, reflected temperature 20 C, distance 4 m.
- `index.json`: each frame's settings and its Sp1, Sp2 and Bx1 readings. Positions are in the 320x240 grid; Bx1's box is x0, y0, x1, y1 with x1 and y1 exclusive.

## objects.json

One entry per inverter with its block, breaker, rating, latest SCADA reading and the paths of its camera frame, thermal frame and radiometric data.
Paths start with `agent/` and are relative to the folder that holds this one.
