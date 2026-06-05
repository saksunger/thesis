# Thesis Plan — Living Document

> Last updated: 2026-06-05 (Phase 5 Iter B-1 done — full anomaly-detection benchmark on `timeline_medium`: 5 detectors (IsoForest, LOF, OneClassSVM, PCA-AE, MLP-AE) × 4 anomaly types × bootstrap 95 % CI × window sweep (2/5/10/30 s) × per-feature-family ablation. **88 MATLAB + 63 Python tests green**. A-2 PR-AUC = **0.858 [0.820, 0.897]** (OneClassSVM, best in benchmark), A-1 = 0.751 (PCA-AE). A-3/A-5 documented at noise floor (Iter B-2 / Phase 7 lift). Drift-degradation figure (Chapter 5 moneyshot) shows ≥ 100 % FPR explosion under drift_mobility, directly motivating Phase 7's drift-aware framework.)
> Owner: Sevda
> Track: **Simulator-based** custom MATLAB micro-simulator.

## Scope (locked — ADR-14 + ADR-15)

**HO-type axis (ADR-14): 5G NR Standalone, intra-RAT, inter-gNB Xn-based handover only.**
- In scope: NR ↔ NR handovers between distinct gNBs (TS 38.300 §9.2.3.2, TS 38.331 §5.5.4 A3, §5.3.10 RLF).
- Out of scope: intra-gNB mobility, N2/AMF-based HO, any inter-RAT handover (NR ↔ LTE), NSA-mode signalling.
- Normative refs: TS 38.331 v17.16.0, TS 38.133 v17.21.0, TR 38.901 v17.1.0.
- LTE specs (TS 36.331 / 36.133) are background only (Bangladesh RSRP decoder).

**KPI-family axis (ADR-15): mobility KPIs only per 3GPP TS 28.554 §6.3.1–6.3.2.**
- In scope: `HOSR`, `HOFR`, `RLF_rate`, `ping_pong_rate` (derived from `events.parquet`); plus the underlying radio measurements `RSRP_serving`, `RSRQ_serving`, `SINR_serving`, `RSRP_neighbor` (from `samples.parquet`) consumed as detector features.
- Out of scope (TS 28.554 §6.3.6 service-quality KPIs): downlink/uplink throughput, packet drop rate, end-to-end latency, jitter. The simulator has no L2 MAC scheduler / MCS feedback / BLER model; reporting these would over-claim simulator fidelity. Throughput is monotone-correlated with SINR_serving so the detector loses no information.

## North-star goals

1. **3GPP-faithful** custom MATLAB micro-simulator: TR 38.901 channel, TS 38.331 §5.5.4 A3 event, TS 38.331 §5.3.10 RLF/T310 — strictly for NR Xn inter-gNB HO.
2. **Calibrated** radio-layer marginal distributions (RSRP/RSRQ/SINR) against the NordicDat `op1/5G-NSA/LTE_B20` segment via KS-statistic + bootstrap CI. The HO control plane itself is verified against the 3GPP spec via unit tests, **not** against real HO event logs (no public 5G SA HO event dataset exists; see ADR-14).
3. **Broad ML benchmark**: 5+ anomaly detectors, 5+ drift detectors. Show that conventional anomaly detection degrades under drift — motivates the drift-aware framework.
4. **Drift-aware adaptive framework**: drift trigger → online retrain of anomaly model. Compare against static and periodic-retrain baselines.
5. **Calibrated config–performance surrogate**: predict HOSR / RLF rate / ping-pong rate from (TTT, hyst, A3 offset, scenario features) with uncertainty.
6. **End-to-end demo**: drift detected → anomaly retrained → surrogate suggests config → KPIs restored. Single timeline figure for defense.
7. **Reproducibility package**: Docker + MATLAB version pin + `make all` + Zenodo dataset.
8. *(Optional)* Workshop paper draft (4–6 pages, IEEE GLOBECOM/EuCNC/WCNC workshop).

---

## Phase status (tick boxes as completed)

### Phase 0 — Foundation — DONE
- [x] Repo skeleton + .gitignore + .gitattributes
- [x] README + plan + design ADRs + schema + 3GPP refs + scenario catalog
- [x] MATLAB toolbox availability confirmed (R2023b: Communications + 5G + Stats/ML + Parallel + DL all licensed)
- [x] 3GPP PDFs downloaded (TS 38.331 v17.16.0, TS 38.133 v17.21.0, TR 38.901 v17.1.0, TS 36.331 v17.16.0, TS 36.133 v17.16.0)
- [x] Makefile auto-detects MATLAB binary; `make matlab-check`, `make test`, `make demo` all work from terminal
- [x] First commit to git (initial scaffold + Phase 1.1/2 v0)
- [x] Real datasets copied to `data/raw_public/` (Bangladesh 535 MB, NordicDat 14 MB; gitignored)
- [ ] DVC installed (deferred — only if/when we publish data externally)

### Phase 1 — Simulator core (1.5 weeks) — IN PROGRESS (v0 skeleton done)
**Goal:** RSRP/RSRQ/SINR per (UE, cell, time) for a multi-cell hex layout.

- [x] `+utils.constants` — physical + default sim constants (UMa + UMi)
- [x] `+utils.hex_layout` — N-tier hex grid, configurable ISD
- [x] `+channel.pathloss_uma` — TR 38.901 Table 7.4.1-1 UMa LoS/NLoS
- [x] `+channel.los_probability` — TR 38.901 Table 7.4.2-1 (UMa, UMi)
- [x] `+channel.los_state` — piecewise-constant LoS state along trajectory (TR 38.901 §7.6.3.3)
- [x] `+channel.shadowing_field` — spatially-correlated 2-D GRF (TR 38.901 Table 7.5-6)
- [x] `+mobility.waypoint_track` — scripted polyline trajectory
- [x] `+ho.measurements` v1 — RSRP + RSRQ + SINR with correlated shadow + LoS
- [x] `runners/demo_phase1.m` — smoke test: 1 UE, 7 cells, 3-panel KPI plot
- [x] `tests/test_pathloss.m` (5 tests), `tests/test_hex_layout.m` (4 tests), `tests/test_measurements.m` (5 tests)
- [x] **v0 demo validated by hand** (9/9 unit tests green; max RSRP = -48 dBm matches formula)
- [x] **v1 demo validated** (14/14 tests pass; PNGs in `data/simulated/phase1_demo/`; serving cell distribution matches geometry)
- [ ] `+channel.pathloss_umi` — UMi (for drift scenario D-2)
- [ ] `+mobility.gauss_markov` — Gauss-Markov mobility (population-scale alternative)
- [ ] `+utils.logger` — structured event log writer (parquet)
- [ ] Sanity plots: RSRP-vs-distance curve, SINR heatmap, UE trajectory overlay

**Acceptance:** A single UE walking through a 7-cell hex layout produces a plausible RSRP time series for serving + 6 neighbors. Unit tests green.

### Phase 2 — HO mechanism (1.5 weeks) — v0 DONE (Alhammadi-style trends verified)
**Goal:** Full 3GPP A3 + RLF event loop with per-event outcome logging.

- [x] `+ho.l3_filter` — TS 38.331 §5.5.3.2 L3 filtering (NaN-skip; configurable α)
- [x] `+ho.a3_evaluate` — TS 38.331 §5.5.4.4 A3 entry condition + per-neighbor TTT timer
- [x] `+ho.rlf_evaluate` — TS 38.331 §5.3.10 RLF state machine (Qout/Qin/N310/N311/T310)
- [x] `+ho.event_loop` — orchestration: L3 → A3 → HO exec → RLF → ping-pong
- [x] `+ho.empty_event` — canonical event-row schema (matches `docs/schema.md`)
- [x] Per-event log: `(event_id, t, type, ue, src, tgt, ttt_ms, hyst_dB, a3_off_dB, rsrp_serv/tgt_dBm, sinr_serv_dB, outcome)`
- [x] HO execution latency modelled (`ho_exec_s`, default 50 ms); RLF during HO → HO_FAIL
- [x] Ping-pong detector (5 s window, configurable per TR 36.839 / Alhammadi 2023)
- [x] `runners/demo_phase2.m` — single UE, 7-hex, full HO event loop + 2 PNGs
- [x] `runners/sweep_ttt_hyst.m` — 6 TTT × 4 hyst × 3 seed sanity sweep + heatmaps
- [x] `tests/test_l3_filter.m` (4 tests), `tests/test_a3_evaluate.m` (6), `tests/test_rlf_evaluate.m` (5), `tests/test_event_loop.m` (5)
- [x] **Sanity check vs Alhammadi 2023 / Farooq 2022 — qualitative trends confirmed**
  - Hysteresis ↑ → HO_ATTEMPT count ↓ (6.67 → 5.33 across 0–3 dB)
  - TTT 1024 ms → HOSR drops to 0.33–0.50 and RLF count jumps from 0 → ~3.3 per run
  - hyst = 2 dB minimizes ping-pong rate at every TTT (0.00 across the column)
- [ ] `+ho.meas_report` — explicit reporting interval gating (not in v0; using per-tick L3)
- [ ] `+mobility.gauss_markov` for population-scale (deferred to Phase 4)
- [ ] `+utils.logger` for parquet event-log writer (deferred to Phase 4)

**Acceptance:** ✓ For (TTT, hyst) ∈ {256 ms, 2 dB} HOSR = 1.00, ping-pong = 0, RLF = 0 over 240 s walk; TTT-vs-HOSR and TTT-vs-RLF trends match published curves; 34/34 unit tests green.

**Go/No-Go gate:** PASSED. Proceeding to Phase 3 (calibration).

### Phase 3 — Calibration vs real data (1 week) — v0 DONE (KS ≤ 0.20 for RSRP & SINR vs op1/5G-NSA/LTE_B20)

**Step 1 — loaders + EDA**
- [x] Python env: `requirements.txt` (pandas 2.3, scipy 1.17, pyarrow 21, scikit-learn 1.9, matplotlib 3.10, seaborn, jupyter), `.venv/` under repo root
- [x] `analysis/common/paths.py` — single source of truth for data paths + presence check
- [x] `analysis/calibration/load_bangladesh.py` — Processed Dataset (3 schema variants, 3 timestamp formats, RSRP/RSRQ TS 36.133 decoders) + Event Statistics loader, dedup on natural key
- [x] `analysis/calibration/load_nordicdat.py` — full CSV → canonical schema + `summarize_segments()` + `detect_cell_transitions()` (HO-proxy)
- [x] `analysis/calibration/eda_report.py` — five-number summaries, segment census, RSRP/SINR CDFs, time-coverage plots
- [x] **Findings logged in `docs/schema.md` §4** (RSRP encoding confirmed, RSRQ Rel-13 extended range, time format quirks, segmentation target)
- [x] **Calibration target identified (per ADR-14):** primary target = NordicDat `op1/5G-NSA/LTE_B20` (43 k rows, closest 5G-flavored real reference). LTE segments `op1/LTE/LTE_B20` (26 k) and `op3/LTE/LTE_B3` (10 k) are loaded and KS-tested only as cross-RAN sanity references — **not calibration targets** for the NR-locked simulator.

**Step 2 — KS-test pipeline + sim tuning**
- [x] `+utils.write_kpis_parquet` + `+utils.write_events_parquet` — canonical parquet writers (matches `docs/schema.md` §1/§2)
- [x] `runners/calibration_run.m` — multi-UE multi-seed scenario, one parquet per UE + JSON metadata sidecar
- [x] `runners/calibration_sweep.m` — 3×3 (ISD × area) grid runner
- [x] `tests/test_logger.m` — 4 unit tests (parquet schema, serving-cell argmax, empty/non-empty events)
- [x] `analysis/calibration/ks_test.py` — KS-statistic + bootstrap 95 % CI + Q-Q grid + CDF overlay
- [x] `analysis/calibration/ks_sweep_compare.py` — sorts configs by KS-stat, generates ISD×area heatmap
- [x] `make eda`, `make calibrate-sim`, `make calibrate-sweep`, `make calibrate` wired up (38/38 MATLAB tests green)
- [x] **Baseline KS (default config, 144 012 sim samples vs op1/5G-NSA/LTE_B20)**
  - RSRP: KS = 0.175 [CI 0.164, 0.186], Δp50 = +3.1 dB
  - SINR: KS = 0.192 [CI 0.183, 0.200], Δp50 = -2.2 dB
  - RSRQ: KS = 0.467 [CI 0.458, 0.478], Δp50 = -0.5 dB ⚠ (formula-driven, see findings)
- [x] **Tuning sweep (3 ISD × 3 area)** confirmed default `isd_m=500, area_m=1500` is locally optimal for RSRP fit; larger ISD or area degrades all KPIs.
- [x] **Findings note:** `docs/calibration_findings.md`
- [ ] Bangladesh HO rate cross-check (`~310 attempts/hour`) — deferred; not blocking
- [ ] RSRQ formula refinement (deterministic load → traffic-aware) — explicit Phase 4 task

**Acceptance:** ✓ Sim-vs-real KS ≤ 0.20 on RSRP and SINR for the matched 5G-NSA segment, with Δp50 ≤ 3 dB. RSRQ residual mismatch documented and routed to Phase 4. P-values trivially 0 due to n > 10 000; KS-statistic + bootstrap CI is the reported metric (defensible for this sample size per Massey 1951, Conover 1999).

**Go/No-Go gate:** PASSED. Proceeding to Phase 4 (data generation: long timelines + drift/anomaly scenarios).

### Phase 4 — Data generation (split into 3 iterations)

**Iteration A — Foundation + 1 drift end-to-end (DONE)**
- [x] `+channel.pathloss_umi` — TR 38.901 Table 7.4.1-1 UMi-Street Canyon (5 unit tests)
- [x] `+ho.measurements` branches on `params.scenario` ∈ {`UMa`, `UMi`}
- [x] `+utils.constants` — UMi shadow stds + h_bs_m_umi
- [x] `+utils.run_phase` — generic single-phase runner (refactor of calibration_run inner loop)
- [x] `+scenarios.baseline` — phase spec builder (UMa default)
- [x] `+scenarios.drift_channel_swap` — D-2: UMa → UMi (BS height 25 → 10 m, shadow 4/6 → 4/7.82 dB, decorrel 37/50 → 10/15 m)
- [x] `+scenarios.apply_overrides` — recursive nested struct merge with typo-detection
- [x] `runners/build_timeline.m` — JSON-driven multi-phase builder, parquet + ground truth + run_metadata.json
- [x] `+scenarios/timelines/timeline_short.json` — 3-phase smoke timeline (baseline → drift → post-drift, 3×60s)
- [x] `tests/test_pathloss_umi.m` (5), `tests/test_scenarios.m` (5), `tests/test_run_phase.m` (4), `tests/test_build_timeline.m` (4)
- [x] `make sim TIMELINE=timeline_short` end-to-end (~24 s, 216 k samples, 2 691 events, drift visible in P10 RSRP + 2.3× HO-rate jump)
- [x] **56/56 unit tests green**

**Iteration B — Remaining 3 drifts + 4 anomalies (DONE)**
- [x] `+scenarios.drift_traffic_shift` — D-1 per-phase n_ue bump (load-shift proxy; per-tick load-dependent interference deferred to Iter C)
- [x] `+scenarios.drift_mobility` — D-3 per-phase UE speed change (`+utils.run_phase` now builds straight-line tracks with `displacement = speed × duration`, so requested speed is exact)
- [x] `+scenarios.drift_reconfig` — D-4 nested-override TTT/hyst/A3 push at phase boundary; radio identical to baseline (cleanest "config-only" drift signal)
- [x] `+anomalies.rlf_burst` — A-1 SINR collapse on N UEs in window
- [x] `+anomalies.meas_glitch` — A-2 stuck-at RSRP per UE (constant or first-tick freeze)
- [x] `+anomalies.slow_degrade` — A-5 linear SINR ramp per UE (`rate_db_per_s` × elapsed-in-window)
- [x] `+anomalies.interference_spike` — A-3 per-cell SINR drop visible to all UEs that see the targeted cells
- [x] `+anomalies.apply_all` dispatcher + `+utils.run_phase` hook (post-channel, pre-event-loop, so RLF bursts actually trigger RLF events)
- [x] `runners/build_timeline.m` routes top-level `ground_truth_anomaly` array to the phase specs and emits `ground_truth_anomaly.parquet` (timeline-global times)
- [x] `+scenarios/timelines/timeline_iter_b.json` — 6-phase / 6 min compressed timeline covering all 4 drifts + all 4 anomalies (baseline → D-1 → D-3 → D-2 → D-4 → baseline; anomalies in phase 1 & 6)
- [x] 21 new MATLAB tests (`test_anomalies.m` × 13, `test_scenarios.m` × 6 new, `test_build_timeline.m` × 2 new)
- [x] **End-to-end smoke run** verified in data:
  - D-1: UE count 12 → 30 reflected in samples; HO_ATTEMPT 70 → 167 in the affected phase.
  - D-3: HO_ATTEMPT 70 → 207 (3×) reflecting faster cell-boundary crossings.
  - D-4: TTT 1024 ms collapsed HO_SUCCESS to 18/54 and produced 44 RLFs in the affected phase (validates that bad config push **does** translate into visible RLF spike — supports thesis value-prop).
  - A-1: 18 extra RLFs concentrated on the 3 targeted UEs inside the burst window.
  - A-2: targeted UE RSRP `std = 0` over the freeze window (perfect flat-line).
  - A-3: targeted cell SINR median dropped 12.9 → 2.3 dB (≈ injected `delta_db = -10`).
  - A-5: targeted UE net SINR −9 dB over the 60 s ramp (matches `-0.15 dB/s × 60 s`).

**Iteration C — Production timelines + static sweep (DONE)**
Scope chosen via hybrid plan (see chat history): the three highest-leverage Iter C items shipped now; the rest deferred to Iter D so we can iterate on Phase 5 Iter B results before re-investing in simulator infrastructure.

- [x] `runners/sweep_static.m` — TTT (3) × hyst (4) × A3-off (3) × seeds (10) = 360 runs, parfor, writes `data/simulated/sweep_static/sweep_config_perf.parquet` + `sweep_metadata.json`. Each row carries the controlled knobs and the mobility KPIs (ADR-15) used by the Phase 8 surrogate.
- [x] `+utils.run_phase` accepts optional `ue_state_in` and returns `ue_state_out`; `build_timeline.m` threads state across phases when `global.carry_over_ues=true`. Default false → fully backward-compatible. New UEs (D-1 traffic-shift) get random init; existing UEs keep position **and direction is freshly randomised per phase (Random-Direction mobility model)**. Discovered during Iter C integration that preserving direction made UEs walk straight off the cell footprint after a handful of phases (timeline_medium phase-30 RSRP drifted from −96 to −159 dBm — see commit fix); switched to Random-Direction + out-of-area re-init. Resulting timeline_medium keeps per-phase RSRP within [−96, −87] dBm and SINR within [3, 8] dB across all 30 phases.
- [x] `tools/gen_timeline.py` — parametric generator (`--n-phases`, `--drifts-per-type`, `--anomalies-per-type`, `--carry-over-ues`, `--rng-seed`). Tests cover layout determinism, head-baseline cleanliness, anomaly-windows-in-phase, A-3 cell-id bounds, affected-UE bounds vs per-phase n_ue.
- [x] `+scenarios/timelines/timeline_medium.json` — generated 30-phase × 60s production timeline (1 800 s ≈ 30 min sim). 22 baseline phases, 8 drift phases (2 of each D-1..D-4), 40 anomaly instances (10 of each A-1/A-2/A-3/A-5). `carry_over_ues=true`. Head/tail of 3 baseline phases each kept clean for detector training/recovery analysis.
- [x] Tests: `test_sweep_static.m` (4: schema, metadata, rate finiteness, TTT-monotonicity) + `test_run_phase.m` (6 new: ue_state shape, position continuity, displacement-scales-with-speed, new-UE random init, out-of-area triggers re-init, random-direction-per-phase) + `test_build_timeline.m` (2 new: carry-over flag end-to-end + default-false discontinuity) + `tools/tests/test_gen_timeline.py` (13). Totals: **88 MATLAB tests / 38 Python tests, all green**.
- [x] Makefile: `make sweep-static`, `make gen-timeline-medium`; `pytest` target picks up `tools/tests/`.

**Deferred to Iter D** (re-evaluated after Phase 5 Iter B feedback):
- [ ] Per-tick load-dependent interference (upgrade D-1 from n_ue-proxy to real PRB-occupancy → SINR penalty; also addresses ADR-13 RSRQ residual)
- [ ] `timelines/timeline_long.json` — 90-day equivalent (overkill until medium drives clear need)
- [ ] D-5..D-8 + A-4 / A-6 / A-7 (additional scenarios; pick based on which failure modes Iter B exposes)
- [ ] Optional: parfor in `build_timeline` over phases / over UEs within a phase

**Acceptance (Iter A + B + C):** ✓
- Iter A + B: Parquet artefacts in `data/simulated/timeline_iter_b/` (540 K samples, 1 908 events); all 4 drift types + 4 anomaly types observable.
- Iter C: 360-run `sweep_config_perf.parquet` (Phase 8 training matrix) + `timeline_medium.json` (Phase 5 Iter B statistical-power source) + UE-continuity flag (per-UE drift trajectory realism). All 88/88 MATLAB + 38/38 Python tests green.
- Iter C drift visibility (computed from regenerated `timeline_medium`, baseline phase vs the immediately-following drift phase):

  | Drift | Phase pair | RSRP base→drift (dBm) | SINR base→drift (dB) | Events base→drift |
  |---|---|---|---|---|
  | D-2 | 4 → 5  | −93.3 → −89.7 | 3.1 → 4.1  | 197 → 518 |
  | D-3 | 7 → 8  | −89.9 → −93.4 | 4.0 → 4.7  | 176 → 524 |
  | D-4 | 10 → 11 | −91.1 → −88.2 | 5.8 → 7.7 | 216 → 125 (events ↓: TTT/hyst raised) |
  | D-1 | 22 → 23 | −90.7 → −92.2 | 7.2 → 6.2 | 150 → 312 (n_ue 12→24)|

  D-4 produces *fewer* events (longer dwell time per UE, as designed for the reconfig drift). The other three produce 2-3× more events. This is the kind of clean, defensible drift-signature table the thesis chapter on Phase 4 needs.

### Phase 5 — Anomaly benchmark (1 week)
**Feature scope (per ADR-15):** sliding-window aggregates over mobility KPIs only. No throughput/latency/jitter features. Window-feature whitelist:
- From `samples.parquet`: `RSRP_serving` p10/p50/p90/std, `SINR_serving` p10/p50/p90/std, `RSRQ_serving` p10/p50/p90/std, `RSRP_neighbor_top1` p50, serving-cell-id mode, UE speed.
- From `events.parquet` aggregated per (UE × window): `HO_ATTEMPT` count, `HO_SUCCESS` count, `HO_FAIL` count, `RLF` count, `PING_PONG` count → derived rates `HOSR`, `HOFR_rate`, `RLF_rate`, `ping_pong_rate`.

**Iter A (smoke) — DONE:**
- [x] `analysis/common/paths.py` extended with `timeline_dir()` + `assert_timeline_present()`
- [x] `analysis/anomaly/features.py` — window aggregator (5 s width, 1 s slide; phase-boundary-aware)
- [x] `analysis/anomaly/labels.py` — ground-truth labelling with per-UE / per-anomaly-id columns + half-open interval rule
- [x] `analysis/anomaly/detectors.py` — IsoForest, LOF (novelty=True), PCA-AE (linear AE via TruncatedSVD)
- [x] `analysis/anomaly/smoke_eval.py` — train on Phase 1 pre-A-1 baseline / score everything / per-phase + per-anomaly PR-AUC + FPR tables + 3-panel plot
- [x] `analysis/anomaly/tests/` — 25 unit tests (features × 7, labels × 8, detectors × 10), all green
- [x] `make anomaly-smoke TIMELINE=timeline_iter_b` runs in ~12 s on one core
- [x] **Smoke run on `timeline_iter_b` (5 040 windows / 4 detectors / 4 anomaly types):**
  - **C1 PASS** — per-anomaly best PR-AUC: A-1 = 0.63 (PCA-AE), A-2 = 0.63 (PCA-AE), A-3 = 0.46 (IsoForest), A-5 = 0.62 (LOF). All 4 anomaly types meet the > 0.1 threshold.
  - **C2 PASS** — drift-degradation visible: LOF FPR 5.8× baseline, PCA-AE 3.7×, IsoForest 3.1×. Phase 3 (D-3 mobility, speed 10 → 25 m/s) drives LOF and PCA-AE FPR to **1.00** (false-alarm storm). This is the headline narrative for the adaptive framework in Phase 7.
- [x] Plot: `data/processed/anomaly_smoke_timeline_iter_b/smoke_summary.png` (3 panels: FPR-per-phase / TPR-per-phase / PR-AUC-per-anomaly).
- [x] Detector-by-drift observations logged for thesis discussion:
  - D-3 mobility (speed change) is the highest-impact drift on classical detectors.
  - D-1 (traffic shift) and D-4 (reconfig) are mild (~7-9 % FPR) because they don't shift the per-UE radio feature distribution much.
  - D-4 channel swap moderate (15-36 % FPR) — UMa → UMi RSRP/SINR distribution shift is real but smaller than D-3's velocity shift.
  - A-3 (interference spike) TPR is depressed by labelling slack: GT marks "all UEs" but only UEs whose serving cell = targeted cell actually feel it → Iter B will refine labels using `serving_cell_id`.

**Iter B-1 (full benchmark on `timeline_medium`) — DONE:**
- [x] `analysis/anomaly/labels.py` — cell-conditional A-3 labelling (`serving_cell_mode ∈ affected_cell_ids`); falls back to all-cells match when GT predates the rule
- [x] `analysis/anomaly/features.py` — `feature_groups()` helper (7 families: rsrp / rsrq / sinr / speed / cell / events / derived). Disjoint, exhaustive, unit-checked.
- [x] `analysis/anomaly/detectors.py` — added `OneClassSVMDetector` (RBF, nu=0.05, max-train cap) + `MLPReconErrDetector` (16-8-16 bottleneck AE, early stopping). Roster now hits **5+ detectors** (plan bar): IsoForest, LOF, OneClassSVM, PCA-AE, MLP-AE.
- [x] `analysis/anomaly/metrics.py` — bootstrap PR-AUC CI (stratified resampling preserves prevalence; 1 000 resamples × 95 % CI; consistent with ADR-12 calibration convention).
- [x] `analysis/anomaly/benchmark_eval.py` — full Iter B orchestrator: reference run + window-size sweep (2 / 5 / 10 / 30 s) + per-feature-family ablation on the winning detector. Writes 8 CSVs + 1 figure + JSON summary to `data/processed/anomaly_benchmark_timeline_medium/`.
- [x] `analysis/anomaly/tests/` — **63 Python tests green** (25 new: labels A-3 × 4, features groups × 3, metrics bootstrap × 10, detectors expand × 8).
- [x] `make anomaly-benchmark` runs in ~12.8 min on a single core for `timeline_medium` (21 504 windows × 5 detectors × 4 anomaly types × 4 window sizes × 8 ablation passes).
- [x] **Full benchmark on `timeline_medium` (30 phases × 60 s, 21 504 windows, 898 positive at W=5 s):**
  - **Table 5.1 PR-AUC ± 95 % CI (best detector per anomaly type, W=5 s):**
    | Anomaly | Best detector | PR-AUC [95 % CI] | Story |
    |---|---|---|---|
    | meas_glitch (A-2)        | OneClassSVM | **0.858 [0.820, 0.897]** | Strongest — sharp stuck-at signal |
    | rlf_burst (A-1)          | PCA-AE      | **0.751 [0.706, 0.804]** | All 5 detectors ≥ 0.6 |
    | interference_spike (A-3) | LOF         | 0.152 [0.135, 0.175]     | Cell-conditional honest labels (avg 7 % UE coverage) |
    | slow_degrade (A-5)       | PCA-AE      | 0.048 [0.039, 0.065]     | At noise floor — classical detectors' structural limit |
  - **Table 5.2 — Window-size sweep moneyshots:** A-2 peaks at W=2 s with OneClassSVM = **0.919** (sharp signal favours short windows). A-1 PR-AUC monotonically improves with longer W (event accumulation). A-3 and A-5 never recover at any W.
  - **Table 5.3 — Per-feature-family ablation on LOF (drop-one ΔPR-AUC vs full):**
    | Dropped | A-1 | A-2 | A-3 | A-5 |
    |---|---|---|---|---|
    | **rsrq** | **−0.076** | **−0.333** | **−0.071** | +0.007 |
    | rsrp     | −0.021     | −0.017     | −0.005     | +0.004 |
    | events   | +0.009     | +0.025     | +0.010     | −0.002 |
    | derived  | +0.008     | +0.050     | +0.001     | 0.000  |
    | cell / sinr / speed | ≈0 | ≈0 | ≈0 | ≈0 |
    → **RSRQ percentiles is LOF's dominant signal**; dropping it crashes A-2 by 33 percentage points. Event counts + derived rates actually *hurt* A-2 (≈+0.05 when dropped) — over-engineered for that anomaly type.
  - **Overall full-timeline PR-AUC (across all anomalies):** IsoForest leads with **0.425 [0.391, 0.460]**; LOF/OneClassSVM around 0.15 (dragged down by A-3/A-5 prevalence). IsoForest's edge is the broadest distribution coverage; LOF/OCSVM win on per-anomaly-type but lose when pooled.
- [x] **Drift-degradation figure**: `drift_degradation.png` — Chapter 5 moneyshot. Two panels (FPR and TPR) per phase per detector, with drift phases colour-shaded by drift type:
  - Baseline phases (1-4): all detectors at ~5 % FPR (design target) — calibrated.
  - **Drift_mobility phases (8, 20)**: FPR explodes to **100 %** for LOF/OneClassSVM/PCA-AE (false-alarm storm) — direct evidence that classical detectors break under velocity shift.
  - drift_reconfig phases (11, 14): FPR climbs to 25-35 % — moderate but visible.
  - drift_channel_swap phases (5, 17): 15-30 % FPR — UMa→UMi shift detectable but milder than D-3.
  - drift_traffic_shift phases (23, 26): mild — n_ue bump alone doesn't shift per-UE radio features much.
  - TPR (recall) stays in 0.7-1.0 range, demonstrating the **TPR/FPR trade-off under drift** — this is the headline narrative motivating Phase 7's drift-aware adaptive framework.

**Iter B-1 acceptance check (against the agreed gate):**
| Criterion | Target | Actual | Verdict |
|---|---|---|---|
| Table 5.1 PR-AUC with bootstrap 95 % CI ready | ✓ | 5 × 4 cells, all CIs computed | **PASS** |
| 5+ detectors in benchmark roster | ✓ | 5 (IsoForest, LOF, OCSVM, PCA-AE, MLP-AE) | **PASS** |
| Window-size sweep across 2/5/10/30 s | ✓ | 4 windows × 5 detectors | **PASS** |
| Per-feature-family ablation | ✓ | 7 families × LOF | **PASS** |
| Drift-degradation figure ready | ✓ | `drift_degradation.png` | **PASS** |
| A-3 PR-AUC: Iter A 0.46 → Iter B > 0.7 | 0.70 | 0.152 | **MISS (data-quality)** — see below |
| Best-detector PR-AUC > 0.5 on ≥ 3/4 anomaly types | 3 / 4 | 2 / 4 (A-1, A-2) | **MISS** (scientific finding) |

**Iter B-1 deferred to Iter B-2 / Phase 7:**
- [ ] **A-3 timeline data quality**: `timeline_medium`'s `gen_timeline.py` picks `affected_cell_ids` uniformly from `[1, n_cells]`, but only ~7 % of UEs camp on a randomly chosen cell on average (one A-3 instance has 0 % UE coverage → undetectable by construction). Iter B-2 should bias cell selection toward high-traffic cells to lift A-3 PR-AUC to the > 0.5 bar without changing the labelling rule.
- [ ] **A-5 slow_degrade**: classical detectors are at noise floor (PR-AUC ≈ prevalence). Requires *temporal* features (gradient/trend per UE) or a recurrent AE (LSTM-AE) to surface the slow ramp. Defer to Iter B-2 (LSTM-AE) or Phase 7 (online windowed-trend detector).
- [ ] LSTM-AE / Transformer-AE — only worth implementing once A-5 is verified to need them (PyTorch dep is non-trivial).
- [ ] Stratified per-(detector × drift-type) recall breakdown table for the Phase 7 retraining-trigger comparison.

**Acceptance (Iter A):** ✓ PASS — both criteria met on `timeline_iter_b`. Green-lit Phase 4 Iter C investment + Phase 5 Iter B.
**Acceptance (Iter B-1):** Tables 5.1 / 5.2 / 5.3 + Figure "drift_degradation" delivered. 2/4 anomaly types meet the > 0.5 bar; the other two are documented as structural limits of classical detectors and motivate Iter B-2 / Phase 7. **Conditional PASS** (thesis Chapter 5 has its core tables + figure + interpretation).

### Phase 6 — Drift benchmark (1 week)
- [ ] `analysis/drift/detectors.py` — wrappers around ADWIN, DDM, EDDM, KSWIN, Page-Hinkley
- [ ] MMD + Energy distance batch detectors
- [ ] Feature streams: RSRP histogram, HOSR rolling, RLF rate rolling
- [ ] Metrics: detection latency (ground-truth start vs detection), FPR, miss rate
- [ ] Timeline figure: injected drifts vs detector signals

**Acceptance:** Table 6.1 + Figure ready for thesis chapter 6.

### Phase 7 — Adaptive framework (1 week)
- [ ] `analysis/adaptive/online_retrain.py` — River-based incremental retrain on drift trigger
- [ ] Strategies compared: static / periodic / drift-triggered
- [ ] PR-AUC over time (3 lines)
- [ ] Total retraining cost (CPU seconds, samples used)

**Acceptance:** Drift-triggered ≥ periodic, and substantially > static under drift. Cost analysis honest.

### Phase 8 — Config–performance surrogate (1 week)
- [ ] `analysis/config_perf/surrogate.py` — LightGBM, GP, quantile GBM
- [ ] Targets (per ADR-15 — mobility KPIs only): `HOSR`, `HOFR_rate`, `RLF_rate`, `ping_pong_rate`. No throughput targets.
- [ ] Features: (TTT, hyst, A3 offset, scenario features)
- [ ] Uncertainty calibration: reliability diagram
- [ ] Inverse query: optimal config under KPI constraint

**Acceptance:** MAE per KPI reported, uncertainty calibrated, inverse query produces non-trivial recommendations.

### Phase 9 — End-to-end demo (4–5 days)
- [ ] `analysis/demo/timeline_runner.py` — pipe: drift detect → anomaly retrain → surrogate query → recommended config
- [ ] Single "moneyshot" figure: KPI timeline with intervention markers
- [ ] Baseline: no-adaptive vs adaptive
- [ ] Cumulative HOSR-loss-avoided number

**Acceptance:** Figure ready for thesis chapter 9 and defense slide.

### Phase 10 — Writing + reproducibility (2 weeks)
- [ ] Thesis chapters 1–10 drafted
- [ ] Abstract + conclusion
- [ ] Dockerfile + `make all` reproduces all figures
- [ ] MATLAB version pinned in README
- [ ] Zenodo upload (datasets + repo snapshot)
- [ ] *(Optional)* Workshop paper draft

---

## Decision log (for ADR-style tracking)

See `docs/design.md` for architecture decisions. Add a new ADR entry whenever a Phase introduces a binding choice (simulator language, scenario authoring format, sweep grid resolution, etc.).

## Risk register (mirror of plan risks)

| Risk | P | Mitigation | Status |
|---|---|---|---|
| MATLAB single-process scaling for sweep | M | parfor + persist per-seed parquet, merge offline | open |
| Calibration KS-test fail on key KPI | M | document residual honestly; tighten via Phase 4 traffic-load model rather than expanding scope to LTE (ADR-14 keeps NR-only) | mitigated for RSRP/SINR (Phase 3 v0); RSRQ deferred to Phase 4 |
| Deep AE underperforms on small data | L | drop transformer, keep IsoForest + LSTM-AE | open |
| Online retrain pipeline complexity | M | start with sklearn `partial_fit` before River | open |
| Surrogate uncertainty poorly calibrated | L | fall back to quantile GBM | open |
| Writing crammed into last 2 weeks | H | draft chapter at end of each phase, not at end | open |
