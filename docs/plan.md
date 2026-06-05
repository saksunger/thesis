# Thesis Plan — Living Document

> Last updated: 2026-06-05 (Phase 4 Iter B done — 4 drift scenarios + 4 anomaly injectors + end-to-end smoke run; 76/76 tests green)
> Owner: Sevda
> Track: **Simulator-based** custom MATLAB micro-simulator.

## Scope (locked — ADR-14)

**5G NR Standalone, intra-RAT, inter-gNB Xn-based handover only.**
- In scope: NR ↔ NR handovers between distinct gNBs (TS 38.300 §9.2.3.2, TS 38.331 §5.5.4 A3, §5.3.10 RLF).
- Out of scope: intra-gNB mobility, N2/AMF-based HO, any inter-RAT handover (NR ↔ LTE), NSA-mode signalling.
- Normative refs: TS 38.331 v17.16.0, TS 38.133 v17.21.0, TR 38.901 v17.1.0.
- LTE specs (TS 36.331 / 36.133) are background only (Bangladesh RSRP decoder).

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

**Iteration C — Production timelines + static sweep (LATER)**
- [ ] `runners/sweep_static.m` — TTT × hyst × A3 × 20 seeds (parfor)
- [ ] `timelines/timeline_medium.json` — 30-day equivalent (compressed via duration scaling)
- [ ] `timelines/timeline_long.json` — 90-day equivalent
- [ ] Per-tick load-dependent interference (upgrade D-1 from n_ue-proxy to real load model; also addresses ADR-13 RSRQ deferral)
- [ ] Per-phase UE continuity (UEs persist across phase boundaries) — currently each phase resets UE population
- [ ] D-5..D-8 + A-4 / A-6 / A-7 if time permits
- [ ] Optional: parfor in `build_timeline` over phases (independent) or over UEs within a phase

**Acceptance (Iter A + B):** ✓ Parquet artefacts in `data/simulated/timeline_iter_b/` (540 K samples, 1 908 events); ground-truth drift + anomaly tables align to injected effects; all 4 drift types + all 4 anomaly types observable in raw data; 76/76 unit tests green.

### Phase 5 — Anomaly benchmark (1 week)
- [ ] `analysis/anomaly/features.py` — KPI window features (per UE/per cell, sliding window)
- [ ] Models: IsoForest, LOF, OneClassSVM, Autoencoder, LSTM-AE, Transformer-AE
- [ ] Temporal split: pre-drift train, post-drift test (key experiment)
- [ ] Metrics: PR-AUC, ROC-AUC per anomaly type
- [ ] Plot: model PR-AUC over time (degradation under drift)

**Acceptance:** Table 5.1 (PR-AUC per model per anomaly type) + Figure "drift degradation" ready for thesis chapter 5.

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
- [ ] Targets: HOSR, RLF rate, ping-pong rate
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
