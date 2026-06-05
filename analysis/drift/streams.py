"""Build per-second fleet-level univariate streams for drift detection.

A *stream* is a time-indexed univariate signal that classical drift
detectors (ADWIN, KSWIN, Page-Hinkley) consume one sample at a time.
This module turns the simulator's per-tick parquet output into 6 such
streams (per ADR-15: mobility KPIs only; no throughput / latency):

    rsrp_serving_mean      from samples.parquet  (RSRP serving, fleet mean)
    sinr_serving_mean      from samples.parquet  (SINR serving, fleet mean)
    ho_rate_rolling        from events.parquet   (HO_ATTEMPT/sec, 10 s window)
    hosr_rolling           from events.parquet   (success / (success+fail), 10 s window)
    ping_pong_rate_rolling from events.parquet   (PING_PONG/sec, 10 s window)
    rlf_rate_rolling       from events.parquet   (RLF/sec, 10 s window)

Why fleet-level? Drift in the simulator is injected at the network /
configuration level (e.g. D-2 swaps the channel for *all* cells, D-3
raises *all* UEs' speed). Per-UE streams would be noisy and require
per-UE detectors; the thesis stays at the fleet level for tractability
(plan.md Phase 6).

Why per-second cadence? 1 Hz is the natural resolution of `events.parquet`
(rolling rates) and is dense enough that drift latency can be reported
in seconds (the headline Chapter 6 number).

UE-speed is intentionally NOT in the roster — D-3 is *defined* by a
UE-speed shift, so detecting D-3 from UE-speed would be cheating. The
interesting question is whether downstream KPIs (HO rate, RLF rate)
surface D-3 indirectly.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Whitelist (single source of truth)
# ---------------------------------------------------------------------------

# Mapping: stream_name -> the underlying KPI tag that
# `ground_truth_drift.affected_kpis` references. Used by labels.py to
# decide which drifts label this stream.
STREAM_TO_KPI_TAGS: dict[str, tuple[str, ...]] = {
    "rsrp_serving_mean":      ("rsrp_serving_dbm",),
    "sinr_serving_mean":      ("sinr_serving_db",),
    "ho_rate_rolling":        ("event_rate", "ho_rate"),
    "hosr_rolling":           ("hosr",),
    "ping_pong_rate_rolling": ("ping_pong_rate",),
    "rlf_rate_rolling":       ("rlf_rate",),
}

# Public ordered list (CLI / docs use this order)
STREAM_NAMES: tuple[str, ...] = tuple(STREAM_TO_KPI_TAGS)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StreamConfig:
    """Knobs for the stream builder.

    Attributes:
        cadence_s   : output sample period (default 1.0 s).
        roll_window_s: rolling window for event-derived rates (default 10.0 s).
    """

    cadence_s: float = 1.0
    roll_window_s: float = 10.0

    def __post_init__(self) -> None:
        if self.cadence_s <= 0:
            raise ValueError(f"cadence_s must be > 0 (got {self.cadence_s})")
        if self.roll_window_s < self.cadence_s:
            raise ValueError(
                f"roll_window_s ({self.roll_window_s}) must be >= "
                f"cadence_s ({self.cadence_s})"
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_streams(
    samples: pd.DataFrame,
    events: pd.DataFrame,
    cfg: StreamConfig | None = None,
) -> pd.DataFrame:
    """Return a long-form DataFrame with all 6 streams stacked.

    Output schema (one row per (time_s, stream_name)):
        - time_s : float, in [t_min, t_max] at `cfg.cadence_s` cadence.
        - stream_name : str, one of `STREAM_NAMES`.
        - value : float, may be NaN when no data is available in the
          window (e.g. hosr_rolling before any HO_ATTEMPT). NaNs are
          carried through; downstream detectors handle them.
        - phase_id : int, the phase active at this `time_s` (taken from
          the first sample in the second's bucket).
        - scenario_name : str, same provenance.

    Args:
        samples : DataFrame from `samples.parquet`. Required columns:
                  `time_s`, `phase_id`, `scenario_name`,
                  `rsrp_serving_dbm`, `sinr_serving_db`.
        events  : DataFrame from `events.parquet`. Required columns:
                  `event_time_s`, `event_type`. May be empty.
        cfg     : StreamConfig, defaults to 1 s cadence / 10 s rolling.
    """
    cfg = cfg or StreamConfig()
    _validate(samples, events)

    # 1 Hz time grid (inclusive of t_max)
    t_min = float(np.floor(samples["time_s"].min()))
    t_max = float(np.ceil(samples["time_s"].max()))
    grid = np.arange(t_min, t_max + cfg.cadence_s, cfg.cadence_s)

    # --- per-second phase / scenario_name (from samples)
    phase_map = _per_second_phase_map(samples, grid)

    # --- 1) RSRP serving fleet mean (per-second bucket)
    rsrp_mean = _per_second_mean(samples, "rsrp_serving_dbm", grid, cfg)
    sinr_mean = _per_second_mean(samples, "sinr_serving_db", grid, cfg)

    # --- 3..6) Event-derived rolling rates
    ho_rate = _rolling_event_rate(events, "HO_ATTEMPT", grid, cfg)
    pp_rate = _rolling_event_rate(events, "PING_PONG", grid, cfg)
    rlf_rate = _rolling_event_rate(events, "RLF", grid, cfg)
    hosr = _rolling_hosr(events, grid, cfg)

    streams_dict = {
        "rsrp_serving_mean":      rsrp_mean,
        "sinr_serving_mean":      sinr_mean,
        "ho_rate_rolling":        ho_rate,
        "hosr_rolling":           hosr,
        "ping_pong_rate_rolling": pp_rate,
        "rlf_rate_rolling":       rlf_rate,
    }

    chunks = []
    for name in STREAM_NAMES:
        chunks.append(
            pd.DataFrame(
                {
                    "time_s": grid,
                    "stream_name": name,
                    "value": streams_dict[name],
                    "phase_id": phase_map["phase_id"],
                    "scenario_name": phase_map["scenario_name"],
                }
            )
        )
    return pd.concat(chunks, ignore_index=True)


def pivot_streams(long_df: pd.DataFrame) -> pd.DataFrame:
    """Convenience: pivot long-form streams to wide (time_s x stream)."""
    wide = long_df.pivot_table(
        index="time_s", columns="stream_name", values="value"
    )
    # Re-order columns to canonical order
    wide = wide.reindex(columns=list(STREAM_NAMES))
    return wide


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate(samples: pd.DataFrame, events: pd.DataFrame) -> None:
    req_s = {"time_s", "phase_id", "scenario_name",
             "rsrp_serving_dbm", "sinr_serving_db"}
    missing = req_s - set(samples.columns)
    if missing:
        raise ValueError(
            f"samples missing required columns: {sorted(missing)}"
        )
    if not events.empty:
        req_e = {"event_time_s", "event_type"}
        missing_e = req_e - set(events.columns)
        if missing_e:
            raise ValueError(
                f"events missing required columns: {sorted(missing_e)}"
            )


def _per_second_phase_map(samples: pd.DataFrame, grid: np.ndarray) -> dict:
    """For each second in `grid`, take the phase_id / scenario_name of the
    first sample in that bucket. NaN-safe: empty seconds inherit from the
    previous second (forward-fill)."""
    s = samples.sort_values("time_s")
    # Bin to second buckets
    bins = np.floor(s["time_s"].to_numpy() / max(grid[1] - grid[0], 1e-9)).astype(int)
    idx0 = (grid / max(grid[1] - grid[0], 1e-9)).astype(int)
    df = pd.DataFrame({"bin": bins, "phase_id": s["phase_id"].to_numpy(),
                       "scenario_name": s["scenario_name"].to_numpy()})
    first_per_bin = df.drop_duplicates(subset="bin", keep="first").set_index("bin")
    phase = first_per_bin.reindex(idx0)
    phase["phase_id"] = phase["phase_id"].ffill().bfill().astype(int)
    phase["scenario_name"] = phase["scenario_name"].ffill().bfill()
    return {
        "phase_id": phase["phase_id"].to_numpy(),
        "scenario_name": phase["scenario_name"].to_numpy(),
    }


def _per_second_mean(
    samples: pd.DataFrame,
    col: str,
    grid: np.ndarray,
    cfg: StreamConfig,
) -> np.ndarray:
    """Mean of `col` across all samples whose time_s lands in each
    `[grid[i], grid[i]+cadence_s)` bucket. NaN where the bucket is empty.
    """
    t = samples["time_s"].to_numpy()
    v = samples[col].to_numpy(dtype=float)
    # Bucket index per sample
    bin_idx = np.floor((t - grid[0]) / cfg.cadence_s).astype(int)
    # Aggregate via groupby on the int bucket
    df = pd.DataFrame({"bin": bin_idx, "v": v})
    agg = df.groupby("bin")["v"].mean()
    out = np.full(grid.shape, np.nan, dtype=float)
    valid_bins = agg.index.to_numpy()
    in_range = (valid_bins >= 0) & (valid_bins < len(grid))
    out[valid_bins[in_range]] = agg.to_numpy()[in_range]
    return out


def _rolling_event_rate(
    events: pd.DataFrame,
    event_type: str,
    grid: np.ndarray,
    cfg: StreamConfig,
) -> np.ndarray:
    """Rolling count of `event_type` per second, divided by
    `roll_window_s`. Window is right-aligned: at time t, counts events in
    `[t - roll_window_s, t]`.

    Returns an array of length `len(grid)`; positions before
    `roll_window_s` worth of data exist are still defined (using a
    shorter retro-window) so downstream detectors don't need NaN
    handling at the head.
    """
    if events.empty:
        return np.zeros(grid.shape, dtype=float)
    et = events.loc[events["event_type"] == event_type, "event_time_s"].to_numpy()
    if et.size == 0:
        return np.zeros(grid.shape, dtype=float)
    et_sorted = np.sort(et)
    out = np.zeros(grid.shape, dtype=float)
    # For each grid point: count events in (t - W, t]
    # Use searchsorted on the sorted event times.
    upper = np.searchsorted(et_sorted, grid, side="right")
    lower = np.searchsorted(et_sorted, grid - cfg.roll_window_s, side="right")
    counts = upper - lower
    out = counts.astype(float) / cfg.roll_window_s
    return out


def _rolling_hosr(
    events: pd.DataFrame,
    grid: np.ndarray,
    cfg: StreamConfig,
) -> np.ndarray:
    """Rolling HO success rate over `roll_window_s`: success / (success+fail).

    Returns NaN when no HO_SUCCESS or HO_FAIL events happened in the
    window (denominator zero — undefined).
    """
    if events.empty:
        return np.full(grid.shape, np.nan, dtype=float)
    succ_t = np.sort(events.loc[events["event_type"] == "HO_SUCCESS",
                                "event_time_s"].to_numpy())
    fail_t = np.sort(events.loc[events["event_type"] == "HO_FAIL",
                                "event_time_s"].to_numpy())
    W = cfg.roll_window_s

    def _count(arr: np.ndarray) -> np.ndarray:
        if arr.size == 0:
            return np.zeros(grid.shape, dtype=int)
        upper = np.searchsorted(arr, grid, side="right")
        lower = np.searchsorted(arr, grid - W, side="right")
        return upper - lower

    n_succ = _count(succ_t)
    n_fail = _count(fail_t)
    denom = n_succ + n_fail
    out = np.full(grid.shape, np.nan, dtype=float)
    mask = denom > 0
    out[mask] = n_succ[mask] / denom[mask]
    return out
