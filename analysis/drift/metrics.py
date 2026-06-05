"""Detection-latency, miss-rate, and FPR metrics for Phase 6 benchmark.

The benchmark answers three questions per (detector × drift_instance ×
stream) cell:

    1. **Was the drift detected at all?**  (miss-rate)
    2. **If yes, how long after the GT start time?**  (detection latency)
    3. **How many false alarms during clean baseline phases?**  (FPR)

This module provides:

- :func:`detection_latency` — per (drift_id, detector, stream) latency
  in seconds (NaN = missed).
- :func:`fpr_per_detector_stream` — fraction of baseline-phase time-
  points where the detector fired but no drift was active.
- :func:`bootstrap_latency_ci` — median latency + 95% percentile CI
  via bootstrap, mirroring the ADR-12 calibration convention also used
  in Phase 5 anomaly bootstrap.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Per-drift latency
# ---------------------------------------------------------------------------

def detection_latency(
    detections: pd.DataFrame,
    ground_truth_drift: pd.DataFrame,
    streams: tuple[str, ...] | None = None,
    max_latency_s: float | None = None,
) -> pd.DataFrame:
    """Compute per (drift_id × detector × stream) detection latency.

    Args:
        detections : long-form DataFrame with columns
            `detector`, `stream_name`, `time_s` — one row per detection
            firing.
        ground_truth_drift: from `ground_truth_drift.parquet`, columns
            `drift_id`, `start_time_s`, `end_time_s`, `affected_kpis`.
            For Iter A we treat each ROW as a distinct instance, even
            when `drift_id` repeats (e.g. D-2 fires twice in
            `timeline_medium`); we re-key rows to `<drift_id>#<i>` for
            unambiguous joining.
        streams    : optional tuple of stream names to restrict to.
        max_latency_s : optional ceiling above which a detection counts
                       as a "miss" (default: no cap; only detections
                       within `[start_time_s, end_time_s + 30s]` are
                       considered, allowing a small post-drift recovery
                       window).

    Returns:
        DataFrame with columns:
        - `drift_instance` : f"{drift_id}#{i}" (deterministic per row)
        - `drift_id`
        - `start_time_s`, `end_time_s`
        - `detector`, `stream_name`
        - `detection_time_s` : earliest firing in the eligible window,
          NaN if none.
        - `latency_s`        : detection_time_s - start_time_s, NaN if
          missed.
        - `missed`           : 1 if no detection, 0 otherwise.
    """
    # Assign deterministic per-row instance keys
    gtd = ground_truth_drift.reset_index(drop=True).copy()
    gtd["drift_instance"] = (
        gtd.groupby("drift_id").cumcount().add(1).astype(str)
    )
    gtd["drift_instance"] = gtd["drift_id"].astype(str) + "#" + gtd["drift_instance"]

    out_rows = []
    det_stream_pairs = (
        detections[["detector", "stream_name"]].drop_duplicates().itertuples(index=False)
        if not detections.empty else []
    )
    # If streams arg given, intersect
    if streams is not None:
        det_stream_pairs = [
            (d, s) for d, s in det_stream_pairs if s in streams
        ]
    det_stream_pairs = list(det_stream_pairs)

    # Also enumerate all stream names that appear in detections, so we
    # don't silently drop ones with zero firings.
    all_streams = (
        sorted(detections["stream_name"].unique()) if not detections.empty
        else (list(streams) if streams else [])
    )
    all_dets = (
        sorted(detections["detector"].unique()) if not detections.empty else []
    )

    # Full grid of (drift × detector × stream) so missed cells appear
    for _, dr in gtd.iterrows():
        d_t0 = float(dr["start_time_s"])
        d_t1 = float(dr["end_time_s"])
        upper = d_t1 + (max_latency_s if max_latency_s is not None else 30.0)
        for det in all_dets:
            for sn in all_streams:
                # Was this stream even eligible for this drift? We
                # accept any (detector × stream) pair; the per-stream
                # eligibility (via affected_kpis) is enforced upstream
                # by `analysis.drift.labels` when computing FPR/TPR.
                df = detections[
                    (detections["detector"] == det)
                    & (detections["stream_name"] == sn)
                    & (detections["time_s"] >= d_t0)
                    & (detections["time_s"] < upper)
                ]
                if df.empty:
                    detection_time = np.nan
                    latency = np.nan
                    missed = 1
                else:
                    detection_time = float(df["time_s"].min())
                    latency = detection_time - d_t0
                    missed = 0
                out_rows.append(
                    {
                        "drift_instance": dr["drift_instance"],
                        "drift_id": dr["drift_id"],
                        "start_time_s": d_t0,
                        "end_time_s": d_t1,
                        "detector": det,
                        "stream_name": sn,
                        "detection_time_s": detection_time,
                        "latency_s": latency,
                        "missed": missed,
                    }
                )
    return pd.DataFrame(out_rows)


# ---------------------------------------------------------------------------
# Per (detector × stream) FPR on baseline phases
# ---------------------------------------------------------------------------

def fpr_per_detector_stream(
    detections: pd.DataFrame,
    streams_long: pd.DataFrame,
) -> pd.DataFrame:
    """False-alarm rate per (detector × stream) on baseline phases.

    A baseline phase is any row in `streams_long` whose `scenario_name`
    equals exactly `"baseline"`. FPR is computed as
    `n_false_alarms / n_baseline_seconds_in_stream`. Note this is a
    *rate per second* (not a probability), since detections are events.

    Args:
        detections   : firing log, columns `detector`, `stream_name`,
                       `time_s`.
        streams_long : per-(stream, time) table from `build_streams`
                       (provides the baseline time-budget per stream).

    Returns:
        DataFrame columns `detector`, `stream_name`, `n_baseline_s`,
        `n_false_alarms`, `fpr_per_s`.
    """
    baseline_mask = streams_long["scenario_name"] == "baseline"
    baseline_streams = streams_long.loc[baseline_mask, ["stream_name", "time_s"]]
    n_per_stream = baseline_streams.groupby("stream_name")["time_s"].count()

    rows = []
    if detections.empty:
        for sn, n in n_per_stream.items():
            rows.append({"detector": "<none>", "stream_name": sn,
                         "n_baseline_s": int(n),
                         "n_false_alarms": 0, "fpr_per_s": 0.0})
        return pd.DataFrame(rows)

    # Build a set of baseline timestamps per stream for O(1) lookup
    baseline_set: dict[str, set] = {
        sn: set(g["time_s"].to_numpy().round(6))
        for sn, g in baseline_streams.groupby("stream_name")
    }

    for (det, sn), g in detections.groupby(["detector", "stream_name"]):
        n_baseline = int(n_per_stream.get(sn, 0))
        n_false = int(
            sum(1 for t in g["time_s"].to_numpy().round(6)
                if t in baseline_set.get(sn, set()))
        )
        rows.append(
            {
                "detector": det,
                "stream_name": sn,
                "n_baseline_s": n_baseline,
                "n_false_alarms": n_false,
                "fpr_per_s": (n_false / n_baseline) if n_baseline > 0 else float("nan"),
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Bootstrap CI on median latency
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BootstrapLatencyResult:
    median: float
    ci_low: float
    ci_high: float
    n_obs: int
    n_resamples: int
    alpha: float

    def as_str(self, decimals: int = 1) -> str:
        if not np.isfinite(self.median):
            return "nan"
        fmt = f"{{:.{decimals}f}}"
        return (f"{fmt.format(self.median)} "
                f"[{fmt.format(self.ci_low)}, {fmt.format(self.ci_high)}]")


def bootstrap_latency_ci(
    latencies_s: np.ndarray,
    n_resamples: int = 1000,
    alpha: float = 0.05,
    random_state: int = 42,
) -> BootstrapLatencyResult:
    """Median latency + percentile CI by bootstrap.

    Args:
        latencies_s : 1-D array of latency-in-seconds; NaNs (missed)
                      are dropped before resampling (so the CI is
                      conditional on "detected at all"). If the
                      resulting array is empty, returns NaN result.
        n_resamples : bootstrap reps.
        alpha       : CI level (0.05 -> 95 % percentile CI).
    """
    arr = np.asarray(latencies_s, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return BootstrapLatencyResult(
            median=float("nan"), ci_low=float("nan"), ci_high=float("nan"),
            n_obs=0, n_resamples=n_resamples, alpha=alpha,
        )
    rng = np.random.default_rng(random_state)
    point = float(np.median(arr))
    boot = np.empty(n_resamples, dtype=float)
    n = arr.size
    for i in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        boot[i] = np.median(arr[idx])
    lo = float(np.quantile(boot, alpha / 2))
    hi = float(np.quantile(boot, 1 - alpha / 2))
    return BootstrapLatencyResult(
        median=point, ci_low=lo, ci_high=hi,
        n_obs=int(n), n_resamples=n_resamples, alpha=alpha,
    )
