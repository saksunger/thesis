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

## Reproducing this thesis

Three reviewer profiles, copy-paste recipes for each in
[`docs/reproducibility.md`](docs/reproducibility.md):

- **Profile A — no MATLAB license** (most reviewers): hash-locked Python +
  cached simulator output from the Zenodo deposit → regenerates every
  Chapter 5–9 + 11 figure in ~30 min.
- **Profile B — Docker**: `docker build && docker run` — host-tooling-agnostic,
  internally Profile A.
- **Profile C — full from-scratch** (requires MATLAB R2023b): `make all-full`
  — Phase 1 → 11 end-to-end, ~3–4 h wall clock.

Cached simulator artifacts (~600 MB) plus the manifest+SHA256 are deposited at
`https://doi.org/10.5281/zenodo.XXXXXXX` (placeholder until thesis submission;
workflow lands in Phase 10 A5). The canonical SHA256 table for that deposit
is also committed in-repo as [`data/manifest.sha256`](data/manifest.sha256) —
verify your local cache with `make verify-cache` (or `sha256sum -c`).

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

The full pinned software stack — including MATLAB build number, every Python
package SHA256, and reviewer recipes for the three reproduction profiles — is
in [`docs/reproducibility.md`](docs/reproducibility.md). Summary:

- **MATLAB R2023b** (build `23.2.0.2365128`, GA release; later releases
  untested). All four required toolboxes at v23.2:
  - Communications Toolbox
  - 5G Toolbox
  - Statistics and Machine Learning Toolbox
  - Parallel Computing Toolbox
  - Deep Learning Toolbox is optional (only needed if Phase 5 Iter B-2 LSTM-AE
    is re-run; deferred in current results).
  - `make matlab-check` verifies all of the above against your local install.
- **Python 3.12.3** (3.11 source-installs too but results not validated).
- `make`, `git`.

**Reviewers without MATLAB** can reproduce every Chapter 5–9 + 11 figure from
cached simulator output — see [`docs/reproducibility.md`](docs/reproducibility.md)
Profile A.

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
make pytest                # run all Python unit tests (anomaly + drift + adaptive + config_perf + demo + tools + external_validation + repro manifest: 379 tests)
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
make drift-benchmark       # Phase 6 Iter A: 7 detectors x 6 streams x bootstrap latency CI on timeline_medium (~4.5 min)
make adaptive-benchmark    # Phase 7 Iter A: 4 retraining strategies (static / periodic / drift-naive / drift-filtered) x PCA-AE base detector x ADWIN trigger on timeline_medium (~1.5 min)
make surrogate-benchmark   # Phase 8 Iter A: HistGB point + Conformal Quantile GB on sweep_config_perf (360 rows) x 3 KPIs (HOSR/RLF/PP); group-5-fold CV + inverse query @ median deployment (~1.5 min)
make end2end-demo          # Phase 9 Iter A: full pipeline replay - drift detect (ADWIN) -> filtered retrain (PCA-AE + bottom-q) -> surrogate query (HistGB + ConformalQuantileGB) -> counterfactual KPI uplift on timeline_medium (~1 min). 4-panel moneyshot figure.
make seed-replication-all  # Phase 11 Iter A: cross-seed (master_seed ∈ {42..46}) replication of all 4 Python pipelines + bootstrap-CI aggregation (~1.5 h MATLAB + Python; bottleneck is the 5x MATLAB sim runs).
make gen-timeline-dense-urban && make sim TIMELINE=timeline_dense_urban && make anomaly-benchmark TIMELINE=timeline_dense_urban && make drift-benchmark TIMELINE=timeline_dense_urban && make adaptive-benchmark TIMELINE=timeline_dense_urban && make end2end-demo TIMELINE=timeline_dense_urban && make cross-scenario-compare CROSS_BASE=timeline_medium CROSS_CONTRAST=timeline_dense_urban
                           # Phase 11 Iter B chain: cross-scenario validation (medium vs dense_urban, ~35 min MATLAB+Python).
