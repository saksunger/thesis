# KPI Schema and Canonical Naming

This is the single source of truth for KPI naming across the simulator, real datasets, and ML pipeline. Every column referenced in code or thesis text must be in this table.

Convention:
- Canonical names are `snake_case`.
- Units appear in the column name when ambiguous (`rsrp_dbm`, `time_s`, `ttt_ms`).
- "NULL" = field not present in that source.

---

## 1. Sample-level KPIs (per UE × cell × time)

| Canonical name        | Unit  | Simulator (MATLAB) | Bangladesh Parent CSV               | NordicDat CSV          | Notes |
|-----------------------|-------|--------------------|-------------------------------------|------------------------|-------|
| `time_s`              | s     | `t`                | `Timestamp` (HH:MM:SS.sss)          | `timestamp` (epoch s)  | Bangladesh has only time-of-day, no date inside file |
| `ue_id`               | int   | `ue.id`            | NULL (single device per file)       | NULL (single device)   | Assign per-file synthetic id for real datasets |
| `serving_cell_id`     | int   | `ue.serving.id`    | `Serving Cell ID`                   | `serving_cell_id`      | |
| `neighbor_cell_id`    | int   | `meas(k).cell.id`  | `Neighbor Cell ID`                  | NULL                   | Bangladesh has 1 best neighbor; NordicDat none |
| `rsrp_serving_dbm`    | dBm   | `meas.rsrp_serv`   | `Serving Cell RSRP` (verify unit)   | `rsrp`                 | **Bangladesh unit needs verification** — values look like positive integers, possibly offset/encoded |
| `rsrq_serving_db`     | dB    | `meas.rsrq_serv`   | `Serving Cell RSRQ`                 | `rsrq`                 | Same caveat |
| `sinr_serving_db`     | dB    | `meas.sinr_serv`   | `Serving Cell CINR`                 | `sinr`                 | Bangladesh names it CINR (LTE), treat as serving SINR equivalent |
| `rsrp_neighbor_dbm`   | dBm   | `meas.rsrp_nbr(k)` | `NeighCell RSRP`                    | NULL                   | |
| `rsrq_neighbor_db`    | dB    | `meas.rsrq_nbr(k)` | `NeighCell RSRQ`                    | NULL                   | |
| `rssi_serving_dbm`    | dBm   | derived            | NULL                                | `rssi`                 | |
| `ue_lat_deg`          | deg   | `ue.pos.lat`       | NULL                                | `latitude`             | |
| `ue_lon_deg`          | deg   | `ue.pos.lon`       | NULL                                | `longitude`            | |
| `ue_speed_mps`        | m/s   | `ue.vel.abs`       | NULL                                | `velocity_abs`         | |
| `ue_heading_deg`      | deg   | `ue.vel.heading`   | NULL                                | `heading`              | |
| `throughput_dl_kbps`  | kbps  | NULL (out of scope)| NULL                                | `throughput_downlink`  | Used only in calibration / drift features on real side |
| `throughput_ul_kbps`  | kbps  | NULL               | NULL                                | `throughput_uplink`    | |
| `ran_type`            | enum  | const `5G_NSA`/`LTE` per scenario | NULL (LTE inferred)        | `ran` (`LTE` / `5G-NSA`) | |
| `band`                | enum  | const per scenario | NULL                                | `band` (`LTE_B3` etc.) | |
| `operator_id`         | int   | NULL               | NULL                                | `operator`             | |

---

## 2. Event-level (per HO attempt / RLF / ping-pong)

