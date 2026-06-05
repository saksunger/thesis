"""Unit tests for analysis.config_perf.surrogate."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.config_perf.surrogate import (
    ConformalQuantileGBSurrogate,
    HistGBSurrogate,
    QuantileGBSurrogate,
    benchmark_surrogates,
    fit_per_target,
)


def _toy_problem(n: int = 200, seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({
        "a": rng.uniform(-3, 3, size=n),
        "b": rng.uniform(0, 1, size=n),
    })
    # y = sin(a) + 2*b + noise
    y = pd.Series(np.sin(X["a"]) + 2.0 * X["b"] + 0.1 * rng.normal(size=n), name="y")
    return X, y


# ---------------------------------------------------------------------------
# HistGBSurrogate
# ---------------------------------------------------------------------------

class TestHistGBSurrogate:
    def test_fits_and_predicts_shape(self):
        X, y = _toy_problem()
        m = HistGBSurrogate(random_state=0).fit(X, y)
        preds = m.predict(X)
        assert preds.shape == (len(X),)

    def test_predict_without_fit_raises(self):
        m = HistGBSurrogate()
        with pytest.raises(RuntimeError, match="not fitted"):
            m.predict(pd.DataFrame({"a": [1.0], "b": [0.5]}))

    def test_learns_separable_signal(self):
        """Toy R^2 must clear 0.7 on a learnable function."""
        X, y = _toy_problem(n=500, seed=1)
        m = HistGBSurrogate(random_state=0).fit(X, y)
        preds = m.predict(X)
        ss_res = np.sum((y - preds) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        r2 = 1.0 - ss_res / ss_tot
        assert r2 > 0.7, f"toy R^2 too low: {r2:.3f}"

    def test_handles_dataframe_and_ndarray(self):
        X, y = _toy_problem(n=80)
        m = HistGBSurrogate(random_state=0).fit(X.to_numpy(), y.to_numpy())
        preds = m.predict(X.to_numpy())
        assert preds.shape == (80,)


# ---------------------------------------------------------------------------
# QuantileGBSurrogate
# ---------------------------------------------------------------------------

class TestQuantileGBSurrogate:
    def test_predict_interval_returns_3_arrays(self):
        X, y = _toy_problem(n=200)
        m = QuantileGBSurrogate(random_state=0).fit(X, y)
        lo, mid, hi = m.predict_interval(X)
        assert lo.shape == mid.shape == hi.shape == (200,)

    def test_predict_returns_median(self):
        X, y = _toy_problem(n=200)
        m = QuantileGBSurrogate(random_state=0).fit(X, y)
        _, mid, _ = m.predict_interval(X)
        np.testing.assert_array_equal(m.predict(X), mid)

    def test_interval_monotonicity_enforced(self):
        """lo <= mid <= hi must hold for every row, even if the underlying
        independent quantile models swap order in a pathological corner."""
        X, y = _toy_problem(n=200)
        m = QuantileGBSurrogate(random_state=0).fit(X, y)
        lo, mid, hi = m.predict_interval(X)
        assert (lo <= mid + 1e-9).all()
        assert (mid <= hi + 1e-9).all()

    def test_nominal_coverage_property(self):
        m = QuantileGBSurrogate(q_lo=0.1, q_hi=0.9)
        assert m.nominal_coverage == pytest.approx(0.8)
        m2 = QuantileGBSurrogate(q_lo=0.05, q_hi=0.95)
        assert m2.nominal_coverage == pytest.approx(0.9)

    def test_invalid_quantiles_raise(self):
        with pytest.raises(ValueError, match="q_lo"):
            QuantileGBSurrogate(q_lo=0.0)
        with pytest.raises(ValueError, match="q_lo"):
            QuantileGBSurrogate(q_lo=0.5)
        with pytest.raises(ValueError, match="q_hi"):
            QuantileGBSurrogate(q_hi=0.5)
        with pytest.raises(ValueError, match="q_hi"):
            QuantileGBSurrogate(q_hi=1.0)

    def test_empirical_coverage_close_to_nominal_on_iid_noise(self):
        """On large iid Gaussian-noise data the 80% PI must cover ~80%."""
        rng = np.random.default_rng(7)
        n = 1500
        X = pd.DataFrame({"x": rng.uniform(0, 1, size=n)})
        y = pd.Series(rng.normal(size=n))  # iid noise; surrogate learns mean=0
        m = QuantileGBSurrogate(q_lo=0.1, q_hi=0.9, random_state=0).fit(X, y)
        lo, _, hi = m.predict_interval(X)
        cov = float(np.mean((y.to_numpy() >= lo) & (y.to_numpy() <= hi)))
        # Generous training-set coverage tolerance; CV test in eval.py is stricter
        assert 0.65 <= cov <= 0.95, f"unexpected training coverage: {cov:.3f}"


# ---------------------------------------------------------------------------
# ConformalQuantileGBSurrogate
# ---------------------------------------------------------------------------

class TestConformalQuantileGB:
    def test_predict_interval_widens_naive(self):
        """Conformal interval must be at least as wide as naive on the same data."""
        rng = np.random.default_rng(11)
        n = 300
        X = pd.DataFrame({"x": rng.uniform(0, 1, size=n)})
        y = pd.Series(rng.normal(size=n))
        naive = QuantileGBSurrogate(random_state=0).fit(X, y)
        conformal = ConformalQuantileGBSurrogate(random_state=0).fit(X, y)
        lo_n, _, hi_n = naive.predict_interval(X)
        lo_c, _, hi_c = conformal.predict_interval(X)
        width_n = float(np.mean(hi_n - lo_n))
        width_c = float(np.mean(hi_c - lo_c))
        # Conformal widens by q_hat >= 0; on iid noise with naive under-coverage
        # we expect strict widening
        assert width_c >= width_n

    def test_coverage_passes_acceptance_band(self):
        """80% PI empirical coverage on held-out data should land in [0.7, 0.95]."""
        rng = np.random.default_rng(11)
        n = 400
        n_train, n_test = 300, 100
        X = pd.DataFrame({"x": rng.uniform(-2, 2, size=n)})
        y = pd.Series(np.sin(X["x"]) + 0.5 * rng.normal(size=n))
        m = ConformalQuantileGBSurrogate(random_state=0).fit(X.iloc[:n_train], y.iloc[:n_train])
        lo, _, hi = m.predict_interval(X.iloc[n_train:])
        cov = float(np.mean((y.iloc[n_train:].to_numpy() >= lo)
                            & (y.iloc[n_train:].to_numpy() <= hi)))
        # CQR finite-sample guarantee is P >= 1-alpha = 0.8; allow some
        # slack for the n=100 test set and tree non-determinism
        assert 0.7 <= cov <= 0.99, f"unexpected conformal coverage {cov:.3f}"

    def test_predict_returns_median(self):
        rng = np.random.default_rng(0)
        X = pd.DataFrame({"x": rng.uniform(0, 1, size=120)})
        y = pd.Series(rng.normal(size=120))
        m = ConformalQuantileGBSurrogate(random_state=0).fit(X, y)
        _, mid, _ = m.predict_interval(X)
        np.testing.assert_array_equal(m.predict(X), mid)

    def test_too_few_samples_raises(self):
        X = pd.DataFrame({"x": np.arange(10)})
        y = pd.Series(np.arange(10))
        with pytest.raises(ValueError, match=">= 20 fit samples"):
            ConformalQuantileGBSurrogate(random_state=0).fit(X, y)

    def test_invalid_cal_frac_raises(self):
        with pytest.raises(ValueError, match="calibration_frac"):
            ConformalQuantileGBSurrogate(calibration_frac=0.0)
        with pytest.raises(ValueError, match="calibration_frac"):
            ConformalQuantileGBSurrogate(calibration_frac=0.6)


# ---------------------------------------------------------------------------
# fit_per_target
# ---------------------------------------------------------------------------

class TestFitPerTarget:
    def test_returns_one_model_per_column(self):
        rng = np.random.default_rng(0)
        X = pd.DataFrame({"a": rng.normal(size=80), "b": rng.normal(size=80)})
        Y = pd.DataFrame({
            "y1": rng.normal(size=80),
            "y2": rng.normal(size=80),
        })
        models = fit_per_target(lambda: HistGBSurrogate(random_state=0), X, Y)
        assert set(models) == {"y1", "y2"}
        for _, m in models.items():
            assert isinstance(m, HistGBSurrogate)
            assert m.predict(X).shape == (80,)

    def test_targets_subset(self):
        rng = np.random.default_rng(0)
        X = pd.DataFrame({"a": rng.normal(size=50)})
        Y = pd.DataFrame({"y1": rng.normal(size=50), "y2": rng.normal(size=50)})
        models = fit_per_target(
            lambda: HistGBSurrogate(random_state=0), X, Y, targets=("y1",),
        )
        assert set(models) == {"y1"}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestBenchmarkSurrogates:
    def test_returns_known_names(self):
        reg = benchmark_surrogates()
        assert "HistGB" in reg
        assert "QuantileGB" in reg
        assert "ConformalQuantileGB" in reg
        assert callable(reg["HistGB"])
        assert callable(reg["QuantileGB"])
        assert callable(reg["ConformalQuantileGB"])

    def test_factories_produce_fresh_instances(self):
        reg = benchmark_surrogates()
        a = reg["HistGB"]()
        b = reg["HistGB"]()
        assert a is not b