make nordicdat-face-validity # Phase 11 Iter C: PCA-AE + ADWIN on NordicDat 5G-NSA operator=1 segment, fully self-supervised. G1..G3 acceptance + markdown report + 4-panel figure (~2 min).
```

Expected output of `make test` and `make pytest`:

```
=== 88/88 PASS ===   # MATLAB
379 passed           # Python (anomaly + drift + adaptive + config_perf + demo + tools + external_validation + repro manifest)
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
data/processed/drift_benchmark_timeline_medium/         # Phase 6 Iter A full benchmark:
  ├── table_6_1_latency_summary.csv                     #   Table 6.1 (median latency [95 % CI] per detector × drift_type)
  ├── table_6_2_miss_rate.csv                           #   Table 6.2 (miss rate per detector × drift_type)
  ├── table_6_3_baseline_fpr.csv                        #   Table 6.3 (baseline FPR per detector × stream)
  ├── per_drift_latency.csv                             #   long-form (drift_instance × detector × stream) latency
  ├── detection_log.csv                                 #   raw firing events
  ├── drift_detection_heatmap.png                       #   Chapter 6 moneyshot: 2-panel (latency + FPR) heatmap
  └── benchmark_summary.json                            #   config + winning detector + elapsed
data/processed/adaptive_benchmark_timeline_medium/      # Phase 7 Iter A drift-aware adaptive benchmark:
  ├── table_7_1_strategy_summary.csv                    #   Table 7.1 (overall + drift-phase PR-AUC + cost-ledger per strategy)
  ├── sliding_pr_auc.csv                                #   long-form sliding PR-AUC (120 s window / 30 s stride) per strategy
  ├── retrain_log.csv                                   #   per-fit event log (t_s / reason / pool size / CPU sec)
  ├── per_window_scores.csv                             #   per-(strategy, window) anomaly score for the appendix
  ├── adaptive_pr_auc_over_time.png                     #   Chapter 7 moneyshot: 2-panel (4-line PR-AUC vs time + cost-vs-gain bar)
  └── benchmark_summary.json                            #   config + drift-phase ids + acceptance verdict
data/processed/end_to_end_demo_timeline_medium/         # Phase 9 Iter A end-to-end adaptive demo:
  ├── intervention_log.csv                              #   per-intervention record (t_trigger / drift_streams / current cfg / recommended cfg / 3-KPI predictions with 90% CI / signed uplift per KPI / scenario context echo)
  ├── per_window.csv                                    #   19 488-row anomaly-score trace (Phase 7 winner's per-window record)
  ├── retrain_log.csv                                   #   1 warmup + 7 drift-triggered refits with CPU sec + sample-pool sizes
  ├── sliding_pr_auc.csv                                #   PR-AUC over time (120 s window / 30 s stride) for Panel A
  ├── end_to_end_moneyshot.png                          #   Chapter 9 defense plate: 4-panel (sliding PR-AUC + anomaly score + intervention table + cumulative HOSR uplift step)
  └── demo_summary.json                                 #   config + cumulative uplift per KPI + acceptance verdict (D2/D3/D4/D5)
