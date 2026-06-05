"""Evaluation metrics for the config-performance surrogate.

Two metric families:

1. **Point-estimate accuracy** (any regressor): MAE, RMSE, R^2 per
   target. The Iter A headline is MAE because the targets live on
   bounded / small-positive scales where MAE is interpretable in the
   same units as the target (`HOSR MAE = 0.04` reads as "4 percentage
   points of HO success rate" in expected user terms).

2. **Calibration of prediction intervals** (only
   :class:`QuantileGBSurrogate`-like models that expose
   ``predict_interval``): empirical coverage at nominal levels in
   {0.5, 0.8, 0.9}. The reliability diagram plots empirical-vs-nominal
   and a well-calibrated surrogate sits on the diagonal.

The functions here all consume the (y_true, y_pred) numpy interface so
the caller can use them with any model, not just our wrappers.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import (
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)


# -------------------------------------------------------------------------
# Point-estimate accuracy
# -------------------------------------------------------------------------

@dataclass(frozen=True)
class RegressionScores:
    mae: float
    rmse: float
    r2: float
    n: int

    def as_dict(self) -> dict:
        return {"mae": self.mae, "rmse": self.rmse, "r2": self.r2, "n": self.n}


def regression_scores(
    y_true: np.ndarray, y_pred: np.ndarray,
) -> RegressionScores:
    """Return MAE / RMSE / R^2 / n. NaN-safe (drops rows with NaN in either)."""
    y_true = np.asarray(y_true, dtype=float).ravel()
    y_pred = np.asarray(y_pred, dtype=float).ravel()
    if y_true.shape != y_pred.shape:
        raise ValueError(
            f"y_true {y_true.shape} != y_pred {y_pred.shape}"
        )
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    yt, yp = y_true[mask], y_pred[mask]
    if len(yt) == 0:
        return RegressionScores(
            mae=float("nan"), rmse=float("nan"),
            r2=float("nan"), n=0,
        )
    mae = float(mean_absolute_error(yt, yp))
    rmse = float(np.sqrt(mean_squared_error(yt, yp)))
    # r2 is undefined when y_true is constant — return NaN, don't raise.
    if np.var(yt) == 0.0:
        r2 = float("nan")
    else:
        r2 = float(r2_score(yt, yp))
    return RegressionScores(mae=mae, rmse=rmse, r2=r2, n=int(len(yt)))


# -------------------------------------------------------------------------
# Coverage / reliability
# -------------------------------------------------------------------------

@dataclass(frozen=True)
class CoverageResult:
    nominal: float        # nominal coverage level (e.g. 0.8 for q in {0.1, 0.9})
    empirical: float      # fraction of y_true that fell inside [lo, hi]
    width_mean: float     # mean interval width (hi - lo)
    n: int

    def as_dict(self) -> dict:
        return {
            "nominal": self.nominal,
            "empirical": self.empirical,
            "width_mean": self.width_mean,
            "n": self.n,
        }


def coverage(
    y_true: np.ndarray, y_lo: np.ndarray, y_hi: np.ndarray,
    nominal: float,
) -> CoverageResult:
    """Empirical coverage of [y_lo, y_hi] vs y_true.

    Args:
        y_true  : ground-truth values, shape (n,).
        y_lo    : lower bound of the prediction interval, shape (n,).
        y_hi    : upper bound of the prediction interval, shape (n,).
        nominal : the nominal coverage of the (lo, hi) pair (e.g. 0.8
                  for q in {0.1, 0.9}). Used to populate the result; not
                  used in the computation.
    """
    y_true = np.asarray(y_true, dtype=float).ravel()
    y_lo = np.asarray(y_lo, dtype=float).ravel()
    y_hi = np.asarray(y_hi, dtype=float).ravel()
    if not (y_true.shape == y_lo.shape == y_hi.shape):
        raise ValueError(
            f"shape mismatch: y_true={y_true.shape} "
            f"y_lo={y_lo.shape} y_hi={y_hi.shape}"
        )
    if not (0.0 < nominal < 1.0):
        raise ValueError(f"nominal must be in (0, 1) (got {nominal})")
    mask = (
        np.isfinite(y_true) & np.isfinite(y_lo) & np.isfinite(y_hi)
    )
    yt, lo, hi = y_true[mask], y_lo[mask], y_hi[mask]
    if len(yt) == 0:
        return CoverageResult(
            nominal=nominal, empirical=float("nan"),
            width_mean=float("nan"), n=0,
        )
    inside = (yt >= lo) & (yt <= hi)
    return CoverageResult(
        nominal=float(nominal),
        empirical=float(np.mean(inside)),
        width_mean=float(np.mean(hi - lo)),
        n=int(len(yt)),
    )


def reliability_table(
    y_true: np.ndarray,
    intervals: dict[float, tuple[np.ndarray, np.ndarray]],
) -> pd.DataFrame:
    """Return a long-form reliability table for plotting.

    Args:
        y_true    : 1-D array of true values.
        intervals : dict mapping nominal level -> (lo_array, hi_array).
                    E.g. ``{0.5: (q25, q75), 0.8: (q10, q90), 0.9: (q05, q95)}``.

    Returns:
        DataFrame with columns ``nominal, empirical, width_mean, n``.
    """
    rows = []
    for nom, (lo, hi) in sorted(intervals.items()):
        c = coverage(y_true, lo, hi, nominal=nom)
        rows.append(c.as_dict())
    return pd.DataFrame(rows)


# -------------------------------------------------------------------------
# CV runner (point estimate, per-target)
# -------------------------------------------------------------------------

def cv_evaluate_point(
    surrogate_factory,
    X: pd.DataFrame, Y: pd.DataFrame,
    splits: list[tuple[np.ndarray, np.ndarray]],
    targets: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """Run k-fold CV and return long-form per-(target, fold) scores.

    Args:
        surrogate_factory : zero-arg callable producing a fresh model.
        X, Y              : feature / target DataFrames.
        splits            : output of
                            :func:`analysis.config_perf.data.grouped_kfold_splits`.
        targets           : optional subset of Y.columns.

    Returns:
        Long-form DataFrame with columns
        ``[target, fold, n_train, mae, rmse, r2, n_test]``.
    """
    cols = list(Y.columns) if targets is None else [t for t in targets if t in Y.columns]
    rows = []
    for f_idx, (tr, te) in enumerate(splits):
        X_tr, X_te = X.iloc[tr], X.iloc[te]
        Y_tr, Y_te = Y.iloc[tr], Y.iloc[te]
        for t in cols:
            model = surrogate_factory()
            model.fit(X_tr, Y_tr[t])
            preds = model.predict(X_te)
            sc = regression_scores(Y_te[t].to_numpy(), preds)
            rows.append({
                "target": t,
                "fold": int(f_idx),
                "n_train": int(len(tr)),
                "n_test": int(sc.n),
                "mae": sc.mae,
                "rmse": sc.rmse,
                "r2": sc.r2,
            })
    return pd.DataFrame(rows)


def cv_evaluate_intervals(
    quantile_surrogate_factory,
    X: pd.DataFrame, Y: pd.DataFrame,
    splits: list[tuple[np.ndarray, np.ndarray]],
    targets: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """CV-evaluate prediction-interval coverage for a quantile surrogate.

    The factory MUST produce a surrogate exposing ``predict_interval``
    (e.g. :class:`QuantileGBSurrogate`).

    Returns:
        Long-form DataFrame with columns
        ``[target, fold, nominal, empirical, width_mean, n]``.
        Only the single (q_lo, q_hi) the surrogate was constructed with
        is reported per row (Iter A: 0.1 / 0.9, nominal 0.8).
    """
    cols = list(Y.columns) if targets is None else [t for t in targets if t in Y.columns]
    rows = []
    for f_idx, (tr, te) in enumerate(splits):
        X_tr, X_te = X.iloc[tr], X.iloc[te]
        Y_tr, Y_te = Y.iloc[tr], Y.iloc[te]
        for t in cols:
            model = quantile_surrogate_factory()
            model.fit(X_tr, Y_tr[t])
            lo, _mid, hi = model.predict_interval(X_te)
            cov = coverage(Y_te[t].to_numpy(), lo, hi,
                           nominal=model.nominal_coverage)
            rows.append({
                "target": t,
                "fold": int(f_idx),
                **cov.as_dict(),
            })
    return pd.DataFrame(rows)


# -------------------------------------------------------------------------
# Aggregators
# -------------------------------------------------------------------------

def aggregate_cv_scores(scores_long: pd.DataFrame) -> pd.DataFrame:
    """Roll a long-form CV scores table up to per-target mean ± std."""
    if scores_long.empty:
        return scores_long
    g = scores_long.groupby("target", sort=False)
    out = pd.DataFrame({
        "mae_mean":  g["mae"].mean(),
        "mae_std":   g["mae"].std(ddof=0),
        "rmse_mean": g["rmse"].mean(),
        "rmse_std":  g["rmse"].std(ddof=0),
        "r2_mean":   g["r2"].mean(),
        "r2_std":    g["r2"].std(ddof=0),
        "n_folds":   g["fold"].nunique(),
    }).reset_index()
    return out


def aggregate_cv_coverage(cov_long: pd.DataFrame) -> pd.DataFrame:
    """Roll a long-form coverage table up to per-(target, nominal) mean."""
    if cov_long.empty:
        return cov_long
    g = cov_long.groupby(["target", "nominal"], sort=False)
    out = pd.DataFrame({
        "empirical_mean": g["empirical"].mean(),
        "empirical_std":  g["empirical"].std(ddof=0),
        "width_mean":     g["width_mean"].mean(),
        "n_folds":        g["fold"].nunique(),
    }).reset_index()
    return out
