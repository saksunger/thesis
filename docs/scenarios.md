# Scenario Catalog — Drift and Anomaly Injection

> This is the **ground-truth labelling source** for drift and anomaly detection benchmarks. Every scenario listed here is implemented under `simulator/+scenarios/` and writes a labelled timeline event to `events_ground_truth.parquet`.

> **Scope (ADR-14):** every drift and every anomaly below is intra-5G-NR. There are no inter-RAT scenarios (no NR→LTE fallback, no NR→Wi-Fi). D-2 (UMa ↔ UMi) swaps channel models within TR 38.901, both of which are 5G NR channels. The 7-hex layout is treated as 7 distinct gNBs, so every HO event is by construction inter-gNB Xn (TS 38.300 §9.2.3.2).

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

| Drift ID | Name                | What changes                                              | Implementation (Iter B)             | Realism justification |
|---------:|---------------------|-----------------------------------------------------------|-------------------------------------|-----------------------|
| D-1      | Traffic load shift  | UE density bump in the affected phase                     | `+scenarios.drift_traffic_shift`, `params.n_ue` | Diurnal patterns common in real networks |
| D-2      | Channel model swap  | UMa → UMi-Street Canyon at phase boundary (BS height, path loss, shadow std) | `+scenarios.drift_channel_swap` | Cell densification / new small cells |
| D-3      | Mobility shift      | UE speed change (default 10 → 25 m/s)                     | `+scenarios.drift_mobility`, `params.speed_mps` | Rush-hour traffic onset |
| D-4      | Reconfiguration     | Operator pushes new (TTT, hyst, A3 off)                   | `+scenarios.drift_reconfig`, `params.ho_params.*` | Network parameter optimization in production |
| D-5      | Cell outage         | One cell goes silent at `t_drift`, recovers at `t_recover` | _Iter C — deferred_                | Hardware fault, maintenance |
| D-6      | Antenna tilt change | Center cell antenna pattern modified (sidelobe leakage)   | _Iter C — deferred_                | Tilt optimization rollout |
| D-7      | Interference rise   | Background interference floor +3 dB stepwise              | _Iter C — deferred_                | New neighboring deployment |
| D-8      | Slow degradation    | Linear SINR drop 0.05 dB/hour over multiple days          | _Iter C — deferred_                | Slow equipment aging |

**Implementation notes (Iter B):**

- **D-1** swaps the active UE population: the simulator does not yet model
  per-tick load-dependent inter-cell interference (ADR-13 deferral), so
  this drift mainly shifts the event-rate distribution rather than the
  per-UE radio distribution. A more faithful implementation lands in
  Iter C after we add a traffic-load model.
- **D-3** is implemented via straight-line trajectories with displacement
  = `speed_mps × duration_s` in `+utils.run_phase`. The phase boundary
  forces a fresh set of UEs at the new speed (no within-phase ramp).
- **D-4** changes the RRM control plane only — radio environment is
  identical to baseline. This is the cleanest "config-only" drift,
  ideal for isolating the configuration-performance surrogate signal.

**Labeling protocol.** For each drift, we record:
- `drift_id`
- `start_time_s`, `end_time_s` (or `None` for monotone)
- `affected_cells` (list of cell_id)
- `affected_kpis` (list of canonical KPI names expected to shift)
- `expected_direction` (`up`/`down`/`shape`)

---

## 3. Anomaly scenarios

| Anomaly ID | Name              | What happens                                                 | Implementation (Iter B)         | Spec fields | Realism justification |
|-----------:|-------------------|--------------------------------------------------------------|----------------------------------|-------------|-----------------------|
| A-1        | RLF burst         | SINR drops by `delta_db` on all cells for N UEs in [t, t+Δ]  | `+anomalies.rlf_burst`           | `delta_db`, `affected_ue_ids` | Lightning, jammer, equipment fault |
| A-2        | Measurement glitch | UE RSRP frozen at constant value for [t, t+Δ]                | `+anomalies.meas_glitch`         | `stuck_value_dbm`, `affected_ue_ids` | UE firmware bug, sensor fault |
| A-3        | Interference spike | Targeted cells lose `delta_db` SINR in window (all UEs view)  | `+anomalies.interference_spike`  | `delta_db`, `affected_cell_ids` | Microwave oven, radar |
| A-4        | GPS error          | UE position jumps > 200 m for one sample                     | _Iter C — deferred_              | — | GPS multipath in urban canyon |
| A-5        | Slow degradation   | Linear SINR ramp on one UE over window                       | `+anomalies.slow_degrade`        | `rate_db_per_s`, `affected_ue_ids` | Antenna damage, water ingress |
| A-6        | Excessive HO       | One UE oscillates between 2 cells (forced ping-pong burst)   | _Iter C — deferred_              | — | Cell-edge UE with bad config |
| A-7        | Coverage hole transient | Local RSRP drops > 20 dB for [t, t+Δ] in a sub-area       | _Iter C — deferred_              | — | Tunnel, building shadow |

