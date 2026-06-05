"""Deployment-context aggregator + current-config lookup.

Two pieces of information the demo orchestrator needs at every drift
trigger:

1. :class:`DeploymentContext` — the 9 scenario percentile features
   (RSRP / SINR / RSRQ at p10, p50, p90) computed from the most recent
   ``context_window_s`` seconds of timeline samples. These are the
   features the Phase 8 surrogate was trained on; passing the current
   deployment's percentile snapshot to `inverse_query` is how we
   localise the surrogate's prediction to *here, right now*.

2. :func:`current_config` — the (TTT, hyst, A3 offset) the simulator
   was running with at ``t_now_s``. Recovered from
   `events.parquet` (which carries `cfg_ttt_ms / cfg_hyst_db /
   cfg_a3_off_db` columns per event). Used as the "before"
   counterfactual anchor: the surrogate predicts KPIs *under this
   exact deployment context* for both the current config (what the
   simulator was actually doing) and the recommended config (what the
   surrogate suggests). The difference is the predicted intervention
   uplift.

ADR-15 enforcement
------------------
DeploymentContext exposes mobility-relevant radio features only. No
throughput-derived columns are referenced by anything in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

from analysis.config_perf.data import SCENARIO_COLS


# ---------------------------------------------------------------------------
# Column name conventions in `samples.parquet` (timeline_medium schema)
# ---------------------------------------------------------------------------
# The simulator writes per-tick per-UE measurements with these column
# names. The deployment context aggregator computes percentiles across
# the most recent `context_window_s` seconds, pooled over all UEs.
#
# Mapping from samples-column -> SCENARIO_COLS name is fixed; if the
# simulator schema ever drifts (e.g. column rename), the build_context
# function will raise rather than silently produce NaN-filled context.

_RAW_TO_PERCENTILES: dict[str, tuple[str, str, str]] = {
    # raw col -> (p10 name, p50 name, p90 name) in SCENARIO_COLS
    "rsrp_serving_dbm": ("rsrp_p10_dbm", "rsrp_p50_dbm", "rsrp_p90_dbm"),
    "sinr_serving_db":  ("sinr_p10_db",  "sinr_p50_db",  "sinr_p90_db"),
    "rsrq_serving_db":  ("rsrq_p10_db",  "rsrq_p50_db",  "rsrq_p90_db"),
}

# Sanity check: every SCENARIO_COLS entry must be reachable
_REACHABLE = set()
for triple in _RAW_TO_PERCENTILES.values():
    _REACHABLE.update(triple)
_MISSING = set(SCENARIO_COLS) - _REACHABLE
if _MISSING:  # pragma: no cover - module-load invariant
    raise RuntimeError(
        f"context.py percentile map is incomplete; missing: {_MISSING}"
    )


# ---------------------------------------------------------------------------
# DeploymentContext
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeploymentContext:
    """9 scenario percentile features at a single point in time.

    Attributes mirror :data:`analysis.config_perf.data.SCENARIO_COLS`
    one-to-one. Construct via :func:`build_context`.
    """

    rsrp_p10_dbm: float
    rsrp_p50_dbm: float
    rsrp_p90_dbm: float
    sinr_p10_db: float
    sinr_p50_db: float
    sinr_p90_db: float
    rsrq_p10_db: float
    rsrq_p50_db: float
    rsrq_p90_db: float
    n_samples: int = 0          # rows that fed this percentile snapshot
    t_window_start_s: float = 0.0
    t_window_end_s: float = 0.0

    def to_surrogate_dict(self) -> dict[str, float]:
        """Return the dict shape `inverse_query` expects."""
        return {
            "rsrp_p10_dbm": self.rsrp_p10_dbm,
            "rsrp_p50_dbm": self.rsrp_p50_dbm,
            "rsrp_p90_dbm": self.rsrp_p90_dbm,
            "sinr_p10_db":  self.sinr_p10_db,
            "sinr_p50_db":  self.sinr_p50_db,
            "sinr_p90_db":  self.sinr_p90_db,
            "rsrq_p10_db":  self.rsrq_p10_db,
            "rsrq_p50_db":  self.rsrq_p50_db,
            "rsrq_p90_db":  self.rsrq_p90_db,
        }

    def is_finite(self) -> bool:
        """True iff every percentile is finite (no NaN / inf)."""
        return all(
            np.isfinite(getattr(self, k))
            for triple in _RAW_TO_PERCENTILES.values()
            for k in triple
        )


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_context(
    samples: pd.DataFrame,
    t_now_s: float,
    context_window_s: float = 60.0,
    time_col: str = "time_s",
) -> DeploymentContext:
    """Compute scenario percentiles over `samples` in [t_now - W, t_now].

    Args:
        samples           : per-tick per-UE measurement DataFrame
                            (the `samples.parquet` from `timeline_medium`).
                            Must contain `time_col` plus the 3 raw
                            radio columns listed in `_RAW_TO_PERCENTILES`.
        t_now_s           : end-of-window timestamp (seconds).
        context_window_s  : window length. Default 60 s = one phase length.
                            Must be > 0.
        time_col          : sample timestamp column name. Default
                            ``"time_s"`` to match the timeline_medium
                            schema; ``"t_s"`` is also accepted (validated
                            at runtime).

    Returns:
        :class:`DeploymentContext`. If the window contains zero rows
        (e.g. queried before the first sample), every percentile is
        NaN and `n_samples = 0` — callers should check `is_finite()`
        before forwarding to the surrogate.

    Raises:
        ValueError : on context_window_s <= 0, missing time column, or
                     missing raw radio columns.
    """
    if context_window_s <= 0:
        raise ValueError(f"context_window_s must be > 0 (got {context_window_s})")
    if time_col not in samples.columns:
        raise ValueError(
            f"samples missing time column {time_col!r}; "
            f"have: {list(samples.columns)[:6]}..."
        )
    required = set(_RAW_TO_PERCENTILES)
    missing = required - set(samples.columns)
    if missing:
        raise ValueError(
            f"samples missing required radio columns: {sorted(missing)}"
        )

    lo = float(t_now_s) - float(context_window_s)
    hi = float(t_now_s)
    mask = (samples[time_col] >= lo) & (samples[time_col] <= hi)
    window = samples.loc[mask]
    n = int(len(window))
    if n == 0:
        return DeploymentContext(
            rsrp_p10_dbm=float("nan"), rsrp_p50_dbm=float("nan"), rsrp_p90_dbm=float("nan"),
            sinr_p10_db=float("nan"),  sinr_p50_db=float("nan"),  sinr_p90_db=float("nan"),
            rsrq_p10_db=float("nan"),  rsrq_p50_db=float("nan"),  rsrq_p90_db=float("nan"),
            n_samples=0,
            t_window_start_s=lo, t_window_end_s=hi,
        )

    pct = {}
    for raw_col, (p10_name, p50_name, p90_name) in _RAW_TO_PERCENTILES.items():
        arr = window[raw_col].to_numpy(dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            pct[p10_name] = float("nan")
            pct[p50_name] = float("nan")
            pct[p90_name] = float("nan")
        else:
            pct[p10_name] = float(np.quantile(arr, 0.10))
            pct[p50_name] = float(np.quantile(arr, 0.50))
            pct[p90_name] = float(np.quantile(arr, 0.90))

    return DeploymentContext(
        rsrp_p10_dbm=pct["rsrp_p10_dbm"],
        rsrp_p50_dbm=pct["rsrp_p50_dbm"],
        rsrp_p90_dbm=pct["rsrp_p90_dbm"],
        sinr_p10_db=pct["sinr_p10_db"],
        sinr_p50_db=pct["sinr_p50_db"],
        sinr_p90_db=pct["sinr_p90_db"],
        rsrq_p10_db=pct["rsrq_p10_db"],
        rsrq_p50_db=pct["rsrq_p50_db"],
        rsrq_p90_db=pct["rsrq_p90_db"],
        n_samples=n,
        t_window_start_s=lo,
        t_window_end_s=hi,
    )


# ---------------------------------------------------------------------------
# Current-config lookup
# ---------------------------------------------------------------------------

# Events column conventions in timeline_medium events.parquet
_EVT_TIME_COL = "event_time_s"
_EVT_CFG_COLS = ("cfg_ttt_ms", "cfg_hyst_db", "cfg_a3_off_db")


def current_config(
    events: pd.DataFrame, t_now_s: float,
) -> dict[str, float] | None:
    """Return the (TTT, hyst, A3) the simulator was running at `t_now_s`.

    The lookup uses the most recent HO event with `event_time_s <= t_now_s`
    and reads its `cfg_*` columns. The simulator stamps every event with
    the active phase's configuration, so this is the source of truth.

    Args:
        events  : `events.parquet` from `timeline_medium`. Must contain
                  ``event_time_s`` and the three ``cfg_*`` columns.
        t_now_s : query timestamp in seconds.

    Returns:
        ``{"ttt_ms": float, "hyst_db": float, "a3_off_db": float}`` or
        ``None`` if no event with ``event_time_s <= t_now_s`` exists
        (e.g. queried before the first event fires; should never happen
        on a real timeline past the warm-up phase).

    Raises:
        ValueError : on missing columns.
    """
    missing = (
        {_EVT_TIME_COL} | set(_EVT_CFG_COLS)
    ) - set(events.columns)
    if missing:
        raise ValueError(
            f"events DataFrame missing columns: {sorted(missing)}"
        )
    if events.empty:
        return None
    mask = events[_EVT_TIME_COL] <= float(t_now_s)
    past = events.loc[mask]
    if past.empty:
        return None
    # Index of the latest event by time
    latest_idx = past[_EVT_TIME_COL].idxmax()
    row = past.loc[latest_idx]
    return {
        "ttt_ms":    float(row["cfg_ttt_ms"]),
        "hyst_db":   float(row["cfg_hyst_db"]),
        "a3_off_db": float(row["cfg_a3_off_db"]),
    }


def per_phase_config_table(
    events: pd.DataFrame,
) -> pd.DataFrame:
    """Return one row per phase_id with its (TTT, hyst, A3) and
    (t_min_s, t_max_s) extents.

    Useful for the moneyshot figure's bottom panel ("current config
    timeline") and for assertions in tests that the timeline carries a
    SINGLE config per phase (Iter A invariant; Iter B will relax this).
    """
    needed = {_EVT_TIME_COL, "phase_id"} | set(_EVT_CFG_COLS)
    missing = needed - set(events.columns)
    if missing:
        raise ValueError(
            f"events DataFrame missing columns for per_phase_config_table: "
            f"{sorted(missing)}"
        )
    if events.empty:
        return pd.DataFrame(
            columns=["phase_id", "ttt_ms", "hyst_db", "a3_off_db",
                     "t_min_s", "t_max_s", "n_events"]
        )
    g = events.groupby("phase_id", sort=True)
    out = pd.DataFrame({
        "phase_id":  g.size().index.astype(int),
        "n_events":  g.size().values,
        "t_min_s":   g[_EVT_TIME_COL].min().values,
        "t_max_s":   g[_EVT_TIME_COL].max().values,
        "ttt_ms":    g["cfg_ttt_ms"].first().values,
        "hyst_db":   g["cfg_hyst_db"].first().values,
        "a3_off_db": g["cfg_a3_off_db"].first().values,
    }).reset_index(drop=True)
    return out
