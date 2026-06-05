# ML-Based Anomaly and Drift-Aware Configuration–Performance Modeling for Adaptive Handover Optimization in 5G/6G Networks

Master's thesis — Wrocław University of Science and Technology, Department of Computer Engineering.

> **Scope (ADR-14):** 5G NR Standalone, intra-RAT, inter-gNB Xn handover only (TS 38.300 §9.2.3.2, TS 38.331 §5.5.4 + §5.3.10). NSA, intra-gNB, N2-based, and inter-RAT HO are out of scope. See `docs/design.md` ADR-14 for the binding scope statement.

## TL;DR

1. A custom MATLAB micro-simulator implements the 3GPP NR HO procedure (TS 38.331 §5.5.4 A3 event, §5.3.10 RLF/T310, TR 38.901 channel model) for **inter-gNB Xn handover in 5G SA only**.
2. Parameter sweeps over (TTT, hysteresis, A3 offset) produce a controlled (config → KPI) dataset.
3. Drift scenarios (intra-NR channel-model swap UMa↔UMi, traffic shift, mobility-profile change) and anomaly injections (RLF bursts, measurement glitches, slow degradation) are scripted with ground-truth labels.
4. ML benchmarks: anomaly detection (IsoForest, LOF, AE, LSTM-AE, Transformer-AE), drift detection (ADWIN, DDM, KSWIN, PH, MMD), drift-aware adaptive retraining, calibrated config–performance surrogate.
5. Calibration: simulator radio-layer marginal distributions (RSRP/RSRQ/SINR) are KS-tested against the NordicDat `op1/5G-NSA/LTE_B20` segment — the closest publicly available 5G-flavored real measurement set. The HO control plane is verified against the 3GPP spec via unit tests, not against real HO event logs (no public 5G SA HO event dataset exists).

See [`docs/plan.md`](docs/plan.md) for the full phased plan, [`docs/design.md`](docs/design.md) for architecture decisions, [`docs/schema.md`](docs/schema.md) for the KPI dictionary.

## Repo layout

```
.
├── docs/                  # Plan, design ADRs, KPI schema, 3GPP refs, scenarios
├── simulator/             # MATLAB micro-simulator (3GPP A3 + RLF)
│   ├── +channel/          # TR 38.901 path-loss + shadowing
│   ├── +mobility/         # Waypoint + Gauss-Markov
│   ├── +ho/               # A3 event, RLF, ping-pong
│   ├── +scenarios/        # Drift + anomaly scripts
│   ├── +utils/            # Logging, IO, plotting helpers
│   ├── runners/           # Sweep runners (parfor)
│   ├── tests/             # MATLAB unit tests
│   └── scripts/           # Helper one-shots (toolbox check etc.)
├── data/
│   ├── raw_public/        # Bangladesh + NordicDat (local copies, gitignored)
│   ├── simulated/         # Generated parquet + demo PNGs (gitignored)
│   ├── processed/         # ML-ready features (gitignored)
│   └── external/          # Misc third-party (3GPP PDFs etc., gitignored)
├── analysis/              # Python: notebooks + scripts
│   ├── calibration/       # KS-test sim vs real
│   ├── anomaly/           # Benchmark
│   ├── drift/             # Benchmark
│   ├── adaptive/          # Drift-triggered retraining
│   ├── config_perf/       # Surrogate model
│   ├── demo/              # End-to-end timeline
│   └── common/            # Shared loaders + metrics
├── tools/                 # Project-level Python helpers (timeline gen, etc.)
├── paper/                 # Thesis source (LaTeX)
├── docker/                # Reproducibility container
├── Makefile               # `make sim`, `make anomaly`, `make all`
└── README.md
```

## Quickstart

### Prerequisites

- **MATLAB R2023b or newer**, with these add-ons (all auto-checked by `make matlab-check`):
  - Communications Toolbox
  - 5G Toolbox
  - Statistics and Machine Learning Toolbox
  - Parallel Computing Toolbox
  - Deep Learning Toolbox (optional, for the LSTM-AE in Phase 5)
- Python 3.11+
- `make`, `git`

### MATLAB path

The Makefile auto-detects MATLAB at `/home/$USER/MATLAB/R2023b/bin/matlab`. Override by exporting `MATLAB_DIR` or passing on the command line:

```bash
make MATLAB_DIR=/opt/matlab/R2024a/bin matlab-check
# or
export MATLAB_DIR=/opt/matlab/R2024a/bin
make matlab-check
```

### Phase 0–5a targets (currently usable)