**Injection mechanism (Iter B).** The timeline JSON's top-level
`ground_truth_anomaly` array declares each injection. `build_timeline.m`
routes each entry to its `phase_id` and attaches it to the phase spec's
`anomalies` cell. During `+utils.run_phase` execution, after the channel
model produces a `meas` struct for a given UE, `+anomalies.apply_all`
filters the anomaly list by `affected_ue_ids` and time mask, then
dispatches to the matching module which mutates the `meas` struct
in-place. The HO event loop then runs on the corrupted view, so e.g.
an `rlf_burst` injection actually triggers RLF events in `events.parquet`.

**Time-units convention.** In the JSON, all anomaly `t_start_s` /
`t_end_s` are **phase-local** (start at 0 inside each phase) for
authoring intuition. The emitted `ground_truth_anomaly.parquet` table
rewrites them as **timeline-global** seconds (offset by the phase's
cumulative start time) so detectors can join cleanly against
`samples.parquet` / `events.parquet`.

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

`simulator/runners/build_timeline.m` takes a timeline-config **JSON** file
(we chose JSON over YAML because MATLAB R2023b's `readstruct` does not
support YAML and we wanted to avoid adding a YAML toolbox dependency)
and emits:
- `samples.parquet` (per-UE × per-tick sample-level KPIs; schema = `docs/schema.md` §1 + `phase_id`/`scenario_name` columns)
- `events.parquet` (HO_ATTEMPT/SUCCESS/FAIL + RLF + PING_PONG events; schema = `docs/schema.md` §2 + per-phase config columns)
- `ground_truth_drift.parquet` (one row per drift event, time bounds derived from phase durations)
- `ground_truth_anomaly.parquet` (one row per anomaly event — landing in Phase 4 Iter B)
- `run_metadata.json` (timeline source, MATLAB version, git SHA, sample/event counts, elapsed)

---

## 5. Reproducibility

Each timeline assembly is fully described by:
1. Timeline config JSON (committed to repo under `simulator/+scenarios/timelines/`)
2. Master seed (logged in `run_metadata.json` and propagated as per-UE seed = `master_seed + phase_id*100 + ue_id`)
3. MATLAB version + simulator git SHA (logged in `run_metadata.json`)

Given (1)+(2)+(3), `make sim TIMELINE=<name>` reproduces bit-identical output (verified by `test_run_phase.test_seeds_are_deterministic`).

---

## 6. Implementation progress

**Iter A — DONE:**
- [x] `+scenarios.baseline` + `+scenarios.drift_channel_swap` (D-2: UMa → UMi, full BS-height + path-loss model + shadow swap)
- [x] `+scenarios.apply_overrides` shared merge helper
- [x] `+scenarios/timelines/timeline_short.json` — dev timeline
- [x] `tests/test_scenarios.m`, `tests/test_run_phase.m`, `tests/test_build_timeline.m`
- [x] Ground-truth drift event log validated by `test_ground_truth_time_bounds_align`

**Iter B — DONE:**
- [x] `+scenarios.drift_traffic_shift` (D-1: per-phase `n_ue` override)
- [x] `+scenarios.drift_mobility` (D-3: per-phase `speed_mps` override; straight-line trajectory uses `displacement = speed × duration`)
- [x] `+scenarios.drift_reconfig` (D-4: TTT/hyst/A3-offset push via nested `ho_params` override)
- [x] `+anomalies.{rlf_burst, meas_glitch, slow_degrade, interference_spike, apply_all}` + per-UE per-tick `meas` corruption hook in `+utils.run_phase`
- [x] `build_timeline.m` routes top-level `ground_truth_anomaly` → phase specs, emits `ground_truth_anomaly.parquet` with timeline-global times
- [x] `+scenarios/timelines/timeline_iter_b.json` (6-phase: baseline → D-1 → D-3 → D-2 → D-4 → baseline; A-1, A-2, A-3, A-5 sprinkled across)
- [x] `tests/test_scenarios.m` extended (D-1/D-3/D-4 builders + override paths), `tests/test_anomalies.m` (~13 stand-alone + dispatcher + integration tests), `tests/test_build_timeline.m` extended (routing + bad-phase error)
- [x] End-to-end smoke run: 6-phase / 540 K samples / 1908 events; all 4 drifts + 4 anomalies observable in the data (see `data/simulated/timeline_iter_b/`)

**Iter C — LATER:**
- [ ] `runners/sweep_static.m` — TTT × hyst × A3 × seeds (parfor)
- [ ] `timeline_medium.json` (30-day equivalent, duration-compressed)
- [ ] `timeline_long.json` (90-day equivalent)
- [ ] D-5..D-8 + A-4 / A-6 / A-7 if time permits
- [ ] Per-phase n_ue continuity (UEs persist across phase boundaries) — currently each phase starts with a fresh UE population
