"""Run the KS-test pipeline over every config subdir under a sweep root,
and print a single comparison table sorted by KS-statistic.

Usage:
    python -m analysis.calibration.ks_sweep_compare \\
        --root data/simulated/calibration_sweep \\
        --kpi rsrp_serving_dbm \\
        --segment 1/5G-NSA/LTE_B20
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from analysis.calibration.ks_test import (
    KPI_PAIRS,
    TARGET_SEGMENTS,
    compute_all,
    load_sim_kpis,
)
from analysis.calibration.load_nordicdat import load_nordicdat
from analysis.common.paths import DATA_PROC


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True,
                    help="sweep root dir; each subdir = one config")
    ap.add_argument("--kpi", default="rsrp_serving_dbm",
                    help="primary KPI for the sort + heatmap")
    ap.add_argument("--segment", default="1/5G-NSA/LTE_B20",
                    help="target segment for the sort + heatmap "
                         "(format: op/ran/band)")
    ap.add_argument("--out", type=Path,
                    default=DATA_PROC / "calibration_ks",
                    help="where to write CSV + heatmap")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    op_str, ran, band = args.segment.split("/")
    op = int(op_str)

    real = load_nordicdat()
    print(f"NordicDat loaded: {len(real):,} rows")

    configs = sorted(p for p in args.root.iterdir() if p.is_dir())
    if not configs:
        print(f"No config subdirs under {args.root}", file=sys.stderr)
        return 1
    print(f"Configs to compare: {len(configs)}")

    rows: list[dict] = []
    for sub in configs:
        try:
            sim = load_sim_kpis(sub)
        except FileNotFoundError:
            print(f"  {sub.name}: no parquet, skipping")
            continue
        res = compute_all(sim, real, n_boot=50)   # cheaper bootstrap on sweep
        for r in res:
            d = r.as_row()
            d["config"] = sub.name
            rows.append(d)

    df = pd.DataFrame(rows)
    csv_fp = args.out / "ks_sweep_summary.csv"
    df.to_csv(csv_fp, index=False)
    print(f"\nFull sweep summary → {csv_fp}")

    # Focused comparison: chosen segment × KPI, sorted by KS-stat
    sel = df[
        (df["operator_id"] == op)
        & (df["ran_type"] == ran)
        & (df["band"] == band)
        & (df["kpi"] == args.kpi)
    ].sort_values("ks_stat").reset_index(drop=True)

    print()
    print(f"=== {args.kpi} vs op{op}/{ran}/{band} (sorted by KS-stat) ===")
    print(sel[
        ["config", "ks_stat", "ks_ci_lo", "ks_ci_hi", "delta_p50",
         "sim_p10", "sim_p50", "sim_p90", "real_p50"]
    ].to_string(index=False))

    if not sel.empty:
        best = sel.iloc[0]
        print(f"\nBest config for {args.kpi} on op{op}/{ran}/{band}: "
              f"{best['config']}  (KS={best['ks_stat']:.3f}, "
              f"Δp50={best['delta_p50']:+.2f} dB)")
        _plot_sweep_heatmap(sel, args.kpi, op, ran, band, args.out)

    return 0


def _plot_sweep_heatmap(sel: pd.DataFrame, kpi: str,
                        op: int, ran: str, band: str,
                        out_dir: Path) -> None:
    """Plot KS-stat as ISD (rows) × area (cols) heatmap when the sweep
    has that structure, else fall back to a bar chart."""
    # Try to parse config = "isd<I>_area<A>"
    import re
    parsed = []
    for _, row in sel.iterrows():
        m = re.match(r"isd(\d+)_area(\d+)", row["config"])
        if m:
            parsed.append((int(m.group(1)), int(m.group(2)),
                           row["ks_stat"], row["delta_p50"]))
    if not parsed:
        return  # not the expected structure; skip

    isds  = sorted({p[0] for p in parsed})
    areas = sorted({p[1] for p in parsed})
    M  = np.full((len(isds), len(areas)), np.nan)
    Dp = np.full((len(isds), len(areas)), np.nan)
    for isd, area, ks, dp in parsed:
        i = isds.index(isd); j = areas.index(area)
        M[i, j]  = ks
        Dp[i, j] = dp

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), constrained_layout=True)
    for ax, mat, ttl, fmt in [
        (axes[0], M,  f"KS-stat ({kpi})",   "%.3f"),
        (axes[1], Dp, f"sim_p50 − real_p50 ({kpi}) [dB]", "%+.1f"),
    ]:
        im = ax.imshow(mat, cmap="viridis" if mat is M else "coolwarm",
                       aspect="auto")
        ax.set_xticks(range(len(areas)))
        ax.set_xticklabels([f"{a} m" for a in areas])
        ax.set_yticks(range(len(isds)))
        ax.set_yticklabels([f"{i} m" for i in isds])
        ax.set_xlabel("trajectory area (half-width)")
        ax.set_ylabel("ISD")
        ax.set_title(ttl)
        for i in range(len(isds)):
            for j in range(len(areas)):
                if not np.isnan(mat[i, j]):
                    ax.text(j, i, fmt % mat[i, j],
                            ha="center", va="center",
                            color="white" if (mat is M and mat[i, j] > 0.3)
                                          else "black",
                            fontsize=9, fontweight="bold")
        plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    fig.suptitle(f"Calibration sweep — target op{op}/{ran}/{band}", fontsize=12)
    fp = out_dir / f"sweep_heatmap_{kpi}.png"
    fig.savefig(fp, dpi=140)
    plt.close(fig)
    print(f"  heatmap → {fp}")


if __name__ == "__main__":
    sys.exit(main())
