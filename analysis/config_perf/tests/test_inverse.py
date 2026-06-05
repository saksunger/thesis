"""Unit tests for analysis.config_perf.inverse."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.config_perf.data import (
    CONTROLLED_COLS,
    FEATURE_COLS,
    SCENARIO_COLS,
    candidate_config_grid,
)
from analysis.config_perf.inverse import (
    Constraint,
    InverseQueryResult,
    inverse_query,
    pareto_front,
)


# ---------------------------------------------------------------------------
# Fake surrogate (returns a deterministic linear combo of features)
# ---------------------------------------------------------------------------

class _DeterministicSurrogate:
    """Always predicts ``w . x + bias`` (no fitting needed)."""

    def __init__(self, weights: dict[str, float], bias: float = 0.0):
        self.weights = weights
        self.bias = bias

    def fit(self, X, y):
        return self

    def predict(self, X) -> np.ndarray:
        # Accepts DataFrame or ndarray
        if isinstance(X, pd.DataFrame):
            cols = X.columns.tolist()
            arr = X.to_numpy(dtype=float)
        else:
            cols = list(FEATURE_COLS)
            arr = np.asarray(X, dtype=float)
        out = np.full(arr.shape[0], self.bias, dtype=float)
        for k, w in self.weights.items():
            if k in cols:
                out += w * arr[:, cols.index(k)]
        return out


def _scenario_dict(**overrides) -> dict[str, float]:
    base = {c: 0.0 for c in SCENARIO_COLS}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Constraint
# ---------------------------------------------------------------------------

class TestConstraint:
    def test_evaluate_le(self):
        c = Constraint("rlf_rate", "<=", 0.05)
        assert c.evaluate(np.array([0.04, 0.05, 0.06])).tolist() == [True, True, False]

    def test_evaluate_ge(self):
        c = Constraint("hosr", ">=", 0.9)
        assert c.evaluate(np.array([0.89, 0.90, 0.91])).tolist() == [False, True, True]

    def test_strict_lt(self):
        c = Constraint("x", "<", 5)
        assert c.evaluate(np.array([4, 5, 6])).tolist() == [True, False, False]

    def test_unknown_op_raises(self):
        with pytest.raises(ValueError, match="unknown op"):
            Constraint("x", "!=", 1.0)


# ---------------------------------------------------------------------------
# inverse_query
# ---------------------------------------------------------------------------

class TestInverseQuery:
    def test_basic_runs_and_returns_result(self):
        surrogates = {
            # HOSR increases with TTT (heuristic for the test)
            "hosr": _DeterministicSurrogate(weights={"ttt_ms": 0.001}, bias=0.5),
            "rlf_rate": _DeterministicSurrogate(weights={"hyst_db": 0.01}, bias=0.02),
        }
        result = inverse_query(
            surrogates=surrogates,
            scenario_features=_scenario_dict(),
            constraints=[Constraint("hosr", ">=", 0.6),
                         Constraint("rlf_rate", "<=", 0.05)],
        )
        assert isinstance(result, InverseQueryResult)
        assert "hosr" in result.candidates.columns
        assert "rlf_rate" in result.candidates.columns
        assert set(CONTROLLED_COLS).issubset(result.candidates.columns)
        # Default grid has 36 candidates
        assert len(result.candidates) == 36

    def test_feasibility_marking(self):
        surrogates = {
            "hosr": _DeterministicSurrogate(weights={}, bias=0.99),  # all feasible
        }
        result = inverse_query(
            surrogates=surrogates,
            scenario_features=_scenario_dict(),
            constraints=[Constraint("hosr", ">=", 0.95)],
        )
        assert result.n_feasible == len(result.candidates)

    def test_no_feasible(self):
        surrogates = {
            "hosr": _DeterministicSurrogate(weights={}, bias=0.10),  # never feasible
        }
        result = inverse_query(
            surrogates=surrogates,
            scenario_features=_scenario_dict(),
            constraints=[Constraint("hosr", ">=", 0.95)],
        )
        assert result.n_feasible == 0
        assert result.recommendations.empty

    def test_sorting_max(self):
        surrogates = {
            "hosr": _DeterministicSurrogate(weights={"ttt_ms": 0.001}, bias=0.5),
        }
        result = inverse_query(
            surrogates=surrogates,
            scenario_features=_scenario_dict(),
            score_target="hosr", score_direction="max",
        )
        # Top recommendation must have the highest HOSR among feasible
        assert result.recommendations["hosr"].iloc[0] == result.recommendations["hosr"].max()

    def test_sorting_min(self):
        surrogates = {
            "rlf_rate": _DeterministicSurrogate(weights={"hyst_db": 0.01}, bias=0.0),
            "hosr": _DeterministicSurrogate(weights={}, bias=1.0),
        }
        result = inverse_query(
            surrogates=surrogates,
            scenario_features=_scenario_dict(),
            score_target="rlf_rate", score_direction="min",
        )
        assert result.recommendations["rlf_rate"].iloc[0] == result.recommendations["rlf_rate"].min()

    def test_missing_scenario_raises(self):
        surrogates = {"hosr": _DeterministicSurrogate(weights={}, bias=1.0)}
        with pytest.raises(ValueError, match="missing required columns"):
            inverse_query(
                surrogates=surrogates,
                scenario_features={"rsrp_p10_dbm": -120.0},  # incomplete
            )

    def test_unknown_score_target_raises(self):
        surrogates = {"hosr": _DeterministicSurrogate(weights={}, bias=1.0)}
        with pytest.raises(ValueError, match="not in surrogates"):
            inverse_query(
                surrogates=surrogates,
                scenario_features=_scenario_dict(),
                score_target="bogus_kpi",
            )

    def test_empty_surrogates_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            inverse_query(surrogates={}, scenario_features=_scenario_dict())

    def test_top_k(self):
        surrogates = {
            "hosr": _DeterministicSurrogate(weights={"ttt_ms": 0.001}, bias=0.5),
        }
        result = inverse_query(surrogates=surrogates, scenario_features=_scenario_dict())
        top = result.top(3)
        assert len(top) <= 3


# ---------------------------------------------------------------------------
# pareto_front
# ---------------------------------------------------------------------------

class TestParetoFront:
    def test_basic(self):
        # 4 points: (1,4), (2,3), (3,2), (4,1) -- all on the Pareto front for max,max
        df = pd.DataFrame({"a": [1, 2, 3, 4], "b": [4, 3, 2, 1]})
        front = pareto_front(df, [("a", "max"), ("b", "max")])
        assert len(front) == 4

    def test_dominated_removed(self):
        # (2, 2) dominates (1, 1)
        df = pd.DataFrame({"a": [1, 2, 3], "b": [1, 2, 0]})
        front = pareto_front(df, [("a", "max"), ("b", "max")])
        # (1, 1) is dominated by (2, 2). (3, 0) is on the front because of a=3.
        idxs = sorted(front.index.tolist())
        assert 0 not in idxs  # (1,1) removed
        assert 1 in idxs and 2 in idxs

    def test_min_direction(self):
        # (1,1) is best when both must be minimised
        df = pd.DataFrame({"a": [1, 2, 3], "b": [1, 2, 3]})
        front = pareto_front(df, [("a", "min"), ("b", "min")])
        assert len(front) == 1
        assert front.iloc[0]["a"] == 1

    def test_empty(self):
        front = pareto_front(pd.DataFrame(), [("a", "max")])
        assert front.empty
