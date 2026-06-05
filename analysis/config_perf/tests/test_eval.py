"""Unit tests for analysis.config_perf.eval."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.config_perf.eval import (
    RegressionScores,
    aggregate_cv_coverage,
    aggregate_cv_scores,
    coverage,
    cv_evaluate_intervals,
    cv_evaluate_point,
    regression_scores,
    reliability_table,
)
from analysis.config_perf.surrogate import HistGBSurrogate, QuantileGBSurrogate


# ---------------------------------------------------------------------------
# regression_scores
# ---------------------------------------------------------------------------

class TestRegressionScores:
    def test_perfect_prediction(self):
        y = np.array([1.0, 2.0, 3.0, 4.0])
        sc = regression_scores(y, y)
        assert sc.mae == 0.0
        assert sc.rmse == 0.0
        assert sc.r2 == pytest.approx(1.0)
        assert sc.n == 4

    def test_random_prediction_yields_negative_r2(self):
        rng = np.random.default_rng(0)
        y = rng.normal(size=100)
        pred = rng.normal(size=100)
        sc = regression_scores(y, pred)
        assert sc.r2 < 0.5  # random predictions can't beat baseline

    def test_constant_y_returns_nan_r2(self):
        y = np.array([5.0, 5.0, 5.0])
        pred = np.array([5.1, 4.9, 5.0])
        sc = regression_scores(y, pred)
        assert np.isnan(sc.r2)  # var(y)=0 -> r2 undefined
        assert sc.mae > 0.0

    def test_nan_safety(self):
        y = np.array([1.0, 2.0, np.nan, 4.0])
        pred = np.array([1.1, 2.1, 3.1, np.nan])
        sc = regression_scores(y, pred)
        assert sc.n == 2  # only 2 rows fully finite

    def test_empty_returns_nan(self):
        sc = regression_scores(np.array([]), np.array([]))
        assert sc.n == 0
        assert np.isnan(sc.mae)


# ---------------------------------------------------------------------------
# coverage
# ---------------------------------------------------------------------------

class TestCoverage:
    def test_all_inside(self):
        y = np.array([1.0, 2.0, 3.0])
        lo = np.array([0.0, 1.0, 2.0])
        hi = np.array([2.0, 3.0, 4.0])
        c = coverage(y, lo, hi, nominal=0.8)
        assert c.empirical == 1.0
        assert c.width_mean == pytest.approx(2.0)
        assert c.n == 3

    def test_none_inside(self):
        y = np.array([10.0, 20.0])
        lo = np.array([0.0, 0.0])
        hi = np.array([1.0, 1.0])
        c = coverage(y, lo, hi, nominal=0.8)
        assert c.empirical == 0.0

    def test_inclusive_boundary(self):
        """Boundary values y == lo or y == hi must count as inside."""
        y = np.array([1.0, 5.0])
        lo = np.array([1.0, 4.0])
        hi = np.array([3.0, 5.0])
        c = coverage(y, lo, hi, nominal=0.5)
        assert c.empirical == 1.0

    def test_nan_safety(self):
        y = np.array([1.0, np.nan, 3.0])
        lo = np.array([0.0, 0.0, 2.0])
        hi = np.array([2.0, 2.0, 4.0])
        c = coverage(y, lo, hi, nominal=0.5)
        assert c.n == 2

    def test_invalid_nominal_raises(self):
        with pytest.raises(ValueError, match="nominal"):
            coverage(np.array([1.0]), np.array([0.0]), np.array([2.0]),
                     nominal=0.0)
        with pytest.raises(ValueError, match="nominal"):
            coverage(np.array([1.0]), np.array([0.0]), np.array([2.0]),
                     nominal=1.0)


# ---------------------------------------------------------------------------
# reliability_table
# ---------------------------------------------------------------------------

class TestReliabilityTable:
    def test_columns_and_sorted_by_nominal(self):
        y = np.array([1.0, 2.0, 3.0, 4.0])
        intervals = {
            0.9: (np.full(4, 0.0), np.full(4, 5.0)),
            0.5: (np.full(4, 1.5), np.full(4, 3.5)),
        }
        tab = reliability_table(y, intervals)
        assert list(tab.columns) == ["nominal", "empirical", "width_mean", "n"]
        # Must be sorted ascending by nominal
        assert (tab["nominal"].diff().dropna() >= 0).all()


# ---------------------------------------------------------------------------
# CV runners
# ---------------------------------------------------------------------------

def _cv_problem():
    rng = np.random.default_rng(0)
    n = 120
    X = pd.DataFrame({"a": rng.uniform(-2, 2, size=n)})
    Y = pd.DataFrame({
        "y_easy": pd.Series(np.tanh(X["a"]) + 0.05 * rng.normal(size=n)),
        "y_hard": pd.Series(rng.normal(size=n)),  # pure noise
    })
    # 3 folds of size 40 each
    splits = []
    for k in range(3):
        te = np.arange(k * 40, (k + 1) * 40)
        tr = np.setdiff1d(np.arange(n), te)
        splits.append((tr, te))
    return X, Y, splits


class TestCvEvaluatePoint:
    def test_returns_one_row_per_target_per_fold(self):
        X, Y, splits = _cv_problem()
        out = cv_evaluate_point(
            lambda: HistGBSurrogate(random_state=0), X, Y, splits,
        )
        assert len(out) == 2 * 3
        assert {"target", "fold", "mae", "rmse", "r2", "n_train", "n_test"} <= set(out.columns)

    def test_easy_target_easy(self):
        X, Y, splits = _cv_problem()
        out = cv_evaluate_point(
            lambda: HistGBSurrogate(random_state=0), X, Y, splits,
        )
        easy = out[out["target"] == "y_easy"]
        # tanh is learnable -> r2 well above 0
        assert (easy["r2"] > 0.5).all()

    def test_targets_subset(self):
        X, Y, splits = _cv_problem()
        out = cv_evaluate_point(
            lambda: HistGBSurrogate(random_state=0), X, Y, splits,
            targets=("y_easy",),
        )
        assert set(out["target"].unique()) == {"y_easy"}


class TestCvEvaluateIntervals:
    def test_returns_per_target_per_fold_coverage(self):
        X, Y, splits = _cv_problem()
        out = cv_evaluate_intervals(
            lambda: QuantileGBSurrogate(random_state=0), X, Y, splits,
        )
        assert len(out) == 2 * 3
        assert {"target", "fold", "nominal", "empirical", "width_mean"} <= set(out.columns)
        # nominal must be q_hi - q_lo = 0.8 by default
        assert np.allclose(out["nominal"], 0.8)


# ---------------------------------------------------------------------------
# Aggregators
# ---------------------------------------------------------------------------

class TestAggregators:
    def test_aggregate_cv_scores(self):
        scores = pd.DataFrame([
            {"target": "y1", "fold": 0, "mae": 0.10, "rmse": 0.12, "r2": 0.7},
            {"target": "y1", "fold": 1, "mae": 0.20, "rmse": 0.22, "r2": 0.5},
            {"target": "y2", "fold": 0, "mae": 0.05, "rmse": 0.06, "r2": 0.9},
            {"target": "y2", "fold": 1, "mae": 0.07, "rmse": 0.08, "r2": 0.8},
        ])
        out = aggregate_cv_scores(scores).set_index("target")
        assert out.loc["y1", "mae_mean"] == pytest.approx(0.15)
        assert out.loc["y2", "mae_mean"] == pytest.approx(0.06)
        assert out.loc["y1", "n_folds"] == 2

    def test_aggregate_cv_coverage(self):
        cov = pd.DataFrame([
            {"target": "y1", "fold": 0, "nominal": 0.8, "empirical": 0.78, "width_mean": 1.0},
            {"target": "y1", "fold": 1, "nominal": 0.8, "empirical": 0.82, "width_mean": 1.1},
        ])
        out = aggregate_cv_coverage(cov)
        assert len(out) == 1
        assert out["empirical_mean"].iloc[0] == pytest.approx(0.80)
