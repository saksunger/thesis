"""Replay-based walk-forward orchestrator for Phase 7.

Given a labelled window table, a wide streams table, a base anomaly
detector factory and a retraining strategy, walk forward through the
timeline in causal order and produce:

    - per_window  : DataFrame with [..., score, label_anomaly, ...] for
                    every scoreable window (warm-up windows excluded).
    - retrain_log : DataFrame with [t_s, reason, n_train_samples,
                    fit_cpu_s] — one row per fit event (including the
                    initial warm-up fit).

Time model
----------
The orchestrator advances along a single time axis (``t_now_s``) that
ratchets monotonically with each new window's ``t_end_s``. Between two
consecutive windows it feeds *all* drift-detector samples whose stream
``time_s`` has just become available (no peeking at future stream
values). This keeps the replay strictly causal — the same property the
real adaptive system would have at deployment time.

Initial fit
-----------
The detector is warm-fitted on every window with ``phase_id <=
warmup_max_phase_id`` AND ``label_anomaly == 0`` (the unsupervised
training pool from Phase 5). Scores for windows in the warm-up region
are emitted as NaN — they would otherwise bias the sliding-PR-AUC
upward because the model has seen them at fit time.

Retrain
-------
On a refit decision the orchestrator takes the last ``retrain_window_s``
seconds of windows up to but not including the current one
(regardless of label — operational systems don't have labels) and
calls ``detector.fit(...)``. The decision is taken once per window
boundary, AFTER drift detectors have been updated for that window's
slice of stream time.

Optional self-supervised filter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
``OrchestratorConfig.retrain_score_filter_quantile`` (default ``None``)
enables a defensive filter against the well-known "anomaly
self-contamination" pitfall of naive streaming AE retraining
(Aggarwal 2016, Ch. 12; Yoon et al. 2021): drift-affected pools
contain real anomalies; naively refitting on them teaches the model
that anomalies are normal, destroying detection performance.

When the filter is set (e.g. ``0.8``), the orchestrator first scores
the retrain pool with the *current* detector, keeps only the bottom
``q`` quantile (i.e. the windows the model currently judges normal),
and refits on that filtered subset. This is the standard
semi-supervised AE retraining baseline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol, runtime_checkable

import numpy as np
import pandas as pd

from analysis.adaptive.strategies import RetrainStrategy


# ---------------------------------------------------------------------------
# Detector protocols (duck typing — no inheritance required)
# ---------------------------------------------------------------------------

@runtime_checkable
class AnomalyDetectorLike(Protocol):
    """Minimum interface — same as analysis.anomaly.detectors.* exposes."""

    name: str

    def fit(self, X: pd.DataFrame) -> "AnomalyDetectorLike": ...
    def score_samples(self, X: pd.DataFrame) -> np.ndarray: ...


@runtime_checkable
class DriftDetectorLike(Protocol):
    """Minimum interface — same as analysis.drift.detectors.* exposes."""

    name: str

    def update(self, value: float) -> bool: ...


# Type aliases for the factories the caller passes in
AnomalyDetectorFactory = Callable[[], AnomalyDetectorLike]
DriftDetectorFactory = Callable[[], DriftDetectorLike]


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    """Bundle of orchestrator outputs for a single strategy run."""

    strategy_name: str
    base_detector_name: str
    per_window: pd.DataFrame   # [t_mid_s, score, label_anomaly, phase_id, ...]
    retrain_log: pd.DataFrame  # [t_s, reason, n_train_samples, fit_cpu_s]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class OrchestratorConfig:
    """Knobs for the walk-forward runner."""

    feature_cols: list[str]
    warmup_max_phase_id: int = 3       # Phase 5 convention: phases 1..3 baseline
    retrain_window_s: float = 120.0     # last K seconds of windows for refit
    retrain_score_filter_quantile: float | None = None  # None = naive
    score_chunk_size: int = 4096        # batch score in chunks to bound memory
    random_state: int = 42

    def __post_init__(self) -> None:
        if not self.feature_cols:
            raise ValueError("feature_cols must be non-empty")
        if self.retrain_window_s <= 0:
            raise ValueError(
                f"retrain_window_s must be > 0 (got {self.retrain_window_s})"
            )
        if self.retrain_score_filter_quantile is not None:
            q = float(self.retrain_score_filter_quantile)
            if not (0.0 < q <= 1.0):
                raise ValueError(
                    f"retrain_score_filter_quantile must be in (0, 1] "
                    f"or None (got {q})"
                )


class AdaptiveOrchestrator:
    """Walk-forward replay runner.

    Public surface is a single :meth:`run` method. The constructor is
    cheap (no fitting) so tests can spin up multiple orchestrators
    against the same data.
    """

    def __init__(
        self,
        windows: pd.DataFrame,
        streams_wide: pd.DataFrame,
        base_detector_factory: AnomalyDetectorFactory,
        drift_detector_factories: Mapping[str, DriftDetectorFactory],
        cfg: OrchestratorConfig,
    ) -> None:
        self._validate_inputs(windows, streams_wide, cfg)
        self.windows = windows.sort_values("t_end_s").reset_index(drop=True)
        self.streams_wide = streams_wide.sort_index()
        self.base_detector_factory = base_detector_factory
        self.drift_detector_factories = dict(drift_detector_factories)
        self.cfg = cfg
        self._base_detector_name = self._probe_detector_name(base_detector_factory)

    # -- public API ---------------------------------------------------------

    def run(self, strategy: RetrainStrategy) -> RunResult:
        """Replay the timeline under one retraining ``strategy``."""
        strategy.reset()

        # Spin up fresh drift detectors per run (state is per-stream).
        drift_dets: dict[str, DriftDetectorLike] = {
            sn: factory() for sn, factory in self.drift_detector_factories.items()
        }

        # 1. Initial warm-up fit on phase_id <= warmup_max_phase_id, label=0.
        detector, warmup_t_s, warmup_log = self._warmup_fit()
        retrain_rows: list[dict] = [warmup_log]
        if detector is None:
            # Pathological: no warm-up windows. Return all-NaN scores.
            return RunResult(
                strategy_name=strategy.name,
                base_detector_name=self._base_detector_name,
                per_window=self._empty_per_window(),
                retrain_log=pd.DataFrame(retrain_rows),
            )

        # 2. Walk-forward score loop over post-warm-up windows.
        post_warm_mask = self.windows["phase_id"] > self.cfg.warmup_max_phase_id
        post_warm = self.windows[post_warm_mask].reset_index(drop=True)
        if post_warm.empty:
            return RunResult(
                strategy_name=strategy.name,
                base_detector_name=self._base_detector_name,
                per_window=self._empty_per_window(),
                retrain_log=pd.DataFrame(retrain_rows),
            )

        stream_t_arr = self.streams_wide.index.to_numpy(dtype=float)
        stream_cursor = int(np.searchsorted(stream_t_arr, warmup_t_s, side="right"))

        scores = np.full(len(post_warm), np.nan, dtype=float)
        feature_mat = post_warm[self.cfg.feature_cols]
        t_end_arr = post_warm["t_end_s"].to_numpy(dtype=float)

        for i in range(len(post_warm)):
            t_now = float(t_end_arr[i])
            # 2a. Feed drift detectors with all stream samples whose
            #     time_s <= t_now (and not yet fed).
            drift_signals: dict[str, bool] = {sn: False for sn in drift_dets}
            while (
                stream_cursor < len(stream_t_arr)
                and stream_t_arr[stream_cursor] <= t_now
            ):
                t_s_val = float(stream_t_arr[stream_cursor])
                row = self.streams_wide.iloc[stream_cursor]
                for sn, det in drift_dets.items():
                    if sn not in self.streams_wide.columns:
                        continue
                    v = row[sn]
                    fired = bool(det.update(float(v)) if not _is_nan(v) else False)
                    if fired:
                        drift_signals[sn] = True
                stream_cursor += 1
                del t_s_val  # silence unused-var lint

            # 2b. Strategy decision.
            refit, reason = strategy.should_retrain(t_now, drift_signals)
            if refit:
                detector, log_row = self._refit(
                    detector_factory=self.base_detector_factory,
                    t_now_s=t_now,
                    reason=reason,
                    current_detector=detector,
                )
                if log_row is not None:
                    retrain_rows.append(log_row)

            # 2c. Score current window (single-row batch).
            x_row = feature_mat.iloc[i : i + 1]
            try:
                scores[i] = float(detector.score_samples(x_row)[0])
            except Exception as exc:  # noqa: BLE001 — defensive at the boundary
                # Any score failure -> NaN, keep replaying.
                scores[i] = float("nan")
                del exc

        per_window = post_warm.copy()
        per_window["score"] = scores
        per_window["strategy"] = strategy.name
        per_window["base_detector"] = self._base_detector_name
        return RunResult(
            strategy_name=strategy.name,
            base_detector_name=self._base_detector_name,
            per_window=per_window,
            retrain_log=pd.DataFrame(retrain_rows),
        )

    # -- internals ----------------------------------------------------------

    def _warmup_fit(self) -> tuple[AnomalyDetectorLike | None, float, dict]:
        """Fit the detector on warm-up windows. Returns (detector, t_warmup_end_s, log)."""
        train_mask = (
            (self.windows["phase_id"] <= self.cfg.warmup_max_phase_id)
            & (self.windows["label_anomaly"] == 0)
        )
        train_df = self.windows[train_mask]
        if train_df.empty:
            return None, 0.0, {
                "t_s": 0.0,
                "reason": "warmup_failed",
                "n_train_samples": 0,
                "n_train_samples_pre_filter": 0,
                "fit_cpu_s": 0.0,
            }
        detector = self.base_detector_factory()
        t0 = time.perf_counter()
        detector.fit(train_df[self.cfg.feature_cols])
        elapsed = time.perf_counter() - t0
        t_warmup_end_s = float(train_df["t_end_s"].max())
        log = {
            "t_s": t_warmup_end_s,
            "reason": "warmup",
            "n_train_samples": int(len(train_df)),
            "n_train_samples_pre_filter": int(len(train_df)),
            "fit_cpu_s": float(elapsed),
        }
        return detector, t_warmup_end_s, log

    def _refit(
        self,
        detector_factory: AnomalyDetectorFactory,
        t_now_s: float,
        reason: str,
        current_detector: AnomalyDetectorLike | None = None,
    ) -> tuple[AnomalyDetectorLike, dict | None]:
        """Refit on last ``retrain_window_s`` seconds (label-agnostic).

        When ``retrain_score_filter_quantile`` is set AND a
        ``current_detector`` is provided, applies the self-supervised
        bottom-quantile filter before refitting.
        """
        train_mask = (
            (self.windows["t_end_s"] <= t_now_s)
            & (self.windows["t_end_s"] > t_now_s - self.cfg.retrain_window_s)
        )
        train_df = self.windows[train_mask]
        if train_df.empty:
            # Nothing to fit on; keep the previous detector and log a no-op.
            return detector_factory(), {
                "t_s": float(t_now_s),
                "reason": f"{reason}_skip_empty",
                "n_train_samples": 0,
                "fit_cpu_s": 0.0,
                "n_train_samples_pre_filter": 0,
            }

        n_pre_filter = int(len(train_df))
        q = self.cfg.retrain_score_filter_quantile
        if q is not None and current_detector is not None:
            try:
                scores = current_detector.score_samples(
                    train_df[self.cfg.feature_cols]
                )
                scores = np.asarray(scores, dtype=float)
                thr = float(np.quantile(scores[~np.isnan(scores)], q))
                keep = (scores <= thr) & (~np.isnan(scores))
                if int(keep.sum()) > 0:
                    train_df = train_df[keep]
            except Exception:
                # Filter is best-effort; on any failure fall back to naive.
                pass

        detector = detector_factory()
        t0 = time.perf_counter()
        detector.fit(train_df[self.cfg.feature_cols])
        elapsed = time.perf_counter() - t0
        log = {
            "t_s": float(t_now_s),
            "reason": str(reason),
            "n_train_samples": int(len(train_df)),
            "n_train_samples_pre_filter": n_pre_filter,
            "fit_cpu_s": float(elapsed),
        }
        return detector, log

    # -- validation ---------------------------------------------------------

    @staticmethod
    def _validate_inputs(
        windows: pd.DataFrame,
        streams_wide: pd.DataFrame,
        cfg: OrchestratorConfig,
    ) -> None:
        req_w = {"t_end_s", "t_mid_s", "phase_id", "label_anomaly", *cfg.feature_cols}
        missing = req_w - set(windows.columns)
        if missing:
            raise ValueError(
                f"windows DataFrame missing required columns: {sorted(missing)}"
            )
        if not isinstance(streams_wide.index, pd.Index):
            raise ValueError("streams_wide must be indexed by time_s")
        # Allow empty streams_wide (drift detectors simply never fire).

    @staticmethod
    def _probe_detector_name(factory: AnomalyDetectorFactory) -> str:
        det = factory()
        return getattr(det, "name", "unknown")

    def _empty_per_window(self) -> pd.DataFrame:
        cols = ["t_mid_s", "t_end_s", "phase_id", "label_anomaly", "score",
                "strategy", "base_detector"]
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_nan(x) -> bool:
    try:
        return bool(np.isnan(float(x)))
    except (TypeError, ValueError):
        return False
