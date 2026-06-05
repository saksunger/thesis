"""Unit tests for analysis.config_perf.data."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analysis.config_perf.data import (
    CONTROLLED_COLS,
    FEATURE_COLS,
    SCENARIO_COLS,
    TARGET_COLS,
    SweepTable,
    assert_hofr_redundant,
    candidate_config_grid,
    grouped_kfold_splits,
    load_sweep,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _synthetic_sweep(
    n_configs: int = 12, n_seeds: int = 5, hofr_violation: float = 0.0,
) -> pd.DataFrame:
    """Build a synthetic sweep DataFrame that satisfies the schema."""
    rng = np.random.default_rng(0)
    ttts = [256.0, 480.0, 1024.0]
    hysts = [0.0, 1.0, 3.0]
    a3s = [0.0, 3.0, 6.0]
    rows = []
    # Pad / clip to n_configs
    triples = [(t, h, a) for t in ttts for h in hysts for a in a3s][:n_configs]
    rid = 0
    for (t, h, a) in triples:
        for seed in range(1, n_seeds + 1):
            rid += 1
            hosr = float(np.clip(rng.uniform(0.5, 1.0), 0.0, 1.0))
            row = {
                "run_id": rid, "seed": seed,
                "ttt_ms": t, "hyst_db": h, "a3_off_db": a,
                "hosr": hosr,
                "hofr_rate": 1.0 - hosr + hofr_violation,  # violates if != 0
                "rlf_rate": float(rng.uniform(0.0, 0.1)),
                "ping_pong_rate": float(rng.uniform(0.0, 0.2)),
                "rsrp_p10_dbm": float(rng.uniform(-121, -118)),
                "rsrp_p50_dbm": float(rng.uniform(-115, -100)),
                "rsrp_p90_dbm": float(rng.uniform(-90, -75)),
                "sinr_p10_db": float(rng.uniform(-6, -4)),
                "sinr_p50_db": float(rng.uniform(-1, 2)),
                "sinr_p90_db": float(rng.uniform(13, 22)),
                "rsrq_p10_db": float(rng.uniform(-17.5, -16)),
                "rsrq_p50_db": float(rng.uniform(-14.5, -12.5)),
                "rsrq_p90_db": float(rng.uniform(-11.1, -10.8)),
            }
            rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

class TestSchema:
    def test_controlled_cols_in_feature_cols(self):
        for c in CONTROLLED_COLS:
            assert c in FEATURE_COLS

    def test_scenario_cols_in_feature_cols(self):
        for c in SCENARIO_COLS:
            assert c in FEATURE_COLS

    def test_feature_cols_partition(self):
        assert set(FEATURE_COLS) == set(CONTROLLED_COLS) | set(SCENARIO_COLS)
        assert len(FEATURE_COLS) == len(CONTROLLED_COLS) + len(SCENARIO_COLS)

    def test_targets_explicit(self):
        # HOFR_rate is intentionally NOT a target (redundant with HOSR)
        assert "hofr_rate" not in TARGET_COLS
        assert "hosr" in TARGET_COLS
        assert len(TARGET_COLS) == 3


# ---------------------------------------------------------------------------
# HOFR redundancy assertion
# ---------------------------------------------------------------------------

class TestHofrRedundant:
    def test_clean_passes(self):
        df = _synthetic_sweep(hofr_violation=0.0)
        assert_hofr_redundant(df)  # must not raise

    def test_violation_raises(self):
        df = _synthetic_sweep(hofr_violation=0.05)
        with pytest.raises(ValueError, match="HOFR_rate is not exactly 1 - HOSR"):
            assert_hofr_redundant(df)

    def test_tiny_rounding_tolerated(self):
        df = _synthetic_sweep(hofr_violation=1e-9)
        assert_hofr_redundant(df, atol=1e-6)  # must not raise


# ---------------------------------------------------------------------------
# SweepTable
# ---------------------------------------------------------------------------

class TestSweepTable:
    def test_X_Y_shapes(self):
        df = _synthetic_sweep(n_configs=6, n_seeds=4)
        st = SweepTable(df=df)
        assert st.X.shape == (24, len(FEATURE_COLS))
        assert st.Y.shape == (24, len(TARGET_COLS))

    def test_X_column_order(self):
        df = _synthetic_sweep()
        st = SweepTable(df=df)
        assert list(st.X.columns) == list(FEATURE_COLS)

    def test_groups_match_n_configs(self):
        df = _synthetic_sweep(n_configs=8, n_seeds=3)
        st = SweepTable(df=df)
        assert len(np.unique(st.groups)) == st.n_configs == 8

    def test_X_is_a_copy(self):
        df = _synthetic_sweep(n_configs=4, n_seeds=2)
        st = SweepTable(df=df)
        x1 = st.X
        x1.iloc[0, 0] = -999.0
        # The original df must be unchanged
        assert df["ttt_ms"].iloc[0] != -999.0


# ---------------------------------------------------------------------------
# Grouped K-Fold
# ---------------------------------------------------------------------------

class TestGroupedKFold:
    def test_no_overlap_between_train_and_test(self):
        df = _synthetic_sweep(n_configs=9, n_seeds=4)
        st = SweepTable(df=df)
        for tr, te in grouped_kfold_splits(st, n_splits=3):
            assert len(set(tr) & set(te)) == 0

    def test_full_coverage(self):
        df = _synthetic_sweep(n_configs=9, n_seeds=4)
        st = SweepTable(df=df)
        seen = set()
        for _, te in grouped_kfold_splits(st, n_splits=3):
            seen |= set(te)
        assert seen == set(range(len(df)))

    def test_groups_do_not_leak(self):
        """The same (TTT, hyst, A3) tuple must never split across train/test."""
        df = _synthetic_sweep(n_configs=9, n_seeds=4)
        st = SweepTable(df=df)
        for tr, te in grouped_kfold_splits(st, n_splits=3):
            tr_groups = set(st.groups[tr])
            te_groups = set(st.groups[te])
            assert tr_groups.isdisjoint(te_groups), (
                f"groups leaked: intersection = {tr_groups & te_groups}"
            )

    def test_too_many_splits_raises(self):
        df = _synthetic_sweep(n_configs=3, n_seeds=2)
        st = SweepTable(df=df)
        with pytest.raises(ValueError, match="exceeds n_configs"):
            grouped_kfold_splits(st, n_splits=5)


# ---------------------------------------------------------------------------
# Candidate grid
# ---------------------------------------------------------------------------

class TestCandidateGrid:
    def test_default_returns_canonical_iter_c_grid(self):
        grid = candidate_config_grid()
        # 3 TTT x 4 hyst x 3 A3 = 36
        assert len(grid) == 36
        assert set(grid.columns) >= set(CONTROLLED_COLS)
        assert set(grid["ttt_ms"]) == {256.0, 480.0, 1024.0}
        assert set(grid["hyst_db"]) == {0.0, 1.0, 3.0, 6.0}
        assert set(grid["a3_off_db"]) == {0.0, 3.0, 6.0}

    def test_table_override(self):
        df = _synthetic_sweep(n_configs=6, n_seeds=3)
        st = SweepTable(df=df)
        grid = candidate_config_grid(st)
        assert len(grid) == st.n_configs == 6


# ---------------------------------------------------------------------------
# load_sweep (integration: requires the real parquet)
# ---------------------------------------------------------------------------

class TestLoadSweep:
    def test_missing_path_raises(self, tmp_path: Path):
        bogus = tmp_path / "does_not_exist.parquet"
        with pytest.raises(FileNotFoundError, match="make sweep-static"):
            load_sweep(bogus)

    def test_real_parquet_loads_if_present(self):
        """Skip if the parquet hasn't been generated yet."""
        try:
            st = load_sweep()
        except FileNotFoundError:
            pytest.skip("sweep_config_perf.parquet not present (run make sweep-static)")
        assert st.df.shape[0] >= 1
        assert set(FEATURE_COLS) | set(TARGET_COLS) <= set(st.df.columns)
