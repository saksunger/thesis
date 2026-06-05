"""Phase 7 evaluation metrics: sliding PR-AUC + cost ledger.

The orchestrator emits a per-window score table::

    columns: [t_mid_s, score, label_anomaly, phase_id, ...]

This module turns that table into the artifacts Chapter 7 needs:

    sliding_pr_auc(...)  -> DataFrame [t_center_s, pr_auc, n_pos, n_neg]
    overall_pr_auc(...)  -> float (PR-AUC over the full timeline)
    drift_phase_pr_auc(...) -> float (PR-AUC restricted to drift-affected
                              windows, used by Iter A acceptance C3)
    cost_summary(...)    -> dict from the retrain ledger

Sliding-PR-AUC convention
-------------------------
At each grid time ``t_c`` we take the windows whose ``t_mid_s`` falls
inside ``[t_c - eval_window_s/2, t_c + eval_window_s/2]`` and compute
PR-AUC on that subset. Eval windows with < ``min_pos`` positives or
< ``min_neg`` negatives produce NaN — Chapter 7's plot drops those
points instead of pretending PR-AUC is defined on degenerate samples.
This is the same defensive pattern used by
:func:`analysis.anomaly.metrics.bootstrap_pr_auc`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score


# ---------------------------------------------------------------------------
# Sliding PR-AUC
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SlidingConfig:
    """Window/stride knobs for the sliding-PR-AUC computation."""

    eval_window_s: float = 120.0
    stride_s: float = 30.0
    min_pos: int = 2
    min_neg: int = 5

    def __post_init__(self) -> None:
        if self.eval_window_s <= 0:
            raise ValueError(f"eval_window_s must be > 0 (got {self.eval_window_s})")
        if self.stride_s <= 0:
            raise ValueError(f"stride_s must be > 0 (got {self.stride_s})")


def sliding_pr_auc(
    per_window: pd.DataFrame,
    cfg: SlidingConfig | None = None,
    score_col: str = "score",
    label_col: str = "label_anomaly",
    time_col: str = "t_mid_s",
) -> pd.DataFrame:
    """Compute PR-AUC on sliding ``eval_window_s`` chunks at ``stride_s``.

    Args:
        per_window : DataFrame with at least ``[time_col, score_col,
                     label_col]``. Rows where ``score`` is NaN are
                     discarded (warm-up windows have NaN scores).
        cfg        : :class:`SlidingConfig` (defaults to 120 s window /
                     30 s stride / min_pos=2 / min_neg=5).
        score_col  : name of the score column.
        label_col  : name of the binary 0/1 label column.
        time_col   : name of the time column (seconds).

    Returns:
        DataFrame with columns::

            t_center_s, pr_auc, n_pos, n_neg

        Sorted by ``t_center_s``. Empty/degenerate windows produce a row
        with ``pr_auc=NaN`` (and matching ``n_pos`` / ``n_neg`` counts)
        so the plot can show a gap rather than skipping silently.
    """
    cfg = cfg or SlidingConfig()
    empty = pd.DataFrame({
        "t_center_s": pd.Series(dtype="float64"),
        "pr_auc":     pd.Series(dtype="float64"),
        "n_pos":      pd.Series(dtype="int64"),
        "n_neg":      pd.Series(dtype="int64"),
    })
    if per_window.empty:
        return empty
    pw = per_window.dropna(subset=[score_col]).copy()
    if pw.empty:
        return empty
    t_min = float(pw[time_col].min())
    t_max = float(pw[time_col].max())
    half = cfg.eval_window_s / 2.0
    # When the timeline is shorter than eval_window_s, fall back to one
    # window centered on the timeline midpoint — this prevents the
    # silent empty-DataFrame return that broke downstream pivoting.
    if t_max - t_min < cfg.eval_window_s:
        centers = np.array([(t_min + t_max) / 2.0])
    else:
        centers = np.arange(t_min + half, t_max - half + 1e-9, cfg.stride_s)
        if centers.size == 0:
            centers = np.array([(t_min + t_max) / 2.0])

    t_arr = pw[time_col].to_numpy()
    s_arr = pw[score_col].to_numpy(dtype=float)
    y_arr = pw[label_col].to_numpy(dtype=int)

    rows = []
    for tc in centers:
        lo, hi = tc - half, tc + half
        mask = (t_arr >= lo) & (t_arr <= hi)
        if not mask.any():
            rows.append({"t_center_s": float(tc), "pr_auc": float("nan"),
                         "n_pos": 0, "n_neg": 0})
            continue
        y = y_arr[mask]
        s = s_arr[mask]
        n_pos = int(y.sum())
        n_neg = int(len(y) - n_pos)
        if n_pos < cfg.min_pos or n_neg < cfg.min_neg:
            rows.append({"t_center_s": float(tc), "pr_auc": float("nan"),
                         "n_pos": n_pos, "n_neg": n_neg})
            continue
        rows.append({
            "t_center_s": float(tc),
            "pr_auc": float(average_precision_score(y, s)),
            "n_pos": n_pos,
            "n_neg": n_neg,
        })
    if not rows:
        return empty
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Aggregate PR-AUC helpers
# ---------------------------------------------------------------------------

def overall_pr_auc(
    per_window: pd.DataFrame,
    score_col: str = "score",
    label_col: str = "label_anomaly",
) -> float:
    """PR-AUC over the full per-window table. Returns NaN if degenerate."""
    pw = per_window.dropna(subset=[score_col])
    if pw.empty:
        return float("nan")
    y = pw[label_col].to_numpy(dtype=int)
    if y.sum() == 0 or y.sum() == len(y):
        return float("nan")
    s = pw[score_col].to_numpy(dtype=float)
    return float(average_precision_score(y, s))


def drift_phase_pr_auc(
    per_window: pd.DataFrame,
    drift_phase_ids: Sequence[int],
    score_col: str = "score",
    label_col: str = "label_anomaly",
    phase_col: str = "phase_id",
) -> float:
    """PR-AUC restricted to windows whose ``phase_id`` is in ``drift_phase_ids``.

    Used by Iter A acceptance C3: the drift-triggered strategy must beat
    static "under drift" — i.e. on the subset of windows that fall in
    drift-affected phases. Returns NaN if no eligible windows or all
    one-class.
    """
    if not drift_phase_ids:
        return float("nan")
    pw = per_window[per_window[phase_col].isin(list(drift_phase_ids))]
    return overall_pr_auc(pw, score_col=score_col, label_col=label_col)


# ---------------------------------------------------------------------------
# Cost ledger
# ---------------------------------------------------------------------------

def cost_summary(retrain_log: pd.DataFrame) -> dict:
    """Roll up a retrain-log DataFrame into a single-row cost summary.

    Args:
        retrain_log : DataFrame with at least columns
                      ``[t_s, n_train_samples, fit_cpu_s]``.
                      May be empty (returns zero-valued summary).

    Returns:
        dict with::

            n_refits             : int (total fit events including warm-up)
            n_train_samples_sum  : int (sum across all fits)
            fit_cpu_s_sum        : float (total CPU sec across all fits)
            fit_cpu_s_mean       : float (per-fit average; 0 if empty)
            n_train_samples_mean : float (per-fit average; 0 if empty)
            first_refit_t_s      : float | None (timeline time of first refit
                                   AFTER warm-up; None if no post-warm-up
                                   refits)
    """
    if retrain_log is None or retrain_log.empty:
        return {
            "n_refits": 0,
            "n_train_samples_sum": 0,
            "fit_cpu_s_sum": 0.0,
            "fit_cpu_s_mean": 0.0,
            "n_train_samples_mean": 0.0,
            "first_refit_t_s": None,
        }
    n = int(len(retrain_log))
    n_samples_sum = int(retrain_log["n_train_samples"].sum())
    cpu_sum = float(retrain_log["fit_cpu_s"].sum())
    post_warmup = retrain_log[retrain_log["reason"] != "warmup"]
    first_t = (
        float(post_warmup["t_s"].iloc[0]) if not post_warmup.empty else None
    )
    return {
        "n_refits": n,
        "n_train_samples_sum": n_samples_sum,
        "fit_cpu_s_sum": cpu_sum,
        "fit_cpu_s_mean": cpu_sum / n,
        "n_train_samples_mean": n_samples_sum / n,
        "first_refit_t_s": first_t,
    }