```bash
make help                  # show all targets
make matlab-check          # verify required toolboxes are installed + licensed
make test                  # run all MATLAB unit tests (Phases 1–4c: 88 tests)
make pytest                # run all Python unit tests (anomaly + tools: 38 tests)
make demo                  # Phase 1 channel/measurement smoke test
make demo-ho               # Phase 2 HO event loop smoke test (single UE)
make sweep-ttt             # Phase 2 small TTT × hysteresis sanity sweep (~30 s)
make eda                   # Phase 3 EDA report (Bangladesh + NordicDat)
make calibrate-sim         # Phase 3 calibration scenario (12 UE × 120 s → parquet, ~6 s)
make calibrate-sweep       # Phase 3 3×3 (ISD × area) tuning sweep + heatmap (~30 s)
make calibrate             # Phase 3 end-to-end: sim run → KS-test → summary CSV + plots
make sim                   # Phase 4 timeline build (TIMELINE=timeline_iter_b, ~70 s, or timeline_medium ~5 min)
                           #   variants: TIMELINE=timeline_short | timeline_iter_b | timeline_medium
make sweep-static          # Phase 4c: 360-run (TTT × hyst × A3 × seed) static sweep → config-perf matrix
make gen-timeline-medium   # Phase 4c: regenerate timeline_medium.json via tools/gen_timeline.py
make anomaly-smoke         # Phase 5 Iter A: IsoForest + LOF + PCA-AE smoke eval on TIMELINE
make anomaly-benchmark     # Phase 5 Iter B: 5 detectors x bootstrap CI x window sweep x ablation on timeline_medium (~13 min)
```

Expected output of `make test` and `make pytest`:

```
=== 88/88 PASS ===   # MATLAB
63 passed            # Python
```

Expected artifacts:

```
data/simulated/phase1_demo/phase1_layout.png         # 7-cell hex + UE trajectory
data/simulated/phase1_demo/phase1_kpis.png           # RSRP / RSRQ / SINR vs time
data/simulated/phase2_demo/phase2_serving.png        # serving cell + HO events over time
data/simulated/phase2_demo/phase2_l3rsrp.png         # L3-filtered RSRP per cell
data/simulated/phase2_sweep/sweep_ho_attempt.png     # heatmap: TTT × hyst → HO count
data/simulated/phase2_sweep/sweep_ping_pong.png      # heatmap: TTT × hyst → pp rate
data/simulated/phase2_sweep/sweep_results.mat        # raw tensors for analysis
data/simulated/calibration_final/kpis_ue*.parquet    # 144k-row sim sample for KS-test
data/simulated/calibration_final/events_ue*.parquet  # HO/RLF event log per UE
data/processed/calibration_eda/                      # Phase 3 EDA figures
data/processed/calibration_ks/ks_summary_final.csv   # ← reported KS-stats
data/processed/calibration_ks/cdf_final.png          # CDF overlay (sim vs 3 segments)
data/processed/calibration_ks/qq_final.png           # Q-Q grid (sim vs 3 segments)
data/processed/calibration_ks/sweep_heatmap_*.png    # ISD × area calibration heatmap
data/simulated/timeline_iter_b/samples.parquet          # Phase 4b: per-tick KPI feed (~32 MB, 540 K rows)
data/simulated/timeline_iter_b/events.parquet           # Phase 4b: HO/RLF/PING_PONG events (~1 908 rows)
data/simulated/timeline_iter_b/ground_truth_drift.parquet    # Phase 4b: 4 injected drift labels (D-1..D-4)
data/simulated/timeline_iter_b/ground_truth_anomaly.parquet  # Phase 4b: 4 injected anomaly labels (A-1, A-2, A-3, A-5)
data/simulated/timeline_iter_b/run_metadata.json        # timeline source, seed, git SHA
data/simulated/timeline_medium/samples.parquet          # Phase 4c: 30-phase production timeline (~3M rows)
data/simulated/timeline_medium/events.parquet           # Phase 4c: HO/RLF/PP events across the timeline
data/simulated/timeline_medium/ground_truth_drift.parquet    # Phase 4c: 8 drift labels (2 per D-1..D-4)
data/simulated/timeline_medium/ground_truth_anomaly.parquet  # Phase 4c: 40 anomaly labels (10 per A-1/A-2/A-3/A-5)
data/simulated/sweep_static/sweep_config_perf.parquet   # Phase 4c: 360-row (TTT × hyst × A3 × seed) config-perf training matrix for Phase 8 surrogate
data/simulated/sweep_static/sweep_metadata.json         # grid, seeds, git SHA
data/processed/anomaly_smoke_timeline_iter_b/           # Phase 5 Iter A smoke results (per-phase / per-anomaly metrics, summary plot)
data/processed/anomaly_benchmark_timeline_medium/       # Phase 5 Iter B full benchmark:
  ├── table_5_1_pr_auc_wide.csv                         #   Table 5.1 (PR-AUC ± 95 % CI per detector × anomaly type)
  ├── table_5_2_window_sweep_wide.csv                   #   Table 5.2 (window-size sweep at 2/5/10/30 s)
  ├── table_5_3_ablation_wide.csv                       #   Table 5.3 (per-feature-family ablation on winning detector)
  ├── per_anomaly_with_ci.csv                           #   long-form Table 5.1 with raw bootstrap bounds
  ├── per_phase_fpr_tpr.csv                             #   per-phase per-detector FPR/TPR (feeds the figure)
  ├── overall_pr_auc_with_ci.csv                        #   single-number PR-AUC + CI per detector
  ├── window_sweep_per_anomaly.csv                      #   long-form sweep results
  ├── ablation_winning_detector.csv                     #   long-form ablation (delta PR-AUC vs full)
  ├── drift_degradation.png                             #   Chapter 5 moneyshot: FPR/TPR across phases with drift shading
  └── benchmark_summary.json                            #   config + winning detector + elapsed
```

