# Scenario Catalog — Drift and Anomaly Injection

> This is the **ground-truth labelling source** for drift and anomaly detection benchmarks. Every scenario listed here is implemented under `simulator/+scenarios/` and writes a labelled timeline event to `events_ground_truth.parquet`.

## 1. Baseline scenario (no drift, no anomaly)

| Attribute            | Value                                |
|----------------------|--------------------------------------|
| Layout               | 7-cell hex, ISD 500 m                |
| Channel              | TR 38.901 UMa LoS/NLoS               |
| Shadow std           | 4 dB LoS / 6 dB NLoS                 |
| Decorrelation dist.  | 37 m                                 |
| Carrier              | 3.5 GHz, 100 MHz BW                  |
| UE count             | 50                                   |
| UE mobility          | Gauss-Markov, mean speed 5 m/s       |
| HO config            | TTT 256 ms, hysteresis 2 dB, A3 off 0 dB |
| Duration             | configurable per timeline assembly   |

All drift scenarios start from baseline and apply a transformation at time `t_drift`.

---

## 2. Drift scenarios

| Drift ID | Name                | What changes                                              | Param                 | Realism justification |
|---------:|---------------------|-----------------------------------------------------------|-----------------------|-----------------------|
| D-1      | Traffic load shift  | UE count ramps from 50 → 200 over 24 h                    | `start_t, end_t, n0, n1` | Diurnal patterns common in real networks |
| D-2      | Channel model swap  | UMa → UMi at `t_drift` (denser scattering, lower BS height) | `t_drift`             | Cell densification / new small cells |
| D-3      | Mobility shift      | Pedestrian (1.5 m/s) → vehicular (15 m/s) at `t_drift`    | `t_drift, v0, v1`     | Rush-hour traffic onset |
| D-4      | Reconfiguration     | Operator pushes new (TTT, hyst) at `t_drift`              | `t_drift, new_cfg`    | Network parameter optimization in production |
| D-5      | Cell outage         | One cell goes silent at `t_drift`, recovers at `t_recover` | `cell_id, t_drift, t_recover` | Hardware fault, maintenance |
| D-6      | Antenna tilt change | Center cell antenna pattern modified (sidelobe leakage)   | `t_drift, tilt_delta` | Tilt optimization rollout |
| D-7      | Interference rise   | Background interference floor +3 dB stepwise              | `t_drift, delta_db`   | New neighboring deployment |
| D-8      | Slow degradation    | Linear SINR drop 0.05 dB/hour over multiple days          | `rate, t_start`       | Slow equipment aging |

**Labeling protocol.** For each drift, we record:
- `drift_id`
- `start_time_s`, `end_time_s` (or `None` for monotone)
- `affected_cells` (list of cell_id)
- `affected_kpis` (list of canonical KPI names expected to shift)
- `expected_direction` (`up`/`down`/`shape`)

---

## 3. Anomaly scenarios

| Anomaly ID | Name              | What happens                                                 | Param                          | Realism justification |
|-----------:|-------------------|--------------------------------------------------------------|--------------------------------|-----------------------|
| A-1        | RLF burst         | Interference spike causes T310 expiry on N UEs in [t, t+Δ]   | `t, duration_s, n_ues`         | Lightning, jammer, equipment fault |
| A-2        | Measurement glitch | UE reports stuck-at value (last sample repeated) for [t, t+Δ] | `ue_id, t, duration_s`         | UE firmware bug, sensor fault |
| A-3        | Interference spike | One cell SINR drops ≥ 10 dB for [t, t+Δ]                     | `cell_id, t, duration_s, delta_db` | Microwave oven, radar |
| A-4        | GPS error          | UE position jumps > 200 m for one sample                     | `ue_id, t, jump_m`             | GPS multipath in urban canyon |
| A-5        | Slow degradation   | One UE's effective SINR linearly degrades over hours          | `ue_id, t_start, rate_db_per_h` | Antenna damage, water ingress |
| A-6        | Excessive HO       | One UE oscillates between 2 cells (forced ping-pong burst)   | `ue_id, t, duration_s, period_s` | Cell-edge UE with bad config |
| A-7        | Coverage hole transient | Local RSRP drops > 20 dB for [t, t+Δ] in a sub-area       | `area, t, duration_s, delta_db` | Tunnel, building shadow |

**Labeling protocol.** For each anomaly:
- `anomaly_id`
- `start_time_s`, `end_time_s`
- `affected_ues` (list of ue_id) and/or `affected_cells`
- `severity` (continuous, normalized 0..1)

---

## 4. Timeline assembly

A "timeline" is a 30/60/90-day equivalent simulation built by concatenating phases:
1. Baseline warmup (≥ 7 days equivalent) → models can learn "normal."
2. Sequence of `(drift_event, anomaly_events_within_drift)` interspersed with baseline gaps.
3. Recovery / steady-state tail (≥ 7 days).

`simulator/runners/build_timeline.m` will take a timeline-config YAML and emit:
- `events.parquet` (HO + RLF + ping-pong events)
- `kpis_1s.parquet` (per-cell 1s KPIs)
- `ground_truth_drift.parquet` (one row per drift event)
- `ground_truth_anomaly.parquet` (one row per anomaly event)

---

## 5. Reproducibility

Each timeline assembly is fully described by:
1. Timeline config YAML (committed to repo under `simulator/+scenarios/timelines/`)
2. Master seed (logged in the parquet metadata)
3. MATLAB version + simulator git SHA (logged in the parquet metadata)

Given (1)+(2)+(3), `make sim TIMELINE=<name>` reproduces bit-identical output.

---

## 6. To-do (Phase 4 close-out)

- [ ] Implement each scenario as a separate function under `+scenarios/`
- [ ] Author 3 canonical timelines: `timeline_short.yml` (3 days, for dev), `timeline_medium.yml` (30 days), `timeline_long.yml` (90 days)
- [ ] Unit test each scenario in isolation (`tests/test_scenarios.m`)
- [ ] Validate that ground truth event log matches what the scenario function injected
