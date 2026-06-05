"""Window-feature aggregator for Phase 5 anomaly benchmark.

Reads `samples.parquet` and `events.parquet` produced by `make sim`
(Phase 4 timeline builder) and emits a per-(UE, window) feature
DataFrame consumable by any sklearn-compatible detector.

ADR-15 enforcement: only mobility KPIs are aggregated. The feature
whitelist is the single source of truth for what enters the detectors.

Window convention
-----------------
- Width:  `window_s` seconds (default 5.0)
- Slide:  `slide_s` seconds (default 1.0)
- Per UE: a window's [t_start, t_end) interval is over the UE's local
  time axis (already timeline-global thanks to build_timeline's
  per-phase offset).
- Windows fully outside a phase boundary are dropped — we never mix
  samples from two phases inside one window because phase boundaries
  reset the simulator's UE population (Iter B note).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# --- Feature whitelist (ADR-15) -----------------------------------------
# Sample-level columns we aggregate per (UE, window):
SAMPLE_NUMERIC_COLS: tuple[str, ...] = (
    "rsrp_serving_dbm",
    "rsrq_serving_db",
    "sinr_serving_db",
    "rsrp_neighbor_dbm",
    "rsrq_neighbor_db",
    "ue_speed_mps",
)
# Aggregation suffixes for each numeric column:
_QUANTILE_SUFFIXES: tuple[tuple[str, float], ...] = (
    ("p10", 0.10),
    ("p50", 0.50),
    ("p90", 0.90),
)

# Event types we count per window:
EVENT_TYPES: tuple[str, ...] = (
    "HO_ATTEMPT",
    "HO_SUCCESS",
    "HO_FAIL",
    "RLF",
    "PING_PONG",
)


@dataclass(frozen=True)
class WindowConfig:
    """Window aggregation knobs (kept as a dataclass so tests can override)."""

    window_s: float = 5.0
    slide_s: float = 1.0

    def __post_init__(self) -> None:
        if self.window_s <= 0:
            raise ValueError(f"window_s must be > 0 (got {self.window_s})")
        if self.slide_s <= 0:
            raise ValueError(f"slide_s must be > 0 (got {self.slide_s})")
        if self.slide_s > self.window_s:
            raise ValueError(
                f"slide_s ({self.slide_s}) must not exceed window_s "
                f"({self.window_s}); otherwise samples between windows "
                f"would be dropped."
            )


def feature_columns() -> list[str]:
    """Return the ordered list of feature column names.

    Useful for downstream code that needs to enumerate the feature
    matrix dimensions without re-running aggregation.
    """
    cols: list[str] = []
    for c in SAMPLE_NUMERIC_COLS:
        for suffix, _ in _QUANTILE_SUFFIXES:
            cols.append(f"{c}__{suffix}")
        cols.append(f"{c}__std")
    cols.extend(
        [
            "serving_cell_mode",
            *(f"n_{ev.lower()}" for ev in EVENT_TYPES),
            "hosr",
            "hofr_rate",
            "rlf_rate",
            "ping_pong_rate",
        ]
    )
    return cols


def feature_groups() -> dict[str, list[str]]:
    """Group feature columns by signal family for per-family ablation.

    Used by Phase 5 Iter B ablation studies ("drop one family at a
    time → which family carries the signal for each anomaly type?").
    The groups are disjoint and exhaustive: union over all groups ==
    :func:`feature_columns`.

    Groups:
        rsrp     : serving + neighbour RSRP percentiles / std
        rsrq     : serving + neighbour RSRQ percentiles / std
        sinr     : serving SINR percentiles / std
        speed    : UE-speed percentiles / std
        cell     : serving_cell_mode (categorical-ish, single column)
        events   : raw event counts per window (n_ho_attempt, n_rlf, ...)
        derived  : derived rates (HOSR, HOFR_rate, RLF_rate, ping_pong_rate)
    """
    groups: dict[str, list[str]] = {
        "rsrp":    [],
        "rsrq":    [],
        "sinr":    [],
        "speed":   [],
        "cell":    ["serving_cell_mode"],
        "events":  [f"n_{ev.lower()}" for ev in EVENT_TYPES],
        "derived": ["hosr", "hofr_rate", "rlf_rate", "ping_pong_rate"],
    }
    for c in SAMPLE_NUMERIC_COLS:
        if c.startswith("rsrp"):
            family = "rsrp"
        elif c.startswith("rsrq"):
            family = "rsrq"
        elif c.startswith("sinr"):
            family = "sinr"
        elif c.startswith("ue_speed"):
            family = "speed"
        else:
            raise AssertionError(f"unknown SAMPLE_NUMERIC_COL family: {c}")
        for suffix, _ in _QUANTILE_SUFFIXES:
            groups[family].append(f"{c}__{suffix}")
        groups[family].append(f"{c}__std")

    # Internal consistency: union must equal feature_columns().
    all_grouped = [c for cols in groups.values() for c in cols]
    expected = feature_columns()
    if set(all_grouped) != set(expected) or len(all_grouped) != len(expected):
        raise AssertionError(
            f"feature_groups() inconsistency: "
            f"missing={set(expected) - set(all_grouped)}, "
            f"extra={set(all_grouped) - set(expected)}"
        )
    return groups


def aggregate_windows(
    samples: pd.DataFrame,
    events: pd.DataFrame,
    cfg: WindowConfig | None = None,
) -> pd.DataFrame:
    """Aggregate per-tick samples + per-event log into per-(UE, window) rows.

    Args:
        samples : DataFrame from `samples.parquet` (must contain at least
            `ue_id`, `time_s`, `phase_id`, `serving_cell_id`, and every
            column listed in :data:`SAMPLE_NUMERIC_COLS`).
        events  : DataFrame from `events.parquet` (must contain
            `ue_id`, `event_time_s`, `event_type`, `phase_id`). May be
            empty.
        cfg     : `WindowConfig` instance (defaults to 5 s width / 1 s slide).

    Returns:
        DataFrame with one row per (ue_id, window_id) and the following
        index-like columns plus all columns from :func:`feature_columns`:
        - `ue_id`, `phase_id`, `scenario_name`
        - `window_id` (sequential per UE within its phase)
        - `t_start_s`, `t_end_s`, `t_mid_s`
    """
    cfg = cfg or WindowConfig()
    _validate_inputs(samples, events)

    out_chunks: list[pd.DataFrame] = []

    # Per (UE, phase) so windows never cross a phase boundary
    for (ue_id, phase_id), s_chunk in samples.groupby(["ue_id", "phase_id"], sort=True):
        s_chunk = s_chunk.sort_values("time_s").reset_index(drop=True)
        t_min = float(s_chunk["time_s"].iloc[0])
        t_max = float(s_chunk["time_s"].iloc[-1])

        # Window start times: [t_min, t_min+slide, t_min+2*slide, ...]
        # such that t_start + window_s <= t_max + small_epsilon
        eps = 1e-9
        starts = np.arange(t_min, t_max - cfg.window_s + eps, cfg.slide_s)
        if starts.size == 0:
            continue

        e_chunk = events[
            (events["ue_id"] == ue_id) & (events["phase_id"] == phase_id)
        ]

        scenario_name = s_chunk["scenario_name"].iloc[0]
        time_arr = s_chunk["time_s"].to_numpy()

        rows: list[dict] = []
        for w_idx, t0 in enumerate(starts):
            t1 = t0 + cfg.window_s
            # samples in [t0, t1)
            mask = (time_arr >= t0) & (time_arr < t1)
            if not mask.any():
                continue
            s_win = s_chunk.iloc[mask.nonzero()[0]]
            row = _aggregate_sample_window(s_win)
            row.update(_aggregate_event_window(e_chunk, t0, t1, cfg.window_s))
            row.update(
                {
                    "ue_id": int(ue_id),
                    "phase_id": int(phase_id),
                    "scenario_name": scenario_name,
                    "window_id": int(w_idx),
                    "t_start_s": float(t0),
                    "t_end_s": float(t1),
                    "t_mid_s": float(t0 + cfg.window_s / 2),
                }
            )
            rows.append(row)

        if rows:
            out_chunks.append(pd.DataFrame(rows))

    if not out_chunks:
        return _empty_features_df()

    out = pd.concat(out_chunks, ignore_index=True)
    # Stable column order: index-like first, features in `feature_columns()`
    index_cols = [
        "ue_id",
        "phase_id",
        "scenario_name",
        "window_id",
        "t_start_s",
        "t_end_s",
        "t_mid_s",
    ]
    return out[index_cols + feature_columns()]


# -------------------------------------------------------------------------
# Internal helpers
# -------------------------------------------------------------------------

def _validate_inputs(samples: pd.DataFrame, events: pd.DataFrame) -> None:
    required_sample_cols = {
        "ue_id",
        "time_s",
        "phase_id",
        "serving_cell_id",
        "scenario_name",
        *SAMPLE_NUMERIC_COLS,
    }
    missing = required_sample_cols - set(samples.columns)
    if missing:
        raise ValueError(
            f"samples DataFrame missing required columns: {sorted(missing)}"
        )
    if not events.empty:
        required_event_cols = {"ue_id", "event_time_s", "event_type", "phase_id"}
        missing_e = required_event_cols - set(events.columns)
        if missing_e:
            raise ValueError(
                f"events DataFrame missing required columns: {sorted(missing_e)}"
            )


def _aggregate_sample_window(s_win: pd.DataFrame) -> dict:
    out: dict = {}
    for c in SAMPLE_NUMERIC_COLS:
        vals = s_win[c].to_numpy(dtype=float)
        # nan-aware quantiles; fall back to nan if all-nan
        for suffix, q in _QUANTILE_SUFFIXES:
            out[f"{c}__{suffix}"] = float(np.nanquantile(vals, q)) if vals.size else np.nan
        out[f"{c}__std"] = float(np.nanstd(vals, ddof=0)) if vals.size else np.nan
    # serving cell mode = most-frequent serving_cell_id in the window
    serving = s_win["serving_cell_id"].to_numpy()
    if serving.size:
        values, counts = np.unique(serving, return_counts=True)
        out["serving_cell_mode"] = int(values[np.argmax(counts)])
    else:
        out["serving_cell_mode"] = -1
    return out


def _aggregate_event_window(
    e_chunk: pd.DataFrame,
    t0: float,
    t1: float,
    window_s: float,
) -> dict:
    if e_chunk.empty:
        counts = {ev: 0 for ev in EVENT_TYPES}
    else:
        et = e_chunk["event_time_s"].to_numpy()
        mask = (et >= t0) & (et < t1)
        in_win = e_chunk.iloc[mask.nonzero()[0]]
        vc = in_win["event_type"].value_counts()
        counts = {ev: int(vc.get(ev, 0)) for ev in EVENT_TYPES}

    n_attempt = counts["HO_ATTEMPT"]
    n_success = counts["HO_SUCCESS"]
    n_fail = counts["HO_FAIL"]
    # HOSR = success / (success + fail). NaN when no attempts (no signal).
    denom = n_success + n_fail
    hosr = (n_success / denom) if denom > 0 else np.nan

    return {
        **{f"n_{ev.lower()}": counts[ev] for ev in EVENT_TYPES},
        "hosr": hosr,
        "hofr_rate": n_fail / window_s,
        "rlf_rate": counts["RLF"] / window_s,
        "ping_pong_rate": counts["PING_PONG"] / window_s,
    }


def _empty_features_df() -> pd.DataFrame:
    cols = [
        "ue_id",
        "phase_id",
        "scenario_name",
        "window_id",
        "t_start_s",
        "t_end_s",
        "t_mid_s",
        *feature_columns(),
    ]
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in cols})
