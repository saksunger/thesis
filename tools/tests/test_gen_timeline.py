"""Unit tests for tools.gen_timeline parametric timeline generator (Iter C)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import gen_timeline as gt


# ---------------------------------------------------------------------------
# Build / structure
# ---------------------------------------------------------------------------
def _default_args(tmp_path: Path, **overrides) -> object:
    base = dict(
        out=str(tmp_path / "tl.json"),
        timeline_id="test_tl",
        description="",
        n_phases=15,
        phase_duration_s=30.0,
        master_seed=11,
        n_ue=8,
        area_m=1000.0,
        carry_over_ues=True,
        drifts_per_type=1,
        anomalies_per_type=3,
        head_baseline_phases=2,
        tail_baseline_phases=2,
        rng_seed=7,
    )
    base.update(overrides)

    class A:
        pass

    a = A()
    for k, v in base.items():
        setattr(a, k, v)
    return a


def test_build_timeline_returns_expected_top_level_fields(tmp_path):
    args = _default_args(tmp_path)
    tl = gt.build_timeline(args)
    for f in [
        "timeline_id",
        "description",
        "scope_note",
        "generator",
        "global",
        "phases",
        "ground_truth_drift",
        "ground_truth_anomaly",
    ]:
        assert f in tl, f"missing field {f}"


def test_drift_count_matches_drifts_per_type(tmp_path):
    args = _default_args(tmp_path, drifts_per_type=2, n_phases=20)
    tl = gt.build_timeline(args)
    # 4 drift types × 2 = 8
    assert len(tl["ground_truth_drift"]) == 8
    drift_ids = [d["drift_id"] for d in tl["ground_truth_drift"]]
    for did in ["D-1", "D-2", "D-3", "D-4"]:
        assert drift_ids.count(did) == 2, f"{did} should appear 2x"


def test_anomaly_count_matches_anomalies_per_type(tmp_path):
    args = _default_args(tmp_path, anomalies_per_type=5)
    tl = gt.build_timeline(args)
    # 4 anomaly types × 5 = 20
    assert len(tl["ground_truth_anomaly"]) == 20
    types = [a["type"] for a in tl["ground_truth_anomaly"]]
    for at in ["rlf_burst", "meas_glitch", "interference_spike", "slow_degrade"]:
        assert types.count(at) == 5, f"{at} should appear 5x"


def test_head_baseline_phases_have_no_anomalies(tmp_path):
    args = _default_args(tmp_path, head_baseline_phases=3, anomalies_per_type=20)
    tl = gt.build_timeline(args)
    # phases 1, 2, 3 should be strictly clean
    for a in tl["ground_truth_anomaly"]:
        assert a["phase_id"] > 3, (
            f"anomaly {a['anomaly_id']} landed in head-baseline phase {a['phase_id']}"
        )


def test_head_and_tail_baseline_phases_are_actually_baseline(tmp_path):
    args = _default_args(tmp_path, head_baseline_phases=3, tail_baseline_phases=3, n_phases=20)
    tl = gt.build_timeline(args)
    for pid in [1, 2, 3, 18, 19, 20]:
        phase = next(p for p in tl["phases"] if p["phase_id"] == pid)
        assert phase["scenario"] == "baseline", (
            f"phase {pid} should be baseline but is {phase['scenario']}"
        )


def test_global_block_carries_carry_over_flag(tmp_path):
    args = _default_args(tmp_path, carry_over_ues=True)
    tl = gt.build_timeline(args)
    assert tl["global"]["carry_over_ues"] is True

    args = _default_args(tmp_path, carry_over_ues=False)
    tl = gt.build_timeline(args)
    assert tl["global"]["carry_over_ues"] is False


def test_anomaly_windows_inside_phase_duration(tmp_path):
    args = _default_args(tmp_path, phase_duration_s=60.0, anomalies_per_type=10)
    tl = gt.build_timeline(args)
    for a in tl["ground_truth_anomaly"]:
        assert 0 <= a["t_start_s"] < a["t_end_s"] <= 60.0, (
            f"anomaly {a['anomaly_id']} window [{a['t_start_s']}, {a['t_end_s']}] "
            f"out of [0, 60]"
        )


def test_rng_seed_makes_output_deterministic(tmp_path):
    args = _default_args(tmp_path, rng_seed=99)
    tl_a = gt.build_timeline(args)
    args = _default_args(tmp_path, rng_seed=99)
    tl_b = gt.build_timeline(args)
    # Same seed → same drift placement and same anomaly placement
    assert tl_a["ground_truth_drift"] == tl_b["ground_truth_drift"]
    assert tl_a["ground_truth_anomaly"] == tl_b["ground_truth_anomaly"]


def test_rng_seed_change_changes_anomaly_layout(tmp_path):
    args1 = _default_args(tmp_path, rng_seed=1, anomalies_per_type=10)
    args2 = _default_args(tmp_path, rng_seed=99999, anomalies_per_type=10)
    tl1 = gt.build_timeline(args1)
    tl2 = gt.build_timeline(args2)
    # Very unlikely the anomaly lists match exactly
    assert tl1["ground_truth_anomaly"] != tl2["ground_truth_anomaly"]


def test_phase_layout_too_small_raises(tmp_path):
    # 4 head + 4 tail = 8, plus 12 drift phases (3/type), total 20.
    # n_phases=10 cannot fit -> ValueError.
    args = _default_args(tmp_path, n_phases=10, drifts_per_type=3,
                         head_baseline_phases=4, tail_baseline_phases=4)
    with pytest.raises(ValueError):
        gt.build_timeline(args)


def test_a3_affected_cell_ids_within_layout(tmp_path):
    # Hex layout n_tiers=2 → 7 cells (ids 1..7). A-3 cells must be in that range.
    args = _default_args(tmp_path, anomalies_per_type=10, n_phases=20)
    tl = gt.build_timeline(args)
    for a in tl["ground_truth_anomaly"]:
        if a["type"] == "interference_spike":
            for cid in a["affected_cell_ids"]:
                assert 1 <= cid <= 7, f"cell id {cid} out of [1, 7]"


def test_affected_ue_ids_respect_phase_n_ue(tmp_path):
    # D-1 raises n_ue. If an anomaly lands in a D-1 phase, its
    # affected_ue_ids may legitimately exceed the global n_ue=8.
    # Other anomalies must stay within the phase's n_ue.
    args = _default_args(tmp_path, n_ue=8, drifts_per_type=2,
                         anomalies_per_type=20, n_phases=20)
    tl = gt.build_timeline(args)
    phase_n_ue = {}
    for p in tl["phases"]:
        phase_n_ue[p["phase_id"]] = int(p["params"].get("n_ue", 8))
    for a in tl["ground_truth_anomaly"]:
        if "affected_ue_ids" in a:
            for ue in a["affected_ue_ids"]:
                limit = phase_n_ue[a["phase_id"]]
                assert 1 <= ue <= limit, (
                    f"anomaly {a['anomaly_id']} in phase {a['phase_id']} "
                    f"references ue_id {ue} > n_ue {limit}"
                )


def test_cli_writes_file(tmp_path):
    out = tmp_path / "out.json"
    rc = gt.main(
        [
            "--out", str(out),
            "--timeline-id", "cli_test",
            "--n-phases", "10",
            "--drifts-per-type", "1",
            "--anomalies-per-type", "2",
            "--head-baseline-phases", "2",
            "--tail-baseline-phases", "2",
            "--rng-seed", "3",
        ]
    )
    assert rc == 0
    assert out.exists()
    tl = json.loads(out.read_text())
    assert tl["timeline_id"] == "cli_test"
    assert len(tl["phases"]) == 10
    assert len(tl["ground_truth_drift"]) == 4
    assert len(tl["ground_truth_anomaly"]) == 8


# ---------------------------------------------------------------------------
# Phase 11 Iter B - variant flag tests
# ---------------------------------------------------------------------------
