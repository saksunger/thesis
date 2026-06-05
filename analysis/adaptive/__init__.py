"""Phase 7 — Drift-aware adaptive anomaly-detection framework.

Combines the Phase 5 anomaly detector (e.g. PCA-AE) with the Phase 6
drift detector (e.g. ADWIN on KPI streams) into a single orchestrator
that compares three retraining strategies:

    1. STATIC          : fit once, never refit.
    2. PERIODIC(K s)   : refit every K seconds of timeline time.
    3. DRIFT-TRIGGERED : refit whenever a watched drift detector fires.

The headline contribution is the 3-line "PR-AUC over time" figure that
shows when the drift-triggered strategy outperforms the periodic
baseline (and both outperform the static one) under simulated drift.
A cost ledger reports (#refits, samples used, CPU seconds) so the
comparison is operationally honest.

Modules:
    strategies.py     : RetrainStrategy ABC + Static / Periodic / DriftTriggered
    orchestrator.py   : replay-based walk-forward runner
    metrics.py        : sliding-window PR-AUC + cost-ledger summarisers
    benchmark_eval.py : CLI entry point (loads timeline, runs 3 strategies,
                        emits CSVs + figure under data/processed/)

See ``docs/plan.md`` Phase 7 for acceptance criteria.
"""