| Canonical name        | Unit  | Simulator                 | Bangladesh Event Stats          | NordicDat                              | Notes |
|-----------------------|-------|---------------------------|---------------------------------|----------------------------------------|-------|
| `event_id`            | int   | autoinc                   | autoinc                         | autoinc                                | |
| `event_time_s`        | s     | `t`                       | `Time`                          | derived (cell transition timestamp)    | |
| `event_type`          | enum  | `HO_ATTEMPT`/`HO_SUCCESS`/`HO_FAIL`/`RLF`/`PING_PONG` | `Event` × `Result` | inferred from `serving_cell_id` transitions | NordicDat: no fail label, use "rapid bounce < T_pp" as proxy |
| `ue_id`               | int   | `ue.id`                   | NULL                            | NULL                                   | |
| `source_cell_id`      | int   | `src`                     | derived from `Freq` field       | `serving_cell_id[t-1]`                 | |
| `target_cell_id`      | int   | `tgt`                     | derived from `Freq` field       | `serving_cell_id[t]`                   | |
| `outcome`             | enum  | `success`/`fail`/`rlf`    | `Result` (`Attempt`/`Success`/`Fail`) | proxy: `fail` if returns to source within T_pp_s | |
| `ttt_ms`              | ms    | `cfg.ttt`                 | NULL                            | NULL                                   | **Real datasets cannot fill this** |
| `hysteresis_db`       | dB    | `cfg.hyst`                | NULL                            | NULL                                   | |
| `a3_offset_db`        | dB    | `cfg.a3_offset`           | NULL                            | NULL                                   | |
| `trigger_quantity`    | enum  | `RSRP`/`RSRQ`             | NULL                            | NULL                                   | |
| `rsrp_serv_pre_dbm`   | dBm   | snapshot                  | join from Parent on timestamp   | join from main feed                    | |
| `rsrp_tgt_pre_dbm`    | dBm   | snapshot                  | from `NeighCell RSRP`           | NULL                                   | |
| `rsrp_serv_post_dbm`  | dBm   | snapshot                  | join from Parent (post)         | join from main feed                    | |
| `t_to_next_event_s`   | s     | derived                   | derived                         | derived                                | |

---

## 3. Aggregate KPIs (per cell × 1-second bin)

| Canonical name        | Unit | Simulator (computed in `+utils.aggregator`) |
|-----------------------|------|---------------------------------------------|
| `bin_start_s`         | s    | bin left edge |
| `cell_id`             | int  | |
| `rsrp_avg_dbm`        | dBm  | mean over all UEs in cell |
| `rsrp_p10_dbm`        | dBm  | 10th percentile |
| `rsrp_p50_dbm`        | dBm  | median |
| `rsrp_p90_dbm`        | dBm  | 90th percentile |
| `sinr_p10_db`         | dB   | |
| `sinr_p50_db`         | dB   | |
| `sinr_p90_db`         | dB   | |
| `ho_attempt_count`    | int  | |
| `ho_success_count`    | int  | |
| `ho_fail_count`       | int  | |
| `rlf_count`           | int  | |
| `ping_pong_count`     | int  | |
| `hosr`                | frac | `ho_success_count / max(ho_attempt_count, 1)` |
| `n_ues_in_cell`       | int  | |

---

## 4. Open schema questions to resolve in Phase 0 / 1

1. **Bangladesh RSRP unit.** Values like `61, 62` are not standard dBm (typically negative). Likely: encoded as `(RSRP_dBm + offset)` per 3GPP TS 36.133 Table 9.1.4-1 (LTE RSRP reporting range 0..97 maps to -140..-44 dBm with 1 dB step). Confirm by checking ranges across all Bangladesh files; if so, apply `rsrp_dbm = encoded - 140` (or similar). **Document mapping in `analysis/common/loaders.py`.**
2. **Bangladesh CINR sign.** Values include `-8, -4, -6, 1` so might be in dB already. Verify.
3. **NordicDat timestamp.** Unix epoch float, no timezone. Assume UTC, document.
4. **Bangladesh measurement reports `.txt`.** Raw L3 RRC dumps. **Not parsed initially** — deferred. If we need richer per-event detail, parser is a side project.
5. **NordicDat `serving_cell_id` numbering.** 9-digit IDs (e.g., `20738048`) — encoded eNB ID + sector. Not split for v0; treat as opaque ID.

---

## 5. Reserved enum values

`event_type`: `HO_ATTEMPT | HO_SUCCESS | HO_FAIL | RLF | PING_PONG`
`outcome`: `success | fail | rlf | pp_fail`
`ran_type`: `LTE | NR_SA | NR_NSA`
`trigger_quantity`: `RSRP | RSRQ | SINR`
