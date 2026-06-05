"""Statistical evaluation utilities for Phase 5 Iter B benchmark.

Bootstrap confidence intervals on PR-AUC (and other detector scores).
The convention follows ADR-12 (calibration uses bootstrap CIs on the
KS-statistic with the same percentile method), so anomaly-detection
results in Chapter 5 are reported with statistically consistent CIs.

Stratified resampling (`stratify=True`, default) preserves the
positive-class prevalence in each bootstrap sample. This matters when
positives are rare (~1-3 % in our windows) because vanilla resampling
can produce all-negative bootstraps whose PR-AUC is undefined.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.metrics import average_precision_score


@dataclass(frozen=True)
class BootstrapResult:
    """Container for bootstrap point estimate + percentile CI."""

    point: float
    ci_low: float
    ci_high: float
    n_resamples: int
    n_valid: int
    alpha: float

    def as_dict(self) -> dict:
        return {
            "point": self.point,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "n_resamples": self.n_resamples,
            "n_valid": self.n_valid,
            "alpha": self.alpha,
        }


def bootstrap_pr_auc(
    y_true: np.ndarray,
    y_score: np.ndarray,
    n_resamples: int = 1000,
    alpha: float = 0.05,
    stratify: bool = True,
    random_state: int = 42,
) -> BootstrapResult:
    """Compute PR-AUC point estimate + percentile CI by bootstrap.

    Args:
        y_true       : binary labels (0/1).
        y_score      : anomaly scores, same length as `y_true`.
        n_resamples  : bootstrap sample count (default 1000 — sufficient
                       for a 95 % CI as long as n_valid >> 100).
        alpha        : CI level. 0.05 -> 95 % CI. Two-sided percentile
                       interval: [alpha/2 quantile, 1-alpha/2 quantile].
        stratify     : if True, resample positives and negatives
                       separately preserving the prevalence (default).
                       Recommended whenever positives are < 5 %.
        random_state : RNG seed for reproducibility.

    Returns:
        :class:`BootstrapResult` with the point estimate (PR-AUC on the
        original, un-resampled data), the lower/upper CI bounds, and
        bookkeeping (n_resamples requested, n_valid actually computed
        after dropping degenerate bootstraps).

    Edge cases:
        - 0 positives or 0 negatives in the input → returns NaN
          everywhere with `n_valid=0`. Downstream code can filter on
          `result.n_valid > 0`.
        - Bootstrap samples that happen to contain only one class are
          silently dropped (those PR-AUCs are undefined). CI is taken
          over the surviving valid samples; `n_valid` reports how many.
    """
    y_true = np.asarray(y_true).astype(int)
    y_score = np.asarray(y_score).astype(float)
    if y_true.shape != y_score.shape:
        raise ValueError(
            f"y_true {y_true.shape} != y_score {y_score.shape}"
        )

    n = len(y_true)
    n_pos = int(y_true.sum())
    n_neg = n - n_pos
    if n_pos == 0 or n_neg == 0:
        return BootstrapResult(
            point=float("nan"),
            ci_low=float("nan"),
            ci_high=float("nan"),
            n_resamples=n_resamples,
            n_valid=0,
            alpha=alpha,
        )

    point = float(average_precision_score(y_true, y_score))

    rng = np.random.default_rng(random_state)
    pos_idx = np.flatnonzero(y_true == 1)
    neg_idx = np.flatnonzero(y_true == 0)

    samples: list[float] = []
    for _ in range(n_resamples):
        if stratify:
            i_pos = rng.choice(pos_idx, size=n_pos, replace=True)
            i_neg = rng.choice(neg_idx, size=n_neg, replace=True)
            idx = np.concatenate([i_pos, i_neg])
        else:
            idx = rng.integers(0, n, size=n)
        y_b = y_true[idx]
        s_b = y_score[idx]
        if y_b.sum() in (0, len(y_b)):
            continue  # degenerate resample; skip
        samples.append(float(average_precision_score(y_b, s_b)))

    if not samples:
        return BootstrapResult(
            point=point,
            ci_low=float("nan"),
            ci_high=float("nan"),
            n_resamples=n_resamples,
            n_valid=0,
            alpha=alpha,
        )

    lo = float(np.quantile(samples, alpha / 2))
    hi = float(np.quantile(samples, 1 - alpha / 2))
    return BootstrapResult(
        point=point,
        ci_low=lo,
        ci_high=hi,
        n_resamples=n_resamples,
        n_valid=len(samples),
        alpha=alpha,
    )


def format_ci(result: BootstrapResult, decimals: int = 3) -> str:
    """Format a BootstrapResult as ``0.732 [0.690, 0.771]`` for tables."""
    if not np.isfinite(result.point):
        return "nan"
    fmt = f"{{:.{decimals}f}}"
    return (
        f"{fmt.format(result.point)} "
        f"[{fmt.format(result.ci_low)}, {fmt.format(result.ci_high)}]"
    )
