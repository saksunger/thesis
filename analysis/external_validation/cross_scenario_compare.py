"""Phase 11 Iter B — Cross-scenario comparator.

Reads the headline metrics produced by Phase 5/6/7/9 on TWO timelines
(typically ``timeline_medium`` and ``timeline_dense_urban``) and emits
a structured comparison + the Iter B acceptance check.

Inputs
------
For each timeline name, the comparator looks for these summary files
(produced by ``make {anomaly,drift,adaptive,end2end}-benchmark
TIMELINE=<name>``)::

    data/processed/anomaly_benchmark_<name>/benchmark_summary.json
    data/processed/drift_benchmark_<name>/benchmark_summary.json
    data/processed/adaptive_benchmark_<name>/benchmark_summary.json
    data/processed/end_to_end_demo_<name>/demo_summary.json

Outputs (under ``data/processed/external_validation/``)
-------------------------------------------------------
    cross_scenario_table_<base>_vs_<contrast>.csv
        Long-format: one row per (metric, timeline) with the value.
    cross_scenario_delta_<base>_vs_<contrast>.csv
        Wide-format: one row per metric with `base`, `contrast`,
        `delta` (= contrast - base), and `relative_delta` columns.
    cross_scenario_acceptance_<base>_vs_<contrast>.json
        F1..F5 pass/fail dict.
    cross_scenario_bars_<base>_vs_<contrast>.png
        Grouped-bar comparison of headline metrics across the two
        scenarios.

Acceptance criteria (Phase 11 Iter B)
-------------------------------------
    F1: every pipeline (anomaly/drift/adaptive/demo) has a summary
        file for BOTH timelines (pipeline portability).
    F2: anomaly winning detector overlaps - i.e. at least one detector
        is in the top-2 ranking on BOTH timelines. Iter A's E2 was
        intra-scenario, F2 is inter-scenario.
    F3: filtered > naive sign preserved
        (sign(adaptive__delta_filtered_minus_naive) is the same on
         both timelines; magnitude allowed to differ).
    F4: demo still recovers a D-4 config push
        (>= 1 intervention with non-zero config delta on the contrast
        timeline; we only need a count here, not the same magnitude).
    F5: demo cumulative HOSR uplift is positive on the contrast
        timeline (no requirement of equal magnitude vs. base).

Usage
-----
    python -m analysis.external_validation.cross_scenario_compare \\
        --base timeline_medium --contrast timeline_dense_urban
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from analysis.common.paths import DATA_PROC


OUTPUT_DIR: Path = DATA_PROC / "external_validation"


# ---------------------------------------------------------------------------
# Pipeline summary loaders (same schema as run_seed_replication consumes)
# ---------------------------------------------------------------------------

PIPELINE_SUMMARY_FILES: dict[str, tuple[str, str]] = {
    # name -> (subdir_template, filename)
    "anomaly":  ("anomaly_benchmark_{tl}",  "benchmark_summary.json"),
    "drift":    ("drift_benchmark_{tl}",    "benchmark_summary.json"),
    "adaptive": ("adaptive_benchmark_{tl}", "benchmark_summary.json"),
    "demo":     ("end_to_end_demo_{tl}",    "demo_summary.json"),
}


def _summary_path(timeline: str, pipeline: str) -> Path:
    subdir, fname = PIPELINE_SUMMARY_FILES[pipeline]
    return DATA_PROC / subdir.format(tl=timeline) / fname


def load_pipeline_summaries(timeline: str) -> dict[str, dict | None]:
    """Read all 4 pipeline summaries; missing files map to ``None``."""
    out: dict[str, dict | None] = {}
    for pipeline in PIPELINE_SUMMARY_FILES:
        path = _summary_path(timeline, pipeline)
        if path.is_file():
            with open(path) as f:
                out[pipeline] = json.load(f)
        else:
            out[pipeline] = None
    return out


# ---------------------------------------------------------------------------
# Scalar metric extraction (mirrors run_seed_replication.extract_metrics)
# ---------------------------------------------------------------------------

def extract_scalars(
    summaries: dict[str, dict | None],
    *,
    timeline: str | None = None,
) -> dict[str, object]:
    """Flatten the 4 summaries into ``metric_name -> scalar`` dict.

    ``timeline`` is optional; when supplied, enables a disk fallback
    that recovers the ``anomaly__winning_detector_overall`` column from
    ``overall_pr_auc_with_ci.csv`` for legacy benchmark runs that don't
    yet emit ``winning_detector_by_overall_pr_auc`` in their JSON.
    """
    out: dict[str, object] = {}
    for pipeline, summary in summaries.items():
        if summary is None:
            out[f"{pipeline}__missing"] = True
            continue
        if pipeline == "anomaly":
            per_type = (
                summary.get("winning_detector_by_per_type_mean_pr_auc")
                or summary.get("winning_detector_by_mean_pr_auc")
            )
            overall_w = summary.get("winning_detector_by_overall_pr_auc")
            if overall_w is None and timeline:
                overall_csv = (
                    _summary_path(timeline, "anomaly").parent
                    / "overall_pr_auc_with_ci.csv"
                )
                if overall_csv.is_file():
                    import csv as _csv
                    with open(overall_csv) as _f:
                        rows = list(_csv.DictReader(_f))
                    if rows:
                        try:
                            top = max(rows, key=lambda r: float(r["pr_auc"]))
                            overall_w = top.get("detector")
                        except (KeyError, ValueError):
                            overall_w = None
            out["anomaly__winning_detector_per_type_mean"] = per_type
            out["anomaly__winning_detector_overall"] = overall_w
            # Back-compat alias (legacy column name -> per-type-mean).
            out["anomaly__winning_detector"] = per_type
            out["anomaly__elapsed_s"] = summary.get("elapsed_s")
        elif pipeline == "drift":
            out["drift__winning_detector"] = summary.get("winning_detector")
            out["drift__n_detections"] = summary.get("n_detections")
            out["drift__n_drifts"] = summary.get("n_drifts")
            out["drift__elapsed_s"] = summary.get("elapsed_s")
        elif pipeline == "adaptive":
            strats = summary.get("strategies", []) or []
            for s in strats:
                sid = s.get("strategy") or "unknown"
                out[f"adaptive__{sid}__overall_pr_auc"] = s.get("overall_pr_auc")
                out[f"adaptive__{sid}__drift_phase_pr_auc"] = s.get(
                    "drift_phase_pr_auc"
                )
                out[f"adaptive__{sid}__n_refits"] = s.get("cost__n_refits")
            f_val = out.get(
                "adaptive__drift_triggered_filtered__drift_phase_pr_auc"
            )
            n_val = out.get(
                "adaptive__drift_triggered_naive__drift_phase_pr_auc"
            )
            if isinstance(f_val, (int, float)) and isinstance(n_val, (int, float)):
                out["adaptive__delta_filtered_minus_naive"] = float(f_val) - float(n_val)
        elif pipeline == "demo":
            upl = summary.get("cumulative_predicted_uplift", {}) or {}
            out["demo__cum_uplift_hosr"] = upl.get("hosr")
            out["demo__cum_uplift_rlf_rate"] = upl.get("rlf_rate")
            out["demo__n_interventions"] = summary.get("n_interventions")
            out["demo__n_retrains"] = summary.get("n_retrains")
            acc = summary.get("acceptance", {}) or {}
            for k, v in acc.items():
                out[f"demo__acceptance__{k}"] = (
                    bool(v.get("pass")) if isinstance(v, dict) else None
                )
    return out


# ---------------------------------------------------------------------------
# Tables + deltas
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MetricDelta:
    metric: str
    base_value: object
    contrast_value: object
    delta: float | None             # contrast - base (None if either side non-numeric)
    relative_delta: float | None    # delta / |base| (None if base==0 / non-numeric)


def compute_deltas(
    base_metrics: dict[str, object],
    contrast_metrics: dict[str, object],
) -> list[MetricDelta]:
    keys = sorted(set(base_metrics) | set(contrast_metrics))
    out: list[MetricDelta] = []
    for k in keys:
        bv = base_metrics.get(k)
        cv = contrast_metrics.get(k)
        delta: float | None = None
        relative: float | None = None
        if isinstance(bv, (int, float)) and isinstance(cv, (int, float)) \
                and not isinstance(bv, bool) and not isinstance(cv, bool):
            try:
                delta = float(cv) - float(bv)
                if abs(float(bv)) > 1e-12:
                    relative = delta / abs(float(bv))
            except (TypeError, ValueError):
                pass
        out.append(MetricDelta(
            metric=k, base_value=bv, contrast_value=cv,
            delta=delta, relative_delta=relative,
        ))
    return out


# ---------------------------------------------------------------------------
# Anomaly detector top-2 overlap (F2 helper)
# ---------------------------------------------------------------------------

def load_anomaly_top2(timeline: str) -> list[str]:
    """Return up to 2 best anomaly detectors by mean PR-AUC.

    Reads ``per_anomaly_with_ci.csv`` if available; falls back to
    ``benchmark_summary.json`` winning detector for a length-1 list.
    """
    csv_path = DATA_PROC / f"anomaly_benchmark_{timeline}" / "per_anomaly_with_ci.csv"
    if csv_path.is_file():
        means: dict[str, list[float]] = {}
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for r in reader:
                det = r.get("detector")
                try:
                    val = float(r.get("pr_auc", ""))
                except (TypeError, ValueError):
                    continue
                if det:
                    means.setdefault(det, []).append(val)
        if means:
            ordered = sorted(
                means.items(),
                key=lambda x: -(sum(x[1]) / len(x[1])),
            )
            return [d for d, _ in ordered[:2]]
    # Fallback to summary
    summary = _summary_path(timeline, "anomaly")
    if summary.is_file():
        doc = json.loads(summary.read_text())
        w = doc.get("winning_detector_by_mean_pr_auc")
        return [w] if isinstance(w, str) else []
    return []


# ---------------------------------------------------------------------------
# Acceptance check (F1..F5)
# ---------------------------------------------------------------------------

@dataclass
class AcceptanceCheck:
    code: str
    name: str
    passed: bool
    actual: str
    target: str


def check_acceptance(
    base: str,
    contrast: str,
    base_metrics: dict[str, object],
    contrast_metrics: dict[str, object],
    base_top2: list[str],
    contrast_top2: list[str],
) -> list[AcceptanceCheck]:
    out: list[AcceptanceCheck] = []

    # F1: every pipeline summary present for both timelines
    missing_base = [p for p in PIPELINE_SUMMARY_FILES
                    if base_metrics.get(f"{p}__missing")]
    missing_contrast = [p for p in PIPELINE_SUMMARY_FILES
                        if contrast_metrics.get(f"{p}__missing")]
    out.append(AcceptanceCheck(
        code="F1",
        name="pipeline portability",
        passed=not missing_base and not missing_contrast,
        actual=(f"base missing: {missing_base or 'none'}; "
                f"contrast missing: {missing_contrast or 'none'}"),
        target="no missing summaries on either side",
    ))

    # F2: anomaly winners overlap (at least 1 detector in both top-2 lists)
    overlap = sorted(set(base_top2) & set(contrast_top2))
    out.append(AcceptanceCheck(
        code="F2",
        name="anomaly detector ranking partially stable",
        passed=bool(overlap),
        actual=(f"base top-2: {base_top2}; contrast top-2: {contrast_top2}; "
                f"overlap: {overlap}"),
        target=">= 1 detector in both top-2",
    ))

    # F3: filtered > naive sign preserved
    b_delta = base_metrics.get("adaptive__delta_filtered_minus_naive")
    c_delta = contrast_metrics.get("adaptive__delta_filtered_minus_naive")
    if isinstance(b_delta, (int, float)) and isinstance(c_delta, (int, float)):
        b_pos = float(b_delta) > 0
        c_pos = float(c_delta) > 0
        passed = b_pos == c_pos and b_pos  # both positive
        out.append(AcceptanceCheck(
            code="F3",
            name="filtered > naive sign preserved",
            passed=passed,
            actual=f"base={float(b_delta):+.4f}, contrast={float(c_delta):+.4f}",
            target="both > 0",
        ))
    else:
        out.append(AcceptanceCheck(
            code="F3",
            name="filtered > naive sign preserved",
            passed=False,
            actual=f"missing adaptive delta: base={b_delta}, contrast={c_delta}",
            target="both > 0",
        ))

    # F4: demo still recovers a D-4 config push (>= 1 intervention)
    n_int = contrast_metrics.get("demo__n_interventions")
    if isinstance(n_int, (int, float)):
        passed = int(n_int) >= 1
        out.append(AcceptanceCheck(
            code="F4",
            name="demo still recovers config push",
            passed=passed,
            actual=f"contrast n_interventions = {int(n_int)}",
            target=">= 1",
        ))
    else:
        out.append(AcceptanceCheck(
            code="F4",
            name="demo still recovers config push",
            passed=False,
            actual="no n_interventions on contrast",
            target=">= 1",
        ))

    # F5: contrast HOSR uplift > 0
    upl = contrast_metrics.get("demo__cum_uplift_hosr")
    if isinstance(upl, (int, float)):
        passed = float(upl) > 0
        out.append(AcceptanceCheck(
            code="F5",
            name="contrast cum HOSR uplift positive",
            passed=passed,
            actual=f"contrast cum_uplift_hosr = {float(upl):+.4f}",
            target="> 0",
        ))
    else:
        out.append(AcceptanceCheck(
            code="F5",
            name="contrast cum HOSR uplift positive",
            passed=False,
            actual="no cum_uplift_hosr on contrast",
            target="> 0",
        ))

    return out


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def write_long_table(
    base: str, contrast: str,
    base_metrics: dict[str, object], contrast_metrics: dict[str, object],
    out_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "timeline", "value"])
        for k in sorted(set(base_metrics) | set(contrast_metrics)):
            w.writerow([k, base, _fmt(base_metrics.get(k))])
            w.writerow([k, contrast, _fmt(contrast_metrics.get(k))])


def write_delta_table(deltas: list[MetricDelta], out_path: Path,
                      base: str, contrast: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", f"{base}", f"{contrast}",
                    "delta_contrast_minus_base", "relative_delta"])
        for d in deltas:
            w.writerow([
                d.metric,
                _fmt(d.base_value),
                _fmt(d.contrast_value),
                _fmt(d.delta),
                _fmt(d.relative_delta),
            ])


def write_acceptance(checks: list[AcceptanceCheck], out_path: Path) -> None:
    doc = {c.code: {"name": c.name, "passed": c.passed,
                    "actual": c.actual, "target": c.target}
           for c in checks}
    doc["_all_pass"] = all(c.passed for c in checks)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(doc, f, indent=2)


def write_bar_figure(
    base: str, contrast: str,
    base_metrics: dict[str, object],
    contrast_metrics: dict[str, object],
    out_path: Path,
) -> None:
    headline_metrics = [
        ("adaptive__drift_triggered_filtered__drift_phase_pr_auc",
         "Filtered drift-phase\nPR-AUC"),
        ("adaptive__drift_triggered_naive__drift_phase_pr_auc",
         "Naive drift-phase\nPR-AUC"),
        ("adaptive__delta_filtered_minus_naive",
         "Filtered - Naive\ndelta"),
        ("demo__cum_uplift_hosr",
         "Demo cum\nHOSR uplift"),
        ("demo__n_interventions",
         "Demo\n#interventions"),
        ("drift__n_detections",
         "Drift\n#detections"),
    ]

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except Exception as exc:  # pragma: no cover - optional dep
        print(f"WARN: matplotlib unavailable, skipping figure ({exc}).")
        return

    labels: list[str] = []
    base_vals: list[float] = []
    contrast_vals: list[float] = []
    for col, label in headline_metrics:
        bv = base_metrics.get(col)
        cv = contrast_metrics.get(col)
        if isinstance(bv, (int, float)) and isinstance(cv, (int, float)):
            labels.append(label)
            base_vals.append(float(bv))
            contrast_vals.append(float(cv))
    if not labels:
        return

    x = np.arange(len(labels))
    w = 0.36
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.bar(x - w / 2, base_vals, width=w, label=base, color="C0")
    ax.bar(x + w / 2, contrast_vals, width=w, label=contrast, color="C1")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(loc="best")
    ax.set_title(f"Phase 11 Iter B — cross-scenario headline metrics "
                 f"({base} vs {contrast})", fontsize=11)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


def _fmt(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        return f"{v:.6g}"
    return str(v)


# ---------------------------------------------------------------------------
# Pretty-print acceptance
# ---------------------------------------------------------------------------

def print_acceptance_summary(checks: list[AcceptanceCheck]) -> None:
    width = max((len(c.actual) for c in checks), default=30)
    print()
    print("Phase 11 Iter B acceptance:")
    print("  " + "-" * (12 + width + 30))
    for c in checks:
        mark = "PASS" if c.passed else "FAIL"
        print(f"  [{mark}] {c.code}  {c.name}")
        print(f"         actual: {c.actual}")
        print(f"         target: {c.target}")
    all_pass = all(c.passed for c in checks)
    print()
    print(f"  Overall: {'PASS' if all_pass else 'FAIL'} "
          f"({sum(c.passed for c in checks)}/{len(checks)} checks pass)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--base", default="timeline_medium",
                   help='Baseline timeline name (default: "timeline_medium").')
    p.add_argument("--contrast", default="timeline_dense_urban",
                   help='Contrast timeline name (default: "timeline_dense_urban").')
    a = p.parse_args(argv)

    print(f"Loading summaries: base={a.base}, contrast={a.contrast}")
    base_summaries = load_pipeline_summaries(a.base)
    contrast_summaries = load_pipeline_summaries(a.contrast)

    base_metrics = extract_scalars(base_summaries, timeline=a.base)
    contrast_metrics = extract_scalars(contrast_summaries, timeline=a.contrast)

    deltas = compute_deltas(base_metrics, contrast_metrics)

    base_top2 = load_anomaly_top2(a.base)
    contrast_top2 = load_anomaly_top2(a.contrast)

    checks = check_acceptance(
        a.base, a.contrast, base_metrics, contrast_metrics,
        base_top2, contrast_top2,
    )

    label = f"{a.base}_vs_{a.contrast}"
    long_csv = OUTPUT_DIR / f"cross_scenario_table_{label}.csv"
    delta_csv = OUTPUT_DIR / f"cross_scenario_delta_{label}.csv"
    acc_json = OUTPUT_DIR / f"cross_scenario_acceptance_{label}.json"
    bar_png = OUTPUT_DIR / f"cross_scenario_bars_{label}.png"

    write_long_table(a.base, a.contrast, base_metrics, contrast_metrics, long_csv)
    write_delta_table(deltas, delta_csv, a.base, a.contrast)
    write_acceptance(checks, acc_json)
    write_bar_figure(a.base, a.contrast, base_metrics, contrast_metrics, bar_png)

    print(f"\nLong-format table : {long_csv}")
    print(f"Delta table       : {delta_csv}")
    print(f"Acceptance JSON   : {acc_json}")
    print(f"Bar-chart figure  : {bar_png}")
    print_acceptance_summary(checks)

    return 0 if all(c.passed for c in checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
