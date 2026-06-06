"""Phase 11 Iter A — Cross-seed robustness aggregator.

Consumes the wide CSV produced by ``experiments.run_seed_replication``
(one row per seed, one column per ``<pipeline>__<metric>``) and
produces:

    * ``seed_robustness_table.csv``         per-metric mean ± 95 % CI
                                            (percentile bootstrap)
    * ``seed_robustness_acceptance.json``   E1..E5 pass/fail dict
    * ``seed_robustness_boxplot.png``       6-panel box-plot figure
                                            (key adaptive + demo metrics)
    * ``seed_robustness_detector_table.csv`` anomaly-detector ranking
                                             stability across seeds

Bootstrap CI methodology
------------------------
Percentile bootstrap with B = 5 000 resamples (with replacement) over
the per-seed values. Reports mean, std, [p2.5, p97.5]. CI is
"distribution-free" — no normality assumption — which suits us because
with only 5 seeds, sample-size-asymptotic intervals (t-CI) would be
wide and uninformative anyway.

Acceptance criteria (Phase 11 Iter A)
-------------------------------------
    E1:  all seeds in input ran every pipeline (no ``<pipeline>__missing``).
    E2a: anomaly winner stable when ranking by per-anomaly-type mean
         PR-AUC (modal share >= 0.80). This metric weights every
         anomaly family equally regardless of positive count, so it is
         the noisier of the two anomaly-winner definitions when the
         top detectors cluster within ~0.03 PR-AUC.
    E2b: anomaly winner stable when ranking by overall (pooled-timeline)
         PR-AUC (modal share >= 0.80). Reflects "deploy ONE detector
         for everything" semantics and is the metric most operators
         would care about.
    E3:  filtered > naive replicates
         (mean of ``adaptive__delta_filtered_minus_naive`` > 0
          AND bootstrap-CI lower bound > 0).
    E4:  Phase 9 cumulative HOSR uplift replicates
         (mean ``demo__cum_uplift_hosr`` > 0
          AND bootstrap-CI lower bound > 0).
    E5:  drift-detector volume stable
         (CV(``drift__n_detections``) across seeds < 0.30).

Usage
-----
    python -m analysis.external_validation.seed_robustness \\
        --in data/processed/external_validation/seed_replication_timeline_medium.csv

Or via Makefile (added by Phase 11 patch):

    make seed-replication-aggregate
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np

from analysis.common.paths import DATA_PROC


OUTPUT_DIR: Path = DATA_PROC / "external_validation"


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def load_replication_csv(path: Path) -> list[dict[str, object]]:
    """Load the wide CSV produced by run_seed_replication, coercing types."""
    rows: list[dict[str, object]] = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            out: dict[str, object] = {}
            for k, v in r.items():
                if v == "" or v is None:
                    out[k] = None
                elif v == "true":
                    out[k] = True
                elif v == "false":
                    out[k] = False
                else:
                    try:
                        out[k] = int(v) if "." not in v and "e" not in v.lower() else float(v)
                    except ValueError:
                        try:
                            out[k] = float(v)
                        except ValueError:
                            out[k] = v
            rows.append(out)
    return rows


# ---------------------------------------------------------------------------
# Bootstrap CI
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BootstrapStat:
    metric: str
    n: int
    mean: float
    std: float
    ci_lo: float
    ci_hi: float
    raw: tuple[float, ...]


def bootstrap_mean_ci(
    values: Iterable[float],
    *,
    n_bootstrap: int = 5_000,
    alpha: float = 0.05,
    rng_seed: int = 42,
) -> tuple[float, float, float, float]:
    """Percentile-bootstrap CI for the mean.

    Returns ``(mean, std, ci_lo, ci_hi)`` where the CI is the
    ``[alpha/2, 1-alpha/2]`` quantile of bootstrap-sample means.
    Returns ``(nan, nan, nan, nan)`` if fewer than 2 numeric values.
    """
    xs = np.asarray([float(v) for v in values if v is not None and not _isnan(v)],
                    dtype=float)
    if len(xs) < 2:
        return (float("nan"), float("nan"), float("nan"), float("nan"))
    rng = np.random.default_rng(rng_seed)
    idx = rng.integers(0, len(xs), size=(n_bootstrap, len(xs)))
    boots = xs[idx].mean(axis=1)
    lo = float(np.quantile(boots, alpha / 2.0))
    hi = float(np.quantile(boots, 1.0 - alpha / 2.0))
    return (float(xs.mean()), float(xs.std(ddof=1)), lo, hi)


def _isnan(x: object) -> bool:
    try:
        return isinstance(x, float) and math.isnan(x)
    except Exception:
        return False


def compute_bootstrap_table(rows: list[dict[str, object]]) -> list[BootstrapStat]:
    """Pivot the wide CSV by metric and bootstrap the mean across seeds.

    Skips:
        - non-numeric columns (strings, booleans, ``seed``, ``timeline``)
        - columns where fewer than 2 numeric values are available
    """
    skip = {"seed", "timeline"}
    metric_names: list[str] = []
    for r in rows:
        for k in r:
            if k in skip or k in metric_names:
                continue
            if k.endswith("__winning_detector") or k.endswith("__missing"):
                continue
            metric_names.append(k)

    out: list[BootstrapStat] = []
    for m in metric_names:
        vals_raw = [r.get(m) for r in rows]
        vals: list[float] = []
        for v in vals_raw:
            if isinstance(v, bool):
                vals.append(1.0 if v else 0.0)
            elif isinstance(v, (int, float)) and not _isnan(v):
                vals.append(float(v))
        mean, std, lo, hi = bootstrap_mean_ci(vals)
        out.append(BootstrapStat(metric=m, n=len(vals), mean=mean, std=std,
                                 ci_lo=lo, ci_hi=hi, raw=tuple(vals)))
    return out


# ---------------------------------------------------------------------------
# Detector ranking stability
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DetectorRankingStability:
    pipeline: str             # "anomaly" or "drift"
    winners: Counter          # detector_name -> seed_count
    n_seeds: int
    modal_share: float        # max seed_count / n_seeds
    stable: bool              # True if modal_share >= 4/5


def compute_detector_stability(
    rows: list[dict[str, object]],
    *,
    threshold: float = 4 / 5,
) -> dict[str, DetectorRankingStability]:
    """Compute modal-share stability for each "winner column" in ``rows``.

    Pipeline keys recognized:
      * ``anomaly__per_type_mean`` -- detector that maximises the mean
        of per-anomaly-type PR-AUC (equal weight per anomaly family).
      * ``anomaly__overall`` -- detector that maximises pooled-timeline
        PR-AUC (mass-weighted across anomaly families).
      * ``anomaly`` -- back-compat alias; reads the legacy column
        ``anomaly__winning_detector`` (which points at per-type-mean).
      * ``drift`` -- best drift detector by ADWIN / batch winner.

    Phase 11 Iter A surfaced that ``anomaly__per_type_mean`` and
    ``anomaly__overall`` can disagree (Simpson's paradox between
    per-type vs pooled aggregation). Reporting BOTH is intentional:
    they answer different operational questions.
    """
    column_map = {
        "anomaly": "anomaly__winning_detector",
        "anomaly__per_type_mean": "anomaly__winning_detector_per_type_mean",
        "anomaly__overall": "anomaly__winning_detector_overall",
        "drift": "drift__winning_detector",
    }
    out: dict[str, DetectorRankingStability] = {}
    for pipeline_key, col in column_map.items():
        winners_raw = [r.get(col) for r in rows]
        winners = [w for w in winners_raw if isinstance(w, str)]
        if not winners:
            continue
        ctr = Counter(winners)
        modal_share = max(ctr.values()) / len(winners)
        out[pipeline_key] = DetectorRankingStability(
            pipeline=pipeline_key,
            winners=ctr,
            n_seeds=len(winners),
            modal_share=modal_share,
            stable=modal_share >= threshold,
        )
    return out


# ---------------------------------------------------------------------------
# Acceptance check (E1..E5)
# ---------------------------------------------------------------------------

@dataclass
class AcceptanceCheck:
    code: str
    name: str
    passed: bool
    actual: str
    target: str


def check_acceptance(
    rows: list[dict[str, object]],
    stats: list[BootstrapStat],
    rankings: dict[str, DetectorRankingStability],
) -> list[AcceptanceCheck]:
    out: list[AcceptanceCheck] = []
    stat_by_metric = {s.metric: s for s in stats}

    # E1: every seed ran every pipeline (no <pipeline>__missing flags set)
    pipelines = ["anomaly", "drift", "adaptive", "demo"]
    n_missing = 0
    for r in rows:
        for p in pipelines:
            if bool(r.get(f"{p}__missing")):
                n_missing += 1
    out.append(AcceptanceCheck(
        code="E1",
        name="all seeds ran every pipeline",
        passed=(n_missing == 0 and len(rows) > 0),
        actual=f"{len(rows)} seeds, {n_missing} (pipeline, seed) misses",
        target="0 misses",
    ))

    # E2a: anomaly per-type-mean winner stable across seeds.
    # Tight top-cluster (LOF / OCSVM / PCA-AE / MLP-AE all within ~0.03
    # PR-AUC of each other in Phase 5) means rank-1 jitters seed-to-seed.
    # FAIL here is a known feature, not a fragility of the framework.
    a_pt = rankings.get("anomaly__per_type_mean") or rankings.get("anomaly")
    if a_pt is None:
        out.append(AcceptanceCheck(
            code="E2a",
            name="anomaly winner stable (per-type-mean PR-AUC)",
            passed=False,
            actual="no per-type-mean winners recorded",
            target=">= 0.80 modal share",
        ))
    else:
        out.append(AcceptanceCheck(
            code="E2a",
            name="anomaly winner stable (per-type-mean PR-AUC)",
            passed=a_pt.stable,
            actual=f"modal_share={a_pt.modal_share:.2f} "
                   f"(winners: {dict(a_pt.winners)})",
            target=">= 0.80",
        ))

    # E2b: anomaly overall (pooled) winner stable across seeds.
    # Reflects "deploy ONE detector for everything" semantics — what
    # an operator actually sees. Expected to be much more stable than
    # E2a because pooled PR-AUC is mass-weighted.
    a_ov = rankings.get("anomaly__overall")
    if a_ov is None:
        out.append(AcceptanceCheck(
            code="E2b",
            name="anomaly winner stable (overall pooled PR-AUC)",
            passed=False,
            actual="no overall winners recorded "
                   "(re-run benchmark or seed replication to populate)",
            target=">= 0.80 modal share",
        ))
    else:
        out.append(AcceptanceCheck(
            code="E2b",
            name="anomaly winner stable (overall pooled PR-AUC)",
            passed=a_ov.stable,
            actual=f"modal_share={a_ov.modal_share:.2f} "
                   f"(winners: {dict(a_ov.winners)})",
            target=">= 0.80",
        ))

    # E3: filtered > naive across seeds
    delta = stat_by_metric.get("adaptive__delta_filtered_minus_naive")
    if delta is None or math.isnan(delta.mean):
        out.append(AcceptanceCheck(
            code="E3", name="filtered > naive replicates",
            passed=False,
            actual="no delta_filtered_minus_naive column",
            target="mean > 0 and CI_lo > 0",
        ))
    else:
        passed = delta.mean > 0 and delta.ci_lo > 0
        out.append(AcceptanceCheck(
            code="E3", name="filtered > naive replicates",
            passed=passed,
            actual=f"mean={delta.mean:+.4f} CI95=[{delta.ci_lo:+.4f}, {delta.ci_hi:+.4f}]",
            target="mean > 0 and CI_lo > 0",
        ))

    # E4: Phase 9 cumulative HOSR uplift replicates
    upl = stat_by_metric.get("demo__cum_uplift_hosr")
    if upl is None or math.isnan(upl.mean):
        out.append(AcceptanceCheck(
            code="E4", name="Phase 9 cumulative HOSR uplift replicates",
            passed=False, actual="no demo__cum_uplift_hosr column",
            target="mean > 0 and CI_lo > 0",
        ))
    else:
        passed = upl.mean > 0 and upl.ci_lo > 0
        out.append(AcceptanceCheck(
            code="E4", name="Phase 9 cumulative HOSR uplift replicates",
            passed=passed,
            actual=f"mean={upl.mean:+.4f} CI95=[{upl.ci_lo:+.4f}, {upl.ci_hi:+.4f}]",
            target="mean > 0 and CI_lo > 0",
        ))

    # E5: drift detection volume stable (CV < 0.30 across seeds)
    n_det = stat_by_metric.get("drift__n_detections")
    if n_det is None or math.isnan(n_det.mean) or n_det.mean <= 0:
        out.append(AcceptanceCheck(
            code="E5", name="drift detection volume stable",
            passed=False, actual="no drift__n_detections or mean<=0",
            target="CV across seeds < 0.30",
        ))
    else:
        cv = n_det.std / n_det.mean if n_det.mean != 0 else float("inf")
        out.append(AcceptanceCheck(
            code="E5", name="drift detection volume stable",
            passed=cv < 0.30,
            actual=f"mean={n_det.mean:.1f} std={n_det.std:.2f} CV={cv:.2f}",
            target="CV < 0.30",
        ))

    return out


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_bootstrap_table(stats: list[BootstrapStat], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "n_seeds", "mean", "std", "ci_lo_95", "ci_hi_95",
                    "raw_values"])
        for s in stats:
            w.writerow([
                s.metric, s.n, _fmt(s.mean), _fmt(s.std),
                _fmt(s.ci_lo), _fmt(s.ci_hi),
                ";".join(_fmt(v) for v in s.raw),
            ])


def write_detector_table(
    rankings: dict[str, DetectorRankingStability], out_path: Path
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pipeline", "detector", "wins", "n_seeds",
                    "modal_share", "stable"])
        for pl, r in rankings.items():
            for det, count in r.winners.most_common():
                w.writerow([pl, det, count, r.n_seeds,
                            _fmt(r.modal_share), str(r.stable).lower()])


def write_acceptance_json(checks: list[AcceptanceCheck], out_path: Path) -> None:
    doc = {c.code: {
        "name": c.name, "passed": c.passed,
        "actual": c.actual, "target": c.target,
    } for c in checks}
    doc["_all_pass"] = all(c.passed for c in checks)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(doc, f, indent=2)


def write_boxplot(
    rows: list[dict[str, object]], out_path: Path
) -> None:
    """6-panel boxplot of key headline metrics across seeds.

    Lazy-imports matplotlib so the rest of the module stays test-friendly.
    """
    if not rows:
        return
    panels = [
        ("adaptive__static__drift_phase_pr_auc", "Static\nstrategy"),
        ("adaptive__periodic__drift_phase_pr_auc", "Periodic\nstrategy"),
        ("adaptive__drift_triggered_naive__drift_phase_pr_auc", "Drift-trig\nnaive"),
        ("adaptive__drift_triggered_filtered__drift_phase_pr_auc", "Drift-trig\nfiltered"),
        ("demo__cum_uplift_hosr", "Demo cum\nHOSR uplift"),
        ("drift__n_detections", "Drift\n#detections"),
    ]
    series: list[tuple[str, list[float]]] = []
    for col, label in panels:
        vals = []
        for r in rows:
            v = r.get(col)
            if isinstance(v, (int, float)) and not _isnan(v):
                vals.append(float(v))
        series.append((label, vals))

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - matplotlib missing
        print(f"WARN: matplotlib unavailable, skipping figure ({exc}).")
        return

    fig, axes = plt.subplots(2, 3, figsize=(12, 7), sharey=False)
    axes_flat = axes.flatten()
    for ax, (label, vals) in zip(axes_flat, series):
        if vals:
            ax.boxplot(vals, vert=True, widths=0.5)
            for v in vals:
                ax.plot(1, v, "o", color="C1", alpha=0.6, markersize=6)
        ax.set_title(label, fontsize=10)
        ax.set_xticks([])
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle(
        "Phase 11 Iter A — Cross-seed robustness "
        f"(n_seeds = {len(rows)}, structure held fixed)",
        fontsize=12,
    )
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


def _fmt(v: float | int | None) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        return f"{v:.6g}"
    return str(v)


# ---------------------------------------------------------------------------
# Pretty-print acceptance summary
# ---------------------------------------------------------------------------

def print_acceptance_summary(checks: list[AcceptanceCheck]) -> None:
    width = max(len(c.actual) for c in checks) if checks else 30
    print()
    print("Phase 11 Iter A acceptance:")
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
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--in", dest="in_path", required=True,
                   help="seed_replication_<base>.csv produced by experiments.run_seed_replication")
    p.add_argument("--label", default=None,
                   help="Output label suffix (defaults to base timeline name parsed from input file).")
    a = p.parse_args(argv)

    in_path = Path(a.in_path)
    if not in_path.is_file():
        raise FileNotFoundError(f"input CSV not found: {in_path}")

    rows = load_replication_csv(in_path)
    print(f"Loaded {len(rows)} seed rows from {in_path}")
    if not rows:
        print("Empty input -> no work to do.")
        return 1

    label = a.label or in_path.stem.removeprefix("seed_replication_")
    out_table = OUTPUT_DIR / f"seed_robustness_table_{label}.csv"
    out_detectors = OUTPUT_DIR / f"seed_robustness_detector_table_{label}.csv"
    out_acc = OUTPUT_DIR / f"seed_robustness_acceptance_{label}.json"
    out_boxplot = OUTPUT_DIR / f"seed_robustness_boxplot_{label}.png"

    stats = compute_bootstrap_table(rows)
    rankings = compute_detector_stability(rows)
    checks = check_acceptance(rows, stats, rankings)

    write_bootstrap_table(stats, out_table)
    write_detector_table(rankings, out_detectors)
    write_acceptance_json(checks, out_acc)
    write_boxplot(rows, out_boxplot)

    print(f"\nBootstrap table   : {out_table}")
    print(f"Detector ranking  : {out_detectors}")
    print(f"Acceptance JSON   : {out_acc}")
    print(f"Box-plot figure   : {out_boxplot}")
    print_acceptance_summary(checks)

    return 0 if all(c.passed for c in checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
