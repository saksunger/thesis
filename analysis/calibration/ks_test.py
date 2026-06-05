"""KS-test pipeline: simulator vs real-data KPI distributions.

**Scope** (set in ADR-14, docs/design.md): the simulator is a 5G NR SA,
intra-RAT, inter-gNB Xn-handover model. Real-world public datasets
with matching scope (5G SA HO event logs) do not exist; NordicDat's
5G-NSA segment is the closest available radio-layer reference.
We therefore calibrate **radio-layer marginal distributions only**
(serving-cell RSRP / RSRQ / SINR shape against the 5G-NSA NR carrier),
and **never** mix real and simulated samples. The HO control plane
itself is verified against 3GPP TS 38.331 §5.5.4 (A3) and §5.3.10 (RLF)
in the simulator unit tests, not against real HO event logs.

Outputs:
    - per-segment / per-KPI KS-statistic + p-value + bootstrap 95% CI
    - Q-Q plot per (KPI, segment) pair
    - summary table printed and saved to ``calibration_ks_summary.csv``

Usage:
    python -m analysis.calibration.ks_test --sim data/simulated/calibration_baseline
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

from analysis.calibration.load_nordicdat import load_nordicdat
from analysis.common.paths import DATA_PROC


# Reference segments. Chosen in `docs/schema.md` §4.4 by row-count
# dominance (top-3 NordicDat segments cover ~85 % of the corpus).
#
# The first segment (op1 / 5G-NSA / LTE_B20) is the **primary calibration
# target** — it is the closest real-world analog to our 5G NR SA
# simulator scope. The two LTE segments are kept in the summary as
# cross-RAN sanity references only (we expect them to be more distant).
TARGET_SEGMENTS: list[tuple[int, str, str]] = [
    (1, "5G-NSA", "LTE_B20"),   # primary calibration target
    (1, "LTE",    "LTE_B20"),   # cross-RAN sanity reference
    (3, "LTE",    "LTE_B3"),    # cross-RAN + cross-band sanity reference
]

# Map sim KPI column → real KPI column for the comparison.
KPI_PAIRS: list[tuple[str, str, str, str]] = [
    # (sim_col, real_col, axis_label, fig_suffix)
    ("rsrp_serving_dbm", "rsrp_serving_dbm", "RSRP (dBm)", "rsrp"),
    ("rsrq_serving_db",  "rsrq_serving_db",  "RSRQ (dB)",  "rsrq"),
    ("sinr_serving_db",  "sinr_serving_db",  "SINR (dB)",  "sinr"),
]


# =============================================================================
# Result container
# =============================================================================
@dataclass(frozen=True)
class KSResult:
    operator_id: int
    ran_type: str
    band: str
    kpi: str
    ks_stat: float
    p_value: float
    ks_stat_ci_lo: float
    ks_stat_ci_hi: float
    n_sim: int
    n_real: int
    sim_p10: float
    sim_p50: float
    sim_p90: float
    real_p10: float
    real_p50: float
    real_p90: float

    def as_row(self) -> dict:
        return {
            "operator_id": self.operator_id,
            "ran_type":    self.ran_type,
            "band":        self.band,
            "kpi":         self.kpi,
            "ks_stat":     round(self.ks_stat, 4),
            "ks_ci_lo":    round(self.ks_stat_ci_lo, 4),
            "ks_ci_hi":    round(self.ks_stat_ci_hi, 4),
            "p_value":     self.p_value,
            "n_sim":       self.n_sim,
            "n_real":      self.n_real,
            "sim_p10":     round(self.sim_p10, 2),
            "sim_p50":     round(self.sim_p50, 2),
            "sim_p90":     round(self.sim_p90, 2),
            "real_p10":    round(self.real_p10, 2),
            "real_p50":    round(self.real_p50, 2),
            "real_p90":    round(self.real_p90, 2),
            "delta_p50":   round(self.sim_p50 - self.real_p50, 2),
        }


# =============================================================================
# Core API
# =============================================================================
def load_sim_kpis(sim_dir: Path) -> pd.DataFrame:
    """Concatenate all kpis_ue*.parquet under ``sim_dir`` into one frame."""
    files = sorted(sim_dir.glob("kpis_ue*.parquet"))
    if not files:
        raise FileNotFoundError(
            f"No kpis_ue*.parquet under {sim_dir}. Run calibration_run.m first."
        )
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def ks_with_bootstrap(
    sim_vals: np.ndarray,
    real_vals: np.ndarray,
    n_boot: int = 200,
    seed: int = 42,
) -> tuple[float, float, float, float]:
    """Two-sample KS test plus a paired-resample 95% CI on the statistic.

    Returns:
        (ks_stat, p_value, ci_lo, ci_hi) — CI is over the bootstrap
        distribution of the KS statistic when resampling both inputs with
        replacement (preserves dependence structure trivially since the
        two distributions are independent).
    """
    sim_vals  = np.asarray(sim_vals,  dtype=np.float64)
    real_vals = np.asarray(real_vals, dtype=np.float64)
    sim_vals  = sim_vals[np.isfinite(sim_vals)]
    real_vals = real_vals[np.isfinite(real_vals)]
    if len(sim_vals) < 10 or len(real_vals) < 10:
        return float("nan"), float("nan"), float("nan"), float("nan")

    res = stats.ks_2samp(sim_vals, real_vals)
    ks_stat = float(res.statistic)
    p_value = float(res.pvalue)

    rng = np.random.default_rng(seed)
    # Bootstrap sample size capped to keep cost predictable
    n_s = min(len(sim_vals),  10_000)
    n_r = min(len(real_vals), 10_000)
    stats_boot = np.empty(n_boot, dtype=np.float64)
    for i in range(n_boot):
        bs_s = rng.choice(sim_vals,  size=n_s, replace=True)
        bs_r = rng.choice(real_vals, size=n_r, replace=True)
        stats_boot[i] = stats.ks_2samp(bs_s, bs_r).statistic
    lo, hi = np.percentile(stats_boot, [2.5, 97.5])
    return ks_stat, p_value, float(lo), float(hi)


def compute_all(
    sim: pd.DataFrame,
    real: pd.DataFrame,
    n_boot: int = 200,
) -> list[KSResult]:
    """Run the full target-segment × KPI grid and return all KSResults."""
    out: list[KSResult] = []
    for (op, ran, band) in TARGET_SEGMENTS:
        sub_real = real[
            (real["operator_id"] == op)
            & (real["ran_type"] == ran)
            & (real["band"] == band)
        ]
        if sub_real.empty:
            print(f"WARN: real segment op{op}/{ran}/{band} is empty, skipping")
            continue
        for sim_col, real_col, _label, _suf in KPI_PAIRS:
            ks, p, lo, hi = ks_with_bootstrap(
                sim[sim_col].to_numpy(),
                sub_real[real_col].to_numpy(),
                n_boot=n_boot,
            )
            s = sim[sim_col].dropna()
            r = sub_real[real_col].dropna()
            out.append(
                KSResult(
                    operator_id=int(op), ran_type=ran, band=band, kpi=sim_col,
                    ks_stat=ks, p_value=p,
                    ks_stat_ci_lo=lo, ks_stat_ci_hi=hi,
                    n_sim=int(len(s)), n_real=int(len(r)),
                    sim_p10=float(s.quantile(0.10)),
                    sim_p50=float(s.quantile(0.50)),
                    sim_p90=float(s.quantile(0.90)),
                    real_p10=float(r.quantile(0.10)),
                    real_p50=float(r.quantile(0.50)),
                    real_p90=float(r.quantile(0.90)),
                )
            )
    return out


# =============================================================================
# Plots
# =============================================================================
def plot_qq_panel(
    sim: pd.DataFrame,
    real: pd.DataFrame,
    out_dir: Path,
    label: str,
) -> None:
    """Q-Q plot grid: rows = KPI, cols = target segment."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = KPI_PAIRS
    cols = TARGET_SEGMENTS
    fig, axes = plt.subplots(
        len(rows), len(cols),
        figsize=(4.5 * len(cols), 3.5 * len(rows)),
        constrained_layout=True,
    )
    if len(rows) == 1:
        axes = axes.reshape(1, -1)

    for j, (op, ran, band) in enumerate(cols):
        sub_real = real[
            (real["operator_id"] == op)
            & (real["ran_type"] == ran)
            & (real["band"] == band)
        ]
        for i, (sim_col, real_col, axis_label, _) in enumerate(rows):
            ax = axes[i, j]
            s = np.sort(sim[sim_col].dropna().to_numpy())
            r = np.sort(sub_real[real_col].dropna().to_numpy())
            if len(s) == 0 or len(r) == 0:
                ax.set_title("(no data)")
                continue
            # quantile-quantile via interpolation onto a common probability grid
            n = min(len(s), len(r), 2000)
            q = np.linspace(0.005, 0.995, n)
            sq = np.quantile(s, q)
            rq = np.quantile(r, q)
            ax.plot(rq, sq, "k.", markersize=2, alpha=0.5)
            lo = min(sq.min(), rq.min()); hi = max(sq.max(), rq.max())
            ax.plot([lo, hi], [lo, hi], "r--", linewidth=1, label="y = x")
            ax.set_xlabel(f"real {axis_label}"); ax.set_ylabel(f"sim {axis_label}")
            if i == 0:
                ax.set_title(f"op{op}/{ran}/{band}", fontsize=10)
            ax.grid(True, alpha=0.3)

    fig.suptitle(f"Q-Q plots: simulator vs NordicDat — {label}", fontsize=12)
    fp = out_dir / f"qq_{label}.png"
    fig.savefig(fp, dpi=140)
    plt.close(fig)
    print(f"  Q-Q grid → {fp}")


