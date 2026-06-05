"""Phase 6 — Drift detection on simulator timelines.

Stream builders, ground-truth labels, detector wrappers and a benchmark
orchestrator for the 4 drift types injected by Phase 4 timelines (D-1
traffic-shift, D-2 channel-swap, D-3 mobility, D-4 reconfig).

ADR-15 enforcement: drift detection is performed on mobility KPIs only
(no throughput / latency / jitter). The stream whitelist is the single
source of truth.

Sub-modules:
    streams   - Build univariate timestamped streams from samples /
                events parquet (fleet-level aggregates, 1 Hz cadence).
    labels    - Map ground_truth_drift -> per-(stream, t) binary label
                using the `affected_kpis` column.
    detectors - Uniform `.update(x) -> bool` API over river ADWIN /
                KSWIN / PageHinkley / DDM / EDDM + custom MMD-batch /
                Energy-distance-batch detectors.
    metrics   - Detection latency, miss rate, FPR, bootstrap CI helpers.
    benchmark_eval - Full benchmark CLI (drift x detector x stream
                matrix on `timeline_medium`).
"""

from __future__ import annotations
