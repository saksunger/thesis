# Thesis Plan — Living Document

> Last updated: 2026-06-05 (Phase 2 v0 done)
> Owner: Sevda
> Track: **Simulator-based** custom MATLAB micro-simulator.

## North-star goals

1. **3GPP-faithful** custom MATLAB micro-simulator: TR 38.901 channel, TS 38.331 §5.5.4 A3 event, TS 38.331 §5.3.10 RLF/T310.
2. **Calibrated** against real datasets (NordicDat + Bangladesh) via KS test on RSRP/RSRQ/SINR distributions and HO event rates.
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
- [ ] First commit to git
- [ ] Data symlinks set up (Bangladesh + NordicDat under `data/raw_public/`)
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

### Phase 3 — Calibration vs real data (1 week)
- [ ] `analysis/calibration/load_nordicdat.py` — segment by (operator, RAN, band)
- [ ] `analysis/calibration/load_bangladesh.py` — join Parent + Event Statistics
- [ ] KS-test sim RSRP vs NordicDat RSRP (per band)
- [ ] KS-test sim RSRQ, SINR
- [ ] Sim HO-event rate vs Bangladesh Intra-LTE-HO rate
- [ ] Sim HOSR vs Bangladesh Event Stats Success/(Success+Fail)
- [ ] Tune shadow sigma, cell density, UE speed distribution to minimize KS-stat
- [ ] Q-Q plots + KS test table for thesis chapter 4.3

**Acceptance:** Main distribution KS p > 0.05 (RSRP, SINR), or honest written justification if not.

### Phase 4 — Data generation (1 week)
- [ ] `simulator/runners/sweep_static.m` — TTT × hyst × A3 × 20 seeds (parfor)
- [ ] `+scenarios.drift_traffic_shift` — load 30%→90% over 7 days
- [ ] `+scenarios.drift_channel_swap` — UMa → UMi midway
- [ ] `+scenarios.drift_mobility` — pedestrian → vehicular midway
- [ ] `+scenarios.drift_reconfig` — operator pushes new (TTT, hyst) at t=T
- [ ] `+scenarios.anomaly_rlf_burst` — interference spike
- [ ] `+scenarios.anomaly_meas_glitch` — sensor stuck-at value
- [ ] `+scenarios.anomaly_slow_degrade` — gradual SINR drop
- [ ] `+scenarios.anomaly_gps_error` — large position jump
- [ ] `simulator/runners/build_timeline.m` — assemble 30/60/90-day timeline with ground-truth event log

**Acceptance:** Parquet files in `data/simulated/`, every injected event recorded, reproducible from seed.

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
| Calibration KS-test fail on key KPI | M | document mismatch honestly, narrow to LTE band only if needed | open |
| Deep AE underperforms on small data | L | drop transformer, keep IsoForest + LSTM-AE | open |
| Online retrain pipeline complexity | M | start with sklearn `partial_fit` before River | open |
| Surrogate uncertainty poorly calibrated | L | fall back to quantile GBM | open |
| Writing crammed into last 2 weeks | H | draft chapter at end of each phase, not at end | open |
