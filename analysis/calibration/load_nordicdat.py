"""Loader for the NordicDat LTE + 5G-NSA drive-test dataset.

Single entry point: `load_nordicdat()` returns a per-sample DataFrame with
canonical schema (see `docs/schema.md` §1). Values are already in real
units (dBm / dB) — no decoding needed.

The most useful query is per-segment, where a segment is one
`(operator, ran, band)` tuple — RSRP / RSRQ / SINR distributions are only
meaningfully comparable inside a segment. Use `summarize_segments()` for
a quick segment census.
"""

from __future__ import annotations

import pandas as pd

from analysis.common.paths import NORDICDAT_CSV, assert_real_data_present


# Columns we keep and rename to canonical names. Columns omitted from this
# mapping are dropped from the returned frame to keep downstream code lean.
_RENAME = {
    "timestamp": "time_s",                # unix epoch seconds (float)
    "latitude": "ue_lat_deg",
    "longitude": "ue_lon_deg",
    "elevation": "ue_elev_m",
    "velocity_abs": "ue_speed_mps",
    "acceleration_abs": "ue_accel_mps2",
    "rsrq": "rsrq_serving_db",
    "rssi": "rssi_serving_dbm",
    "rsrp": "rsrp_serving_dbm",
    "sinr": "sinr_serving_db",
    "band": "band",
    "ran": "ran_type",
    "serving_cell_id": "serving_cell_id",
    "operator": "operator_id",
    "throughput_downlink": "throughput_dl_kbps",
    "throughput_uplink": "throughput_ul_kbps",
    "heading": "ue_heading_deg",
    "gnss_mode": "gnss_mode",
    "service_status": "service_status",
}


def load_nordicdat(
    drop_no_signal: bool = True,
) -> pd.DataFrame:
    """Load the entire NordicDat CSV as a canonical-schema DataFrame.

    Args:
        drop_no_signal: If True (default), drop rows where RSRP is NaN or
            below -140 dBm (no-coverage stuck-at-floor samples typical of
            tunnels / dead zones). Real-world calibration distributions
            should not be biased by these.

    Returns:
        DataFrame, ~91 455 rows × 19 columns. ``time_s`` is unix epoch
        seconds (UTC), ``ran_type`` ∈ {LTE, 5G-NSA}, ``band`` is the raw
        string label (e.g. ``LTE_B3``, ``NR_n78``), ``operator_id`` is
        an integer 1..4.
    """
    assert_real_data_present()
    df = pd.read_csv(NORDICDAT_CSV)

    missing = set(_RENAME) - set(df.columns)
    if missing:
        raise ValueError(
            f"NordicDat CSV missing expected columns: {sorted(missing)}"
        )

    df = df.rename(columns=_RENAME)[list(_RENAME.values())]

    # Normalize categoricals
    df["ran_type"] = df["ran_type"].astype("string")
    df["band"] = df["band"].astype("string")
    df["operator_id"] = pd.to_numeric(df["operator_id"], errors="coerce").astype("Int64")
    df["serving_cell_id"] = pd.to_numeric(df["serving_cell_id"], errors="coerce").astype("Int64")

    if drop_no_signal:
        mask = df["rsrp_serving_dbm"].notna() & (df["rsrp_serving_dbm"] > -140.0)
        df = df.loc[mask].reset_index(drop=True)

    return df


def summarize_segments(df: pd.DataFrame) -> pd.DataFrame:
    """Per (operator, ran, band) segment row count + KPI summary.

    Returned columns: ``operator_id, ran_type, band, n_rows, t_span_h,
    rsrp_p10, rsrp_p50, rsrp_p90, sinr_p10, sinr_p50, sinr_p90,
    n_serving_cells``.
    """
    def _agg(g: pd.DataFrame) -> pd.Series:
        return pd.Series(
            {
                "n_rows": len(g),
                "t_span_h": float(g["time_s"].max() - g["time_s"].min()) / 3600.0,
                "rsrp_p10": g["rsrp_serving_dbm"].quantile(0.10),
                "rsrp_p50": g["rsrp_serving_dbm"].quantile(0.50),
                "rsrp_p90": g["rsrp_serving_dbm"].quantile(0.90),
                "sinr_p10": g["sinr_serving_db"].quantile(0.10),
                "sinr_p50": g["sinr_serving_db"].quantile(0.50),
                "sinr_p90": g["sinr_serving_db"].quantile(0.90),
                "n_serving_cells": g["serving_cell_id"].nunique(),
            }
        )

    return (
        df.groupby(["operator_id", "ran_type", "band"], dropna=False)
          .apply(_agg, include_groups=False)
          .reset_index()
          .sort_values("n_rows", ascending=False)
          .reset_index(drop=True)
    )


def detect_cell_transitions(df: pd.DataFrame) -> pd.DataFrame:
    """Return one row per serving-cell transition (HO-proxy event log).

    NordicDat has no explicit HO log; we infer transitions from the
    `serving_cell_id` column. A transition is any (i, i-1) pair within the
    same (operator, ran) segment where the cell id changes and the time
    gap is < 60 s (>60 s = drive-test pause, not a real handover).

    Columns: ``time_s, dt_s, operator_id, ran_type, band, source_cell_id,
    target_cell_id``.
    """
    df = df.sort_values(["operator_id", "ran_type", "time_s"]).reset_index(drop=True)

    prev = df.shift(1)
    same_seg = (df["operator_id"] == prev["operator_id"]) & (df["ran_type"] == prev["ran_type"])
    cell_changed = df["serving_cell_id"] != prev["serving_cell_id"]
    dt = df["time_s"] - prev["time_s"]
    short_gap = dt < 60.0

    mask = same_seg & cell_changed & short_gap & prev["serving_cell_id"].notna()
    ev = df.loc[mask, ["time_s", "operator_id", "ran_type", "band", "serving_cell_id"]].copy()
    ev["dt_s"] = dt.loc[mask].values
    ev["source_cell_id"] = prev.loc[mask, "serving_cell_id"].values
    ev = ev.rename(columns={"serving_cell_id": "target_cell_id"})
    return ev[
        ["time_s", "dt_s", "operator_id", "ran_type", "band",
         "source_cell_id", "target_cell_id"]
    ].reset_index(drop=True)


if __name__ == "__main__":
    pd.options.display.width = 160
    pd.options.display.max_columns = 30
    pd.options.display.max_rows = 30

    print("=== NordicDat per-sample feed ===")
    df = load_nordicdat()
    print(f"rows: {len(df):,}  time span: "
          f"{(df['time_s'].max() - df['time_s'].min()) / 86400:.1f} days  "
          f"unique cells: {df['serving_cell_id'].nunique()}")
    print()
    print("--- per-segment summary ---")
    print(summarize_segments(df).head(20))
    print()
    print("--- inferred HO-proxy events (cell transitions) ---")
    ev = detect_cell_transitions(df)
    print(f"transitions: {len(ev):,}")
    print(ev.head(10))