data/processed/external_validation/                     # Phase 11 external validation (Iter A + B):
  # Iter A — cross-seed replication on timeline_medium (5 seeds):
  ├── seed_replication_timeline_medium.csv              #   wide table: 5 seeds × ~40 namespaced <pipeline>__<metric> columns
  ├── seed_robustness_table_timeline_medium.csv         #   bootstrap mean ± 95 % CI per headline metric
  ├── seed_robustness_detector_table_timeline_medium.csv#   modal-share / stability per (pipeline, detector)
  ├── seed_robustness_acceptance_timeline_medium.json   #   machine-readable E1–E5 verdict (E2 split into E2a/E2b)
  ├── seed_robustness_boxplot_timeline_medium.png       #   6-panel box-plot figure for the Chapter 11 defense plate
  # Iter B — cross-scenario validation (medium vs dense_urban):
  ├── cross_scenario_table_timeline_medium_vs_timeline_dense_urban.csv     #   long-format per-pipeline metric pairs
  ├── cross_scenario_delta_timeline_medium_vs_timeline_dense_urban.csv     #   Δ + Δ% per metric
  ├── cross_scenario_acceptance_timeline_medium_vs_timeline_dense_urban.json  #   F1..F5 verdict
  ├── cross_scenario_bars_timeline_medium_vs_timeline_dense_urban.png      #   bar-chart figure for the Chapter 11.2 plate
  # Iter C — NordicDat face validity (real 5G-NSA op telemetry, no ground truth):
  ├── nordicdat_face_validity.md                        #   human-readable report with G1..G3 verdict + caveat
  ├── nordicdat_face_validity.json                      #   machine-readable verdict
  ├── nordicdat_face_validity.png                       #   4-panel figure (Chapter 11.3 plate)
  ├── nordicdat_anomaly_windows.csv                     #   per-flagged-window timestamps + PCA-AE scores
  └── nordicdat_drift_events.csv                        #   ADWIN change-point log
