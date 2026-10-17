# Plant profile

## Overview

A 5 MWac solar farm with a battery and a substation. Four PV blocks feed four inverters (1.25 MWac each) onto a 34.5 kV collector bus. A 2 MW / 4 MWh battery (BESS-1) shares the bus. The bus steps up to 115 kV through one transformer (XFMR-1) and exports to the grid at the POI revenue meter (POI-1). A met station (MET-1) measures the weather. One sim tick is one minute.

## Topology

MET-1 (weather) | PV block Bb -> combiners CB-b.1..CB-b.6 (DC strings) -> INV-b -> AC breaker 52-Ib -> 34.5 kV collector bus <- 52-B1 <- BESS-1 | collector bus -> main breaker 52-M -> XFMR-1 (34.5/115 kV) -> POI-1 -> grid

## Asset table

| Asset | Count | Rating | Telemetry points | Controls |
|---|---|---|---|---|
| `MET-1` | 1 | met station | `ghi_wm2`, `poa_wm2`, `ambient_c`, `wind_ms` | none |
| `CB-b.k` (b = 1..4, k = 1..6) | 24 | 12 strings each | `string_a[1..12]`, `dc_v`, `insulation_mohm` | `isolate`, `restore` |
| `INV-1` .. `INV-4` | 4 | 1.25 MWac each | `ac_kw`, `dc_kw`, `dc_v`, `heatsink_c`, `fan_rpm`, `status`, `gfdi`, `last_update_ts` | `restart`, `reset_alarm`, `set_curtail_pct` |
| `52-I1` .. `52-I4`, `52-B1`, `52-M` | 6 | 34.5 kV breakers | `state`, `trip_count` | `open`, `close` |
| `BESS-1` | 1 | 2 MW / 4 MWh (PCS + racks) | `soc_pct`, `p_kw`, `max_cell_c`, `rack_alarm` | `set_dispatch_kw` |
| `XFMR-1` | 1 | 34.5/115 kV step-up | `oil_top_c`, `winding_hot_c`, `load_pct`, `dga_h2_ppm`, `dga_c2h2_ppm`, `buchholz` | none |
| `POI-1` | 1 | 5 MWac export point, revenue meter | `export_kw`, `pf`, `freq_hz` | `set_plant_limit_kw` |

## PV blocks and combiner boxes

Blocks B1, B2, B3 and B4. Block Bb has six combiner boxes CB-b.1 to CB-b.6 (for example CB-1.1 .. CB-1.6, CB-2.1 .. CB-2.6, CB-3.1 .. CB-3.6, CB-4.1 .. CB-4.6), 24 in all. Each combiner reports 12 string currents `string_a[1..12]` in amps, DC voltage `dc_v` and insulation resistance `insulation_mohm`. Block Bb feeds inverter INV-b. Controls: `isolate`, `restore`.

## Inverters

INV-1, INV-2, INV-3 and INV-4, 1.25 MWac each (5 MWac total), one per block. Points: `ac_kw`, `dc_kw`, `dc_v`, `heatsink_c`, `fan_rpm`, `status` (one of `running`, `derated`, `tripped`, `offline`), `gfdi`, `last_update_ts` (time of the last SCADA update). Controls: `restart`, `reset_alarm`, `set_curtail_pct`. INV-n connects to the collector bus through AC breaker 52-In.

## Breakers

Six 34.5 kV breakers: 52-I1, 52-I2, 52-I3, 52-I4 (one per inverter), 52-B1 (battery) and 52-M (main, between the collector bus and XFMR-1). Points: `state` (one of `open`, `closed`, `tripped`), `trip_count`. Controls: `open`, `close`.

## Battery BESS-1

2 MW / 4 MWh battery with a PCS and racks, on the collector bus through 52-B1. Points: `soc_pct`, `p_kw`, `max_cell_c`, `rack_alarm`. Control: `set_dispatch_kw`.

## Transformer XFMR-1

34.5/115 kV step-up transformer between 52-M and POI-1. Points: `oil_top_c`, `winding_hot_c`, `load_pct`, `dga_h2_ppm`, `dga_c2h2_ppm`, `buchholz`. No direct controls.

## POI-1 revenue meter

Point of interconnection with the grid and the revenue meter. Points: `export_kw`, `pf`, `freq_hz`. Control: `set_plant_limit_kw`.

## Met station MET-1

Points: `ghi_wm2` (global horizontal irradiance), `poa_wm2` (plane-of-array irradiance), `ambient_c`, `wind_ms`. No controls.
