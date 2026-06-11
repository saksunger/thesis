"""Phase 6 drift-detection full benchmark.

Pipeline (single CLI invocation):

1. Load `samples.parquet`, `events.parquet`, `ground_truth_drift.parquet`
   for the requested timeline (default `timeline_medium`).
2. Build 6 univariate streams via :func:`analysis.drift.streams.build_streams`.
3. Label streams per (stream, t) via :func:`label_streams`.
4. Run each of the 7 benchmark detectors on each stream in PARALLEL
   (each detector fresh per stream, no cross-stream state). Record
   firing timestamps.
5. Per (drift_instance x detector x stream): detection latency.
6. Per (detector x stream): baseline FPR-per-second.
7. Bootstrap CI on median latency per (detector x drift_type) summary.
8. Dump CSV tables + a 2-panel summary figure to
   ``data/processed/drift_benchmark_<timeline>/``.

Outputs:
    per_drift_latency.csv          long-form (drift_instance x detector x stream)
    table_6_1_latency_summary.csv  best-stream median latency per (detector x drift_type) with 95% CI
    table_6_2_miss_rate.csv        miss-rate per (detector x drift_type)
    table_6_3_baseline_fpr.csv     baseline FPR per (detector x stream)
    detection_log.csv              raw firing events
    drift_detection_heatmap.png    Chapter 6 moneyshot (median latency heatmap)
    benchmark_summary.json         config + winning detector + elapsed
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from analysis.common.paths import DATA_PROC, assert_timeline_present, timeline_dir
from analysis.common.plotstyle import apply_thesis_style
from analysis.drift.detectors import benchmark_detectors
from analysis.drift.labels import label_streams
from analysis.drift.metrics import (
    bootstrap_latency_ci,
    detection_latency,
    fpr_per_detector_stream,
)
from analysis.drift.streams import STREAM_NAMES, StreamConfig, build_streams


# Map drift_id prefix -> drift_type (humanised). Stable across runs.
DRIFT_ID_TO_TYPE: dict[str, str] = {
    "D-1": "traffic_shift",
    "D-2": "channel_swap",
    "D-3": "mobility",
    "D-4": "reconfig",
}


@dataclass
class BenchmarkConfig:
    timeline: str = "timeline_medium"
    cadence_s: float = 1.0
    roll_window_s: float = 10.0
    bootstrap_resamples: int = 1000
    bootstrap_alpha: float = 0.05
    out_label: str | None = None
    random_state: int = 42


def _parse_args(argv: list[str] | None = None) -> BenchmarkConfig:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--timeline", default="timeline_medium")
    p.add_argument("--cadence-s", type=float, default=1.0)
    p.add_argument("--roll-window-s", type=float, default=10.0)
    p.add_argument("--bootstrap-resamples", type=int, default=1000)
    p.add_argument("--bootstrap-alpha", type=float, default=0.05)
    p.add_argument("--out-label", default=None)
    p.add_argument("--random-state", type=int, default=42)
    a = p.parse_args(argv)
    return BenchmarkConfig(
        timeline=a.timeline,
        cadence_s=a.cadence_s,
        roll_window_s=a.roll_window_s,
        bootstrap_resamples=a.bootstrap_resamples,
        bootstrap_alpha=a.bootstrap_alpha,
        out_label=a.out_label,
        random_state=a.random_state,
    )


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def _load_inputs(cfg: BenchmarkConfig):
    assert_timeline_present(cfg.timeline)
    base = timeline_dir(cfg.timeline)
    print(f"[load] {base}")
    samples = pd.read_parquet(base / "samples.parquet")
    events = pd.read_parquet(base / "events.parquet")
    gtd_fp = base / "ground_truth_drift.parquet"
    if not gtd_fp.is_file():
        sys.exit(f"ERROR: {gtd_fp} missing — drift benchmark needs ground_truth_drift.")
    gtd = pd.read_parquet(gtd_fp)
    print(f"       samples={len(samples):,} events={len(events):,} gtd={len(gtd)} entries")
    return samples, events, gtd


def _run_detectors_on_streams(
    streams_wide: pd.DataFrame,
) -> pd.DataFrame:
    """Run every detector on every stream; return firing log.

    Each detector instance is created fresh PER stream so state from
    one stream does not leak into another.
    """
    detections = []
    for stream_name in STREAM_NAMES:
        if stream_name not in streams_wide.columns:
            continue
        series = streams_wide[stream_name].to_numpy()
        times = streams_wide.index.to_numpy()
        for det in benchmark_detectors():
            t0 = time.perf_counter()
            firings = []
            for t, v in zip(times, series):
                if det.update(v):
                    firings.append(float(t))
            elapsed = time.perf_counter() - t0
            print(
                f"  {stream_name:<22} {det.name:<14} "
                f"-> {len(firings):>3} firings ({elapsed:.2f}s)"
            )
            for ft in firings:
                detections.append(
                    {"stream_name": stream_name, "detector": det.name, "time_s": ft}
                )
    return pd.DataFrame(detections)


def _summarise_per_drift_type(
    per_drift: pd.DataFrame,
    cfg: BenchmarkConfig,
) -> pd.DataFrame:
    """Build Table 6.1: median latency [95 % CI] per (detector × drift_type).

    For each (detector × drift_type), pick the BEST stream (lowest
    median latency) and report its bootstrap CI. Other streams are
    available in `per_drift_latency.csv` for the appendix.
    """
    df = per_drift.copy()
    df["drift_type"] = df["drift_id"].map(DRIFT_ID_TO_TYPE).fillna("other")

    rows = []
    for (det, dtype), g in df.groupby(["detector", "drift_type"]):
        # Per-stream median latency (NaNs = misses; dropped)
        per_stream = (
            g.groupby("stream_name")["latency_s"]
            .apply(lambda x: np.nan if x.dropna().empty else np.median(x.dropna()))
        )
        if per_stream.dropna().empty:
            rows.append(
                {
                    "detector": det,
                    "drift_type": dtype,
                    "best_stream": "<none>",
                    "n_drifts": int(g["drift_instance"].nunique()),
                    "n_detected": int((g["missed"] == 0).sum()),
                    "miss_rate": float((g["missed"] == 1).mean()),
                    "median_latency_s": float("nan"),
                    "ci_low_s": float("nan"),
                    "ci_high_s": float("nan"),
                    "ci_str": "missed all",
                }
            )
            continue
        best_stream = per_stream.dropna().idxmin()
        latencies = g.loc[g["stream_name"] == best_stream, "latency_s"].dropna().to_numpy()
        boot = bootstrap_latency_ci(
            latencies,
            n_resamples=cfg.bootstrap_resamples,
            alpha=cfg.bootstrap_alpha,
            random_state=cfg.random_state,
        )
        rows.append(
            {
                "detector": det,
                "drift_type": dtype,
                "best_stream": best_stream,
                "n_drifts": int(g["drift_instance"].nunique()),
                "n_detected": int((g["missed"] == 0).sum()),
                "miss_rate": float((g["missed"] == 1).mean()),
                "median_latency_s": boot.median,
                "ci_low_s": boot.ci_low,
                "ci_high_s": boot.ci_high,
                "ci_str": boot.as_str(decimals=1),
            }
        )
    return pd.DataFrame(rows)


def _miss_rate_table(per_drift: pd.DataFrame) -> pd.DataFrame:
    df = per_drift.copy()
    df["drift_type"] = df["drift_id"].map(DRIFT_ID_TO_TYPE).fillna("other")
    # Per drift_instance the BEST stream: if detected by ANY stream, count as detected
    instance_status = df.groupby(["detector", "drift_type", "drift_instance"])["missed"].min()
    inst_df = instance_status.reset_index()
    rows = []
    for (det, dtype), g in inst_df.groupby(["detector", "drift_type"]):
        n = len(g)
        n_missed = int((g["missed"] == 1).sum())
        rows.append(
            {
                "detector": det,
                "drift_type": dtype,
                "n_drifts": int(n),
                "n_missed": n_missed,
                "miss_rate": float(n_missed / n) if n > 0 else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def _save_heatmap(
    summary: pd.DataFrame,
    fpr_table: pd.DataFrame,
    cfg: BenchmarkConfig,
    fp: Path,
) -> None:
    """Chapter 6 moneyshot: 2-panel heatmap (detector x drift_type).

    Panel A: median detection latency (lower=greener).
    Panel B: max baseline FPR per detector across streams (lower=greener).

    Together they tell the precision/recall trade-off story: batch
    detectors (MMD, Energy) tend to win Panel A but lose Panel B; ADWIN
    tends to win Panel B with moderate Panel A.

    NaN cells (missed all) are rendered grey so reviewers immediately
    see (detector x drift_type) combos the detector cannot handle.
    """
    if summary.empty:
        return
    pivot_latency = summary.pivot(
        index="detector", columns="drift_type", values="median_latency_s"
    )
    order = ["traffic_shift", "channel_swap", "mobility", "reconfig"]
    cols = [c for c in order if c in pivot_latency.columns]
    pivot_latency = pivot_latency[cols]

    # Per-detector max baseline FPR (worst-stream)
    if not fpr_table.empty:
        max_fpr = fpr_table.groupby("detector")["fpr_per_s"].max()
    else:
        max_fpr = pd.Series(dtype=float)

    apply_thesis_style()
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 5.2),
                             gridspec_kw={"width_ratios": [3, 1]})

    # ------- Panel A: latency heatmap -------
    ax = axes[0]
    data = pivot_latency.to_numpy()
    masked = np.ma.masked_invalid(data)
    im = ax.imshow(masked, cmap="YlGn_r", vmin=0, vmax=60, aspect="auto")
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels(cols, rotation=20, ha="right")
    ax.set_yticks(range(len(pivot_latency.index)))
    ax.set_yticklabels(pivot_latency.index)
    ax.set_title("Median detection latency (s)")
    for i, det in enumerate(pivot_latency.index):
        for j, _ in enumerate(cols):
            val = data[i, j]
            if np.isnan(val):
                ax.add_patch(plt.Rectangle((j - 0.5, i - 0.5), 1, 1,
                                           fill=True, color="lightgray"))
                ax.text(j, i, "missed", ha="center", va="center",
                        color="dimgray", fontsize=11, weight="bold")
            else:
                color = "white" if val < 25 else "black"
                ax.text(j, i, f"{val:.1f}", ha="center", va="center",
                        color=color, fontsize=12, weight="bold")
    cb = plt.colorbar(im, ax=ax, shrink=0.85, label="latency (s, lower=better)")

    # ------- Panel B: per-detector max baseline FPR -------
    ax = axes[1]
    detectors_order = list(pivot_latency.index)
    fpr_vals = np.array(
        [max_fpr.get(d, np.nan) for d in detectors_order], dtype=float
    ).reshape(-1, 1)
    fpr_masked = np.ma.masked_invalid(fpr_vals)
    im2 = ax.imshow(fpr_masked, cmap="YlOrRd", vmin=0, vmax=0.20, aspect="auto")
    ax.set_xticks([0])
    ax.set_xticklabels(["max FPR\nacross streams"], fontsize=11)
    ax.set_yticks(range(len(detectors_order)))
    ax.set_yticklabels([""] * len(detectors_order))
    ax.set_title("Baseline false-alarm rate")
    for i, det in enumerate(detectors_order):
        val = fpr_vals[i, 0]
        if np.isnan(val):
            ax.text(0, i, "n/a", ha="center", va="center",
                    color="dimgray", fontsize=11)
        else:
            color = "black" if val < 0.10 else "white"
            ax.text(0, i, f"{val:.3f}", ha="center", va="center",
                    color=color, fontsize=12, weight="bold")
    plt.colorbar(im2, ax=ax, shrink=0.85, label="FPR /s (lower=better)")

    fig.suptitle(
        f"Phase 6 - drift detection benchmark "
        f"({cfg.timeline}, cadence={cfg.cadence_s}s, rolling W={cfg.roll_window_s}s)",
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(fp, dpi=200, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_benchmark(cfg: BenchmarkConfig) -> dict:
    t_start = time.perf_counter()
    out_dir = DATA_PROC / f"drift_benchmark_{cfg.out_label or cfg.timeline}"
    out_dir.mkdir(parents=True, exist_ok=True)

    samples, events, gtd = _load_inputs(cfg)

    print(f"\n=== Building streams (cadence={cfg.cadence_s}s, "
          f"rolling W={cfg.roll_window_s}s) ===")
    streams_long = build_streams(
        samples, events,
        StreamConfig(cadence_s=cfg.cadence_s, roll_window_s=cfg.roll_window_s),
    )
    print(f"      streams_long rows={len(streams_long):,}")

    print("\n=== Labelling streams from ground_truth_drift ===")
    labelled = label_streams(streams_long, gtd)
    n_pos = int(labelled["label_drift"].sum())
    print(f"      positive (stream, t) rows: {n_pos:,} "
          f"(prevalence={n_pos / max(len(labelled), 1):.4f})")

    print("\n=== Running 7 detectors on 6 streams (42 runs) ===")
    # Pivot to wide for per-stream iteration; preserve time_s as index
    # via the first stream (all streams share the same time grid).
    grid_df = labelled[labelled["stream_name"] == STREAM_NAMES[0]][["time_s"]]
    wide = pd.DataFrame({"time_s": grid_df["time_s"].to_numpy()}).set_index("time_s")
    for sn in STREAM_NAMES:
        s = labelled.loc[labelled["stream_name"] == sn, ["time_s", "value"]].set_index("time_s")
        wide[sn] = s["value"]
    detections = _run_detectors_on_streams(wide)
    print(f"\n      total firings logged: {len(detections):,}")

    print("\n=== Computing per-(drift × detector × stream) latency ===")
    per_drift = detection_latency(detections, gtd)
    per_drift.to_csv(out_dir / "per_drift_latency.csv", index=False)
    detections.to_csv(out_dir / "detection_log.csv", index=False)

    print("=== Summarising per (detector × drift_type) ===")
    summary = _summarise_per_drift_type(per_drift, cfg)
    summary.to_csv(out_dir / "table_6_1_latency_summary.csv", index=False)

    miss_table = _miss_rate_table(per_drift)
    miss_table.to_csv(out_dir / "table_6_2_miss_rate.csv", index=False)

    print("=== Computing baseline FPR per (detector × stream) ===")
    fpr_table = fpr_per_detector_stream(detections, streams_long)
    fpr_table.to_csv(out_dir / "table_6_3_baseline_fpr.csv", index=False)

    print(f"=== Saving heatmap to {out_dir/'drift_detection_heatmap.png'} ===")
    _save_heatmap(summary, fpr_table, cfg, out_dir / "drift_detection_heatmap.png")

    # Winner = detector with lowest mean latency across drift types (NaN -> max penalty)
    if not summary.empty:
        # Treat misses as 60s penalty (size of a phase) for ranking
        summary_rank = summary.copy()
        summary_rank["lat_for_rank"] = summary_rank["median_latency_s"].fillna(60.0)
        winner = (
            summary_rank.groupby("detector")["lat_for_rank"].mean().idxmin()
        )
    else:
        winner = "<none>"

    elapsed = round(time.perf_counter() - t_start, 1)
    summary_json = {
        "config": asdict(cfg),
        "elapsed_s": elapsed,
        "winning_detector": winner,
        "out_dir": str(out_dir),
        "n_detections": int(len(detections)),
        "n_drifts": int(len(gtd)),
        "n_streams": len(STREAM_NAMES),
    }
    with open(out_dir / "benchmark_summary.json", "w") as f:
        json.dump(summary_json, f, indent=2, default=str)

    # Pretty-print headline tables
    print("\n=== Table 6.1: median detection latency [95 % CI] per (detector × drift_type) ===")
    if not summary.empty:
        print(
            summary.pivot(index="detector", columns="drift_type", values="ci_str").to_string()
        )
    print("\n=== Table 6.2: miss rate per (detector × drift_type) ===")
    if not miss_table.empty:
        print(
            miss_table.pivot(index="detector", columns="drift_type", values="miss_rate")
            .round(2).to_string()
        )
    print("\n=== Table 6.3: baseline FPR per (detector × stream)  [false alarms / s] ===")
    if not fpr_table.empty:
        print(
            fpr_table.pivot(index="detector", columns="stream_name", values="fpr_per_s")
            .round(4).to_string()
        )
    print(f"\n[winner] best detector by mean latency: {winner}")
    print(f"Artifacts written to: {out_dir}")
    print(f"Total elapsed: {elapsed:.1f}s")

    return {
        "summary": summary_json,
        "per_drift": per_drift.to_dict(orient="records"),
        "table_6_1": summary.to_dict(orient="records"),
        "table_6_2": miss_table.to_dict(orient="records"),
        "table_6_3": fpr_table.to_dict(orient="records"),
        "winner": winner,
    }


if __name__ == "__main__":
    run_benchmark(_parse_args())