def plot_cdf_overlay(
    sim: pd.DataFrame,
    real: pd.DataFrame,
    out_dir: Path,
    label: str,
) -> None:
    """One overlaid empirical CDF figure per KPI, all segments stacked."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, len(KPI_PAIRS),
                             figsize=(5.5 * len(KPI_PAIRS), 4),
                             constrained_layout=True)
    for ax, (sim_col, real_col, axis_label, _) in zip(axes, KPI_PAIRS):
        s = np.sort(sim[sim_col].dropna().to_numpy())
        y_s = np.arange(1, len(s) + 1) / len(s)
        ax.plot(s, y_s, color="k", linewidth=2,
                label=f"sim (n={len(s):,})")
        for (op, ran, band) in TARGET_SEGMENTS:
            sub_real = real[
                (real["operator_id"] == op)
                & (real["ran_type"] == ran)
                & (real["band"] == band)
            ]
            r = np.sort(sub_real[real_col].dropna().to_numpy())
            if len(r) == 0:
                continue
            y_r = np.arange(1, len(r) + 1) / len(r)
            ax.plot(r, y_r, linewidth=1.2,
                    label=f"op{op}/{ran}/{band} (n={len(r):,})")
        ax.set_xlabel(axis_label); ax.set_ylabel("F(x)")
        ax.grid(True, alpha=0.3); ax.legend(fontsize=8, loc="best")
    fig.suptitle(f"Empirical CDF overlay — {label}", fontsize=12)
    fp = out_dir / f"cdf_{label}.png"
    fig.savefig(fp, dpi=140)
    plt.close(fig)
    print(f"  CDF overlay → {fp}")


# =============================================================================
# CLI
# =============================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sim", type=Path, required=True,
                    help="directory containing kpis_ue*.parquet")
    ap.add_argument("--label", type=str, default=None,
                    help="short label used in output filenames "
                         "(default: sim dir name)")
    ap.add_argument("--n-boot", type=int, default=200,
                    help="bootstrap iterations for KS CI (default: 200)")
    ap.add_argument("--out", type=Path,
                    default=DATA_PROC / "calibration_ks",
                    help="output dir for plots + summary CSV")
    args = ap.parse_args()

    label = args.label or args.sim.name
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"Loading sim KPIs from {args.sim} ...")
    sim = load_sim_kpis(args.sim)
    print(f"  sim rows: {len(sim):,}   UEs: {sim['ue_id'].nunique()}   "
          f"timespan: {sim['time_s'].max() - sim['time_s'].min():.1f} s")

    print("Loading NordicDat ...")
    real = load_nordicdat()
    print(f"  real rows: {len(real):,}")

    print(f"Running KS-tests ({args.n_boot} bootstrap iters) ...")
    results = compute_all(sim, real, n_boot=args.n_boot)

    df = pd.DataFrame([r.as_row() for r in results])
    print()
    print(df.to_string(index=False))

    csv_fp = args.out / f"ks_summary_{label}.csv"
    df.to_csv(csv_fp, index=False)
    print(f"\nSummary CSV → {csv_fp}")

    plot_qq_panel(sim, real, args.out, label)
    plot_cdf_overlay(sim, real, args.out, label)

    return 0


if __name__ == "__main__":
    sys.exit(main())
