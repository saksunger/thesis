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
| `surrogate_benchmark/`                   | `make surrogate-benchmark` | Phase 8 Iter A config-perf surrogate benchmark on `sweep_config_perf.parquet` (360 rows × 3 mobility KPIs). Tables 8.1 (per-target CV MAE/R²/RMSE, HistGB) + 8.2 (per-(target, naive\|conformal) 90 % PI coverage). 3 figures: `mae_per_target.png` (Chapter 8 accuracy panel), `reliability_diagram.png` (Chapter 8 moneyshot — naive QuantileGB under-covers, split-conformal recovers), `inverse_query.png` (Pareto HOSR × RLF scatter, top-3 (TTT, hyst, A3) annotated). `inverse_query_recommendations.csv` carries 12 feasible configs (HOSR ≥ 0.95 ∧ RLF ≤ 0.05 ∧ PP ≤ 0.10) with conformal 90 % CIs. |
| `end_to_end_demo_timeline_medium/`       | `make end2end-demo`     | Phase 9 Iter A end-to-end adaptive demo on `timeline_medium`: PCA-AE + ADWIN + Conformal-QuantileGB pipeline. `intervention_log.csv` (7 drift-triggered interventions with full current/recommended cfg + 3-KPI counterfactual predictions + 90 % CI + scenario context), `per_window.csv`, `retrain_log.csv`, `sliding_pr_auc.csv`, `end_to_end_moneyshot.png` (Chapter 9 defense plate — 4-panel sliding PR-AUC + anomaly score + intervention table + cumulative HOSR uplift). Headline: D-4 #2 bad-config push at t = 780 s detected within 40 s, surrogate recommended (TTT=256, hyst=3, A3=3) for +0.619 predicted HOSR recovery. |

