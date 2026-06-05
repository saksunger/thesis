"""One-shot EDA report for both real datasets.

Outputs:
- Console: per-dataset row count, missingness, RSRP/SINR five-number summary,
  segment census (NordicDat), HO event census (Bangladesh).
- PNGs in `data/processed/calibration_eda/`:
    * bangladesh_rsrp_hist.png   — serving + neighbor RSRP histograms (dBm)
    * bangladesh_cinr_hist.png   — CINR (SINR proxy) histogram (dB)
    * nordicdat_rsrp_cdfs.png    — RSRP CDFs per (operator, ran, band)
    * nordicdat_sinr_cdfs.png    — SINR CDFs per top-4 segment
    * nordicdat_segment_census.png — bar chart of n_rows per segment
    * time_coverage.png          — per-dataset rough time-axis density

Run:
    python -m analysis.calibration.eda_report
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from analysis.calibration.load_bangladesh import (
    load_bangladesh_events,
    load_bangladesh_samples,
)
from analysis.calibration.load_nordicdat import (
    detect_cell_transitions,
    load_nordicdat,
    summarize_segments,
)
from analysis.common.paths import DATA_PROC


OUT_DIR = DATA_PROC / "calibration_eda"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"EDA outputs → {OUT_DIR}")
    print()

    # ------------------------------------------------------------------
    # Bangladesh
    # ------------------------------------------------------------------
    print("=" * 60)
    print(" Bangladesh (LTE drive-test, Processed Dataset + Event Stats)")
    print("=" * 60)
    bang = load_bangladesh_samples()
    print(f"samples: {len(bang):,}   datasets present: "
          f"{sorted(bang['dataset_id'].unique().tolist())}")
    print()
    print("--- decoded KPI five-number summary (dBm/dB) ---")
    print(_five_number(bang[
        ["rsrp_serving_dbm", "rsrp_neighbor_dbm",
         "rsrq_serving_db", "rsrq_neighbor_db", "cinr_serving_db"]
    ]))
    print()
    print("--- missingness ---")
    print(bang[
        ["rsrp_serving_dbm", "rsrp_neighbor_dbm", "cinr_serving_db",
         "ue_lat_deg", "ue_speed_kmh"]
    ].isna().mean().round(3).to_string())

    bang_events = load_bangladesh_events()
    print()
    print(f"--- Intra LTE-HO events: {len(bang_events):,} ---")
    print(bang_events["result"].value_counts().to_string())
    n_att = int(bang_events["result"].eq("Attempt").sum())
    n_suc = int(bang_events["result"].eq("Success").sum())
    n_fail = int(bang_events["result"].isin(["Fail", "Failure"]).sum())
    if n_att:
        print(f"HOSR = Success / Attempt = {n_suc}/{n_att} = {n_suc/n_att:.4f}")

    _plot_bang_rsrp_hist(bang)
    _plot_bang_cinr_hist(bang)

    # ------------------------------------------------------------------
    # NordicDat
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print(" NordicDat (LTE + 5G-NSA, Nordic operators)")
    print("=" * 60)
    nord = load_nordicdat()
    print(f"samples: {len(nord):,}   time span: "
          f"{(nord['time_s'].max() - nord['time_s'].min()) / 86400:.1f} days   "
          f"unique cells: {nord['serving_cell_id'].nunique()}")
    print()
    print("--- top segments (operator × ran × band) ---")
    seg = summarize_segments(nord)
    print(seg.head(10).to_string(index=False))

    ev = detect_cell_transitions(nord)
    print()
    print(f"--- inferred HO-proxy events: {len(ev):,} "
          f"(per-segment, dt<60s gates) ---")
    print(ev.groupby(["operator_id", "ran_type"]).size().to_string())

    _plot_nord_segment_census(seg)
    _plot_nord_kpi_cdfs(nord, seg)
    _plot_time_coverage(bang, nord)

    print()
    print(f"Done. PNGs in {OUT_DIR}")


# =========================================================================
# Helpers
# =========================================================================
def _five_number(df: pd.DataFrame) -> pd.DataFrame:
    return df.describe(percentiles=[0.05, 0.5, 0.95]).round(2)


def _plot_bang_rsrp_hist(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5), constrained_layout=True)
    bins = np.arange(-140, -40, 2)
    ax.hist(df["rsrp_serving_dbm"].dropna(), bins=bins, alpha=0.6,
            label=f"serving (n={df['rsrp_serving_dbm'].count():,})",
            edgecolor="k", linewidth=0.3)
    ax.hist(df["rsrp_neighbor_dbm"].dropna(), bins=bins, alpha=0.6,
            label=f"best neighbor (n={df['rsrp_neighbor_dbm'].count():,})",
            edgecolor="k", linewidth=0.3)
    ax.set_xlabel("RSRP (dBm)"); ax.set_ylabel("count")
    ax.set_title("Bangladesh — serving vs best-neighbor RSRP (decoded)")
    ax.grid(True, alpha=0.3); ax.legend()
    fig.savefig(OUT_DIR / "bangladesh_rsrp_hist.png", dpi=150)
    plt.close(fig)


def _plot_bang_cinr_hist(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5), constrained_layout=True)
    bins = np.arange(-32, 34, 1)
    ax.hist(df["cinr_serving_db"].dropna(), bins=bins, color="steelblue",
            edgecolor="k", linewidth=0.3)
    ax.set_xlabel("CINR / SINR (dB)"); ax.set_ylabel("count")
    ax.set_title(f"Bangladesh — serving CINR (n={df['cinr_serving_db'].count():,})")
    ax.grid(True, alpha=0.3)
    fig.savefig(OUT_DIR / "bangladesh_cinr_hist.png", dpi=150)
    plt.close(fig)


def _plot_nord_segment_census(seg: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(9, 5.5), constrained_layout=True)
    labels = [f"op{int(r.operator_id) if pd.notna(r.operator_id) else '?'} / "
              f"{r.ran_type} / {r.band}" for r in seg.itertuples()]
    y = np.arange(len(seg))
    ax.barh(y, seg["n_rows"], color="steelblue", edgecolor="k", linewidth=0.3)
    ax.set_yticks(y); ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("row count")
    ax.set_title("NordicDat — sample count per (operator × ran × band) segment")
    for i, v in enumerate(seg["n_rows"]):
        ax.text(v, i, f" {int(v):,}", va="center", fontsize=8)
    ax.grid(True, axis="x", alpha=0.3)
    fig.savefig(OUT_DIR / "nordicdat_segment_census.png", dpi=150)
    plt.close(fig)


def _plot_nord_kpi_cdfs(nord: pd.DataFrame, seg: pd.DataFrame) -> None:
    # Pick the 4 largest segments
    top4 = seg.head(4)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)

    for ax, col, xlabel, title in [
        (axes[0], "rsrp_serving_dbm", "RSRP (dBm)", "RSRP CDF, top-4 segments"),
        (axes[1], "sinr_serving_db",  "SINR (dB)",  "SINR CDF, top-4 segments"),
    ]:
        for _, row in top4.iterrows():
            mask = (
                (nord["operator_id"] == row["operator_id"])
                & (nord["ran_type"] == row["ran_type"])
                & (nord["band"] == row["band"])
            )
            x = np.sort(nord.loc[mask, col].dropna().to_numpy())
            if len(x) == 0:
                continue
            y = np.arange(1, len(x) + 1) / len(x)
            label = (f"op{int(row['operator_id'])}/"
                     f"{row['ran_type']}/{row['band']} (n={len(x):,})")
            ax.plot(x, y, label=label, linewidth=1.5)
        ax.set_xlabel(xlabel); ax.set_ylabel("F(x)")
        ax.set_title(title); ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8, loc="best")

    fig.savefig(OUT_DIR / "nordicdat_kpi_cdfs.png", dpi=150)
    plt.close(fig)


def _plot_time_coverage(bang: pd.DataFrame, nord: pd.DataFrame) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(11, 5.2), constrained_layout=True)

    # Bangladesh: per-dataset time_s scatter
    for did, sub in bang.groupby("dataset_id"):
        axes[0].plot(sub["time_s"], np.full(len(sub), did),
                     "|", markersize=4, alpha=0.7,
                     label=f"Dataset {did} (n={len(sub):,})")
    axes[0].set_yticks(sorted(bang["dataset_id"].unique()))
    axes[0].set_ylabel("dataset id"); axes[0].set_xlabel("time_s (file-local)")
    axes[0].set_title("Bangladesh — per-dataset sample time coverage "
                      "(only per-file relative time, no calendar date)")
    axes[0].grid(True, alpha=0.3); axes[0].legend(fontsize=8, loc="upper right")

    # NordicDat: per-segment timeline
    nord = nord.sort_values("time_s")
    rng = (nord["time_s"].min(), nord["time_s"].max())
    for i, ((op, ran, band), sub) in enumerate(
        nord.groupby(["operator_id", "ran_type", "band"])
    ):
        if len(sub) < 50:
            continue
        axes[1].plot(sub["time_s"], np.full(len(sub), i),
                     "|", markersize=2, alpha=0.5)
        axes[1].text(rng[0], i, f"op{int(op)}/{ran}/{band} ",
                     ha="right", va="center", fontsize=8)
    axes[1].set_xlim(rng)
    axes[1].set_yticks([])
    axes[1].set_xlabel("time_s (unix epoch)")
    axes[1].set_title(f"NordicDat — per-segment sample density over "
                      f"{(rng[1]-rng[0])/86400:.0f} days")
    axes[1].grid(True, axis="x", alpha=0.3)

    fig.savefig(OUT_DIR / "time_coverage.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