data/processed/surrogate_benchmark/                     # Phase 8 Iter A config-perf surrogate benchmark:
  ├── table_8_1_point_summary.csv                       #   Table 8.1 (per-target CV MAE / RMSE / R^2, HistGB)
  ├── table_8_2_coverage_summary.csv                    #   Table 8.2 (per-(target, naive|conformal) 90 % PI empirical coverage)
  ├── cv_point_long.csv                                 #   long-form per-(target, fold) point-estimate scores
  ├── cv_coverage_long.csv                              #   long-form per-(target, fold, variant) coverage
  ├── inverse_query_candidates.csv                      #   all 36 (TTT, hyst, A3) candidates scored + feasibility flag
  ├── inverse_query_recommendations.csv                 #   feasible subset sorted by HOSR desc, with conformal 90 % CI
  ├── mae_per_target.png                                #   per-target CV MAE bar with C2 (0.05) threshold line
  ├── reliability_diagram.png                           #   Chapter 8 moneyshot: 2-panel naive-vs-conformal coverage
  ├── inverse_query.png                                 #   Pareto-style HOSR x RLF scatter; top-3 (TTT, hyst, A3) annotated
  └── benchmark_summary.json                            #   config + dataset + scenario + acceptance verdict + top-3 recs
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
pip install --require-hashes -r requirements.txt
```

`requirements.txt` is a **hash-locked lockfile** generated from `requirements.in` via
`pip-tools` (126 packages pinned, every wheel SHA256-verified). Reviewers get a
byte-identical install. Maintainer workflow:

```bash
# edit top-level deps in requirements.in, then:
make pip-compile       # regenerate hash-locked requirements.txt
make pip-sync          # align active venv exactly with the lockfile (destructive)
```

Commit both files. Python 3.12 is the pinned interpreter (Docker image and CI use
3.12-slim). 3.11 also works for source installs but is not what Phase 5–11 results
were produced on.

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
| 4. Data generation (sweeps + drift + anomaly)   | **Iter A + B + C done** (4 drifts + 4 anomalies; 360-run static sweep for Phase 8; 30-phase production timeline; per-phase UE continuity with Random-Direction model; 88 MATLAB + 101 Python tests green) |
| 5. Anomaly benchmark                            | **Iter A + B-1 done** (5 detectors × 4 anomaly types × bootstrap 95 % CI × window sweep × per-family ablation on timeline_medium; A-2 PR-AUC = 0.858 [OneClassSVM], A-1 = 0.751 [PCA-AE]; A-3/A-5 documented at noise floor — motivates Phase 7). Iter B-2 (LSTM-AE + A-3 timeline fix) deferred. |
| 6. Drift benchmark                              | **Iter A done** (7 detectors × 6 streams × bootstrap median-latency CI on timeline_medium; 8/8 drifts detected by all detectors; precision/recall trade-off captured: ADWIN best FPR ≤ 1 %, MMD/Energy-batch best latency 3 s but 18 % FPR; DDM dominates abrupt mobility/reconfig drifts). 38 drift tests green. |
| 7. Adaptive framework                           | **Iter A done** (4 retraining strategies × PCA-AE base × ADWIN trigger on `timeline_medium`; both C2 + C3 acceptance PASS: drift-triggered-filtered overall PR-AUC = 0.248 vs periodic 0.115, drift-phase 0.187 vs static 0.057 (+0.13, target ≥ +0.10). Headline finding: **naive retraining self-poisons** (periodic/drift-naive ≈ 0.04 drift-phase), **self-supervised bottom-80 % filter recovers and beats static** under drift). 49 adaptive tests green. |
| 8. Config–performance surrogate                 | **Iter A done** (HistGB point + Conformal Quantile GB on `sweep_config_perf.parquet` (360 rows × 3 mobility KPIs); group-5-fold CV + inverse query @ median deployment. All 4 acceptance criteria PASS: HOSR MAE = 0.043 (target ≤ 0.05), R² = {0.96, 0.95, 0.72}, conformal 90 % PI empirical coverage = {0.84, 0.72, 0.89} (target ≥ 0.70). Headline finding: **naive quantile GB intervals are systematically too narrow under grouped CV** (~25 pp under-coverage for RLF_rate); **split-conformal calibration recovers** the coverage guarantee across all 3 targets. 12 / 36 inverse-query recommendations satisfy HOSR ≥ 0.95 ∧ RLF ≤ 0.05 ∧ PP ≤ 0.10; top-3 all use TTT = 256 ms.) 72 config_perf tests green. |
| 9. End-to-end demo                              | **Iter A done** (full pipeline replay on `timeline_medium`: PCA-AE + ADWIN drift detector + filtered retrain + HistGB / ConformalQuantileGB surrogate; 7 drift-triggered interventions in 1 800 s, 9 s walk-forward + 55 s total wall clock. All 4 acceptance criteria PASS. Headline finding: **operator's "bad config push" (D-4 #2 raised TTT 256 → 1024 at t = 780 s) detected within 40 s, surrogate inverse-query recommended (TTT=256, hyst=3, A3=3) recovery config worth +0.619 predicted HOSR uplift in the prevailing deployment context** (0.371 → 0.990). Other 6 interventions are conservative micro-adjustments — system does not over-react when current config is sane. Single 4-panel `end_to_end_moneyshot.png` is the Chapter 9 defense plate.) 35 demo tests green. |
| 11. External validation                         | **Iter A + B + C done** (Iter A cross-seed `timeline_medium` 5 seeds: 5 / 6 PASS; Iter B cross-scenario `timeline_dense_urban` 37 cells × 24 UEs: **5 / 5 PASS**; Iter C NordicDat face validity 727 h real 5G-NSA fleet telemetry: **3 / 3 G1-G3 PASS**. Headline findings: (a) Phase 9 HOSR uplift reproduces to 4 decimals across seeds (+0.6214 ± 0.0001) and **scales to +1.2413 in dense_urban**; (b) filtered > naive replicates in both scenarios; (c) drift volume CV = 0.04 + identical winner (Energy-batch) across all scenarios + seeds; (d) **Simpson's paradox is scenario-conditional** — per-type-mean winner splits across {LOF, OCSVM, MLP-AE} on medium tight top-cluster but resolves to clean IsoForest dominance on dense_urban, while overall pooled winner is IsoForest in BOTH scenarios; (e) **face-validity on 30 days of real op telemetry**: PCA-AE flags 1.2 % of eval windows (well under 10 % volume-sanity floor), peak at 13:00 commute hour (3.5 × the daily mean) is consistent with operational load shift — no precision/recall claim because no ground truth, disclaimer enforced by unit test. Iter B introduced the `--variant dense_urban` preset and the `cross_scenario_compare` comparator; Iter C uses fully self-supervised PCA-AE + ADWIN with no simulator-trained weights transferred.) 79 external-validation tests green. |
| 10. Writing + reproducibility package           | planned (runs after Phase 11)       |

See [`docs/plan.md`](docs/plan.md) for live, per-task status.
