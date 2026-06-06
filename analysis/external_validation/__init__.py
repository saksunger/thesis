"""Phase 11 — External validation suite.

Phases 5–9 all train and evaluate on a single realization of
``timeline_medium`` (one MATLAB ``master_seed``, one timeline JSON
structure). That answers internal-validity questions (bootstrap CI,
grouped CV, conformal calibration) but leaves a thesis-defense-grade
external-validity gap: *do the headline findings hold when we vary the
simulator's RNG or the scenario parameter regime?*

Phase 11 closes that gap with three independent dimensions of
generalization, each shipping its own chapter sub-section, figure and
table:

    Iter A — Cross-seed validation
        Hold the timeline structure fixed (same JSON, same
        ``--rng-seed=42``) and vary the MATLAB simulator's
        ``master_seed`` ∈ {42..46}. Isolates simulator noise (channel
        shadowing realization, UE position draws, RNG-driven event
        timing) from scenario randomness. Direct analogy: training the
        same model on the same data with different initialization
        seeds — measures noise in the procedure, not the data.

    Iter B — Cross-scenario validation
        Generate ``timeline_dense_urban.json`` with a deliberately
        different parameter regime (more cells, more UEs, urban speed
        profile) and re-run the full pipeline. Tests robustness to the
        deployment context.

    Iter C — NordicDat face validity
        Apply trained detectors (ADWIN + PCA-AE) to NordicDat
        operational data without retraining. No ground-truth labels —
        the claim is plausibility, not precision/recall.

ADR-15 enforcement
------------------
All scoring, all reported KPIs are mobility-family (HOSR, RLF_rate,
ping_pong_rate). Downlink throughput is excluded.

Modules:
    seed_robustness.py           : Iter A aggregator (bootstrap CI +
                                   per-metric box plots across seeds)
    cross_scenario_compare.py    : Iter B comparator
                                   (timeline_medium vs dense_urban)
    nordicdat_apply.py           : Iter C inference + face-validity report

See ``experiments/run_seed_replication.py`` for the Iter A
orchestrator (CLI), and ``docs/plan.md`` Phase 11 for acceptance
criteria E1..E5 / F1..F5 / G1..G3.
"""
