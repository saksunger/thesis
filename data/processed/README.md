# Processed Data

ML-ready features derived from `data/simulated/` and `data/raw_public/`. Built by Python analysis scripts under `analysis/`.

Gitignored. Reproduce via the relevant `make` target.

## Sub-directories (currently produced)

| Directory | Producer | Contents |
|---|---|---|
| `calibration_eda/`                       | `make eda`              | Phase 3 EDA figures (Bangladesh + NordicDat radio distributions) |
| `calibration_ks/`                        | `make calibrate`        | Phase 3 KS-test summary CSV + CDF/Q-Q overlays + sweep heatmaps |
| `anomaly_smoke_timeline_iter_b/`         | `make anomaly-smoke TIMELINE=timeline_iter_b` | Phase 5 Iter A smoke: per-phase / per-anomaly PR-AUC + FPR tables + 3-panel summary plot |
| `anomaly_benchmark_timeline_medium/`     | `make anomaly-benchmark`| Phase 5 Iter B full benchmark on `timeline_medium`: Tables 5.1 / 5.2 / 5.3 + `drift_degradation.png` (Chapter 5 moneyshot) + bootstrap-CI long-form CSVs |
| `drift_benchmark_timeline_medium/`       | `make drift-benchmark`  | Phase 6 Iter A full benchmark on `timeline_medium`: Tables 6.1 / 6.2 / 6.3 + `drift_detection_heatmap.png` (Chapter 6 moneyshot, 2-panel latency × FPR) + per-(drift × detector × stream) latency long-form |
| `adaptive_benchmark_timeline_medium/`    | `make adaptive-benchmark` | Phase 7 Iter A drift-aware adaptive benchmark on `timeline_medium`: Table 7.1 (per-strategy summary), `sliding_pr_auc.csv` (120 s eval window / 30 s stride), `retrain_log.csv` (per-fit cost ledger), `per_window_scores.csv`, `adaptive_pr_auc_over_time.png` (Chapter 7 moneyshot, 2-panel 4-line PR-AUC + cost-vs-gain). 4 strategies: static / periodic(180s) / drift-triggered-naive / drift-triggered-filtered (q=0.8). |

