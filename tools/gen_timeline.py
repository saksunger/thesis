#!/usr/bin/env python3
"""Parametric multi-phase timeline JSON generator (Phase 4 Iter C).

Builds a timeline JSON consumable by `simulator/runners/build_timeline.m`.
The output matches the schema of the hand-written `timeline_iter_b.json`
reference, just scaled up: more phases, more drift instances, many
anomaly instances per type so Phase 5 Iter B can compute per-anomaly
PR-AUC with meaningful statistical power.

Phase layout:

    [ head_baseline_phases ] [ drift + baseline middle ] [ tail_baseline_phases ]
    ^^^^^^^^^^^^^^^^^^^^^^^                              ^^^^^^^^^^^^^^^^^^^^^^^
     clean training data    drift events evenly spaced    clean recovery window
     (no anomalies either)  with anomalies sprinkled

Drift types (D-1..D-4) are each instantiated `drifts_per_type` times and
placed in evenly-spaced middle slots. Anomaly instances are spread across
phases [head_baseline_phases + 1 .. n_phases]; each type
(A-1/A-2/A-3/A-5) gets `anomalies_per_type` instances with randomised
parameters (within realistic ranges) and randomised affected
UEs/cells/windows.

Reproducibility: every random choice uses `--rng-seed` (default 42).
ADR-14 + ADR-15 are honoured by construction (NR SA scope, mobility-only
KPIs).

Usage examples
--------------

  # Default 30-phase production timeline (used for timeline_medium.json):
  python -m tools.gen_timeline \
      --out simulator/+scenarios/timelines/timeline_medium.json \
      --timeline-id timeline_medium \
      --description "30-phase production timeline (Phase 4 Iter C). 2 instances per drift, 10 anomalies per type."

  # Smaller smoke timeline (useful for local debugging):
  python -m tools.gen_timeline \
      --out /tmp/timeline_smoke.json \
      --timeline-id timeline_smoke \
      --n-phases 8 --drifts-per-type 1 --anomalies-per-type 2
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Variant catalogue (Phase 11 Iter B — cross-scenario generalisation).
#
# A *variant* bundles a coherent set of deployment-regime deltas
# (number of cells, UE count, anomaly-affected cell pool, drift D-1
# n_ue choices) that all need to move together to make the new
# scenario internally consistent. Variants are layered ON TOP of the
# CLI flags so callers can still tune any individual axis.
# ---------------------------------------------------------------------------
VARIANTS: dict[str, dict[str, Any]] = {
    "default": {
        # Production timeline_medium regime (2-tier hex, 19 cells, 12 UEs).
        "global_overrides": {},        # do not change CLI-derived globals
        "phase_params": {},            # no per-phase injection
        "a3_cell_pool": list(range(1, 8)),    # tier-0 + tier-1
        "d1_n_ue_choices": [24, 30],
    },
    "dense_urban": {
        # Cross-scenario Iter B regime (3-tier hex, 37 cells, 24 UEs).
        # area_m is shrunk in proportion so UE density (UEs/km^2) rises.
        # speed_mps left unchanged: D-3 still varies it; baseline keeps
        # mixed pedestrian/vehicular per scenarios.baseline default 10 m/s.
        "global_overrides": {"n_ue": 24, "area_m": 750.0},
        "phase_params": {
            "n_ue": 24,
            "area_m": 750.0,
            "layout_params": {"n_tiers": 3, "isd_m": 350.0},
        },
        "a3_cell_pool": list(range(1, 20)),   # tier-0..2 (cells where UEs realistically camp)
        "d1_n_ue_choices": [48, 60],
    },
}


# ---------------------------------------------------------------------------
# Drift catalogue (must align with `simulator/+scenarios/drift_*.m`).
# D-1's n_ue choices are variant-dependent so the "traffic shift"
# stays a real shift (e.g. doubles baseline UEs) regardless of variant.
# ---------------------------------------------------------------------------
def _drift_types_for_variant(variant_name: str) -> list[dict[str, Any]]:
    """Return the 4 drift entries with variant-tuned D-1 n_ue choices."""
    variant = VARIANTS[variant_name]
    n_ue_choices = tuple(variant["d1_n_ue_choices"])
    return [
        {
            "id": "D-1",
            "scenario": "drift_traffic_shift",
            "params_template": lambda choices=n_ue_choices: {
                "n_ue": random.choice(list(choices))
            },
            "affected_kpis": ["event_rate", "ping_pong_rate"],
            "expected_direction": "up",
            "note": "Traffic-load shift via UE-density bump (n_ue raised).",
        },
        {
            "id": "D-2",
            "scenario": "drift_channel_swap",
            "params_template": lambda: {},
            "affected_kpis": ["rsrp_serving_dbm", "sinr_serving_db", "rsrq_serving_db"],
            "expected_direction": "shape",
            "note": "Channel swap UMa -> UMi-Street Canyon (BS height 25 -> 10 m).",
        },
        {
            "id": "D-3",
            "scenario": "drift_mobility",
            "params_template": lambda: {"speed_mps": random.choice([20, 25, 30])},
            "affected_kpis": ["ho_rate", "dwell_time_s"],
            "expected_direction": "shape",
            "note": "Mobility shift to faster UE speed.",
        },
        {
            "id": "D-4",
            "scenario": "drift_reconfig",
            "params_template": lambda: {
                "ho_params": {
                    "ttt_s": random.choice([0.512, 1.024]),
                    "hyst_db": random.choice([4, 6]),
                }
            },
            "affected_kpis": ["hosr", "ping_pong_rate"],
            "expected_direction": "shape",
            "note": "Operator pushes new RRM config (TTT / hyst raised).",
        },
    ]


# ---------------------------------------------------------------------------
# Anomaly catalogue (must align with `simulator/+anomalies/*.m`)
# A-3's affected-cell pool is variant-dependent (dense_urban has more
# cells, so the pool of "high-traffic" cells where UEs realistically
# camp is bigger).
# ---------------------------------------------------------------------------
def _ue_sample(n_ue: int, k_min: int, k_max: int) -> list[int]:
    """Sample k unique UE ids in [1, n_ue]."""
    k = random.randint(k_min, min(k_max, n_ue))
    return sorted(random.sample(range(1, n_ue + 1), k=k))


def _anomaly_types_for_variant(variant_name: str) -> list[dict[str, Any]]:
    variant = VARIANTS[variant_name]
    a3_pool = list(variant["a3_cell_pool"])
    return [
        {
            "id": "A-1",
            "type": "rlf_burst",
            "params_fn": lambda n_ue: {
                "affected_ue_ids": _ue_sample(n_ue, 1, 3),
                "delta_db": random.choice([-15, -18, -20]),
            },
            "note": "Transient SINR collapse on 1-3 UEs (should trigger RLF events).",
        },
        {
            "id": "A-2",
            "type": "meas_glitch",
            "params_fn": lambda n_ue: {
                "affected_ue_ids": [random.randint(1, n_ue)],
                "stuck_value_dbm": random.choice([-95, -90, -85]),
            },
            "note": "Stuck-at RSRP on a single UE (flat-line in samples).",
        },
        {
            "id": "A-3",
            "type": "interference_spike",
            "params_fn": lambda _n_ue, pool=tuple(a3_pool): {
                "affected_cell_ids": sorted(
                    random.sample(list(pool), k=random.randint(1, 2))
                ),
                "delta_db": random.choice([-8, -10, -12]),
            },
            "note": "Per-cell interference spike, viewable by all UEs that camp on the targeted cell.",
        },
        {
            "id": "A-5",
            "type": "slow_degrade",
            "params_fn": lambda n_ue: {
                "affected_ue_ids": [random.randint(1, n_ue)],
                "rate_db_per_s": random.choice([-0.10, -0.15, -0.20]),
            },
            "note": "Linear SINR ramp on a single UE (antenna damage proxy).",
        },
    ]


# Backwards-compat: code paths that still import DRIFT_TYPES / ANOMALY_TYPES
# (e.g. unit tests pre-Phase-11) keep working with the "default" variant.
DRIFT_TYPES = _drift_types_for_variant("default")
ANOMALY_TYPES = _anomaly_types_for_variant("default")


# ---------------------------------------------------------------------------
# Layout planning
# ---------------------------------------------------------------------------
def _plan_drift_slots(
    n_phases: int,
    head: int,
    tail: int,
    n_drift_phases: int,
) -> list[int]:
    """Evenly distribute drift phases across the middle of the timeline.

    Drift phases are placed at quantile positions inside the middle slot
    range so they are visually evenly spaced even when n_drift_phases
    divides middle_slots unevenly. At least one baseline phase always
    separates consecutive drifts (mimicking real operator behaviour:
    incident -> recovery -> next incident).
    """
    middle_slots = n_phases - head - tail
    if middle_slots <= 0:
        raise ValueError(
            f"n_phases ({n_phases}) too small for head+tail "
            f"({head}+{tail}) baselines"
        )
    if n_drift_phases > middle_slots:
        raise ValueError(
            f"need {n_drift_phases} drift slots but only {middle_slots} "
            f"middle phases available (consider lowering --drifts-per-type "
            f"or raising --n-phases)"
        )
    if n_drift_phases == 0:
        return []

    # Place drifts at evenly spaced fractional positions inside the
    # middle range. Ensure unique integer phase ids and at least one
    # baseline gap between consecutive drifts.
    first_pid = head + 1
    last_pid = head + middle_slots
    span = last_pid - first_pid
    raw = [
        first_pid + round(span * (i + 0.5) / n_drift_phases)
        for i in range(n_drift_phases)
    ]

    # Enforce at least 1 baseline between drifts by pushing collisions
    # forward (and bounded by last_pid).
    drift_pids: list[int] = []
    for pid in raw:
        if drift_pids and pid <= drift_pids[-1] + 1:
            pid = drift_pids[-1] + 2
        if pid > last_pid:
            # Try to wrap backwards instead
            pid = last_pid
            while pid in drift_pids:
                pid -= 1
        drift_pids.append(pid)

    drift_pids = sorted(set(drift_pids))
    if len(drift_pids) != n_drift_phases:
        # Pathological: not enough room for the spacing constraint.
        # Fall back to dense packing (every other slot).
        drift_pids = list(range(first_pid, last_pid + 1, 2))[:n_drift_phases]

    return sorted(drift_pids)


def _assign_drift_types(
    drift_types: list[dict[str, Any]],
    n_drift_phases: int,
    drifts_per_type: int,
) -> list[dict[str, Any]]:
    """Build a shuffled list of drift dicts: each type appears `drifts_per_type` times."""
    bag: list[dict[str, Any]] = []
    for drift in drift_types:
        for _ in range(drifts_per_type):
            bag.append(drift)
    if len(bag) != n_drift_phases:
        raise AssertionError(
            f"internal: bag size {len(bag)} != n_drift_phases {n_drift_phases}"
        )
    random.shuffle(bag)
    return bag


def _build_anomaly_entry(
    atype: dict[str, Any],
    anomaly_counter: int,
    phase_id: int,
    phase_duration_s: float,
    n_ue_for_phase: int,
) -> dict[str, Any]:
    """Construct a single ground_truth_anomaly entry with random window/params."""
    # Pick a window that fits inside [0, phase_duration_s]. Anomaly
    # window 8-20 s wide is wide enough that several sliding windows
    # (5 s width) will be labelled positive, but narrow enough that the
    # phase is mostly clean so the per-phase FPR stays interpretable.
    t_start = random.uniform(2.0, max(2.0, phase_duration_s - 18.0))
    duration = random.uniform(8.0, 20.0)
    t_end = min(t_start + duration, phase_duration_s - 1.0)

    entry: dict[str, Any] = {
        "anomaly_id": f"{atype['id']}-{anomaly_counter:02d}",
        "type": atype["type"],
        "phase_id": phase_id,
        "t_start_s": round(t_start, 2),
        "t_end_s": round(t_end, 2),
    }
    entry.update(atype["params_fn"](n_ue_for_phase))
    entry["note"] = atype["note"]
    return entry


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def build_timeline(args: argparse.Namespace) -> dict[str, Any]:
    random.seed(args.rng_seed)

    # Variant is optional for backwards-compat with pre-Phase-11 callers
    # that build the namespace by hand without the flag.
    variant_name = getattr(args, "variant", "default")
    if variant_name not in VARIANTS:
        raise ValueError(
            f"unknown variant '{variant_name}'. Known: {sorted(VARIANTS)}"
        )
    variant = VARIANTS[variant_name]
    drift_types = _drift_types_for_variant(variant_name)
    anomaly_types = _anomaly_types_for_variant(variant_name)
    baseline_phase_params: dict[str, Any] = dict(variant["phase_params"])

    n_drift_phases = args.drifts_per_type * len(drift_types)
    drift_pids = _plan_drift_slots(
        args.n_phases, args.head_baseline_phases, args.tail_baseline_phases, n_drift_phases
    )
    drift_bag = _assign_drift_types(drift_types, n_drift_phases, args.drifts_per_type)
    drift_map: dict[int, dict[str, Any]] = {pid: drift_bag[i] for i, pid in enumerate(drift_pids)}

    # --- Phases ---
    # Variant-specific overrides (e.g. dense_urban's layout_params) are
    # injected into BOTH baseline and drift phases so the layout stays
    # consistent across the whole timeline. Drift-specific params override
    # any variant defaults (e.g. drift_traffic_shift's n_ue takes
    # precedence over the variant's baseline n_ue).
    phases: list[dict[str, Any]] = []
    phase_n_ue: dict[int, int] = {}
    variant_default_n_ue = int(baseline_phase_params.get("n_ue", args.n_ue))
    for phase_id in range(1, args.n_phases + 1):
        params = dict(baseline_phase_params)
        if phase_id in drift_map:
            d = drift_map[phase_id]
            # drift params override variant baseline defaults
            params.update(d["params_template"]())
            phases.append(
                {"phase_id": phase_id, "scenario": d["scenario"], "params": params}
            )
            phase_n_ue[phase_id] = int(params.get("n_ue", variant_default_n_ue))
        else:
            phases.append(
                {"phase_id": phase_id, "scenario": "baseline", "params": params}
            )
            phase_n_ue[phase_id] = variant_default_n_ue

    # --- Ground truth drift ---
    ground_truth_drift = [
        {
            "drift_id": d["id"],
            "phase_id_start": pid,
            "phase_id_end": pid,
            "affected_kpis": d["affected_kpis"],
            "expected_direction": d["expected_direction"],
            "note": d["note"],
        }
        for pid, d in sorted(drift_map.items())
    ]

    # --- Ground truth anomalies ---
    # Anomalies live anywhere from phase head_baseline_phases + 1 onwards
    # so the first `head` phases stay strictly clean (used by detectors
    # as training data).
    anomaly_phase_pool = list(
        range(args.head_baseline_phases + 1, args.n_phases + 1)
    )
    ground_truth_anomaly: list[dict[str, Any]] = []
    counter = 1
    for atype in anomaly_types:
        for _ in range(args.anomalies_per_type):
            phase_id = random.choice(anomaly_phase_pool)
            ground_truth_anomaly.append(
                _build_anomaly_entry(
                    atype=atype,
                    anomaly_counter=counter,
                    phase_id=phase_id,
                    phase_duration_s=args.phase_duration_s,
                    n_ue_for_phase=phase_n_ue[phase_id],
                )
            )
            counter += 1
    ground_truth_anomaly.sort(key=lambda a: (a["phase_id"], a["t_start_s"]))

    return {
        "timeline_id": args.timeline_id,
        "description": args.description
        or (
            f"Generated timeline: {args.n_phases} phases × "
            f"{args.phase_duration_s:g} s, {n_drift_phases} drift phases "
            f"({args.drifts_per_type} per type), "
            f"{len(ground_truth_anomaly)} anomalies "
            f"({args.anomalies_per_type} per type)."
        ),
        "scope_note": (
            "Per ADR-14: 5G NR SA, intra-RAT, inter-gNB Xn HO only. "
            "Per ADR-15: mobility-family KPIs only (TS 28.554 §6.3.1-2)."
        ),
        "generator": {
            "tool": "tools.gen_timeline",
            "variant": variant_name,
            "rng_seed": args.rng_seed,
            "n_phases": args.n_phases,
            "drifts_per_type": args.drifts_per_type,
            "anomalies_per_type": args.anomalies_per_type,
            "head_baseline_phases": args.head_baseline_phases,
            "tail_baseline_phases": args.tail_baseline_phases,
        },
        "global": {
            "master_seed": args.master_seed,
            "n_ue": int(variant["global_overrides"].get("n_ue", args.n_ue)),
            "area_m": float(variant["global_overrides"].get("area_m", args.area_m)),
            "default_duration_s": args.phase_duration_s,
            "carry_over_ues": args.carry_over_ues,
        },
        "phases": phases,
        "ground_truth_drift": ground_truth_drift,
        "ground_truth_anomaly": ground_truth_anomaly,
    }


def _bool_arg(s: str) -> bool:
    return s.strip().lower() in {"true", "1", "yes", "on"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--out", required=True, help="Output JSON path")
    p.add_argument("--timeline-id", required=True, help="timeline_id field in the JSON")
    p.add_argument("--description", default="", help="Free-text description")
    p.add_argument("--n-phases", type=int, default=30, help="Total phases (default 30)")
    p.add_argument("--phase-duration-s", type=float, default=60.0,
                   help="Per-phase duration in seconds (default 60)")
    p.add_argument("--master-seed", type=int, default=42, help="Simulator master_seed")
    p.add_argument("--n-ue", type=int, default=12, help="Baseline UE count (D-1 may raise it)")
    p.add_argument("--area-m", type=float, default=1500.0, help="UE spawn box half-width (m)")
    p.add_argument("--carry-over-ues", type=_bool_arg, default=True,
                   help="Thread UE positions across phases (default true for production timelines)")
    p.add_argument("--drifts-per-type", type=int, default=2,
                   help="Instances per drift type (D-1..D-4 each get this many)")
    p.add_argument("--anomalies-per-type", type=int, default=10,
                   help="Instances per anomaly type (A-1/A-2/A-3/A-5 each get this many)")
    p.add_argument("--head-baseline-phases", type=int, default=3,
                   help="Initial clean phases (no anomalies, no drifts; training data)")
    p.add_argument("--tail-baseline-phases", type=int, default=3,
                   help="Final clean phases (recovery window for post-drift evaluation)")
    p.add_argument("--rng-seed", type=int, default=42,
                   help="Seed for Python-side random selections (reproducibility)")
    p.add_argument("--variant", default="default",
                   choices=sorted(VARIANTS),
                   help="Scenario variant preset (default: production timeline_medium regime; "
                        "dense_urban: 3-tier hex, 24 UEs, 750 m area, isd_m=350 — Phase 11 "
                        "Iter B cross-scenario benchmark).")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tl = build_timeline(args)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(tl, indent=2) + "\n")

    n_drift = len(tl["ground_truth_drift"])
    n_anom = len(tl["ground_truth_anomaly"])
    total_dur = args.n_phases * args.phase_duration_s
    print(f"Generated {out_path}")
    print(f"  timeline_id     : {args.timeline_id}")
    print(f"  phases          : {args.n_phases} ({n_drift} drift, {args.n_phases - n_drift} baseline)")
    print(f"  total duration  : {total_dur:g} s ({total_dur/60:.1f} min)")
    print(f"  drifts          : {n_drift} ({args.drifts_per_type}/type x 4 types)")
    print(f"  anomalies       : {n_anom} ({args.anomalies_per_type}/type x 4 types)")
    print(f"  carry_over_ues  : {args.carry_over_ues}")
    print(f"  master_seed     : {args.master_seed} (sim) / {args.rng_seed} (generator)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
