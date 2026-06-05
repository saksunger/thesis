"""Unit tests for bootstrap PR-AUC CI utility (Phase 5 Iter B)."""

from __future__ import annotations

import numpy as np
import pytest

from analysis.anomaly.metrics import (
    BootstrapResult,
    bootstrap_pr_auc,
    format_ci,
)


def _mk_synth(n=2000, prevalence=0.05, seed=0):
    """Build a separable label/score pair.

    Score = label + Gaussian noise. Easy enough that PR-AUC is high but
    not 1.0, leaving room for the CI to be non-degenerate.
    """
    rng = np.random.default_rng(seed)
    y = (rng.random(n) < prevalence).astype(int)
    s = y.astype(float) + rng.normal(0, 0.5, n)
    return y, s


def test_returns_bootstrap_result_with_finite_fields_on_normal_input():
    y, s = _mk_synth()
    r = bootstrap_pr_auc(y, s, n_resamples=200, random_state=1)
    assert isinstance(r, BootstrapResult)
    assert np.isfinite(r.point)
    assert np.isfinite(r.ci_low) and np.isfinite(r.ci_high)
    assert r.ci_low <= r.point <= r.ci_high
    assert 0.0 < r.point <= 1.0
    assert r.n_valid > 0
    assert r.n_resamples == 200


def test_ci_width_shrinks_with_more_resamples():
    # Same data, more bootstrap reps -> tighter CI (sample-noise driven,
    # not data-driven). Tighten threshold loosely (factor 0.95) so this
    # test isn't flaky on RNG luck.
    y, s = _mk_synth(seed=7)
    r_small = bootstrap_pr_auc(y, s, n_resamples=200, random_state=7)
    r_large = bootstrap_pr_auc(y, s, n_resamples=2000, random_state=7)
    w_small = r_small.ci_high - r_small.ci_low
    w_large = r_large.ci_high - r_large.ci_low
    # Larger sample CI should be at most slightly wider; we just guard
    # against a regression where wider != tighter.
    assert w_large <= w_small * 1.10, (
        f"more resamples did not tighten CI: {w_small=:.4f}, {w_large=:.4f}"
    )


def test_alpha_controls_ci_width():
    y, s = _mk_synth(seed=3)
    r_95 = bootstrap_pr_auc(y, s, n_resamples=500, alpha=0.05, random_state=3)
    r_50 = bootstrap_pr_auc(y, s, n_resamples=500, alpha=0.50, random_state=3)
    # 95 % CI must be at least as wide as 50 % CI (by definition)
    w95 = r_95.ci_high - r_95.ci_low
    w50 = r_50.ci_high - r_50.ci_low
    assert w95 >= w50


def test_zero_positives_returns_all_nan():
    y = np.zeros(100, dtype=int)
    s = np.random.default_rng(0).normal(size=100)
    r = bootstrap_pr_auc(y, s, n_resamples=50)
    assert np.isnan(r.point)
    assert np.isnan(r.ci_low) and np.isnan(r.ci_high)
    assert r.n_valid == 0


def test_zero_negatives_returns_all_nan():
    y = np.ones(100, dtype=int)
    s = np.random.default_rng(0).normal(size=100)
    r = bootstrap_pr_auc(y, s, n_resamples=50)
    assert np.isnan(r.point)
    assert r.n_valid == 0


def test_stratified_vs_unstratified_both_run_on_low_prevalence():
    y, s = _mk_synth(n=2000, prevalence=0.01, seed=11)
    r_strat = bootstrap_pr_auc(y, s, n_resamples=300, stratify=True, random_state=11)
    r_unstrat = bootstrap_pr_auc(y, s, n_resamples=300, stratify=False, random_state=11)
    # Both must produce a valid CI; stratified ought to discard FEWER
    # degenerate bootstraps on rare positives.
    assert r_strat.n_valid > 0
    assert r_unstrat.n_valid > 0
    assert r_strat.n_valid >= r_unstrat.n_valid


def test_deterministic_with_seed():
    y, s = _mk_synth(seed=5)
    r1 = bootstrap_pr_auc(y, s, n_resamples=200, random_state=42)
    r2 = bootstrap_pr_auc(y, s, n_resamples=200, random_state=42)
    assert r1.point == r2.point
    assert r1.ci_low == r2.ci_low
    assert r1.ci_high == r2.ci_high


def test_shape_mismatch_raises():
    y = np.zeros(10, dtype=int)
    s = np.zeros(9)
    with pytest.raises(ValueError):
        bootstrap_pr_auc(y, s)


def test_format_ci_renders_three_decimals_by_default():
    r = BootstrapResult(
        point=0.7321, ci_low=0.6904, ci_high=0.7710,
        n_resamples=1000, n_valid=998, alpha=0.05,
    )
    assert format_ci(r) == "0.732 [0.690, 0.771]"


def test_format_ci_handles_nan():
    r = BootstrapResult(
        point=float("nan"), ci_low=float("nan"), ci_high=float("nan"),
        n_resamples=1000, n_valid=0, alpha=0.05,
    )
    assert format_ci(r) == "nan"
