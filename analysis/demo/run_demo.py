"""Phase 9 Iter A — end-to-end drift-aware adaptive demo.

Single CLI invocation:

1. Load timeline samples / events / drift labels / anomaly labels.
2. Build per-window features + univariate streams (same logic as
   Phase 7).
3. Fit the Phase 8 surrogate (HistGB point + ConformalQuantileGB) on
   `sweep_config_perf.parquet`.
4. Run :class:`DemoOrchestrator` (PCA-AE base detector,
   drift-triggered-filtered with q=0.8, ADWIN drift detector on
   `watch_streams`).
5. Compute per-window sliding PR-AUC + per-intervention counterfactual
   uplift.
6. Emit CSVs + the 4-panel moneyshot figure under
   `data/processed/end_to_end_demo_<timeline>/`.

Acceptance criteria (see ``docs/plan.md`` Phase 9):
    D1 : pipeline runs end-to-end (exit 0).
    D2 : >= 5 drift-triggered retrains.
    D3 : every intervention produces a valid (TTT, hyst, A3) inside the
         training grid (= 100 % "in-grid" recommendations).
    D4 : on D-4 ("operator pushed a bad config") drift events, the
         recommendation differs from the current config (cfg_changed
         must be True for the matching interventions).
    D5 : sum of signed_uplift(HOSR) across interventions > 0
         (the surrogate believes adaptation helps on average).
    D6 : 4-panel `end_to_end_moneyshot.png` rendered.
    D7 : >= 10 unit tests pass (separate from this run; see
         `analysis/demo/tests/`).
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
from matplotlib.gridspec import GridSpec

from analysis.adaptive.metrics import SlidingConfig, sliding_pr_auc
from analysis.anomaly.detectors import PCAReconErrDetector
from analysis.anomaly.features import (
    WindowConfig,
    aggregate_windows,
    feature_columns,
)
from analysis.anomaly.labels import label_windows
from analysis.common.paths import DATA_PROC, assert_timeline_present, timeline_dir
from analysis.config_perf.data import TARGET_COLS, load_sweep
from analysis.config_perf.surrogate import (
    ConformalQuantileGBSurrogate,
    HistGBSurrogate,
    fit_per_target,
)
from analysis.demo.orchestrator import (
    DemoConfig,
    DemoOrchestrator,
    DemoResult,
    SurrogateBundle,
)
from analysis.drift.detectors import ADWINDetector
from analysis.drift.streams import StreamConfig, build_streams, pivot_streams


# ---------------------------------------------------------------------------
# CLI + config
# ---------------------------------------------------------------------------

@dataclass
class RunDemoConfig:
    timeline: str = "timeline_medium"
    window_s: float = 5.0
    slide_s: float = 1.0
    warmup_max_phase_id: int = 3
    retrain_window_s: float = 120.0
    drift_cooldown_s: float = 30.0
    watch_streams: tuple[str, ...] = ("hosr_rolling", "rlf_rate_rolling")
    cadence_s: float = 1.0
    roll_window_s: float = 10.0
    adwin_delta: float = 0.01
    filter_quantile: float = 0.8
    context_window_s: float = 60.0
    constraint_hosr: float = 0.95
    constraint_rlf: float = 0.05
    constraint_pp: float = 0.10
    eval_window_s: float = 120.0
    eval_stride_s: float = 30.0
    out_label: str | None = None
    random_state: int = 42


def _parse_args(argv: list[str] | None = None) -> RunDemoConfig:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--timeline", default="timeline_medium")
    p.add_argument("--window-s", type=float, default=5.0)
    p.add_argument("--slide-s", type=float, default=1.0)
    p.add_argument("--warmup-max-phase-id", type=int, default=3)
    p.add_argument("--retrain-window-s", type=float, default=120.0)
    p.add_argument("--drift-cooldown-s", type=float, default=30.0)
    p.add_argument("--watch-streams", default="hosr_rolling,rlf_rate_rolling")
    p.add_argument("--cadence-s", type=float, default=1.0)
    p.add_argument("--roll-window-s", type=float, default=10.0)
    p.add_argument("--adwin-delta", type=float, default=0.01)
    p.add_argument("--filter-quantile", type=float, default=0.8)
    p.add_argument("--context-window-s", type=float, default=60.0)
    p.add_argument("--constraint-hosr", type=float, default=0.95)
    p.add_argument("--constraint-rlf", type=float, default=0.05)
    p.add_argument("--constraint-pp", type=float, default=0.10)
    p.add_argument("--eval-window-s", type=float, default=120.0)
    p.add_argument("--eval-stride-s", type=float, default=30.0)
    p.add_argument("--out-label", default=None)
    p.add_argument("--random-state", type=int, default=42)
    a = p.parse_args(argv)
    return RunDemoConfig(
        timeline=a.timeline,
        window_s=a.window_s,
        slide_s=a.slide_s,
        warmup_max_phase_id=a.warmup_max_phase_id,
        retrain_window_s=a.retrain_window_s,
        drift_cooldown_s=a.drift_cooldown_s,
        watch_streams=tuple(s.strip() for s in a.watch_streams.split(",") if s.strip()),
        cadence_s=a.cadence_s,
        roll_window_s=a.roll_window_s,
        adwin_delta=a.adwin_delta,
        filter_quantile=a.filter_quantile,
        context_window_s=a.context_window_s,
        constraint_hosr=a.constraint_hosr,
        constraint_rlf=a.constraint_rlf,
        constraint_pp=a.constraint_pp,
        eval_window_s=a.eval_window_s,
        eval_stride_s=a.eval_stride_s,
        out_label=a.out_label,
        random_state=a.random_state,
    )


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_timeline(cfg: RunDemoConfig):
    assert_timeline_present(cfg.timeline)
    base = timeline_dir(cfg.timeline)
    print(f"[load] {base}", flush=True)
    samples = pd.read_parquet(base / "samples.parquet")
    events = pd.read_parquet(base / "events.parquet")
    gta_fp = base / "ground_truth_anomaly.parquet"
    gtd_fp = base / "ground_truth_drift.parquet"
    if not gta_fp.is_file():
        sys.exit(f"ERROR: {gta_fp} missing - demo needs anomaly GT.")
    if not gtd_fp.is_file():
        sys.exit(f"ERROR: {gtd_fp} missing - demo needs drift GT.")
    gta = pd.read_parquet(gta_fp)
    gtd = pd.read_parquet(gtd_fp)
    print(
        f"       samples={len(samples):,} events={len(events):,} "
        f"gta={len(gta)} gtd={len(gtd)}",
        flush=True,
    )
    return samples, events, gta, gtd


def _build_windows(samples, events, gta, cfg: RunDemoConfig) -> pd.DataFrame:
    print(f"[windows] aggregating (W={cfg.window_s}s, slide={cfg.slide_s}s)...",
          flush=True)
    feats = aggregate_windows(
        samples, events, WindowConfig(window_s=cfg.window_s, slide_s=cfg.slide_s)
    )
    labelled = label_windows(feats, gta)
    n_pos = int(labelled["label_anomaly"].sum())
    print(
        f"          windows={len(labelled):,} positives={n_pos:,} "
        f"(prevalence={n_pos / max(len(labelled), 1):.4f})",
        flush=True,
    )
    return labelled


def _build_streams_wide(samples, events, cfg: RunDemoConfig) -> pd.DataFrame:
    print(f"[streams] building univariate streams (cadence={cfg.cadence_s}s, "
          f"rolling W={cfg.roll_window_s}s)...", flush=True)
    streams_long = build_streams(
        samples, events,
        StreamConfig(cadence_s=cfg.cadence_s, roll_window_s=cfg.roll_window_s),
    )
    wide = pivot_streams(streams_long)
    print(f"          streams_wide shape={wide.shape}", flush=True)
    return wide


# ---------------------------------------------------------------------------
# Surrogate fitting (one-shot on sweep_config_perf)
# ---------------------------------------------------------------------------

def _fit_surrogates(cfg: RunDemoConfig) -> SurrogateBundle:
    print("[surrogate] fitting HistGB + ConformalQuantileGB per target on "
          "sweep_config_perf.parquet...", flush=True)
    sweep = load_sweep()
    point_models = fit_per_target(
        lambda: HistGBSurrogate(random_state=cfg.random_state),
        sweep.X, sweep.Y, targets=tuple(TARGET_COLS),
    )
    quantile_models = fit_per_target(
        lambda: ConformalQuantileGBSurrogate(
            q_lo=0.05, q_hi=0.95, calibration_frac=0.35,
            random_state=cfg.random_state,
        ),
        sweep.X, sweep.Y, targets=tuple(TARGET_COLS),
    )
    bundle = SurrogateBundle(
        point_models=point_models, interval_models=quantile_models,
    )
    bundle.validate()
    print("           done.", flush=True)
    return bundle


# ---------------------------------------------------------------------------
# Drift-phase metadata
# ---------------------------------------------------------------------------

def _drift_phase_ids(gtd: pd.DataFrame, windows: pd.DataFrame) -> list[int]:
    if gtd is None or gtd.empty or windows.empty:
        return []
    wt = windows[["t_mid_s", "phase_id"]].sort_values("t_mid_s").reset_index(drop=True)
    t_mid = wt["t_mid_s"].to_numpy()
    phase_ids = wt["phase_id"].to_numpy()
    drift_set: set[int] = set()
    for _, row in gtd.iterrows():
        lo, hi = float(row["start_time_s"]), float(row["end_time_s"])
        mask = (t_mid >= lo) & (t_mid < hi)
        for p in np.unique(phase_ids[mask]):
            drift_set.add(int(p))
    return sorted(drift_set)


# ---------------------------------------------------------------------------
# Acceptance verdict
# ---------------------------------------------------------------------------

@dataclass
class AcceptanceVerdict:
    d2_pass: bool; d2_msg: str
    d3_pass: bool; d3_msg: str
    d4_pass: bool; d4_msg: str
    d5_pass: bool; d5_msg: str

    def as_dict(self) -> dict:
        return {
            "D2": {"pass": self.d2_pass, "msg": self.d2_msg},
            "D3": {"pass": self.d3_pass, "msg": self.d3_msg},
            "D4": {"pass": self.d4_pass, "msg": self.d4_msg},
            "D5": {"pass": self.d5_pass, "msg": self.d5_msg},
        }


_TRAINING_TTTS = {256.0, 480.0, 1024.0}
_TRAINING_HYSTS = {0.0, 1.0, 3.0, 6.0}
_TRAINING_A3S = {0.0, 3.0, 6.0}


def _in_training_grid(cfg_dict: dict[str, float]) -> bool:
    return (
        cfg_dict.get("ttt_ms")     in _TRAINING_TTTS
        and cfg_dict.get("hyst_db")   in _TRAINING_HYSTS
        and cfg_dict.get("a3_off_db") in _TRAINING_A3S
    )


def _check_acceptance(
    result: DemoResult, gtd: pd.DataFrame,
) -> AcceptanceVerdict:
    interventions = result.interventions
    n_int = len(interventions)

    # D2: >= 5 drift-triggered retrains
    d2_pass = n_int >= 5
    d2_msg = f"{n_int} drift-triggered interventions (target >= 5)"

    # D3: all recommended configs inside the training grid
    if n_int == 0:
        d3_pass = False
        d3_msg = "0 interventions; cannot evaluate"
    else:
        in_grid = sum(_in_training_grid(iv.recommended_cfg) for iv in interventions)
        d3_pass = in_grid == n_int
        d3_msg = f"{in_grid} / {n_int} recommendations inside training grid"

    # D4: on D-4 drifts the recommendation must differ from the current cfg
    if gtd is None or gtd.empty:
        d4_pass = True
        d4_msg = "no drift GT loaded; D4 vacuously PASS"
    else:
        d4_rows = gtd[gtd["drift_id"] == "D-4"]
        d4_windows = [
            (float(row["start_time_s"]) - 5.0, float(row["end_time_s"]) + 60.0)
            for _, row in d4_rows.iterrows()
        ]
        # interventions in any D-4 vicinity (start-5s to end+60s) must have cfg_changed
        n_d4_int = 0
        n_d4_int_changed = 0
        for iv in interventions:
            for lo, hi in d4_windows:
                if lo <= iv.t_trigger_s <= hi:
                    n_d4_int += 1
                    if iv.cfg_changed:
                        n_d4_int_changed += 1
                    break
        if n_d4_int == 0:
            d4_pass = False
            d4_msg = (
                f"no interventions fired in D-4 vicinity "
                f"({len(d4_windows)} D-4 instances in GT) - drift may have been "
                f"missed by ADWIN"
            )
        else:
            d4_pass = n_d4_int_changed == n_d4_int
            d4_msg = (
                f"{n_d4_int_changed} / {n_d4_int} D-4 interventions changed cfg "
                f"(target = all)"
            )

    # D5: total predicted HOSR uplift > 0
    if n_int == 0:
        d5_pass = False
        d5_msg = "0 interventions; cumulative uplift = 0"
    else:
        total_uplift = sum(iv.signed_uplift("hosr") for iv in interventions)
        d5_pass = total_uplift > 0
        d5_msg = f"cumulative predicted HOSR uplift = {total_uplift:+.4f}"

    return AcceptanceVerdict(
        d2_pass=d2_pass, d2_msg=d2_msg,
        d3_pass=d3_pass, d3_msg=d3_msg,
        d4_pass=d4_pass, d4_msg=d4_msg,
        d5_pass=d5_pass, d5_msg=d5_msg,
    )


# ---------------------------------------------------------------------------
# Moneyshot figure (4 panels)
# ---------------------------------------------------------------------------

_DRIFT_COLOURS = {
    "D-1": "#3b8132",   # green (traffic)
    "D-2": "#1f77b4",   # blue (channel)
    "D-3": "#9467bd",   # purple (mobility)
    "D-4": "#d62728",   # red (bad config push) - the headline drift
}


def _save_moneyshot(
    result: DemoResult,
    gtd: pd.DataFrame,
    pr_auc_curve: pd.DataFrame,
    cfg: RunDemoConfig,
    out_fp: Path,
) -> None:
    fig = plt.figure(figsize=(15, 10))
    gs = GridSpec(2, 2, figure=fig, hspace=0.35, wspace=0.20)
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])
    ax_c = fig.add_subplot(gs[1, 0])
    ax_d = fig.add_subplot(gs[1, 1])

    interventions = result.interventions
    iv_times = np.array([iv.t_trigger_s for iv in interventions], dtype=float)

    # --- Panel A: sliding PR-AUC over time -------------------------------
    ax_a.set_title("(A) Sliding PR-AUC over time (drift-triggered-filtered + surrogate)",
                   fontsize=10)
    _shade_drift_phases(ax_a, gtd)
    if not pr_auc_curve.empty:
        ax_a.plot(
            pr_auc_curve["t_center_s"], pr_auc_curve["pr_auc"],
            color="black", lw=1.5, label="PR-AUC",
        )
    for t in iv_times:
        ax_a.axvline(t, color="orange", lw=0.7, alpha=0.7)
    ax_a.set_xlabel("time (s)")
    ax_a.set_ylabel("sliding PR-AUC")
    ax_a.set_ylim(0.0, max(1.0, pr_auc_curve["pr_auc"].max() + 0.05 if not pr_auc_curve.empty else 1.0))
    ax_a.grid(alpha=0.3)
    ax_a.legend(loc="upper right", fontsize=8)

    # --- Panel B: anomaly score raw trace --------------------------------
    ax_b.set_title("(B) Anomaly score (PCA-AE) with intervention markers",
                   fontsize=10)
    _shade_drift_phases(ax_b, gtd)
    if not result.per_window.empty and "score" in result.per_window.columns:
        ax_b.plot(
            result.per_window["t_mid_s"], result.per_window["score"],
            color="black", lw=0.6, alpha=0.85, label="score",
        )
    # Retrain markers
    if not result.retrain_log.empty:
        rt = result.retrain_log
        for _, row in rt.iterrows():
            t = float(row["t_s"])
            reason = str(row.get("reason", ""))
            colour = (
                "#d62728" if reason.startswith("drift:")
                else "#888888"
            )
            ax_b.axvline(t, color=colour, lw=0.6, alpha=0.7)
    ax_b.set_xlabel("time (s)")
    ax_b.set_ylabel("anomaly score")
    ax_b.grid(alpha=0.3)

    # --- Panel C: intervention table -------------------------------------
    ax_c.set_title("(C) Intervention log (drift trigger -> surrogate query)",
                   fontsize=10)
    ax_c.axis("off")
    if interventions:
        rows = [
            [
                f"{iv.t_trigger_s:.0f}",
                ",".join(iv.drift_streams_fired)[:18],
                f"({int(iv.current_cfg['ttt_ms'])},{int(iv.current_cfg['hyst_db'])},{int(iv.current_cfg['a3_off_db'])})",
                f"({int(iv.recommended_cfg['ttt_ms'])},{int(iv.recommended_cfg['hyst_db'])},{int(iv.recommended_cfg['a3_off_db'])})",
                f"{iv.pred_current['hosr']:.3f}",
                f"{iv.pred_recommended['hosr']:.3f}",
                f"{iv.signed_uplift('hosr'):+.3f}",
            ]
            for iv in interventions[:12]   # cap at 12 for legibility
        ]
        col_labels = [
            "t(s)", "drift_streams",
            "current\n(TTT,hyst,A3)", "recommended\n(TTT,hyst,A3)",
            "pred HOSR\n(current)", "pred HOSR\n(recommended)",
            "HOSR\nuplift",
        ]
        tbl = ax_c.table(
            cellText=rows, colLabels=col_labels,
            loc="center", cellLoc="center",
        )
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(7)
        tbl.scale(1.0, 1.4)
        # Highlight rows with positive uplift
        for i, iv in enumerate(interventions[:12]):
            cell = tbl[(i + 1, 6)]
            if iv.signed_uplift("hosr") > 1e-4:
                cell.set_facecolor("#d4edda")
            elif iv.signed_uplift("hosr") < -1e-4:
                cell.set_facecolor("#f8d7da")
    else:
        ax_c.text(0.5, 0.5, "No interventions fired",
                  ha="center", va="center", fontsize=10)

    # --- Panel D: cumulative predicted HOSR uplift -----------------------
    ax_d.set_title("(D) Cumulative predicted HOSR uplift over interventions",
                   fontsize=10)
    if interventions:
        t = np.array([iv.t_trigger_s for iv in interventions])
        u = np.array([iv.signed_uplift("hosr") for iv in interventions])
        cum = np.cumsum(u)
        ax_d.step(t, cum, where="post", color="#d62728", lw=2,
                  label="cumulative HOSR uplift")
        ax_d.fill_between(t, 0, cum, step="post", color="#d62728", alpha=0.15)
        # Annotate each intervention with its delta
        for ti, ui, ci in zip(t, u, cum):
            ax_d.annotate(
                f"{ui:+.3f}", (ti, ci),
                xytext=(0, 5), textcoords="offset points",
                fontsize=7, ha="center", color="#660000",
            )
        ax_d.axhline(0, color="grey", lw=0.5, linestyle="--")
        ax_d.set_xlabel("time of trigger (s)")
        ax_d.set_ylabel("cumulative HOSR uplift")
        ax_d.grid(alpha=0.3)
        ax_d.legend(loc="upper left", fontsize=8)
    else:
        ax_d.text(0.5, 0.5, "No interventions fired",
                  ha="center", va="center", fontsize=10)

    fig.suptitle(
        f"Phase 9 - End-to-end drift-aware adaptive demo  "
        f"(timeline={cfg.timeline}, {len(interventions)} interventions)",
        fontsize=12,
    )
    fig.savefig(out_fp, dpi=140, bbox_inches="tight")
    plt.close(fig)


def _shade_drift_phases(ax, gtd: pd.DataFrame) -> None:
    """Shade drift windows on a time-axis matplotlib axis."""
    if gtd is None or gtd.empty:
        return
    for _, row in gtd.iterrows():
        c = _DRIFT_COLOURS.get(str(row["drift_id"]), "#cccccc")
        ax.axvspan(
            float(row["start_time_s"]), float(row["end_time_s"]),
            color=c, alpha=0.13, lw=0,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_demo(cfg: RunDemoConfig) -> dict:
    t_start = time.perf_counter()
    out_dir = DATA_PROC / f"end_to_end_demo_{cfg.timeline}{('_' + cfg.out_label) if cfg.out_label else ''}"
    out_dir.mkdir(parents=True, exist_ok=True)

    samples, events, gta, gtd = _load_timeline(cfg)
    windows = _build_windows(samples, events, gta, cfg)
    streams_wide = _build_streams_wide(samples, events, cfg)
    surrogates = _fit_surrogates(cfg)

    feat_cols = [c for c in feature_columns() if c in windows.columns]
    print(f"[demo] feature_cols={len(feat_cols)} | "
          f"watch_streams={cfg.watch_streams} | "
          f"filter_q={cfg.filter_quantile}", flush=True)

    demo_cfg = DemoConfig(
        feature_cols=feat_cols,
        warmup_max_phase_id=cfg.warmup_max_phase_id,
        retrain_window_s=cfg.retrain_window_s,
        filter_quantile=cfg.filter_quantile,
        drift_cooldown_s=cfg.drift_cooldown_s,
        watch_streams=cfg.watch_streams,
        context_window_s=cfg.context_window_s,
        constraint_hosr=cfg.constraint_hosr,
        constraint_rlf=cfg.constraint_rlf,
        constraint_pp=cfg.constraint_pp,
        random_state=cfg.random_state,
    )
    orchestrator = DemoOrchestrator(
        windows=windows,
        streams_wide=streams_wide,
        samples=samples,
        events=events,
        base_detector_factory=lambda: PCAReconErrDetector(random_state=cfg.random_state),
        drift_detector_factories={
            sn: (lambda: ADWINDetector(delta=cfg.adwin_delta))
            for sn in cfg.watch_streams
        },
        surrogates=surrogates,
        cfg=demo_cfg,
    )
    print(f"\n=== Walk-forward replay (this is the demo) ===", flush=True)
    t_run = time.perf_counter()
    result = orchestrator.run()
    elapsed_run = time.perf_counter() - t_run
    print(
        f"       elapsed={elapsed_run:.1f}s | "
        f"per_window rows={len(result.per_window):,} | "
        f"retrains={len(result.retrain_log)} | "
        f"interventions={len(result.interventions)}",
        flush=True,
    )

    # Sliding PR-AUC for Panel A
    pr_auc_curve = sliding_pr_auc(
        result.per_window,
        SlidingConfig(eval_window_s=cfg.eval_window_s, stride_s=cfg.eval_stride_s),
    )

    # Persist CSVs
    int_df = result.intervention_log_df()
    result.per_window.to_csv(out_dir / "per_window.csv", index=False)
    result.retrain_log.to_csv(out_dir / "retrain_log.csv", index=False)
    int_df.to_csv(out_dir / "intervention_log.csv", index=False)
    pr_auc_curve.to_csv(out_dir / "sliding_pr_auc.csv", index=False)

    # Acceptance
    verdict = _check_acceptance(result, gtd)

    # Figure
    _save_moneyshot(
        result, gtd, pr_auc_curve, cfg,
        out_fp=out_dir / "end_to_end_moneyshot.png",
    )

    # Headline numbers
    n_int = len(result.interventions)
    n_drift_phases = len(_drift_phase_ids(gtd, windows))
    total_hosr_uplift = (
        float(sum(iv.signed_uplift("hosr") for iv in result.interventions))
        if result.interventions else 0.0
    )
    total_rlf_uplift = (
        float(sum(iv.signed_uplift("rlf_rate") for iv in result.interventions))
        if result.interventions else 0.0
    )

    summary = {
        "config": asdict(cfg),
        "elapsed_s": round(time.perf_counter() - t_start, 1),
        "elapsed_run_s": round(elapsed_run, 1),
        "out_dir": str(out_dir),
        "n_windows": int(len(result.per_window)),
        "n_retrains": int(len(result.retrain_log)),
        "n_interventions": n_int,
        "n_drift_phases": n_drift_phases,
        "cumulative_predicted_uplift": {
            "hosr": total_hosr_uplift,
            "rlf_rate": total_rlf_uplift,
        },
        "acceptance": verdict.as_dict(),
        "base_detector": result.base_detector_name,
    }
    with open(out_dir / "demo_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print("\n=== Acceptance check ===", flush=True)
    for tag, p, msg in [
        ("D2", verdict.d2_pass, verdict.d2_msg),
        ("D3", verdict.d3_pass, verdict.d3_msg),
        ("D4", verdict.d4_pass, verdict.d4_msg),
        ("D5", verdict.d5_pass, verdict.d5_msg),
    ]:
        flag = "PASS" if p else "FAIL"
        print(f"  {tag} ({flag}): {msg}", flush=True)
    print(f"\nArtifacts written to: {out_dir}", flush=True)
    print(f"Total elapsed: {summary['elapsed_s']:.1f}s", flush=True)
    return summary


if __name__ == "__main__":
    run_demo(_parse_args())
