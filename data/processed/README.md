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

