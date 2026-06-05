"""Loader, schema, and cross-validation splitter for the surrogate.

The training matrix is `data/simulated/sweep_static/sweep_config_perf.parquet`
produced by `make sweep-static` (Phase 4 Iter C). It has 360 rows from a
3 × 4 × 3 × 10 (TTT × hyst × A3 × seed) factorial sweep.

Feature / target schema (single source of truth)
------------------------------------------------
Controlled knobs (the inverse-query optimises over these):
    ttt_ms, hyst_db, a3_off_db

Deployment-context features (held fixed per inverse-query call; the
surrogate sees the same channel statistics at training and query time):
    rsrp_p10_dbm, rsrp_p50_dbm, rsrp_p90_dbm
    sinr_p10_db,  sinr_p50_db,  sinr_p90_db
    rsrq_p10_db,  rsrq_p50_db,  rsrq_p90_db

Targets (mobility KPIs, ADR-15):
    hosr                  - HO success ratio in [0, 1]
    rlf_rate              - RLFs per second (small positive)
    ping_pong_rate        - PPs per second (small positive)

HOFR_rate intentionally omitted: equals 1 - HOSR exactly by
construction (rounding-error-only deviation) — see :func:`assert_hofr_redundant`.

CV splitting
------------
Plain k-fold leaks: each (TTT, hyst, A3) is replicated across 10 seeds,
so naive shuffled folds put the same config in both train AND test.
:func:`grouped_kfold_splits` groups by unique config (36 groups) and
splits at the group level, yielding honest out-of-config-set MAE.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

from analysis.common.paths import DATA_SIM

# --- Schema (single source of truth) -------------------------------------

CONTROLLED_COLS: tuple[str, ...] = ("ttt_ms", "hyst_db", "a3_off_db")

SCENARIO_COLS: tuple[str, ...] = (
    "rsrp_p10_dbm", "rsrp_p50_dbm", "rsrp_p90_dbm",
    "sinr_p10_db",  "sinr_p50_db",  "sinr_p90_db",
    "rsrq_p10_db",  "rsrq_p50_db",  "rsrq_p90_db",
)

FEATURE_COLS: tuple[str, ...] = CONTROLLED_COLS + SCENARIO_COLS

TARGET_COLS: tuple[str, ...] = ("hosr", "rlf_rate", "ping_pong_rate")

# Per-target physical bounds for sanity-checking predictions
TARGET_BOUNDS: dict[str, tuple[float, float]] = {
    "hosr":           (0.0, 1.0),
    "rlf_rate":       (0.0, 1.0),       # /s, very generous upper bound
    "ping_pong_rate": (0.0, 1.0),       # /s, very generous upper bound
}


# --- Paths ---------------------------------------------------------------

SWEEP_STATIC_PARQUET: Path = DATA_SIM / "sweep_static" / "sweep_config_perf.parquet"


# --- Loader --------------------------------------------------------------

@dataclass(frozen=True)
class SweepTable:
    """Container for the loaded sweep DataFrame + cached feature/target views."""

    df: pd.DataFrame

    @property
    def X(self) -> pd.DataFrame:
        return self.df[list(FEATURE_COLS)].copy()

    @property
    def Y(self) -> pd.DataFrame:
        return self.df[list(TARGET_COLS)].copy()

    @property
    def groups(self) -> np.ndarray:
        """Group id per row = the unique (TTT, hyst, A3) tuple as a string."""
        return (
            self.df[list(CONTROLLED_COLS)]
            .astype(str).agg("_".join, axis=1)
            .astype("category").cat.codes
            .to_numpy()
        )

    @property
    def n_configs(self) -> int:
        return int(self.df[list(CONTROLLED_COLS)].drop_duplicates().shape[0])


def load_sweep(path: Path | None = None) -> SweepTable:
    """Load `sweep_config_perf.parquet`. Validates schema + HOFR redundancy.

    Args:
        path : optional override; defaults to :data:`SWEEP_STATIC_PARQUET`.

    Raises:
        FileNotFoundError : if the parquet is missing (run `make sweep-static`).
        ValueError        : if required columns are missing.
    """
    p = Path(path) if path is not None else SWEEP_STATIC_PARQUET
    if not p.is_file():
        raise FileNotFoundError(
            f"{p} not found. Generate via: make sweep-static"
        )
    df = pd.read_parquet(p)
    _validate_schema(df)
    assert_hofr_redundant(df)
    return SweepTable(df=df.reset_index(drop=True))


def _validate_schema(df: pd.DataFrame) -> None:
    required = set(FEATURE_COLS) | set(TARGET_COLS) | {"seed"}
    # `hofr_rate` is required for the redundancy assertion only.
    required.add("hofr_rate")
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"sweep_config_perf.parquet missing required columns: "
            f"{sorted(missing)}"
        )


def assert_hofr_redundant(df: pd.DataFrame, atol: float = 1e-6) -> None:
    """Assert that `hofr_rate + hosr == 1` for every row (rounding tol).

    Raises:
        ValueError if any row violates the equality. This is a schema
        invariant of the simulator's sweep runner — a violation means
        the parquet was produced by a different code path and the
        surrogate's "drop HOFR" assumption is unsafe.
    """
    diff = np.abs((df["hosr"] + df["hofr_rate"]) - 1.0).to_numpy()
    if (diff > atol).any():
        worst_idx = int(diff.argmax())
        raise ValueError(
            "HOFR_rate is not exactly 1 - HOSR for every row "
            f"(max abs deviation = {diff[worst_idx]:.6g} at row {worst_idx}). "
            f"The surrogate drops HOFR_rate as redundant; this assumption "
            f"is violated, so the parquet may be from a different sweep runner."
        )


# --- Cross-validation splits --------------------------------------------

def grouped_kfold_splits(
    table: SweepTable, n_splits: int = 5,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Yield grouped k-fold (train_idx, test_idx) pairs.

    Groups by unique (TTT, hyst, A3) tuple, so seed replicates of the
    SAME config land in the same fold. Defends against leakage that
    plain `KFold(shuffle=True)` would introduce (replicates ~ same y
    given X, optimistically inflating R^2).

    Args:
        table     : :class:`SweepTable` from :func:`load_sweep`.
        n_splits  : fold count. Default 5 -> ~7 configs / 70 rows per
                    test fold, ~29 configs / 290 rows per train fold.

    Returns:
        list of (train_idx, test_idx) tuples, length `n_splits`.

    Raises:
        ValueError : if `n_splits` exceeds the number of unique configs
                     (sklearn's GroupKFold requirement).
    """
    if n_splits > table.n_configs:
        raise ValueError(
            f"n_splits={n_splits} exceeds n_configs={table.n_configs}; "
            f"GroupKFold cannot create more folds than there are groups."
        )
    gkf = GroupKFold(n_splits=n_splits)
    return [(tr, te) for tr, te in gkf.split(table.X, table.Y, groups=table.groups)]


# --- Inverse-query candidate grid ---------------------------------------

def candidate_config_grid(table: SweepTable | None = None) -> pd.DataFrame:
    """Return all unique (TTT, hyst, A3) combinations seen in the sweep.

    Used by :func:`analysis.config_perf.inverse.inverse_query` as the
    brute-force candidate set. For the Iter A 3 × 4 × 3 grid this is
    36 rows; the surrogate's prediction call is < 10 ms total.

    If ``table`` is None, returns the canonical Iter C grid (encoded
    explicitly so the inverse-query call site can run without loading
    the parquet — useful for unit tests).
    """
    if table is not None:
        return table.df[list(CONTROLLED_COLS)].drop_duplicates().sort_values(
            list(CONTROLLED_COLS)
        ).reset_index(drop=True)
    # Canonical Iter C grid (from sweep_static metadata)
    ttt = [256.0, 480.0, 1024.0]
    hyst = [0.0, 1.0, 3.0, 6.0]
    a3 = [0.0, 3.0, 6.0]
    rows = [
        {"ttt_ms": t, "hyst_db": h, "a3_off_db": a}
        for t in ttt for h in hyst for a in a3
    ]
    return pd.DataFrame(rows)
