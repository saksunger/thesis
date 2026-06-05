"""Phase 8 — Calibrated config-performance surrogate.

Predicts mobility KPIs (HOSR, RLF_rate, ping_pong_rate) from the
controllable handover knobs (TTT, hysteresis, A3 offset) plus the
deployment-context summary statistics (RSRP/SINR/RSRQ percentiles)
that the simulator exposes per static-sweep run.

Why a surrogate?
    1. The MATLAB simulator costs ~30 seconds per (config × seed) point.
       The Phase 4 Iter C `sweep_static` shipped 360 such points
       (3 × 4 × 3 × 10 = 360). Running a full grid search at deployment
       time is impractical (~10 000 candidate configs × 30 s = 80 hours).
    2. Phase 9's end-to-end demo needs to answer "given current
       deployment context, which (TTT, hyst, A3) maximises HOSR under
       an RLF-rate ceiling?". A fitted surrogate answers in milliseconds.
    3. Calibrated uncertainty (Iter A: quantile regression) lets the
       demo show confidence intervals on the recommendation.

ADR-15 enforcement
------------------
Targets are mobility KPIs only (HOSR, RLF_rate, ping_pong_rate).
Throughput is intentionally absent. HOFR_rate is omitted because it
equals `1 - HOSR` exactly by construction (verified on every row of
the training matrix); modelling it independently would be statistically
redundant and physically meaningless.

Modules
-------
- :mod:`analysis.config_perf.data`        loader + schema + grouped CV
- :mod:`analysis.config_perf.surrogate`   HistGB point + quantile wrappers
- :mod:`analysis.config_perf.eval`        MAE / R^2 / RMSE + reliability
- :mod:`analysis.config_perf.inverse`     inverse-query grid search + Pareto
- :mod:`analysis.config_perf.benchmark_eval`  CLI orchestrator
"""
