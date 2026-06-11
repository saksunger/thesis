"""Phase 7 Iter A — drift-aware adaptive anomaly-detection benchmark.

Compares four retraining strategies on the simulated timeline:

    1. STATIC                   - fit once on baseline phases, never refit.
    2. PERIODIC(180 s)          - refit every 180 s of timeline time
                                  (naive pool, no anomaly filter).
    3. DRIFT-TRIGGERED-NAIVE    - refit when ADWIN fires on HOSR/RLF,
                                  naive pool.
    4. DRIFT-TRIGGERED-FILTERED - same trigger as (3), but the retrain
                                  pool is filtered through the current
                                  detector's score (bottom 80 % kept)
                                  to defend against anomaly
                                  self-contamination (Yoon et al. 2021).

All four strategies share the same base detector (PCA-AE) and the same
warm-up training pool (phases 1..3, label=0). The comparison
isolates two orthogonal axes:

    when to retrain : strategy (static / periodic / drift-triggered)
    how to retrain  : pool filter (naive / self-supervised bottom-q)

The naive vs filtered drift-triggered pair is the headline ablation
(Table 7.2 in the thesis).

Pipeline (single CLI invocation):

1. Load `samples.parquet`, `events.parquet`, `ground_truth_*.parquet`
   for the requested timeline (default `timeline_medium`).
2. Aggregate per-(UE, window) features (`WindowConfig(5s, slide=1s)`).
3. Label windows (Phase 5 cell-conditional rule).
4. Build the 6 univariate streams (Phase 6 1Hz cadence).
5. For each of 3 strategies, run :class:`AdaptiveOrchestrator`.
6. Per strategy: compute sliding PR-AUC, overall PR-AUC, drift-phase
   PR-AUC, and cost-ledger summary.
7. Emit CSVs + 2-panel figure under
   ``data/processed/adaptive_benchmark_<timeline>/``.

Acceptance criteria (see ``docs/plan.md`` Phase 7):
    C2 : drift-triggered overall PR-AUC >= periodic.
    C3 : drift-triggered drift-phase PR-AUC >= static + 0.10.
    C4 : cost ledger reports #refits, samples used, CPU sec.
    C5 : 3-line PR-AUC-over-time figure produced.

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

from analysis.adaptive.metrics import (
    SlidingConfig,
    cost_summary,
    drift_phase_pr_auc,
    overall_pr_auc,
    sliding_pr_auc,
)
from analysis.adaptive.orchestrator import (
    AdaptiveOrchestrator,
    OrchestratorConfig,
    RunResult,
)
from analysis.adaptive.strategies import (
    DriftTriggeredStrategy,
    PeriodicStrategy,
    RetrainStrategy,
    StaticStrategy,
)
from analysis.anomaly.detectors import PCAReconErrDetector
from analysis.anomaly.features import (
    WindowConfig,
    aggregate_windows,
    feature_columns,
)
from analysis.anomaly.labels import label_windows
from analysis.common.paths import DATA_PROC, assert_timeline_present, timeline_dir
from analysis.common.plotstyle import apply_thesis_style
from analysis.drift.detectors import ADWINDetector
from analysis.drift.labels import label_streams
from analysis.drift.streams import STREAM_NAMES, StreamConfig, build_streams, pivot_streams


# ---------------------------------------------------------------------------
# CLI + config
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkConfig:
    timeline: str = "timeline_medium"
    window_s: float = 5.0
    slide_s: float = 1.0
    warmup_max_phase_id: int = 3
    retrain_window_s: float = 120.0
    periodic_period_s: float = 180.0
    drift_cooldown_s: float = 30.0
    watch_streams: tuple[str, ...] = ("hosr_rolling", "rlf_rate_rolling")
    eval_window_s: float = 120.0
    eval_stride_s: float = 30.0
    cadence_s: float = 1.0
    roll_window_s: float = 10.0
    adwin_delta: float = 0.01
    # Self-supervised retrain-pool filter quantile for the FILTERED
    # drift-triggered variant. 0.8 = keep bottom 80 % of pool by current
    # detector's score (top 20 % "model-flagged" rows dropped before
    # refitting).
    filter_quantile: float = 0.8
    out_label: str | None = None
    random_state: int = 42


def _parse_args(argv: list[str] | None = None) -> BenchmarkConfig:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--timeline", default="timeline_medium")
    p.add_argument("--window-s", type=float, default=5.0)
    p.add_argument("--slide-s", type=float, default=1.0)
    p.add_argument("--warmup-max-phase-id", type=int, default=3)
    p.add_argument("--retrain-window-s", type=float, default=120.0)
    p.add_argument("--periodic-period-s", type=float, default=180.0)
    p.add_argument("--drift-cooldown-s", type=float, default=30.0)
    p.add_argument(
        "--watch-streams",
        type=str,
        default="hosr_rolling,rlf_rate_rolling",
        help="Comma-separated stream names the drift trigger watches.",
    )
    p.add_argument("--eval-window-s", type=float, default=120.0)
    p.add_argument("--eval-stride-s", type=float, default=30.0)
    p.add_argument("--cadence-s", type=float, default=1.0)
    p.add_argument("--roll-window-s", type=float, default=10.0)
    p.add_argument("--adwin-delta", type=float, default=0.01)
    p.add_argument("--filter-quantile", type=float, default=0.8)
    p.add_argument("--out-label", default=None)
    p.add_argument("--random-state", type=int, default=42)
    a = p.parse_args(argv)
    return BenchmarkConfig(
        timeline=a.timeline,
        window_s=a.window_s,
        slide_s=a.slide_s,
        warmup_max_phase_id=a.warmup_max_phase_id,
        retrain_window_s=a.retrain_window_s,
        periodic_period_s=a.periodic_period_s,
        drift_cooldown_s=a.drift_cooldown_s,
        watch_streams=tuple(s.strip() for s in a.watch_streams.split(",") if s.strip()),
        eval_window_s=a.eval_window_s,
        eval_stride_s=a.eval_stride_s,
        cadence_s=a.cadence_s,
        roll_window_s=a.roll_window_s,
        adwin_delta=a.adwin_delta,
        filter_quantile=a.filter_quantile,
        out_label=a.out_label,
        random_state=a.random_state,
    )


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_inputs(cfg: BenchmarkConfig):
    assert_timeline_present(cfg.timeline)
    base = timeline_dir(cfg.timeline)
    print(f"[load] {base}", flush=True)
    samples = pd.read_parquet(base / "samples.parquet")
    events = pd.read_parquet(base / "events.parquet")
    gta_fp = base / "ground_truth_anomaly.parquet"
    gtd_fp = base / "ground_truth_drift.parquet"
    if not gta_fp.is_file():
        sys.exit(f"ERROR: {gta_fp} missing — adaptive benchmark needs anomaly GT.")
    if not gtd_fp.is_file():
        sys.exit(f"ERROR: {gtd_fp} missing — adaptive benchmark needs drift GT.")
    gta = pd.read_parquet(gta_fp)
    gtd = pd.read_parquet(gtd_fp)
    print(
        f"       samples={len(samples):,} events={len(events):,} "
        f"gta={len(gta)} gtd={len(gtd)}",
        flush=True,
    )
    return samples, events, gta, gtd


def _build_windows(samples: pd.DataFrame, events: pd.DataFrame, gta: pd.DataFrame,
                   cfg: BenchmarkConfig) -> pd.DataFrame:
    print(f"[windows] aggregating (W={cfg.window_s}s, slide={cfg.slide_s}s)...",
          flush=True)
    feats = aggregate_windows(
        samples, events, WindowConfig(window_s=cfg.window_s, slide_s=cfg.slide_s)
    )
    print(f"          windows={len(feats):,}", flush=True)
    labelled = label_windows(feats, gta)
    n_pos = int(labelled["label_anomaly"].sum())
    print(
        f"          positives={n_pos:,} "
        f"(prevalence={n_pos / max(len(labelled), 1):.4f})",
        flush=True,
    )
    return labelled


def _build_streams_wide(samples: pd.DataFrame, events: pd.DataFrame,
                        cfg: BenchmarkConfig) -> pd.DataFrame:
    print(f"[streams] building 6 streams (cadence={cfg.cadence_s}s, "
          f"rolling W={cfg.roll_window_s}s)...", flush=True)
    streams_long = build_streams(
        samples, events,
        StreamConfig(cadence_s=cfg.cadence_s, roll_window_s=cfg.roll_window_s),
    )
    wide = pivot_streams(streams_long)
    print(f"          streams_wide shape={wide.shape}", flush=True)
    return wide


# ---------------------------------------------------------------------------
# Strategies + run-spec wiring
# ---------------------------------------------------------------------------

@dataclass
class _RunSpec:
    """A single run = (display_name, strategy_factory, filter_quantile)."""
    display_name: str
    strategy_factory: "callable"
    filter_quantile: float | None


def _build_run_specs(cfg: BenchmarkConfig) -> list[_RunSpec]:
    """Return the four Iter A run specs (order matches the figure)."""
    return [
        _RunSpec(
            display_name="static",
            strategy_factory=lambda: StaticStrategy(),
            filter_quantile=None,
        ),
        _RunSpec(
            display_name="periodic",
            strategy_factory=lambda: PeriodicStrategy(period_s=cfg.periodic_period_s),
            filter_quantile=None,
        ),
        _RunSpec(
            display_name="drift_triggered_naive",
            strategy_factory=lambda: DriftTriggeredStrategy(
                cooldown_s=cfg.drift_cooldown_s,
                watch_streams=list(cfg.watch_streams),
            ),
            filter_quantile=None,
        ),
        _RunSpec(
            display_name="drift_triggered_filtered",
            strategy_factory=lambda: DriftTriggeredStrategy(
                cooldown_s=cfg.drift_cooldown_s,
                watch_streams=list(cfg.watch_streams),
            ),
            filter_quantile=cfg.filter_quantile,
        ),
    ]


def _drift_phase_ids(gtd: pd.DataFrame, windows: pd.DataFrame) -> list[int]:
    """Return the set of phase_ids that contain any drift instance.

    The orchestrator's per-window table inherits ``phase_id`` from the
    feature builder, so we can use it directly as the drift-phase
    filter for :func:`drift_phase_pr_auc`.
    """
    if gtd is None or gtd.empty:
        return []
    drift_phase_set: set[int] = set()
    # ground_truth_drift has start_time_s / end_time_s; map them to phase_id
    # via the windows table's (t_start_s, t_end_s, phase_id) mapping.
    if windows.empty:
        return []
    wt = windows[["t_mid_s", "phase_id"]].sort_values("t_mid_s").reset_index(drop=True)
    t_mid = wt["t_mid_s"].to_numpy()
    phase_ids = wt["phase_id"].to_numpy()
    for _, row in gtd.iterrows():
        lo, hi = float(row["start_time_s"]), float(row["end_time_s"])
        mask = (t_mid >= lo) & (t_mid < hi)
        for p in np.unique(phase_ids[mask]):
            drift_phase_set.add(int(p))
    return sorted(drift_phase_set)


# ---------------------------------------------------------------------------
# Aggregation across strategy runs
# ---------------------------------------------------------------------------

def _summarise_runs(
    runs: list[RunResult],
    cfg: BenchmarkConfig,
    drift_phase_ids: list[int],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build Table 7.1 (overall PR-AUC + cost) and the sliding PR-AUC table.

    Returns:
        (summary_table, sliding_table, retrain_log_long)
    """
    summary_rows: list[dict] = []
    sliding_chunks: list[pd.DataFrame] = []
    retrain_chunks: list[pd.DataFrame] = []

    sliding_cfg = SlidingConfig(
        eval_window_s=cfg.eval_window_s,
        stride_s=cfg.eval_stride_s,
    )

    for r in runs:
        overall = overall_pr_auc(r.per_window)
        drift_phase = drift_phase_pr_auc(r.per_window, drift_phase_ids)
        cost = cost_summary(r.retrain_log)
        sliding = sliding_pr_auc(r.per_window, sliding_cfg)
        sliding["strategy"] = r.strategy_name
        sliding["base_detector"] = r.base_detector_name
        sliding_chunks.append(sliding)
        log = r.retrain_log.copy()
        log["strategy"] = r.strategy_name
        log["base_detector"] = r.base_detector_name
        retrain_chunks.append(log)
        summary_rows.append({
            "strategy": r.strategy_name,
            "base_detector": r.base_detector_name,
            "overall_pr_auc": overall,
            "drift_phase_pr_auc": drift_phase,
            **{f"cost__{k}": v for k, v in cost.items()},
        })

    summary_df = pd.DataFrame(summary_rows)
    sliding_df = pd.concat(sliding_chunks, ignore_index=True) if sliding_chunks else pd.DataFrame()
    retrain_df = pd.concat(retrain_chunks, ignore_index=True) if retrain_chunks else pd.DataFrame()
    return summary_df, sliding_df, retrain_df


