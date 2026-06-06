"""Phase 11 Iter B — unit tests for ``tools.gen_timeline`` ``--variant`` flag.

Verifies that the ``dense_urban`` preset (and ``default`` no-op) applies
the correct global and per-phase deltas and keeps the rest of the
timeline machinery (drift placement, anomaly counts, RNG determinism)
working.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import gen_timeline as gt


def _build_args(tmp_path: Path, **overrides):
    base = dict(
        out=str(tmp_path / "tl.json"),
        timeline_id="variant_test",
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
        variant="default",
    )
    base.update(overrides)

    class A:
        pass

    a = A()
    for k, v in base.items():
        setattr(a, k, v)
    return a


# ---------------------------------------------------------------------------
# Variant registry sanity
# ---------------------------------------------------------------------------

class TestVariantRegistry:
    def test_default_variant_no_op(self):
        v = gt.VARIANTS["default"]
        assert v["global_overrides"] == {}
        assert v["phase_params"] == {}
        assert v["a3_cell_pool"] == list(range(1, 8))

    def test_dense_urban_variant_present(self):
        assert "dense_urban" in gt.VARIANTS
        v = gt.VARIANTS["dense_urban"]
        assert v["global_overrides"]["n_ue"] == 24
        assert v["global_overrides"]["area_m"] == 750.0
        lp = v["phase_params"]["layout_params"]
        assert lp["n_tiers"] == 3
        assert lp["isd_m"] == 350.0
        # MATLAB simulator/+utils/run_phase.m requires h_bs_m alongside
        # n_tiers and isd_m; omitting it crashes the sim with a missing
        # field error mid-run (Phase 11 Iter B preflight bug).
        assert lp["h_bs_m"] == 25.0
        # A-3 cell pool should be broader (tiers 0..2 -> cells 1..19)
        assert v["a3_cell_pool"] == list(range(1, 20))

    def test_unknown_variant_raises(self, tmp_path):
        args = _build_args(tmp_path, variant="nope")
        with pytest.raises(ValueError, match="unknown variant"):
            gt.build_timeline(args)


# ---------------------------------------------------------------------------
# Default variant preserves legacy behavior
# ---------------------------------------------------------------------------

class TestDefaultVariant:
    def test_no_layout_injection(self, tmp_path):
        args = _build_args(tmp_path)
        tl = gt.build_timeline(args)
        for p in tl["phases"]:
            if p["scenario"] == "baseline":
                assert "layout_params" not in p["params"]

    def test_globals_unchanged(self, tmp_path):
        args = _build_args(tmp_path, n_ue=8, area_m=1000.0)
        tl = gt.build_timeline(args)
        assert tl["global"]["n_ue"] == 8
        assert tl["global"]["area_m"] == 1000.0

    def test_a3_pool_is_inner_tier(self, tmp_path):
        args = _build_args(tmp_path, drifts_per_type=1, anomalies_per_type=10)
        tl = gt.build_timeline(args)
        a3 = [a for a in tl["ground_truth_anomaly"] if a["type"] == "interference_spike"]
        all_cells = sorted(
            {c for entry in a3 for c in entry["affected_cell_ids"]}
        )
        assert all_cells, "no A-3 entries produced"
        assert max(all_cells) <= 7, f"A-3 should hit only cells 1..7 in default; got {all_cells}"
        assert min(all_cells) >= 1


# ---------------------------------------------------------------------------
# Dense-urban variant deltas
# ---------------------------------------------------------------------------

class TestDenseUrbanVariant:
    def test_global_overrides_applied(self, tmp_path):
        args = _build_args(tmp_path, variant="dense_urban", n_ue=8, area_m=1000.0)
        tl = gt.build_timeline(args)
        # CLI-provided n_ue=8 and area_m=1000 should be OVERRIDDEN by the variant
        # because the variant declares cross-scenario regime deltas.
        assert tl["global"]["n_ue"] == 24
        assert tl["global"]["area_m"] == 750.0

    def test_baseline_phases_inject_layout(self, tmp_path):
        args = _build_args(tmp_path, variant="dense_urban")
        tl = gt.build_timeline(args)
        for p in tl["phases"]:
            if p["scenario"] == "baseline":
                lp = p["params"].get("layout_params")
                assert lp is not None
                assert lp["n_tiers"] == 3
                assert lp["isd_m"] == 350.0
                assert lp["h_bs_m"] == 25.0  # required by MATLAB run_phase.m
                assert p["params"]["n_ue"] == 24
                assert p["params"]["area_m"] == 750.0

    def test_drift_phases_inherit_layout(self, tmp_path):
        # Drift phases should ALSO carry the variant layout so the
        # deployment stays consistent during drift episodes.
        args = _build_args(tmp_path, variant="dense_urban")
        tl = gt.build_timeline(args)
        for p in tl["phases"]:
            if p["scenario"] != "baseline":
                lp = p["params"].get("layout_params")
                assert lp is not None, f"drift phase {p['phase_id']} missing layout"
                assert lp["n_tiers"] == 3
                assert lp["h_bs_m"] == 25.0  # required by MATLAB run_phase.m

    def test_d1_traffic_shift_uses_higher_n_ue(self, tmp_path):
        # D-1's n_ue choices should be bumped to maintain "shift" semantics
        # (baseline=24 -> drift in {48, 60}). drifts_per_type=1 keeps the
        # 15-phase test timeline within the drift-slot spacing budget.
        args = _build_args(tmp_path, variant="dense_urban",
                           drifts_per_type=1)
        tl = gt.build_timeline(args)
        d1_phases = [
            p for p in tl["phases"]
            if p["scenario"] == "drift_traffic_shift"
        ]
        assert len(d1_phases) == 1
        for p in d1_phases:
            assert p["params"]["n_ue"] in {48, 60}

    def test_a3_pool_includes_tier2_cells(self, tmp_path):
        # With dense_urban the A-3 cell pool widens to 1..19; with
        # 30 A-3 instances at least one should hit cell id > 7.
        args = _build_args(tmp_path, variant="dense_urban",
                           anomalies_per_type=30,
                           n_phases=30,
                           head_baseline_phases=3,
                           tail_baseline_phases=3)
        tl = gt.build_timeline(args)
        a3 = [a for a in tl["ground_truth_anomaly"] if a["type"] == "interference_spike"]
        outer = [c for entry in a3 for c in entry["affected_cell_ids"] if c > 7]
        assert outer, f"no A-3 hit tier-2 cells (>7); pool: {sorted(set(c for e in a3 for c in e['affected_cell_ids']))}"

    def test_generator_metadata_records_variant(self, tmp_path):
        args = _build_args(tmp_path, variant="dense_urban")
        tl = gt.build_timeline(args)
        assert tl["generator"]["variant"] == "dense_urban"

    def test_determinism_under_same_rng_seed(self, tmp_path):
        args1 = _build_args(tmp_path, variant="dense_urban", rng_seed=99)
        args2 = _build_args(tmp_path, variant="dense_urban", rng_seed=99)
        tl1 = gt.build_timeline(args1)
        tl2 = gt.build_timeline(args2)
        # Sanity: same RNG seed -> identical anomaly placements
        assert (
            [a["anomaly_id"] for a in tl1["ground_truth_anomaly"]]
            == [a["anomaly_id"] for a in tl2["ground_truth_anomaly"]]
        )

    def test_cli_accepts_variant(self, tmp_path):
        out = tmp_path / "cli_variant.json"
        rc = gt.main([
            "--out", str(out),
            "--timeline-id", "cli_variant",
            "--n-phases", "10",
            "--drifts-per-type", "1",
            "--anomalies-per-type", "2",
            "--head-baseline-phases", "2",
            "--tail-baseline-phases", "2",
            "--rng-seed", "3",
            "--variant", "dense_urban",
        ])
        assert rc == 0
        import json as _json
        doc = _json.loads(out.read_text())
        assert doc["generator"]["variant"] == "dense_urban"
        assert doc["global"]["n_ue"] == 24


# ---------------------------------------------------------------------------
# Cross-variant invariants
# ---------------------------------------------------------------------------

class TestCrossVariantInvariants:
    @pytest.mark.parametrize("variant", ["default", "dense_urban"])
    def test_anomaly_count_matches_request(self, tmp_path, variant):
        args = _build_args(
            tmp_path,
            variant=variant,
            anomalies_per_type=5,
            drifts_per_type=1,
        )
        tl = gt.build_timeline(args)
        # 4 anomaly types x 5 instances = 20
        assert len(tl["ground_truth_anomaly"]) == 4 * 5

    @pytest.mark.parametrize("variant", ["default", "dense_urban"])
    def test_drift_count_matches_request(self, tmp_path, variant):
        # Use n_phases=30 so 4 types * 2 instances = 8 drift slots fit
        # within the spacing constraints (head+tail=4, middle=26 slots
        # with mandatory baseline gaps -> 13 usable drift slots).
        args = _build_args(tmp_path, variant=variant,
                           n_phases=30, drifts_per_type=2)
        tl = gt.build_timeline(args)
        # 4 drift types x 2 instances = 8
        assert len(tl["ground_truth_drift"]) == 4 * 2

    @pytest.mark.parametrize("variant", ["default", "dense_urban"])
    def test_head_baseline_is_clean(self, tmp_path, variant):
        args = _build_args(tmp_path, variant=variant, head_baseline_phases=3)
        tl = gt.build_timeline(args)
        head_pids = set(range(1, 4))
        anom_pids = {a["phase_id"] for a in tl["ground_truth_anomaly"]}
        drift_pids = {d["phase_id_start"] for d in tl["ground_truth_drift"]}
        assert not (head_pids & anom_pids), "head baseline phases should have no anomalies"
        assert not (head_pids & drift_pids), "head baseline phases should have no drift"
