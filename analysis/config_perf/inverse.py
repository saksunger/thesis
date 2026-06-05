"""Inverse-query API: given a KPI constraint, return the best configs.

The Phase 9 demo will pose questions like::

    Given the current deployment context (RSRP/SINR/RSRQ percentiles
    measured at runtime), find the (TTT, hyst, A3) configuration that
    maximises HOSR while keeping RLF_rate <= 0.05 and ping_pong_rate
    <= 0.10.

The Iter A scope solves this by brute-force evaluation of every
(TTT, hyst, A3) candidate in the training-grid (36 points for the
sweep_static grid) using a fitted per-target surrogate. Iter B may
replace the brute-force with Bayesian optimisation if the candidate
grid ever exceeds ~1 000 points, but at 36 the BO setup cost is
strictly larger than the saved query time.

The output is a ranked DataFrame of feasible configurations. Each row
carries the predicted KPIs (point + optional 80 % CI) plus a single
``score`` column that the caller can sort on.

Caller responsibility: pick `score_target` and `direction` according
to the use case. The default `(hosr, "max")` matches the headline
Phase 9 demo question.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd

from analysis.config_perf.data import (
    CONTROLLED_COLS,
    FEATURE_COLS,
    SCENARIO_COLS,
    candidate_config_grid,
)


# -------------------------------------------------------------------------
# Constraints
# -------------------------------------------------------------------------

@dataclass(frozen=True)
class Constraint:
    """A single inequality constraint on a target KPI.

    Example::

        Constraint("rlf_rate", "<=", 0.05)
        Constraint("hosr",     ">=", 0.95)

    Operators supported: ``"<="``, ``">="``, ``"<"``, ``">"``.
    """

    target: str
    op: str
    value: float

    def __post_init__(self) -> None:
        if self.op not in {"<=", ">=", "<", ">"}:
            raise ValueError(f"unknown op {self.op!r}")

    def evaluate(self, predicted: np.ndarray) -> np.ndarray:
        v = float(self.value)
        if self.op == "<=": return predicted <= v
        if self.op == ">=": return predicted >= v
        if self.op == "<":  return predicted <  v
        if self.op == ">":  return predicted >  v
        raise AssertionError("unreachable")  # pragma: no cover


# -------------------------------------------------------------------------
# Result
# -------------------------------------------------------------------------

@dataclass(frozen=True)
class InverseQueryResult:
    """Bundle of (ranked recommendations, all-candidates table, n_feasible)."""

    recommendations: pd.DataFrame   # only feasible rows, sorted by score
    candidates: pd.DataFrame         # all candidates with feasibility flag
    n_feasible: int

    def top(self, k: int = 5) -> pd.DataFrame:
        return self.recommendations.head(k).reset_index(drop=True)


# -------------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------------

def inverse_query(
    surrogates: Mapping[str, "object"],          # target -> fitted surrogate
    scenario_features: Mapping[str, float],       # column -> value (must cover SCENARIO_COLS)
    constraints: list[Constraint] | None = None,
    score_target: str = "hosr",
    score_direction: str = "max",
    candidate_grid: pd.DataFrame | None = None,
    interval_quantile_surrogates: Mapping[str, "object"] | None = None,
) -> InverseQueryResult:
    """Brute-force inverse query against fitted surrogates.

    Args:
        surrogates : mapping ``target_name -> fitted surrogate``. The
            surrogate must expose ``predict(X) -> ndarray`` on a feature
            matrix that contains every column of :data:`FEATURE_COLS`.
        scenario_features : dict of (scenario column -> value) defining
            the deployment context held fixed across all candidates.
            Must cover every column of :data:`SCENARIO_COLS`.
        constraints : list of :class:`Constraint`. Empty / None means
            "no constraint" — every candidate is feasible.
        score_target    : target column to sort feasible candidates on.
        score_direction : ``"max"`` (sort descending) or ``"min"``.
        candidate_grid  : optional override for the (TTT, hyst, A3)
            candidate set. Defaults to
            :func:`analysis.config_perf.data.candidate_config_grid()`
            (the canonical Iter C 3 × 4 × 3 grid).
        interval_quantile_surrogates : optional mapping
            ``target_name -> fitted QuantileGBSurrogate``. When supplied,
            the result table gains ``<target>_lo`` / ``<target>_hi``
            columns from each surrogate's ``predict_interval``. The
            score / constraint evaluation still uses the point estimate.

    Returns:
        :class:`InverseQueryResult` with the feasible (sorted) and full
        (annotated) candidate tables.

    Raises:
        ValueError : on missing surrogates / missing scenario features /
                     unknown score_direction / score_target not in surrogates.
    """
    if not surrogates:
        raise ValueError("surrogates mapping must contain at least one model")
    missing_scen = set(SCENARIO_COLS) - set(scenario_features)
    if missing_scen:
        raise ValueError(
            f"scenario_features missing required columns: {sorted(missing_scen)}"
        )
    if score_direction not in {"max", "min"}:
        raise ValueError(f"score_direction must be 'max' or 'min' (got {score_direction!r})")
    if score_target not in surrogates:
        raise ValueError(
            f"score_target {score_target!r} not in surrogates "
            f"(have {sorted(surrogates)})"
        )

    # Build the candidate feature matrix
    grid = candidate_grid if candidate_grid is not None else candidate_config_grid()
    if not set(CONTROLLED_COLS).issubset(grid.columns):
        raise ValueError(
            f"candidate_grid missing required columns: {sorted(set(CONTROLLED_COLS) - set(grid.columns))}"
        )
    X = grid[list(CONTROLLED_COLS)].copy()
    for c in SCENARIO_COLS:
        X[c] = float(scenario_features[c])
    X = X[list(FEATURE_COLS)]  # canonical column order

    # Per-target predictions
    out = grid[list(CONTROLLED_COLS)].copy()
    for t, model in surrogates.items():
        preds = np.asarray(model.predict(X), dtype=float)
        out[t] = preds

    # Per-target prediction intervals (optional)
    if interval_quantile_surrogates:
        for t, qmodel in interval_quantile_surrogates.items():
            if not hasattr(qmodel, "predict_interval"):
                continue
            lo, _, hi = qmodel.predict_interval(X)
            out[f"{t}_lo"] = lo
            out[f"{t}_hi"] = hi

    # Feasibility
    feasible = np.ones(len(out), dtype=bool)
    constraints = constraints or []
    for c in constraints:
        if c.target not in out.columns:
            raise ValueError(
                f"constraint references unknown target {c.target!r}; "
                f"available: {sorted(c for c in out.columns if c in surrogates)}"
            )
        feasible &= c.evaluate(out[c.target].to_numpy())
    out["feasible"] = feasible

    # Score
    score = out[score_target].to_numpy()
    out["score"] = score if score_direction == "max" else -score
    out["score_target"] = score_target
    out["score_direction"] = score_direction

    # Ranked recommendations (feasible only, sorted high-to-low)
    recs = (
        out[out["feasible"]]
        .sort_values("score", ascending=False)
        .reset_index(drop=True)
    )
    # Drop the negation by sorting on the *original* target value
    recs = recs.sort_values(
        score_target,
        ascending=(score_direction == "min"),
    ).reset_index(drop=True)
    return InverseQueryResult(
        recommendations=recs,
        candidates=out,
        n_feasible=int(feasible.sum()),
    )


# -------------------------------------------------------------------------
# Pareto front (multi-objective sanity helper)
# -------------------------------------------------------------------------

def pareto_front(
    df: pd.DataFrame,
    objectives: list[tuple[str, str]],
) -> pd.DataFrame:
    """Return the rows of ``df`` that are Pareto-optimal wrt ``objectives``.

    Args:
        df         : DataFrame containing all objective columns.
        objectives : list of (column_name, direction) tuples, where
                     ``direction`` is ``"max"`` or ``"min"``. A point is
                     dominated iff there exists another point that is
                     >= on every "max" objective AND <= on every "min"
                     objective AND strictly better on at least one.

    Returns:
        Subset of `df` with only the non-dominated rows, preserving
        the original index.
    """
    if df.empty or not objectives:
        return df.copy()
    arr = np.stack(
        [
            df[col].to_numpy() * (1.0 if direc == "max" else -1.0)
            for col, direc in objectives
        ],
        axis=1,
    )  # higher = better in every column
    n = arr.shape[0]
    keep = np.ones(n, dtype=bool)
    for i in range(n):
        if not keep[i]:
            continue
        # dominates j iff arr[i] >= arr[j] in all dims and > in at least one
        ge_all = np.all(arr >= arr[i], axis=1)
        gt_any = np.any(arr > arr[i], axis=1)
        dominated_by_someone = ge_all & gt_any
        if dominated_by_someone.any():
            keep[i] = False
    return df[keep].copy()