# ---------------------------------------------------------------------------
# Figure
# ---------------------------------------------------------------------------

_STRATEGY_COLOURS: dict[str, str] = {
    "static":                    "#888888",
    "periodic":                  "#1f77b4",
    "drift_triggered_naive":     "#ff7f0e",
    "drift_triggered_filtered":  "#d62728",
}
_STRATEGY_ORDER: list[str] = [
    "static",
    "periodic",
    "drift_triggered_naive",
    "drift_triggered_filtered",
]


def _save_figure(
    summary_df: pd.DataFrame,
    sliding_df: pd.DataFrame,
    retrain_df: pd.DataFrame,
    gtd: pd.DataFrame,
    cfg: BenchmarkConfig,
    fp: Path,
) -> None:
    """Chapter 7 moneyshot: 2-panel figure.

    Panel A (large, left): PR-AUC over time for 3 strategies, with
                           drift-phase intervals shaded. Vertical
                           ticks at retrain events.
    Panel B (small, right): cost (CPU sec total) vs gain (overall PR-AUC).
    """
    apply_thesis_style()
    fig, axes = plt.subplots(
        1, 2, figsize=(11, 5.2), gridspec_kw={"width_ratios": [3, 1]}
    )

    # --- Panel A: sliding PR-AUC over time ---
    ax = axes[0]
    if not gtd.empty:
        for _, dr in gtd.iterrows():
            ax.axvspan(
                float(dr["start_time_s"]), float(dr["end_time_s"]),
                color="orange", alpha=0.10, lw=0,
            )
    for strat, g in sliding_df.groupby("strategy"):
        g = g.sort_values("t_center_s")
        colour = _STRATEGY_COLOURS.get(strat, "black")
        ax.plot(
            g["t_center_s"], g["pr_auc"],
            label=strat, color=colour, lw=2.0, alpha=0.95,
        )
    # Retrain ticks (small triangles along the top of panel A, per strategy)
    if not retrain_df.empty:
        ymax = float(np.nanmax(sliding_df["pr_auc"].to_numpy())) if not sliding_df.empty else 1.0
        ymax = ymax if np.isfinite(ymax) else 1.0
        for strat, g in retrain_df.groupby("strategy"):
            post = g[g["reason"] != "warmup"]
            colour = _STRATEGY_COLOURS.get(strat, "black")
            ax.scatter(
                post["t_s"], np.full(len(post), ymax * 1.02),
                marker="v", color=colour, s=30, zorder=5,
            )
    ax.set_xlabel("timeline time t (s)")
    ax.set_ylabel("sliding PR-AUC (window=120 s)")
    ax.set_title(
        f"Phase 7 - adaptive anomaly detection on {cfg.timeline}\n"
        f"(orange shading = drift-affected phases; triangles = retrain events)"
    )
    ax.legend(loc="upper right", framealpha=0.9)
    ax.set_ylim(-0.02, 1.02)
    ax.grid(alpha=0.3)

    # --- Panel B: cost vs gain ---
    ax = axes[1]
    if not summary_df.empty:
        in_order = [s for s in _STRATEGY_ORDER if s in summary_df["strategy"].tolist()]
        x_pos = np.arange(len(in_order))
        gains = [
            float(summary_df.loc[summary_df["strategy"] == s, "overall_pr_auc"].iloc[0])
            for s in in_order
        ]
        costs = [
            float(summary_df.loc[summary_df["strategy"] == s, "cost__fit_cpu_s_sum"].iloc[0])
            for s in in_order
        ]
        colours = [_STRATEGY_COLOURS.get(s, "black") for s in in_order]
        ax.bar(x_pos, gains, color=colours, alpha=0.85, edgecolor="black")
        ax.set_xticks(x_pos)
        ax.set_xticklabels(in_order, rotation=20, ha="right", fontsize=11)
        ax.set_ylabel("overall PR-AUC")
        ax.set_ylim(0, max(gains) * 1.25 if max(gains) > 0 else 1.0)
        # Annotate cost on top of each bar
        for xi, gain_val, cost_val in zip(x_pos, gains, costs):
            ax.text(
                xi, gain_val + 0.005,
                f"PR-AUC={gain_val:.3f}\nCPU={cost_val:.2f}s",
                ha="center", va="bottom", fontsize=10,
            )
        ax.set_title("Overall PR-AUC + CPU cost")
        ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    fig.savefig(fp, dpi=200, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Acceptance check
# ---------------------------------------------------------------------------

@dataclass
class AcceptanceVerdict:
    c2_pass: bool
    c2_msg: str
    c3_pass: bool
    c3_msg: str

    def as_dict(self) -> dict:
        return {
            "C2": {"pass": self.c2_pass, "msg": self.c2_msg},
            "C3": {"pass": self.c3_pass, "msg": self.c3_msg},
        }


def _check_acceptance(summary_df: pd.DataFrame) -> AcceptanceVerdict:
    """C2: drift_triggered_filtered overall PR-AUC >= periodic overall.
       C3: drift_triggered_filtered drift-phase PR-AUC >= static drift-phase + 0.10.

    The filtered drift-triggered variant is the Iter A headline (the
    naive variant is the contrast baseline that motivates the filter).
    """
    by_strat = summary_df.set_index("strategy")

    def _get(s: str, col: str) -> float:
        if s not in by_strat.index:
            return float("nan")
        return float(by_strat.loc[s, col])

    head = "drift_triggered_filtered"
    dt_overall = _get(head, "overall_pr_auc")
    pe_overall = _get("periodic", "overall_pr_auc")
    dt_drift = _get(head, "drift_phase_pr_auc")
    st_drift = _get("static", "drift_phase_pr_auc")

    c2_pass = np.isfinite(dt_overall) and np.isfinite(pe_overall) and (dt_overall >= pe_overall - 1e-6)
    c2_msg = f"{head} overall={dt_overall:.4f} vs periodic={pe_overall:.4f}"
    delta = dt_drift - st_drift if (np.isfinite(dt_drift) and np.isfinite(st_drift)) else float("nan")
    c3_pass = np.isfinite(delta) and (delta >= 0.10 - 1e-6)
    c3_msg = (
        f"{head} drift-phase={dt_drift:.4f} vs static drift-phase={st_drift:.4f} "
        f"(delta={delta:+.4f}, target>=+0.10)"
    )
    return AcceptanceVerdict(c2_pass=c2_pass, c2_msg=c2_msg, c3_pass=c3_pass, c3_msg=c3_msg)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_benchmark(cfg: BenchmarkConfig) -> dict:
    t_start = time.perf_counter()
    out_dir = DATA_PROC / f"adaptive_benchmark_{cfg.out_label or cfg.timeline}"
    out_dir.mkdir(parents=True, exist_ok=True)

    samples, events, gta, gtd = _load_inputs(cfg)
    windows = _build_windows(samples, events, gta, cfg)
    streams_wide = _build_streams_wide(samples, events, cfg)

    feat_cols = feature_columns()

    # Same base detector factory for every run
    def base_factory():
        return PCAReconErrDetector(random_state=cfg.random_state)

    # Per-stream drift detector factories (only for watched streams)
    drift_factories = {
        sn: (lambda d=cfg.adwin_delta: ADWINDetector(delta=d))
        for sn in cfg.watch_streams
        if sn in streams_wide.columns
    }
    if not drift_factories:
        sys.exit(
            f"ERROR: none of watch_streams={cfg.watch_streams} found in "
            f"streams_wide.columns={list(streams_wide.columns)}"
        )

    specs = _build_run_specs(cfg)
    print(f"\n=== Running {len(specs)} strategies on shared base detector ===",
          flush=True)
    runs: list[RunResult] = []
    for spec in specs:
        # Each spec gets its own OrchestratorConfig (the filter quantile
        # differs between naive and filtered drift-triggered variants).
        orch_cfg = OrchestratorConfig(
            feature_cols=feat_cols,
            warmup_max_phase_id=cfg.warmup_max_phase_id,
            retrain_window_s=cfg.retrain_window_s,
            retrain_score_filter_quantile=spec.filter_quantile,
            random_state=cfg.random_state,
        )
        orch = AdaptiveOrchestrator(
            windows=windows,
            streams_wide=streams_wide,
            base_detector_factory=base_factory,
            drift_detector_factories=drift_factories,
            cfg=orch_cfg,
        )
        strat = spec.strategy_factory()
        t0 = time.perf_counter()
        filt_tag = f", filter_q={spec.filter_quantile}" if spec.filter_quantile is not None else ""
        print(f"[run] {spec.display_name} (strategy={strat.name}{filt_tag})",
              flush=True)
        result = orch.run(strat)
        # Override the strategy_name with the display_name so plots /
        # tables show the run-spec identity (e.g. drift_triggered_filtered)
        # rather than the underlying strategy name.
        result.strategy_name = spec.display_name
        result.per_window["strategy"] = spec.display_name
        elapsed = time.perf_counter() - t0
        n_refit = int((result.retrain_log["reason"] != "warmup").sum()) \
            if not result.retrain_log.empty else 0
        n_scored = int(result.per_window["score"].notna().sum())
        print(
            f"      done in {elapsed:.1f}s  refits={n_refit}  "
            f"scored_windows={n_scored:,}",
            flush=True,
        )
        runs.append(result)

    drift_phase_ids = _drift_phase_ids(gtd, windows)
    print(f"\n=== drift-affected phase_ids: {drift_phase_ids} ===", flush=True)

    summary_df, sliding_df, retrain_df = _summarise_runs(runs, cfg, drift_phase_ids)
    summary_df.to_csv(out_dir / "table_7_1_strategy_summary.csv", index=False)
    sliding_df.to_csv(out_dir / "sliding_pr_auc.csv", index=False)
    retrain_df.to_csv(out_dir / "retrain_log.csv", index=False)

    # Also dump per-window scores for the appendix
    per_window_chunks = []
    for r in runs:
        keep_cols = [
            c for c in ["t_mid_s", "t_end_s", "phase_id", "label_anomaly",
                        "score", "strategy", "base_detector"]
            if c in r.per_window.columns
        ]
        per_window_chunks.append(r.per_window[keep_cols])
    per_window_all = pd.concat(per_window_chunks, ignore_index=True)
    per_window_all.to_csv(out_dir / "per_window_scores.csv", index=False)

    print(f"=== Saving figure to {out_dir / 'adaptive_pr_auc_over_time.png'} ===",
          flush=True)
    _save_figure(summary_df, sliding_df, retrain_df, gtd, cfg,
                 out_dir / "adaptive_pr_auc_over_time.png")

    verdict = _check_acceptance(summary_df)
    elapsed = round(time.perf_counter() - t_start, 1)

    summary_json = {
        "config": _config_to_dict(cfg),
        "elapsed_s": elapsed,
        "out_dir": str(out_dir),
        "drift_phase_ids": drift_phase_ids,
        "acceptance": verdict.as_dict(),
        "strategies": summary_df.to_dict(orient="records"),
    }
    with open(out_dir / "benchmark_summary.json", "w") as f:
        json.dump(summary_json, f, indent=2, default=str)

    # Pretty-print headline table
    print("\n=== Table 7.1: per-strategy summary ===", flush=True)
    if not summary_df.empty:
        pretty = summary_df.copy()
        pretty["overall_pr_auc"] = pretty["overall_pr_auc"].round(4)
        pretty["drift_phase_pr_auc"] = pretty["drift_phase_pr_auc"].round(4)
        cost_cols = [c for c in pretty.columns if c.startswith("cost__")]
        for c in cost_cols:
            if pretty[c].dtype == float:
                pretty[c] = pretty[c].round(4)
        print(pretty.to_string(index=False), flush=True)
    print("\n=== Acceptance check ===", flush=True)
    print(f"  C2 ({'PASS' if verdict.c2_pass else 'FAIL'}): {verdict.c2_msg}", flush=True)
    print(f"  C3 ({'PASS' if verdict.c3_pass else 'FAIL'}): {verdict.c3_msg}", flush=True)
    print(f"\nArtifacts written to: {out_dir}", flush=True)
    print(f"Total elapsed: {elapsed:.1f}s", flush=True)

    return summary_json


def _config_to_dict(cfg: BenchmarkConfig) -> dict:
    d = asdict(cfg)
    # tuples -> lists for JSON
    if isinstance(d.get("watch_streams"), tuple):
        d["watch_streams"] = list(d["watch_streams"])
    return d


if __name__ == "__main__":
    run_benchmark(_parse_args())