Headline Phase 3 result (vs NordicDat op1/5G-NSA/LTE_B20, see [`docs/calibration_findings.md`](docs/calibration_findings.md)):

| KPI  | KS-stat | 95 % CI         | Δ median |
|------|---------|------------------|----------|
| RSRP | 0.175   | [0.164, 0.186]   | +3.1 dB  |
| SINR | 0.192   | [0.183, 0.200]   | −2.2 dB  |
| RSRQ | 0.467   | [0.458, 0.478]   | −0.5 dB (residual; load model deferred to Phase 4) |

### Phase 5+ targets (placeholders)

Will be wired up as we implement the corresponding analysis pipelines.

```bash
make anomaly     # anomaly detection benchmark
make drift       # drift detection benchmark
make adaptive    # drift-aware adaptive framework
make surrogate   # config–performance surrogate
make end2end     # end-to-end timeline demo
make all         # everything end-to-end
```

### Python venv (analysis side)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Real public datasets

| Dataset    | Local path (gitignored)             | Size  | Role in this track (per ADR-14)                                                                 |
|------------|-------------------------------------|-------|--------------------------------------------------------------------------------------------------|
| NordicDat  | `data/raw_public/nordicdat/`        | 14 MB | **In scope.** Segment `op1/5G-NSA/LTE_B20` is the primary calibration target for RSRP/RSRQ/SINR. |
| Bangladesh | `data/raw_public/bangladesh/`       | 535 MB| **Out of scope** for calibration (LTE, not NR SA). Retained as order-of-magnitude HO-rate context only. |

Datasets are **never** mixed with simulated samples on this track — calibration is one-way KS-comparison of marginal distributions. See `data/raw_public/README.md` for repopulation instructions on a fresh clone.

## License & citation

TBD — pick a license before public release. Cite 3GPP TS/TR documents and dataset originators per `docs/3gpp_refs.md` and `data/raw_public/README.md`.

## Status

| Phase                                           | Status                              |
|-------------------------------------------------|-------------------------------------|
| 0. Foundation (repo, docs, Makefile, datasets)  | **done**                            |
| 1.1. Simulator channel core (TR 38.901 UMa)     | **done** (14 unit tests)            |
| 2. HO mechanism v0 (TS 38.331 A3 + RLF)         | **done** (20 unit tests, sweep ok)  |
| 3. Calibration vs real data (KS-test)           | **done** (KS ≤ 0.20, see findings)  |
| 4. Data generation (sweeps + drift + anomaly)   | **Iter A + B + C done** (4 drifts + 4 anomalies; 360-run static sweep for Phase 8; 30-phase production timeline; per-phase UE continuity with Random-Direction model; 88 MATLAB + 63 Python tests green) |
| 5. Anomaly benchmark                            | **Iter A + B-1 done** (5 detectors × 4 anomaly types × bootstrap 95 % CI × window sweep × per-family ablation on timeline_medium; A-2 PR-AUC = 0.858 [OneClassSVM], A-1 = 0.751 [PCA-AE]; A-3/A-5 documented at noise floor — motivates Phase 7). Iter B-2 (LSTM-AE + A-3 timeline fix) deferred. |
| 6. Drift benchmark                              | planned                             |
| 7. Adaptive framework                           | planned                             |
| 8. Config–performance surrogate                 | planned                             |
| 9. End-to-end demo                              | planned                             |
| 10. Writing + reproducibility package           | planned                             |

See [`docs/plan.md`](docs/plan.md) for live, per-task status.
