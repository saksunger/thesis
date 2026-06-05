"""Phase 5 Iter B — full anomaly-detection benchmark.

Builds on the Iter A smoke pipeline (`analysis.anomaly.smoke_eval`) but
produces the thesis-grade artifacts required by Chapter 5:

    Table 5.1  PR-AUC per detector x anomaly_type, with bootstrap 95 % CI
    Table 5.2  Window-size sweep (2 / 5 / 10 / 30 s) per detector
    Table 5.3  Per-feature-family ablation on the best detector
    Figure 5.1 Drift-degradation panel (FPR over time, per detector,
               with drift scenarios overlaid)

Pipeline (single CLI invocation):

1. Load timeline parquet (samples + events + ground_truth_anomaly).
2. Aggregate per-(UE, window) features with `WindowConfig(window_s, ...)`.
3. Label windows via `label_windows` (Iter B: cell-conditional A-3).
4. Build the training pool: phases <= `train_max_phase_id` AND
   `t_end_s <= train_max_time_s` (default = full first 3 baseline
   phases of `timeline_medium`, ~180 s × 12 UEs).
5. Fit each of the 5 benchmark detectors on the training pool.
6. Score the full table; compute PR-AUC per (detector, anomaly_type)
   with bootstrap CI (1000 resamples, stratified).
7. (optional) Window-size sweep — repeat steps 2-6 for each window.
8. (optional) Per-feature-family ablation on the winning detector.
9. Generate the drift-degradation figure (FPR over time per detector,
   with drift-phase windows shaded by scenario).
10. Dump CSVs + figure + a JSON summary to
    ``data/processed/anomaly_benchmark_<timeline>/``.

ADR-15 enforcement: only mobility KPIs flow through the feature
pipeline; throughput is intentionally absent.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from analysis.anomaly.detectors import benchmark_detectors
from analysis.anomaly.features import (
    WindowConfig,
    aggregate_windows,
    feature_columns,
    feature_groups,
)
from analysis.anomaly.labels import anomaly_label_columns, label_windows
from analysis.anomaly.metrics import bootstrap_pr_auc, format_ci
from analysis.common.paths import DATA_PROC, assert_timeline_present, timeline_dir


# -------------------------------------------------------------------------
# CLI + config
# -------------------------------------------------------------------------

@dataclass
class BenchmarkConfig:
    timeline: str = "timeline_medium"
    window_s: float = 5.0
    slide_s: float = 1.0
    train_max_phase_id: int = 3      # phases 1..3 baseline → train pool
    train_max_time_s: float = 1e9    # no time cap (full train phases)
    flag_frac: float = 0.05          # top-5% of train scores = "flagged"
    bootstrap_resamples: int = 1000
    bootstrap_alpha: float = 0.05
    run_window_sweep: bool = True
    window_sweep_sizes: tuple[float, ...] = (2.0, 5.0, 10.0, 30.0)
    run_ablation: bool = True
    out_label: str | None = None
    random_state: int = 42


def _parse_args(argv: list[str] | None = None) -> BenchmarkConfig:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--timeline", default="timeline_medium")
    p.add_argument("--window-s", type=float, default=5.0)
    p.add_argument("--slide-s", type=float, default=1.0)
    p.add_argument("--train-max-phase-id", type=int, default=3)
    p.add_argument("--train-max-time-s", type=float, default=1e9)
    p.add_argument("--flag-frac", type=float, default=0.05)
    p.add_argument("--bootstrap-resamples", type=int, default=1000)
    p.add_argument("--bootstrap-alpha", type=float, default=0.05)
    p.add_argument("--no-window-sweep", dest="run_window_sweep", action="store_false")
    p.add_argument(
        "--window-sweep-sizes",
        type=str,
        default="2.0,5.0,10.0,30.0",
        help="Comma-separated window sizes for the sweep.",
    )
    p.add_argument("--no-ablation", dest="run_ablation", action="store_false")
    p.add_argument("--out-label", default=None)
    p.add_argument("--random-state", type=int, default=42)
    a = p.parse_args(argv)
    sizes = tuple(float(x) for x in a.window_sweep_sizes.split(","))
    return BenchmarkConfig(
        timeline=a.timeline,
        window_s=a.window_s,
        slide_s=a.slide_s,
        train_max_phase_id=a.train_max_phase_id,
        train_max_time_s=a.train_max_time_s,
        flag_frac=a.flag_frac,
        bootstrap_resamples=a.bootstrap_resamples,
        bootstrap_alpha=a.bootstrap_alpha,
        run_window_sweep=a.run_window_sweep,
        window_sweep_sizes=sizes,
        run_ablation=a.run_ablation,
        out_label=a.out_label,
        random_state=a.random_state,
    )


# -------------------------------------------------------------------------
# Core single-config benchmark
# -------------------------------------------------------------------------

@dataclass
class ScoreTable:
    """Bundle of feature table + per-detector scores + flags."""

    df: pd.DataFrame
    detector_names: list[str]
    feature_cols: list[str]


def _load_inputs(cfg: BenchmarkConfig):
    assert_timeline_present(cfg.timeline)
    base = timeline_dir(cfg.timeline)
    print(f"[load] {base}")
    samples = pd.read_parquet(base / "samples.parquet")
    events = pd.read_parquet(base / "events.parquet")
    gta_fp = base / "ground_truth_anomaly.parquet"
    if not gta_fp.is_file():
        sys.exit(f"ERROR: {gta_fp} missing — benchmark needs anomaly GT.")
    gta = pd.read_parquet(gta_fp)
    print(
        f"       samples={len(samples):,} events={len(events):,} "
        f"gta={len(gta)} entries"
    )
    return samples, events, gta


def _build_score_table(
    samples: pd.DataFrame,
    events: pd.DataFrame,
    gta: pd.DataFrame,
    cfg: BenchmarkConfig,
    feature_cols: list[str] | None = None,
    label: str = "",
) -> ScoreTable:
    """Aggregate windows, label, train detectors, score full table."""
    print(f"[bench{label}] aggregating windows (W={cfg.window_s}s, slide={cfg.slide_s}s)...")
    feats = aggregate_windows(
        samples, events, WindowConfig(window_s=cfg.window_s, slide_s=cfg.slide_s)
    )
    print(f"           windows={len(feats):,}")

    labelled = label_windows(feats, gta)
    n_pos = int(labelled["label_anomaly"].sum())
    prev = n_pos / max(len(labelled), 1)
    print(f"[bench{label}] positives={n_pos:,} (prevalence={prev:.4f})")

    feat_cols = feature_cols or feature_columns()
    train_mask = (
        (labelled["phase_id"] <= cfg.train_max_phase_id)
        & (labelled["t_end_s"] <= cfg.train_max_time_s)
        & (labelled["label_anomaly"] == 0)
    )
    train_df = labelled[train_mask].reset_index(drop=True)
    if len(train_df) == 0:
        sys.exit(
            f"ERROR: zero train windows for train_max_phase_id="
            f"{cfg.train_max_phase_id}. Adjust --train-* args."
        )
    print(f"[bench{label}] train windows: {len(train_df):,}")

    score_table = labelled.copy()
    detectors = benchmark_detectors()
    det_names: list[str] = []
    for det in detectors:
        t0 = time.perf_counter()
        det.fit(train_df[feat_cols])
        scores = det.score_samples(score_table[feat_cols])
        score_table[f"score_{det.name}"] = scores
        train_scores = det.score_samples(train_df[feat_cols])
        thr = float(np.quantile(train_scores, 1.0 - cfg.flag_frac))
        score_table[f"flag_{det.name}"] = (scores >= thr).astype(int)
        det_names.append(det.name)
        print(
            f"           {det.name:<14} thr={thr:+.4f} "
            f"({time.perf_counter() - t0:.1f}s)"
        )

    return ScoreTable(df=score_table, detector_names=det_names, feature_cols=feat_cols)


# -------------------------------------------------------------------------
# Metric tables
# -------------------------------------------------------------------------

def _per_anomaly_table(
    score_table: ScoreTable,
    cfg: BenchmarkConfig,
    gta: pd.DataFrame,
) -> pd.DataFrame:
    """Table 5.1 input: PR-AUC per (detector x anomaly_type) with bootstrap CI.

    Per anomaly TYPE (rlf_burst / meas_glitch / interference_spike /
    slow_degrade), we union all label columns of that type's anomaly_ids
    and compute PR-AUC over the windows scoped to those anomalies' own
    phases + adjacent baselines. This is the standard "type-level"
    aggregation used in thesis Chapter 5.
    """
    df = score_table.df
    # Map anomaly_id -> anomaly_type via GT
    aid_to_type = dict(zip(gta["anomaly_id"], gta["anomaly_type"]))
    type_to_aids: dict[str, list[str]] = {}
    for aid, typ in aid_to_type.items():
        type_to_aids.setdefault(typ, []).append(aid)

    rows = []
    for typ, aids in sorted(type_to_aids.items()):
        # Per-type label = OR over all anomaly_ids of this type
        label_cols = [f"label_{a}" for a in aids if f"label_{a}" in df.columns]
        if not label_cols:
            continue
        type_label = (df[label_cols].sum(axis=1) > 0).astype(int)
        # Scope: phases that contain at least one anomaly of this type
        type_phases = sorted(set(int(p) for p in gta.loc[gta["anomaly_type"] == typ, "phase_id"]))
        in_scope = df["phase_id"].isin(type_phases)
        scoped = df[in_scope]
        y = type_label[in_scope].to_numpy()
        if y.sum() == 0 or y.sum() == len(y):
            continue
        for det in score_table.detector_names:
            s = scoped[f"score_{det}"].to_numpy()
            flag = scoped[f"flag_{det}"].to_numpy()
            roc = float(roc_auc_score(y, s))
            boot = bootstrap_pr_auc(
                y, s,
                n_resamples=cfg.bootstrap_resamples,
                alpha=cfg.bootstrap_alpha,
                stratify=True,
                random_state=cfg.random_state,
            )
            rows.append(
                {
                    "anomaly_type": typ,
                    "detector": det,
                    "n_pos": int(y.sum()),
                    "n_neg": int(len(y) - y.sum()),
                    "pr_auc": boot.point,
                    "pr_auc_ci_low": boot.ci_low,
                    "pr_auc_ci_high": boot.ci_high,
                    "roc_auc": roc,
                    "tpr": float(flag[y == 1].mean()),
                    "fpr": float(flag[y == 0].mean()),
                    "ci_str": format_ci(boot),
                }
            )
    return pd.DataFrame(rows)


def _per_phase_fpr_table(score_table: ScoreTable) -> pd.DataFrame:
    """Per-phase FPR + scenario_name for the drift-degradation figure."""
    df = score_table.df
    rows = []
    for phase_id, g in df.groupby("phase_id"):
        scen = g["scenario_name"].iloc[0]
        for det in score_table.detector_names:
            y = g["label_anomaly"].to_numpy()
            flag = g[f"flag_{det}"].to_numpy()
            n_neg = int((y == 0).sum())
            if n_neg == 0:
                fpr = float("nan")
            else:
                fpr = float(flag[y == 0].mean())
            tpr = (
                float(flag[y == 1].mean()) if int((y == 1).sum()) > 0 else float("nan")
            )
            rows.append(
                {
                    "phase_id": int(phase_id),
                    "scenario_name": scen,
                    "detector": det,
                    "n_windows": int(len(y)),
                    "n_pos": int(y.sum()),
                    "n_neg": n_neg,
                    "fpr": fpr,
                    "tpr": tpr,
                }
            )
    return pd.DataFrame(rows)


def _overall_metric_table(score_table: ScoreTable, cfg: BenchmarkConfig) -> pd.DataFrame:
    """Single PR-AUC per detector over the FULL timeline (with bootstrap CI)."""
    df = score_table.df
    rows = []
    y_all = df["label_anomaly"].to_numpy()
    if y_all.sum() == 0:
        return pd.DataFrame(rows)
    for det in score_table.detector_names:
        s = df[f"score_{det}"].to_numpy()
        boot = bootstrap_pr_auc(
            y_all, s,
            n_resamples=cfg.bootstrap_resamples,
            alpha=cfg.bootstrap_alpha,
            stratify=True,
            random_state=cfg.random_state,
        )
        rows.append(
            {
                "detector": det,
                "pr_auc": boot.point,
                "pr_auc_ci_low": boot.ci_low,
                "pr_auc_ci_high": boot.ci_high,
                "ci_str": format_ci(boot),
                "n_pos": int(y_all.sum()),
                "n_neg": int(len(y_all) - y_all.sum()),
            }
        )
    return pd.DataFrame(rows)


# -------------------------------------------------------------------------
# Window-size sweep
# -------------------------------------------------------------------------

def _run_window_sweep(
    samples: pd.DataFrame,
    events: pd.DataFrame,
    gta: pd.DataFrame,
    cfg: BenchmarkConfig,
) -> pd.DataFrame:
    """Re-run benchmark for each window size and collect type-level PR-AUC."""
    print(f"\n=== Window-size sweep over {list(cfg.window_sweep_sizes)} s ===")
    rows = []
    for w in cfg.window_sweep_sizes:
        slide_w = min(cfg.slide_s, w)  # slide must not exceed window_s
        sweep_cfg = BenchmarkConfig(
            **{**asdict(cfg), "window_s": float(w), "slide_s": float(slide_w),
               "run_window_sweep": False, "run_ablation": False}
        )
        st = _build_score_table(samples, events, gta, sweep_cfg, label=f" W={w}s")
        per_type = _per_anomaly_table(st, sweep_cfg, gta)
        per_type["window_s"] = float(w)
        rows.append(per_type)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


# -------------------------------------------------------------------------
# Per-feature-family ablation
# -------------------------------------------------------------------------

def _run_ablation(
    samples: pd.DataFrame,
    events: pd.DataFrame,
    gta: pd.DataFrame,
    cfg: BenchmarkConfig,
    winning_detector: str,
) -> pd.DataFrame:
    """Drop one feature family at a time and re-evaluate the winner.

    Returns a DataFrame with columns `dropped_family`, `n_features`,
    `anomaly_type`, `pr_auc`, `pr_auc_ci_low`, `pr_auc_ci_high`,
    `pr_auc_delta_vs_full`. Positive delta = family was harmful;
    negative delta = family carried signal.
    """
    print(f"\n=== Per-feature-family ablation (winning detector: {winning_detector}) ===")
    groups = feature_groups()
    rows = []

    # Reference: full feature set
    print("[ablation] reference: full feature set")
    full_st = _build_score_table(samples, events, gta, cfg, label=" full")
    full_per_type = _per_anomaly_table(full_st, cfg, gta)
    full_per_type = full_per_type[full_per_type["detector"] == winning_detector]
    ref_map = dict(zip(full_per_type["anomaly_type"], full_per_type["pr_auc"]))
    for _, r in full_per_type.iterrows():
        rows.append(
            {
                "dropped_family": "<none>",
                "n_features": len(full_st.feature_cols),
                "anomaly_type": r["anomaly_type"],
                "pr_auc": r["pr_auc"],
                "pr_auc_ci_low": r["pr_auc_ci_low"],
                "pr_auc_ci_high": r["pr_auc_ci_high"],
                "pr_auc_delta_vs_full": 0.0,
            }
        )

    # Drop-one passes
    full_cols = feature_columns()
    for fam, fam_cols in groups.items():
        kept = [c for c in full_cols if c not in set(fam_cols)]
        print(f"[ablation] drop family={fam} ({len(fam_cols)} features), kept={len(kept)}")
        st = _build_score_table(
            samples, events, gta, cfg, feature_cols=kept, label=f" -{fam}"
        )
        per_type = _per_anomaly_table(st, cfg, gta)
        per_type = per_type[per_type["detector"] == winning_detector]
        for _, r in per_type.iterrows():
            ref = ref_map.get(r["anomaly_type"], float("nan"))
            rows.append(
                {
                    "dropped_family": fam,
                    "n_features": len(kept),
                    "anomaly_type": r["anomaly_type"],
                    "pr_auc": r["pr_auc"],
                    "pr_auc_ci_low": r["pr_auc_ci_low"],
                    "pr_auc_ci_high": r["pr_auc_ci_high"],
                    "pr_auc_delta_vs_full": float(r["pr_auc"] - ref),
                }
            )
    return pd.DataFrame(rows)


# -------------------------------------------------------------------------
# Drift-degradation figure
# -------------------------------------------------------------------------

def _save_drift_degradation_figure(
    per_phase: pd.DataFrame,
    cfg: BenchmarkConfig,
    fp: Path,
) -> None:
    """Two-panel thesis figure: FPR + TPR per detector across phases.

    Drift phases (scenario_name starts with `drift_`) are shaded by
    color according to drift type, so the visual story is "every time
    a drift kicks in, baseline FPR climbs above the design target".
    """
    if per_phase.empty:
        return
    drift_colors = {
        "drift_channel_swap":  "tab:orange",
        "drift_mobility":      "tab:olive",
        "drift_reconfig":      "tab:red",
        "drift_traffic_shift": "tab:purple",
    }

    fig, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
    phases = sorted(per_phase["phase_id"].unique())

    # Panel A: FPR per detector
    ax = axes[0]
    for det, g in per_phase.groupby("detector"):
        g = g.sort_values("phase_id")
        ax.plot(g["phase_id"], g["fpr"], marker="o", linewidth=1.6, label=det)
    ax.axhline(
        cfg.flag_frac,
        color="grey",
        linestyle="--",
        linewidth=1.0,
        label=f"design target ({cfg.flag_frac:.0%})",
    )
    ax.set_ylabel("False-flag rate (FPR)")
    ax.set_title(
        f"Phase 5 Iter B - drift-degradation panel ({cfg.timeline}, W={cfg.window_s}s)"
    )
    ax.legend(loc="upper left", fontsize=8, ncol=2)
    ax.grid(alpha=0.3)

    # Drift shading
    seen_drifts: set[str] = set()
    for ph in phases:
        scen = per_phase.loc[per_phase["phase_id"] == ph, "scenario_name"].iloc[0]
        if scen in drift_colors:
            for sub in axes:
                sub.axvspan(
                    ph - 0.5, ph + 0.5,
                    color=drift_colors[scen], alpha=0.15,
                    label=(scen if scen not in seen_drifts else None),
                )
            seen_drifts.add(scen)

    # Panel B: TPR per detector
    ax = axes[1]
    for det, g in per_phase.groupby("detector"):
        g = g.sort_values("phase_id")
        ax.plot(g["phase_id"], g["tpr"], marker="o", linewidth=1.6, label=det)
    ax.set_xlabel("phase_id (timeline_medium: phase k = [60(k-1), 60k) s)")
    ax.set_ylabel("True-positive rate (recall)")
    ax.legend(loc="upper left", fontsize=8, ncol=2)
    ax.grid(alpha=0.3)

    # De-dupe legends for drift bands at the top of the figure
    handles, labels = axes[0].get_legend_handles_labels()
    dedup = dict(zip(labels, handles))
    axes[0].legend(dedup.values(), dedup.keys(), loc="upper left", fontsize=8, ncol=2)
    handles, labels = axes[1].get_legend_handles_labels()
    dedup = dict(zip(labels, handles))
    axes[1].legend(dedup.values(), dedup.keys(), loc="upper left", fontsize=8, ncol=2)

    fig.tight_layout()
    fig.savefig(fp, dpi=140, bbox_inches="tight")
    plt.close(fig)


# -------------------------------------------------------------------------
# Main entrypoint
# -------------------------------------------------------------------------

def run_benchmark(cfg: BenchmarkConfig) -> dict:
    t_start = time.perf_counter()
    out_dir = DATA_PROC / f"anomaly_benchmark_{cfg.out_label or cfg.timeline}"
    out_dir.mkdir(parents=True, exist_ok=True)

    samples, events, gta = _load_inputs(cfg)

    print(f"\n=== Reference run (W={cfg.window_s}s) ===")
    ref = _build_score_table(samples, events, gta, cfg, label=" ref")
    per_anom = _per_anomaly_table(ref, cfg, gta)
    per_phase = _per_phase_fpr_table(ref)
    overall = _overall_metric_table(ref, cfg)

    per_anom.to_csv(out_dir / "per_anomaly_with_ci.csv", index=False)
    per_phase.to_csv(out_dir / "per_phase_fpr_tpr.csv", index=False)
    overall.to_csv(out_dir / "overall_pr_auc_with_ci.csv", index=False)

    # Pivot Table 5.1 wide (anomaly_type x detector) with CI strings
    if not per_anom.empty:
        wide = per_anom.pivot(index="anomaly_type", columns="detector", values="ci_str")
        wide.to_csv(out_dir / "table_5_1_pr_auc_wide.csv")

    # Winning detector = highest mean PR-AUC across anomaly types
    if per_anom.empty:
        winner = ref.detector_names[0]
    else:
        winner = per_anom.groupby("detector")["pr_auc"].mean().idxmax()
    print(f"\n[winner] best detector by mean PR-AUC: {winner}")

    # Drift-degradation figure
    _save_drift_degradation_figure(per_phase, cfg, out_dir / "drift_degradation.png")

    # Optional: window sweep
    sweep_df = pd.DataFrame()
    if cfg.run_window_sweep:
        sweep_df = _run_window_sweep(samples, events, gta, cfg)
        if not sweep_df.empty:
            sweep_df.to_csv(out_dir / "window_sweep_per_anomaly.csv", index=False)
            sweep_wide = sweep_df.pivot_table(
                index=["window_s", "anomaly_type"],
                columns="detector",
                values="pr_auc",
            ).round(3)
            sweep_wide.to_csv(out_dir / "table_5_2_window_sweep_wide.csv")

    # Optional: per-family ablation on winner
    ablation_df = pd.DataFrame()
    if cfg.run_ablation:
        ablation_df = _run_ablation(samples, events, gta, cfg, winner)
        if not ablation_df.empty:
            ablation_df.to_csv(out_dir / "ablation_winning_detector.csv", index=False)
            abl_wide = ablation_df.pivot_table(
                index="dropped_family",
                columns="anomaly_type",
                values="pr_auc_delta_vs_full",
            ).round(3)
            abl_wide.to_csv(out_dir / "table_5_3_ablation_wide.csv")

    # JSON summary
    summary = {
        "config": asdict(cfg),
        "winning_detector_by_mean_pr_auc": winner,
        "elapsed_s": round(time.perf_counter() - t_start, 1),
        "out_dir": str(out_dir),
    }
    with open(out_dir / "benchmark_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # Pretty-print headline tables to stdout
    print("\n=== Table 5.1: PR-AUC per (detector x anomaly_type) with 95% CI ===")
    if not per_anom.empty:
        print(per_anom.pivot(index="anomaly_type", columns="detector", values="ci_str").to_string())
    print("\n=== Overall (full-timeline) PR-AUC with 95% CI ===")
    if not overall.empty:
        print(overall[["detector", "ci_str", "n_pos", "n_neg"]].to_string(index=False))
    if cfg.run_window_sweep and not sweep_df.empty:
        print("\n=== Table 5.2: Window-size sweep (PR-AUC point estimate) ===")
        print(
            sweep_df.pivot_table(
                index=["window_s", "anomaly_type"],
                columns="detector",
                values="pr_auc",
            ).round(3).to_string()
        )
    if cfg.run_ablation and not ablation_df.empty:
        print(f"\n=== Table 5.3: Ablation (delta PR-AUC vs full) on {winner} ===")
        print(
            ablation_df.pivot_table(
                index="dropped_family",
                columns="anomaly_type",
                values="pr_auc_delta_vs_full",
            ).round(3).to_string()
        )

    print(f"\nArtifacts written to: {out_dir}")
    print(f"Total elapsed: {summary['elapsed_s']:.1f}s")
    return {
        "out_dir": str(out_dir),
        "per_anomaly": per_anom.to_dict(orient="records"),
        "overall": overall.to_dict(orient="records"),
        "per_phase": per_phase.to_dict(orient="records"),
        "window_sweep": sweep_df.to_dict(orient="records") if not sweep_df.empty else [],
        "ablation": ablation_df.to_dict(orient="records") if not ablation_df.empty else [],
        "winner": winner,
        "summary": summary,
    }


if __name__ == "__main__":
    run_benchmark(_parse_args())
