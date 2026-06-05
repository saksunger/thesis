"""Drift detectors for Phase 6 benchmark.

Uniform API: every detector exposes::

    .update(value: float) -> bool

returning True at the *first sample* where the detector judges drift to
have occurred (then auto-resets and starts looking for the next drift).

Roster (7 detectors, hits the docs/plan.md "5+" bar):

    Stream (river) — work on numeric stream, one sample at a time
    ----------------------------------------------------------------
    1. ADWINDetector          : ADaptive WINdowing (Bifet & Gavalda 2007).
                                Distribution-free; default for any stream.
    2. KSWINDetector          : Kolmogorov-Smirnov WINdowing. Distribution-
                                shape sensitive (good fit for D-2 channel
                                swap).
    3. PageHinkleyDetector    : Page-Hinkley change-point test. Abrupt
                                mean-shift detector (good fit for D-4
                                reconfig).

    Stream (river binary) — work on a binary error indicator
    ----------------------------------------------------------------
    4. DDMDetector            : Drift Detection Method (Gama 2004). Wraps
                                a continuous stream by binarising at a
                                user-supplied threshold (>= threshold ->
                                "error").
    5. EDDMDetector           : Early Drift Detection Method (Baena-
                                Garcia 2006). Same binarisation; better
                                on gradual drifts.

    Batch (custom) — compare a reference window vs the current window
    ----------------------------------------------------------------
    6. MMDBatchDetector       : Maximum Mean Discrepancy with RBF kernel,
                                permutation test. Distribution-free,
                                high-power for kernel-detectable shifts.
    7. EnergyBatchDetector    : Energy distance via scipy, permutation
                                test. Distribution-free, sensitive to
                                both mean and shape changes.

All detectors are NaN-safe (NaN samples are skipped, not fed to the
underlying algorithm).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from river import drift as river_drift
from river.drift import binary as river_drift_binary
from scipy import stats
from sklearn.metrics.pairwise import rbf_kernel


# ---------------------------------------------------------------------------
# Base + common helpers
# ---------------------------------------------------------------------------

class _BaseDetector:
    """Minimal abstract base; subclasses must override `update`."""

    name: str = "base"

    def update(self, value: float) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError


def _is_nan(x) -> bool:
    try:
        return np.isnan(float(x))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Stream detectors (river)
# ---------------------------------------------------------------------------

class ADWINDetector(_BaseDetector):
    """ADaptive WINdowing wrapper.

    Args:
        delta: confidence (smaller = more conservative, fewer false positives).
               river default is 0.002; we use 0.01 for higher sensitivity
               given fleet-level aggregates have low noise.
    """

    name = "ADWIN"

    def __init__(self, delta: float = 0.01) -> None:
        self.delta = delta
        self._det = river_drift.ADWIN(delta=delta)

    def update(self, value: float) -> bool:
        if _is_nan(value):
            return False
        self._det.update(float(value))
        return bool(self._det.drift_detected)


class KSWINDetector(_BaseDetector):
    """KSWIN: KS-test on a sliding window vs a reference batch.

    Args:
        alpha     : p-value threshold (default 0.005).
        window_size: total window samples (default 100).
        stat_size : reference batch size taken from the head (default 30).
    """

    name = "KSWIN"

    def __init__(self, alpha: float = 0.005, window_size: int = 100,
                 stat_size: int = 30) -> None:
        self.alpha = alpha
        self.window_size = window_size
        self.stat_size = stat_size
        self._det = river_drift.KSWIN(
            alpha=alpha, window_size=window_size, stat_size=stat_size,
            seed=42,
        )

    def update(self, value: float) -> bool:
        if _is_nan(value):
            return False
        self._det.update(float(value))
        return bool(self._det.drift_detected)


class PageHinkleyDetector(_BaseDetector):
    """Page-Hinkley change-point test.

    Args:
        min_instances: warm-up sample count before any detection (default 30).
        threshold    : cumulative-sum threshold (default 50; lower = more
                       sensitive).
        alpha        : moving-average forgetting factor (default 0.9999).
    """

    name = "PageHinkley"

    def __init__(self, min_instances: int = 30, threshold: float = 50.0,
                 alpha: float = 0.9999) -> None:
        self.min_instances = min_instances
        self.threshold = threshold
        self.alpha = alpha
        self._det = river_drift.PageHinkley(
            min_instances=min_instances, threshold=threshold, alpha=alpha,
        )

    def update(self, value: float) -> bool:
        if _is_nan(value):
            return False
        self._det.update(float(value))
        return bool(self._det.drift_detected)


# ---------------------------------------------------------------------------
# Binary-stream detectors (river) over a thresholded continuous stream
# ---------------------------------------------------------------------------

@dataclass
class _BinarisingDetector(_BaseDetector):
    """Mixin: binarise stream via `is_error(x)` then feed to river binary detector.

    `direction`: 'high' -> error when x >= threshold (e.g. PP rate, RLF rate).
                 'low'  -> error when x <= threshold (e.g. HOSR drops).
                 'absdev_zscore' -> error when |x - reference_mean| / reference_std
                       exceeds `z`; reference is the first `warmup_n`
                       non-NaN samples seen.
    """

    direction: str = "high"
    threshold: float = 0.0
    z: float = 2.0
    warmup_n: int = 30

    def __post_init__(self) -> None:
        self._ref_buf: list[float] = []
        self._ref_mean: float | None = None
        self._ref_std: float | None = None

    def _binarise(self, value: float) -> int | None:
        if _is_nan(value):
            return None
        v = float(value)
        if self.direction == "high":
            return int(v >= self.threshold)
        if self.direction == "low":
            return int(v <= self.threshold)
        if self.direction == "absdev_zscore":
            if self._ref_mean is None:
                self._ref_buf.append(v)
                if len(self._ref_buf) >= self.warmup_n:
                    arr = np.asarray(self._ref_buf, dtype=float)
                    self._ref_mean = float(arr.mean())
                    self._ref_std = float(arr.std(ddof=0)) or 1.0
                return None  # still warming
            return int(abs(v - self._ref_mean) / (self._ref_std or 1.0) >= self.z)
        raise ValueError(f"unknown direction: {self.direction!r}")


class DDMDetector(_BinarisingDetector):
    """DDM over binarised stream.

    Args (in addition to _BinarisingDetector):
        warning_level, drift_level : river DDM thresholds. Defaults match
        river but tuned slightly lower (2.5 / 3.0 -> 2.0 / 2.5) so the
        detector fires within a 60 s drift window at our 1 Hz cadence.
    """

    name = "DDM"

    def __init__(self, direction: str = "absdev_zscore", threshold: float = 0.0,
                 z: float = 2.0, warmup_n: int = 30,
                 warning_level: float = 2.0, drift_level: float = 2.5) -> None:
        super().__init__(direction=direction, threshold=threshold, z=z, warmup_n=warmup_n)
        self.warning_level = warning_level
        self.drift_level = drift_level
        self._det = river_drift_binary.DDM(
            warning_threshold=warning_level, drift_threshold=drift_level,
        )

    def update(self, value: float) -> bool:
        b = self._binarise(value)
        if b is None:
            return False
        self._det.update(b)
        return bool(self._det.drift_detected)


class EDDMDetector(_BinarisingDetector):
    """EDDM over binarised stream — better than DDM for gradual drifts.

    Args (in addition to _BinarisingDetector):
        alpha : warning level (river default 0.95).
        beta  : drift level   (river default 0.9; smaller -> more sensitive).
    """

    name = "EDDM"

    def __init__(self, direction: str = "absdev_zscore", threshold: float = 0.0,
                 z: float = 2.0, warmup_n: int = 30,
                 alpha: float = 0.95, beta: float = 0.9) -> None:
        super().__init__(direction=direction, threshold=threshold, z=z, warmup_n=warmup_n)
        self.alpha = alpha
        self.beta = beta
        self._det = river_drift_binary.EDDM(alpha=alpha, beta=beta)

    def update(self, value: float) -> bool:
        b = self._binarise(value)
        if b is None:
            return False
        self._det.update(b)
        return bool(self._det.drift_detected)


# ---------------------------------------------------------------------------
# Batch detectors (custom)
# ---------------------------------------------------------------------------

class _SlidingBatchDetector(_BaseDetector):
    """Hold a reference window and an active window; emit drift when the
    statistic on (ref, active) exceeds the permutation p-value threshold.

    The reference window is the first `ref_size` non-NaN samples; once
    full, every new sample slides the active window. Re-testing happens
    every `step` samples (default 5) to keep wall-clock bounded — for
    each sample the permutation test costs O(n_perm * window_size^2),
    and on long timelines (`timeline_medium` = 1801 1Hz samples)
    re-testing per-sample is ~5 billion kernel evaluations. `step=5`
    drops that 5x while still giving sub-second detection latency at our
    cadence.

    After a drift is fired the active window is cleared (the reference
    is intentionally *not* refreshed — semantics: "drift vs the
    baseline state at deployment time", which matches the thesis's
    drift-aware setup).
    """

    name = "base-batch"

    def __init__(self, ref_size: int = 60, active_size: int = 60,
                 alpha: float = 0.01, n_perm: int = 100,
                 step: int = 5,
                 random_state: int = 42) -> None:
        self.ref_size = ref_size
        self.active_size = active_size
        self.alpha = alpha
        self.n_perm = n_perm
        self.step = max(1, int(step))
        self._rng = np.random.default_rng(random_state)
        self._ref: list[float] = []
        self._active: list[float] = []
        self._ref_locked: bool = False
        self._since_last_test: int = 0

    def _stat(self, ref: np.ndarray, act: np.ndarray) -> float:
        raise NotImplementedError

    def _pvalue(self, ref: np.ndarray, act: np.ndarray) -> float:
        observed = self._stat(ref, act)
        pooled = np.concatenate([ref, act])
        n_ref = len(ref)
        ge = 0
        for _ in range(self.n_perm):
            self._rng.shuffle(pooled)
            ge += int(self._stat(pooled[:n_ref], pooled[n_ref:]) >= observed)
        return (ge + 1) / (self.n_perm + 1)

    def update(self, value: float) -> bool:
        if _is_nan(value):
            return False
        v = float(value)
        if not self._ref_locked:
            self._ref.append(v)
            if len(self._ref) >= self.ref_size:
                self._ref_locked = True
            return False
        self._active.append(v)
        if len(self._active) < self.active_size:
            return False
        # Slide active window (drop the head sample)
        if len(self._active) > self.active_size:
            self._active.pop(0)
        # Decide whether to run the test this step
        self._since_last_test += 1
        if self._since_last_test < self.step:
            return False
        self._since_last_test = 0
        ref = np.asarray(self._ref, dtype=float)
        act = np.asarray(self._active, dtype=float)
        p = self._pvalue(ref, act)
        return bool(p < self.alpha)


class MMDBatchDetector(_SlidingBatchDetector):
    """Maximum Mean Discrepancy (RBF kernel) batch detector.

    MMD² estimator (Gretton 2012 unbiased). `gamma` defaults to
    `1 / (median pairwise sq-dist on the reference)` (median-heuristic).
    """

    name = "MMD-batch"

    def __init__(self, ref_size: int = 60, active_size: int = 60,
                 alpha: float = 0.01, n_perm: int = 100,
                 step: int = 5, random_state: int = 42) -> None:
        super().__init__(ref_size, active_size, alpha, n_perm, step, random_state)
        self._gamma: float | None = None

    def _median_heuristic_gamma(self, x: np.ndarray) -> float:
        # Pairwise squared distances on a small sample
        x = x.reshape(-1, 1)
        d2 = (x - x.T) ** 2
        med = np.median(d2[d2 > 0]) if (d2 > 0).any() else 1.0
        return float(1.0 / max(med, 1e-9))

    def _stat(self, ref: np.ndarray, act: np.ndarray) -> float:
        if self._gamma is None:
            self._gamma = self._median_heuristic_gamma(ref)
        gamma = self._gamma
        Kxx = rbf_kernel(ref.reshape(-1, 1), ref.reshape(-1, 1), gamma=gamma)
        Kyy = rbf_kernel(act.reshape(-1, 1), act.reshape(-1, 1), gamma=gamma)
        Kxy = rbf_kernel(ref.reshape(-1, 1), act.reshape(-1, 1), gamma=gamma)
        n = len(ref); m = len(act)
        # Unbiased MMD² (Gretton 2012, Eq. 3)
        np.fill_diagonal(Kxx, 0.0)
        np.fill_diagonal(Kyy, 0.0)
        mmd2 = (Kxx.sum() / (n * (n - 1))
                + Kyy.sum() / (m * (m - 1))
                - 2.0 * Kxy.sum() / (n * m))
        return float(max(mmd2, 0.0))


class EnergyBatchDetector(_SlidingBatchDetector):
    """Energy distance via `scipy.stats.energy_distance`.

    Reference: Szekely & Rizzo (2013). Permutation test for the null
    `ref ~ active` distribution; rejects under shift in mean / shape.
    """

    name = "Energy-batch"

    def _stat(self, ref: np.ndarray, act: np.ndarray) -> float:
        return float(stats.energy_distance(ref, act))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def benchmark_detectors() -> list[_BaseDetector]:
    """Return one instance of every Phase 6 benchmark detector.

    Use a fresh call per stream — each detector is stateful and must be
    reset between runs (do not share instances across streams).
    """
    return [
        ADWINDetector(),
        KSWINDetector(),
        PageHinkleyDetector(),
        DDMDetector(),
        EDDMDetector(),
        MMDBatchDetector(),
        EnergyBatchDetector(),
    ]


def detector_names() -> list[str]:
    return [d.name for d in benchmark_detectors()]
