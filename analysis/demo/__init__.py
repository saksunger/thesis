"""Phase 9 — End-to-end drift-aware adaptive demo.

Combines the three preceding ML pipelines into a single walk-forward
replay over `timeline_medium`::

    drift detection     (Phase 6 — ADWIN on HOSR_rate / RLF_rate streams)
        |
        v
    filtered anomaly retrain  (Phase 7 — PCA-AE with bottom-q score filter)
        |
        v
    surrogate-driven config recommendation  (Phase 8 — HistGB +
                                              ConformalQuantileGB)
        |
        v
    counterfactual KPI uplift  (surrogate.predict(current_cfg) vs
                                 surrogate.predict(recommended_cfg)
                                 under the same deployment context)

Why a separate Phase 9 demo (rather than just extending Phase 7)?
----------------------------------------------------------------
The Phase 7 adaptive benchmark answers *"does drift-aware retraining
beat static baselines for anomaly detection?"*. Phase 9 answers the
deployment-facing question: *"given that we detected drift and
retrained, what should the operator change?"*. The surrogate query is
the bridge between online detection and offline config tuning.

ADR-15 enforcement
------------------
All scoring, all surrogate targets, and all reported KPIs are mobility
KPIs only (HOSR, RLF_rate, ping_pong_rate). Throughput is intentionally
absent.

Iter A scope (this iteration)
-----------------------------
Counterfactual uplift is computed via :class:`Option C` (surrogate
prediction under the *same* deployment context for current vs
recommended config). The recommendation is *not* fed back to the
MATLAB simulator in this iteration — that requires per-phase config-
override plumbing in `runners/run_timeline.m`, deferred to Iter B
(Option D, two-timeline counterfactual).

The Iter A deliverable is the **defense moneyshot figure**
(`end_to_end_moneyshot.png`, 4-panel) plus a per-intervention CSV
that the thesis can reference verbatim.

Modules
-------
- :mod:`analysis.demo.context`        deployment-context + current-config lookup
- :mod:`analysis.demo.orchestrator`   walk-forward demo runner
- :mod:`analysis.demo.run_demo`       CLI entry + figure
"""
