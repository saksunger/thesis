"""Phase 5 Iter A — anomaly-detection smoke evaluation.

Pipeline (single CLI invocation):

1. Load `samples.parquet`, `events.parquet`, `ground_truth_anomaly.parquet`
   for the requested timeline (default: `timeline_iter_b`).
2. Aggregate per-(UE, window) mobility features via
   :func:`analysis.anomaly.features.aggregate_windows`.
3. Build per-window binary labels via
   :func:`analysis.anomaly.labels.label_windows`.
4. Train each detector on TRAIN windows (Phase 1 baseline, pre-A-1 only),
   then score ALL windows.
5. Per (detector, phase): compute
   - **TPR**  (recall on positive windows in that phase) — sensitivity
   - **FPR**  (false-positive rate on negative windows in that phase) —
     KEY drift-degradation signal; should be ≈ contamination on baseline
     phases and HIGHER on drift phases (2-5).
   - **PR-AUC** (when at least one positive label exists)
6. Per (detector, anomaly_id): PR-AUC computed over windows from the
   phase that holds that anomaly.
7. Dump CSV tables + a 3-panel summary plot to
   ``data/processed/anomaly_smoke_<timeline>/``.

Thresholding convention
-----------------------
We use the top-k strategy: a window is "flagged" iff its detector score
is in the top `--flag-frac` quantile of the TRAIN-set score distribution.
This is purely a reporting convention to compare FPR across phases on
equal footing; PR-AUC and ROC-AUC are threshold-free.

Acceptance criterion (per `docs/plan.md` Phase 5 Iter A)
--------------------------------------------------------
- Smoke detector PR-AUC > 0.1 on at least 2 of 4 anomaly types (random
  would be approx. the prevalence rate, which is ~1-3% here, so any
  PR-AUC > 0.1 is meaningfully above random).
- Drift-degradation visible: at least one detector shows
  ``FPR_drift_phases > 2 * FPR_baseline_phases``.

Pass → green-light Phase 5 Iter B + Phase 4 Iter C.
Fail → revisit anomaly severity / window size / feature whitelist.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from analysis.anomaly.detectors import all_detectors
from analysis.anomaly.features import WindowConfig, aggregate_windows, feature_columns
from analysis.anomaly.labels import anomaly_label_columns, label_windows
from analysis.common.paths import DATA_PROC, assert_timeline_present, timeline_dir


# -------------------------------------------------------------------------
# CLI + config
# -------------------------------------------------------------------------

@dataclass
class SmokeConfig:
    timeline: str = "timeline_iter_b"
    window_s: float = 5.0
    slide_s: float = 1.0
    train_phase_id: int = 1
    train_max_time_s: float = 25.0    # Phase 1 anomaly A-1 starts at t=25s
    flag_frac: float = 0.05            # top-5% by train score = "flagged"
    out_label: str | None = None


def _parse_args(argv: list[str] | None = None) -> SmokeConfig:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--timeline", default="timeline_iter_b")
    p.add_argument("--window-s", type=float, default=5.0)
    p.add_argument("--slide-s", type=float, default=1.0)
    p.add_argument("--train-phase-id", type=int, default=1)
    p.add_argument(
        "--train-max-time-s",
        type=float,
        default=25.0,
        help="Train on windows whose t_end_s <= this (pre-A-1 in phase 1).",
    )
    p.add_argument("--flag-frac", type=float, default=0.05)
    p.add_argument(
        "--out-label",
        default=None,
        help="Override the output sub-folder label (default: <timeline>).",
    )
    a = p.parse_args(argv)
    return SmokeConfig(**vars(a))


# -------------------------------------------------------------------------
# Core pipeline
# -------------------------------------------------------------------------

def run_smoke(cfg: SmokeConfig) -> dict:
    """Run the full Iter A smoke pipeline. Returns the in-memory report dict."""
    assert_timeline_present(cfg.timeline)
    base = timeline_dir(cfg.timeline)

    print(f"[1/6] Loading parquet from {base} ...")
    samples = pd.read_parquet(base / "samples.parquet")
    events = pd.read_parquet(base / "events.parquet")
    gta_fp = base / "ground_truth_anomaly.parquet"
    if not gta_fp.is_file():
        sys.exit(
            f"ERROR: {gta_fp} missing — cannot label anomaly windows.\n"
            f"Smoke eval requires anomaly ground truth (e.g. timeline_iter_b)."
        )
    ground_truth_anomaly = pd.read_parquet(gta_fp)
    print(
        f"       samples: {len(samples):,} rows, events: {len(events):,} rows, "
        f"GT anomalies: {len(ground_truth_anomaly)} entries"
    )

    print(f"[2/6] Aggregating windows (W={cfg.window_s}s, slide={cfg.slide_s}s) ...")
    feats = aggregate_windows(
        samples, events, WindowConfig(window_s=cfg.window_s, slide_s=cfg.slide_s)
    )
    print(f"       windows: {len(feats):,}")

    print("[3/6] Labelling windows from ground_truth_anomaly ...")
    labelled = label_windows(feats, ground_truth_anomaly)
    n_pos = int(labelled["label_anomaly"].sum())
    prev = n_pos / len(labelled) if len(labelled) else 0.0
    print(f"       positive windows: {n_pos:,}  (prevalence: {prev:.4f})")
    per_id_counts = {
        c.removeprefix("label_"): int(labelled[c].sum())
        for c in anomaly_label_columns(labelled)
    }
    print(f"       per-anomaly: {per_id_counts}")

    print("[4/6] Train/eval split ...")
    feat_cols = feature_columns()
    train_mask = (
        (labelled["phase_id"] == cfg.train_phase_id)
        & (labelled["t_end_s"] <= cfg.train_max_time_s)
    )
    train_df = labelled[train_mask].reset_index(drop=True)
    if len(train_df) == 0:
        sys.exit(
            f"ERROR: zero train windows with phase_id={cfg.train_phase_id} and "
            f"t_end_s<={cfg.train_max_time_s}. Adjust --train-* args."
        )
    # Sanity: train set should be label-free (purely normal)
    n_pos_train = int(train_df["label_anomaly"].sum())
    print(
        f"       train windows: {len(train_df):,}  "
        f"(of which positive: {n_pos_train}; should be 0)"
    )

    print("[5/6] Fitting detectors + scoring full table ...")
    detectors = all_detectors()
    score_table = labelled.copy()
    for det in detectors:
        det.fit(train_df[feat_cols])
        score_table[f"score_{det.name}"] = det.score_samples(score_table[feat_cols])
        # Per-detector flag threshold: top `flag_frac` of TRAIN scores
        train_scores = det.score_samples(train_df[feat_cols])
        thr = float(np.quantile(train_scores, 1.0 - cfg.flag_frac))
        score_table[f"flag_{det.name}"] = (score_table[f"score_{det.name}"] >= thr).astype(int)
        print(f"       {det.name}: flag threshold = {thr:.4f}")

    print("[6/6] Computing per-phase + per-anomaly metrics ...")
    per_phase = _per_phase_metrics(score_table, detectors)
    per_anom = _per_anomaly_metrics(score_table, detectors)
    overall = _overall_metrics(score_table, detectors)

    # Write artifacts
    out_dir = DATA_PROC / f"anomaly_smoke_{cfg.out_label or cfg.timeline}"
    out_dir.mkdir(parents=True, exist_ok=True)
    per_phase.to_csv(out_dir / "per_phase_metrics.csv", index=False)
    per_anom.to_csv(out_dir / "per_anomaly_metrics.csv", index=False)
    overall.to_csv(out_dir / "overall_metrics.csv", index=False)
    _save_summary_plot(per_phase, per_anom, cfg, out_dir / "smoke_summary.png")
    with open(out_dir / "smoke_config.json", "w") as f:
        json.dump(asdict(cfg), f, indent=2)

    print("\n=== Per-phase metrics ===")
    print(per_phase.to_string(index=False))
    print("\n=== Per-anomaly metrics ===")
    print(per_anom.to_string(index=False))
    print("\n=== Overall ===")
    print(overall.to_string(index=False))

    acceptance = _acceptance_check(per_phase, per_anom, cfg)
    print("\n=== Acceptance ===")
    for k, v in acceptance.items():
        marker = "PASS" if v else "FAIL"
        print(f"  [{marker}] {k}")

    print(f"\nArtifacts written to: {out_dir}")
    return {
        "out_dir": str(out_dir),
        "per_phase": per_phase.to_dict(orient="records"),
        "per_anomaly": per_anom.to_dict(orient="records"),
        "overall": overall.to_dict(orient="records"),
        "acceptance": acceptance,
    }


# -------------------------------------------------------------------------
# Metric helpers
# -------------------------------------------------------------------------

def _per_phase_metrics(score_table: pd.DataFrame, detectors: list) -> pd.DataFrame:
    rows = []
    for phase_id, g in score_table.groupby("phase_id"):
        for det in detectors:
            y = g["label_anomaly"].to_numpy()
            s = g[f"score_{det.name}"].to_numpy()
            flag = g[f"flag_{det.name}"].to_numpy()
            n_pos = int(y.sum())
            n_neg = int(len(y) - n_pos)
            # FPR on negative windows = false-flag rate
            fpr = float(flag[y == 0].mean()) if n_neg > 0 else float("nan")
            tpr = float(flag[y == 1].mean()) if n_pos > 0 else float("nan")
            pr_auc = float(average_precision_score(y, s)) if n_pos > 0 and n_pos < len(y) else float("nan")
            roc_auc = float(roc_auc_score(y, s)) if n_pos > 0 and n_pos < len(y) else float("nan")
            rows.append(
                {
                    "phase_id": int(phase_id),
                    "scenario_name": g["scenario_name"].iloc[0],
                    "detector": det.name,
                    "n_windows": int(len(y)),
                    "n_pos": n_pos,
                    "n_neg": n_neg,
                    "tpr": tpr,
                    "fpr": fpr,
                    "pr_auc": pr_auc,
                    "roc_auc": roc_auc,
                }
            )
    return pd.DataFrame(rows)


def _per_anomaly_metrics(score_table: pd.DataFrame, detectors: list) -> pd.DataFrame:
    rows = []
    for aid_col in anomaly_label_columns(score_table):
        aid = aid_col.removeprefix("label_")
        # Restrict to the phase that holds this anomaly + earlier baseline
        # windows for a meaningful PR-AUC (otherwise the negative class is
        # dominated by drift windows which the detector legitimately
        # confuses with anomalies).
        anom_phase_ids = score_table.loc[score_table[aid_col] == 1, "phase_id"].unique().tolist()
        if not anom_phase_ids:
            continue
        scope = score_table[score_table["phase_id"].isin(anom_phase_ids)]
        for det in detectors:
            y = scope[aid_col].to_numpy()
            s = scope[f"score_{det.name}"].to_numpy()
            flag = scope[f"flag_{det.name}"].to_numpy()
            n_pos = int(y.sum())
            if n_pos == 0 or n_pos == len(y):
                continue
            rows.append(
                {
                    "anomaly_id": aid,
                    "phase_ids": ",".join(map(str, sorted(anom_phase_ids))),
                    "detector": det.name,
                    "n_pos": n_pos,
                    "n_neg": int(len(y) - n_pos),
                    "tpr": float(flag[y == 1].mean()),
                    "fpr": float(flag[y == 0].mean()),
                    "pr_auc": float(average_precision_score(y, s)),
                    "roc_auc": float(roc_auc_score(y, s)),
                }
            )
    return pd.DataFrame(rows)


def _overall_metrics(score_table: pd.DataFrame, detectors: list) -> pd.DataFrame:
    rows = []
    for det in detectors:
        y = score_table["label_anomaly"].to_numpy()
        s = score_table[f"score_{det.name}"].to_numpy()
        if y.sum() == 0:
            continue
        rows.append(
            {
                "detector": det.name,
                "n_pos": int(y.sum()),
                "n_neg": int(len(y) - y.sum()),
                "pr_auc": float(average_precision_score(y, s)),
                "roc_auc": float(roc_auc_score(y, s)),
            }
        )
    return pd.DataFrame(rows)


# -------------------------------------------------------------------------
# Plot + acceptance
# -------------------------------------------------------------------------

def _save_summary_plot(
    per_phase: pd.DataFrame,
    per_anom: pd.DataFrame,
    cfg: SmokeConfig,
    fp: Path,
) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))

    # Panel 1: FPR per phase per detector (drift-degradation panel)
    ax = axes[0]
    for det, g in per_phase.groupby("detector"):
        g = g.sort_values("phase_id")
        ax.plot(g["phase_id"], g["fpr"], marker="o", label=det)
    ax.axhline(cfg.flag_frac, color="grey", ls="--", label=f"target ({cfg.flag_frac:.0%})")
    ax.set_xlabel("phase_id")
    ax.set_ylabel("False-flag rate (FPR)")
    ax.set_title("Drift-degradation panel:\nFPR per phase per detector")
    ax.legend()
    ax.grid(alpha=0.3)

    # Panel 2: TPR per phase per detector
    ax = axes[1]
    for det, g in per_phase.groupby("detector"):
        g = g.sort_values("phase_id")
        ax.plot(g["phase_id"], g["tpr"], marker="o", label=det)
    ax.set_xlabel("phase_id")
    ax.set_ylabel("True-positive rate (recall)")
    ax.set_title("Recall per phase per detector\n(NaN = no positives in phase)")
    ax.legend()
    ax.grid(alpha=0.3)

    # Panel 3: PR-AUC per anomaly per detector
    ax = axes[2]
    if not per_anom.empty:
        pivot = per_anom.pivot(index="anomaly_id", columns="detector", values="pr_auc")
        pivot.plot.bar(ax=ax, rot=0)
    ax.set_xlabel("anomaly_id")
    ax.set_ylabel("PR-AUC")
    ax.set_title("Per-anomaly PR-AUC by detector\n(scoped to anomaly's own phase)")
    ax.grid(alpha=0.3, axis="y")

    fig.suptitle(
        f"Phase 5 Iter A — anomaly smoke ({cfg.timeline})", fontsize=12, y=1.02
    )
    fig.tight_layout()
    fig.savefig(fp, dpi=130, bbox_inches="tight")
    plt.close(fig)


def _acceptance_check(
    per_phase: pd.DataFrame,
    per_anom: pd.DataFrame,
    cfg: SmokeConfig,
) -> dict[str, bool]:
    """Apply the two Iter A acceptance criteria from docs/plan.md."""
    # Criterion 1: PR-AUC > 0.1 on >= 2 of 4 anomaly types (across any detector)
    if per_anom.empty:
        n_aids_pass = 0
    else:
        # best detector per anomaly_id
        best = per_anom.groupby("anomaly_id")["pr_auc"].max()
        n_aids_pass = int((best > 0.1).sum())
    crit1 = n_aids_pass >= 2

    # Criterion 2: FPR_drift / FPR_baseline > 2 for at least one detector
    # Baseline phases = baseline scenario_name. Drift phases = drift_* names.
    crit2 = False
    for det, g in per_phase.groupby("detector"):
        base_fpr = g.loc[g["scenario_name"] == "baseline", "fpr"]
        drift_fpr = g.loc[g["scenario_name"].str.startswith("drift_"), "fpr"]
        base_mean = float(base_fpr.dropna().mean()) if not base_fpr.empty else float("nan")
        drift_mean = float(drift_fpr.dropna().mean()) if not drift_fpr.empty else float("nan")
        if np.isfinite(base_mean) and np.isfinite(drift_mean) and base_mean > 0:
            if drift_mean / base_mean > 2.0:
                crit2 = True
                break
    return {
        "C1 PR-AUC > 0.1 on at least 2 anomaly types": crit1,
        "C2 FPR_drift > 2 x FPR_baseline (some detector)": crit2,
    }


if __name__ == "__main__":
    run_smoke(_parse_args())
