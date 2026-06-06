"""Phase 11 Iter A — Cross-seed replication orchestrator.

For each ``master_seed`` in ``--seeds`` (default ``42 43 44 45 46``)
this script reproduces a complete end-to-end run of the thesis pipeline
holding *every other dimension fixed*:

    1. (re)generate ``simulator/+scenarios/timelines/timeline_<base>_seed<N>.json``
       via ``tools.gen_timeline`` (timeline structure identical to
       ``timeline_<base>.json``, only ``master_seed`` differs).
    2. Run MATLAB ``build_timeline`` → parquet bundle under
       ``data/simulated/timeline_<base>_seed<N>/``.
    3. Run Phase 5 (anomaly), Phase 6 (drift), Phase 7 (adaptive),
       Phase 9 (end-to-end demo) on the seeded timeline. Each pipeline
       writes its own ``data/processed/<benchmark>_timeline_<base>_seed<N>/``
       directory.
    4. Collect the headline scalar metrics from every pipeline's
       ``benchmark_summary.json`` / ``demo_summary.json`` into a single
       wide-format CSV
       ``data/processed/external_validation/seed_replication_<base>.csv``
       that ``analysis.external_validation.seed_robustness`` consumes.

The orchestrator is idempotent and resumable: any step whose output
already exists on disk is skipped. Use ``--force`` to override.

Wall clock for the default 5-seed run on ``timeline_medium``:

    sim                ~6 min × 5 = ~30 min  (MATLAB-bound)
    anomaly benchmark  ~13 min × 5 = ~65 min
    drift benchmark    ~5 min × 5 = ~25 min
    adaptive benchmark ~5 min × 5 = ~25 min
    end2end demo       ~1 min × 5 = ~5 min
    ------------------------------------------
    total              ~2.5 h

Examples
--------
    # Default 5-seed run (resumable; re-runs only missing steps):
    python -m experiments.run_seed_replication

    # Custom seed list:
    python -m experiments.run_seed_replication --seeds 42 43 100

    # Different base timeline (e.g. dense_urban for Iter B):
    python -m experiments.run_seed_replication --base timeline_dense_urban

    # Re-run everything from scratch (overwrite outputs):
    python -m experiments.run_seed_replication --force

    # Dry run (print what would happen, do not execute):
    python -m experiments.run_seed_replication --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from analysis.common.paths import DATA_PROC, DATA_SIM, REPO_ROOT


TIMELINE_JSON_DIR: Path = REPO_ROOT / "simulator" / "+scenarios" / "timelines"
OUTPUT_DIR: Path = DATA_PROC / "external_validation"

# Phase-to-target mapping. ``output_dir_template`` formats with the
# seeded timeline name (e.g. ``timeline_medium_seed43``) and must match
# what the underlying pipeline writes.
@dataclass(frozen=True)
class PipelineSpec:
    name: str                      # human label
    module: str                    # python -m <module>
    output_subdir_template: str    # e.g. "anomaly_benchmark_{tl}"
    summary_filename: str          # JSON file written by the pipeline


PIPELINES: tuple[PipelineSpec, ...] = (
    PipelineSpec(
        name="anomaly",
        module="analysis.anomaly.benchmark_eval",
        output_subdir_template="anomaly_benchmark_{tl}",
        summary_filename="benchmark_summary.json",
    ),
    PipelineSpec(
        name="drift",
        module="analysis.drift.benchmark_eval",
        output_subdir_template="drift_benchmark_{tl}",
        summary_filename="benchmark_summary.json",
    ),
    PipelineSpec(
        name="adaptive",
        module="analysis.adaptive.benchmark_eval",
        output_subdir_template="adaptive_benchmark_{tl}",
        summary_filename="benchmark_summary.json",
    ),
    PipelineSpec(
        name="demo",
        module="analysis.demo.run_demo",
        output_subdir_template="end_to_end_demo_{tl}",
        summary_filename="demo_summary.json",
    ),
)


@dataclass
class RunConfig:
    seeds: list[int]
    base: str                      # e.g. "timeline_medium"
    rng_seed: int                  # generator-side seed; default 42 (= structure of timeline_medium)
    n_phases: int
    phase_duration_s: float
    n_ue: int
    area_m: float
    drifts_per_type: int
    anomalies_per_type: int
    head_baseline_phases: int
    tail_baseline_phases: int
    carry_over_ues: bool
    matlab_bin: str
    python_bin: str
    matlab_flags: tuple[str, ...]
    force: bool
    dry_run: bool
    skip_sim: bool
    skip_pipelines: tuple[str, ...]


def _parse_args(argv: list[str] | None = None) -> RunConfig:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seeds", type=int, nargs="+", default=[42, 43, 44, 45, 46])
    p.add_argument("--base", default="timeline_medium",
                   help='Base timeline name to vary the seed of (default: "timeline_medium").')
    p.add_argument("--rng-seed", type=int, default=42,
                   help="Generator-side seed (controls timeline structure). Keep fixed for "
                        "cross-seed validation; varying it confounds structure with sim noise.")
    p.add_argument("--n-phases", type=int, default=30)
    p.add_argument("--phase-duration-s", type=float, default=60.0)
    p.add_argument("--n-ue", type=int, default=12)
    p.add_argument("--area-m", type=float, default=1500.0)
    p.add_argument("--drifts-per-type", type=int, default=2)
    p.add_argument("--anomalies-per-type", type=int, default=10)
    p.add_argument("--head-baseline-phases", type=int, default=3)
    p.add_argument("--tail-baseline-phases", type=int, default=3)
    p.add_argument("--no-carry-over-ues", action="store_true",
                   help="Disable cross-phase UE position threading (default: enabled).")
    p.add_argument("--matlab-bin", default="/home/sensoy/MATLAB/R2023b/bin/matlab")
    p.add_argument("--python-bin", default=sys.executable)
    p.add_argument("--matlab-flags", default="-batch",
                   help="MATLAB invocation flag(s), space-separated (default: -batch).")
    p.add_argument("--force", action="store_true",
                   help="Re-run every step even if outputs already exist.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print intended actions without executing.")
    p.add_argument("--skip-sim", action="store_true",
                   help="Skip MATLAB sim (assume parquets are already present).")
    p.add_argument("--skip-pipeline", choices=[p.name for p in PIPELINES],
                   action="append", default=[],
                   help="Skip one or more downstream pipelines by name. May repeat.")
    a = p.parse_args(argv)
    return RunConfig(
        seeds=a.seeds,
        base=a.base,
        rng_seed=a.rng_seed,
        n_phases=a.n_phases,
        phase_duration_s=a.phase_duration_s,
        n_ue=a.n_ue,
        area_m=a.area_m,
        drifts_per_type=a.drifts_per_type,
        anomalies_per_type=a.anomalies_per_type,
        head_baseline_phases=a.head_baseline_phases,
        tail_baseline_phases=a.tail_baseline_phases,
        carry_over_ues=not a.no_carry_over_ues,
        matlab_bin=a.matlab_bin,
        python_bin=a.python_bin,
        matlab_flags=tuple(a.matlab_flags.split()),
        force=a.force,
        dry_run=a.dry_run,
        skip_sim=a.skip_sim,
        skip_pipelines=tuple(a.skip_pipeline),
    )


# ---------------------------------------------------------------------------
# Step runners (each is idempotent: skip if output present unless --force)
# ---------------------------------------------------------------------------

def _timeline_name(cfg: RunConfig, seed: int) -> str:
    return f"{cfg.base}_seed{seed}"


def _timeline_json_path(cfg: RunConfig, seed: int) -> Path:
    return TIMELINE_JSON_DIR / f"{_timeline_name(cfg, seed)}.json"


def _sim_output_dir(cfg: RunConfig, seed: int) -> Path:
    return DATA_SIM / _timeline_name(cfg, seed)


def _pipeline_output_dir(cfg: RunConfig, seed: int, pl: PipelineSpec) -> Path:
    return DATA_PROC / pl.output_subdir_template.format(tl=_timeline_name(cfg, seed))


def _run(argv: list[str], *, dry_run: bool, log_prefix: str = "") -> int:
    """Subprocess wrapper that streams stdout/stderr live and returns exit code."""
    pretty = " ".join(str(a) for a in argv)
    print(f"{log_prefix}$ {pretty}", flush=True)
    if dry_run:
        return 0
    t0 = time.perf_counter()
    rc = subprocess.call(argv)
    dt = time.perf_counter() - t0
    print(f"{log_prefix}-> exit={rc} ({dt:,.1f}s)", flush=True)
    return rc


def step_generate_timeline(cfg: RunConfig, seed: int) -> Path:
    """Generate the seeded timeline JSON if missing (or --force)."""
    out_json = _timeline_json_path(cfg, seed)
    if out_json.exists() and not cfg.force:
        print(f"[seed={seed}] gen-timeline: {out_json} exists, skipping.")
        return out_json
    argv = [
        cfg.python_bin, "-m", "tools.gen_timeline",
        "--out", str(out_json),
        "--timeline-id", _timeline_name(cfg, seed),
        "--description",
        f"Phase 11 Iter A cross-seed replication. Same structure as "
        f"{cfg.base} (rng_seed={cfg.rng_seed}); master_seed={seed}.",
        "--n-phases", str(cfg.n_phases),
        "--phase-duration-s", str(cfg.phase_duration_s),
        "--master-seed", str(seed),
        "--rng-seed", str(cfg.rng_seed),
        "--n-ue", str(cfg.n_ue),
        "--area-m", str(cfg.area_m),
        "--carry-over-ues", "true" if cfg.carry_over_ues else "false",
        "--drifts-per-type", str(cfg.drifts_per_type),
        "--anomalies-per-type", str(cfg.anomalies_per_type),
        "--head-baseline-phases", str(cfg.head_baseline_phases),
        "--tail-baseline-phases", str(cfg.tail_baseline_phases),
    ]
    rc = _run(argv, dry_run=cfg.dry_run, log_prefix=f"[seed={seed}] ")
    if rc != 0:
        raise RuntimeError(f"gen_timeline failed for seed={seed} (rc={rc})")
    return out_json


def step_run_sim(cfg: RunConfig, seed: int, timeline_json: Path) -> Path:
    """Run MATLAB ``build_timeline`` if outputs missing (or --force)."""
    out_dir = _sim_output_dir(cfg, seed)
    out_samples = out_dir / "samples.parquet"
    out_events = out_dir / "events.parquet"
    if cfg.skip_sim:
        if not (out_samples.exists() and out_events.exists()):
            raise FileNotFoundError(
                f"--skip-sim was set but seed={seed} parquets are missing under {out_dir}"
            )
        print(f"[seed={seed}] sim: --skip-sim → assume {out_dir} is ready.")
        return out_dir
    if out_samples.exists() and out_events.exists() and not cfg.force:
        print(f"[seed={seed}] sim: {out_dir} present, skipping.")
        return out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    matlab_cmd = (
        f"addpath(genpath('{REPO_ROOT}/simulator')); "
        f"build_timeline('{timeline_json}', '{out_dir}')"
    )
    argv = [cfg.matlab_bin, *cfg.matlab_flags, matlab_cmd]
    rc = _run(argv, dry_run=cfg.dry_run, log_prefix=f"[seed={seed}] sim: ")
    if rc != 0:
        raise RuntimeError(f"MATLAB sim failed for seed={seed} (rc={rc})")
    return out_dir


def step_run_pipeline(cfg: RunConfig, seed: int, pl: PipelineSpec) -> Path:
    """Run one Python pipeline if its summary is missing (or --force)."""
    out_dir = _pipeline_output_dir(cfg, seed, pl)
    summary = out_dir / pl.summary_filename
    if pl.name in cfg.skip_pipelines:
        print(f"[seed={seed}] {pl.name}: --skip-pipeline={pl.name}, skipping.")
        return out_dir
    if summary.exists() and not cfg.force:
        print(f"[seed={seed}] {pl.name}: {summary} present, skipping.")
        return out_dir
    if cfg.force and out_dir.exists():
        # Clear stale partial outputs before re-running.
        shutil.rmtree(out_dir)
    argv = [
        cfg.python_bin, "-m", pl.module,
        "--timeline", _timeline_name(cfg, seed),
    ]
    rc = _run(argv, dry_run=cfg.dry_run, log_prefix=f"[seed={seed}] {pl.name}: ")
    if rc != 0:
        raise RuntimeError(f"pipeline '{pl.name}' failed for seed={seed} (rc={rc})")
    return out_dir


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------

def extract_metrics_for_seed(cfg: RunConfig, seed: int) -> dict[str, object]:
    """Read all pipeline summaries for one seed and return a flat dict.

    Keys are namespaced ``<pipeline>__<metric>`` so the aggregator can
    pivot on (seed, metric) cleanly.
    """
    row: dict[str, object] = {
        "seed": seed,
        "timeline": _timeline_name(cfg, seed),
    }

    for pl in PIPELINES:
        out_dir = _pipeline_output_dir(cfg, seed, pl)
        summary_path = out_dir / pl.summary_filename
        if not summary_path.exists():
            row[f"{pl.name}__missing"] = True
            continue
        with open(summary_path) as f:
            doc = json.load(f)
        if pl.name == "anomaly":
            # Phase 11 Iter A revealed two complementary winners (see
            # benchmark_eval.py for the Simpson's-paradox rationale).
            # Prefer fields written by the new benchmark; fall back to
            # the legacy alias and finally to a re-derivation from
            # overall_pr_auc_with_ci.csv for old runs.
            per_type_winner = (
                doc.get("winning_detector_by_per_type_mean_pr_auc")
                or doc.get("winning_detector_by_mean_pr_auc")
            )
            overall_winner = doc.get("winning_detector_by_overall_pr_auc")
            if overall_winner is None:
                overall_csv = out_dir / "overall_pr_auc_with_ci.csv"
                if overall_csv.is_file():
                    import csv as _csv
                    with open(overall_csv) as _f:
                        rows = list(_csv.DictReader(_f))
                    if rows:
                        try:
                            top = max(rows, key=lambda r: float(r["pr_auc"]))
                            overall_winner = top.get("detector")
                        except (KeyError, ValueError):
                            overall_winner = None
            row["anomaly__winning_detector_per_type_mean"] = per_type_winner
            row["anomaly__winning_detector_overall"] = overall_winner
            # Back-compat: keep the original column name pointing at the
            # legacy per-type-mean winner.
            row["anomaly__winning_detector"] = per_type_winner
            row["anomaly__elapsed_s"] = doc.get("elapsed_s")
        elif pl.name == "drift":
            row["drift__winning_detector"] = doc.get("winning_detector")
            row["drift__n_detections"] = doc.get("n_detections")
            row["drift__n_drifts"] = doc.get("n_drifts")
            row["drift__elapsed_s"] = doc.get("elapsed_s")
        elif pl.name == "adaptive":
            strats = doc.get("strategies", []) or []
            for s in strats:
                sid = s.get("strategy") or "unknown"
                row[f"adaptive__{sid}__overall_pr_auc"] = s.get("overall_pr_auc")
                row[f"adaptive__{sid}__drift_phase_pr_auc"] = s.get("drift_phase_pr_auc")
                row[f"adaptive__{sid}__n_refits"] = s.get("cost__n_refits")
                row[f"adaptive__{sid}__fit_cpu_s_sum"] = s.get("cost__fit_cpu_s_sum")
            # Derived: filtered vs naive delta (drift-window PR-AUC).
            f_val = row.get("adaptive__drift_triggered_filtered__drift_phase_pr_auc")
            n_val = row.get("adaptive__drift_triggered_naive__drift_phase_pr_auc")
            if isinstance(f_val, (int, float)) and isinstance(n_val, (int, float)):
                row["adaptive__delta_filtered_minus_naive"] = float(f_val) - float(n_val)
        elif pl.name == "demo":
            upl = doc.get("cumulative_predicted_uplift", {}) or {}
            row["demo__cum_uplift_hosr"] = upl.get("hosr")
            row["demo__cum_uplift_rlf_rate"] = upl.get("rlf_rate")
            row["demo__n_interventions"] = doc.get("n_interventions")
            row["demo__n_retrains"] = doc.get("n_retrains")
            acc = doc.get("acceptance", {}) or {}
            for k, v in acc.items():
                row[f"demo__acceptance__{k}"] = bool(v.get("pass")) if isinstance(v, dict) else None
    return row


def write_summary_csv(rows: list[dict[str, object]], out_path: Path) -> None:
    """Write the seed-vs-metric wide table without a heavy pandas import."""
    if not rows:
        out_path.write_text("seed\n")
        return
    keys: list[str] = ["seed", "timeline"]
    seen = set(keys)
    for r in rows:
        for k in r:
            if k not in seen:
                keys.append(k)
                seen.add(k)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        f.write(",".join(keys) + "\n")
        for r in rows:
            f.write(",".join(_csv_cell(r.get(k)) for k in keys) + "\n")


def merge_with_existing_csv(
    new_rows: list[dict[str, object]], csv_path: Path
) -> list[dict[str, object]]:
    """Merge ``new_rows`` into an existing wide CSV by ``seed``.

    Idempotent + resumable: re-running ``run_seed_replication`` on a
    subset of seeds (e.g. just one failed seed) used to OVERWRITE the
    CSV with only that subset. This helper preserves rows from previous
    runs whose ``seed`` is not in ``new_rows``, then adds/replaces the
    new ones. Pure-Python (no pandas) to keep the orchestrator light.
    """
    if not csv_path.is_file():
        return list(new_rows)
    import csv

    existing_rows: list[dict[str, object]] = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            # Coerce types coming back from the on-disk CSV
            coerced: dict[str, object] = {}
            for k, v in r.items():
                if v == "" or v is None:
                    coerced[k] = None
                elif v == "true":
                    coerced[k] = True
                elif v == "false":
                    coerced[k] = False
                else:
                    try:
                        if "." in v or "e" in v.lower():
                            coerced[k] = float(v)
                        else:
                            coerced[k] = int(v)
                    except (TypeError, ValueError):
                        coerced[k] = v
            existing_rows.append(coerced)

    new_seeds = {int(r["seed"]) for r in new_rows if r.get("seed") is not None}
    merged: list[dict[str, object]] = []
    for r in existing_rows:
        try:
            s = int(r.get("seed"))
        except (TypeError, ValueError):
            continue
        if s not in new_seeds:
            merged.append(r)
    merged.extend(new_rows)
    merged.sort(key=lambda r: int(r.get("seed", 0)))
    return merged


def _csv_cell(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:.6g}"
    s = str(v)
    if "," in s or "\n" in s or '"' in s:
        return '"' + s.replace('"', '""') + '"'
    return s


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_all(cfg: RunConfig) -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    rows: list[dict[str, object]] = []
    failures: list[tuple[int, str]] = []

    print("=" * 72)
    print(f"Phase 11 Iter A — cross-seed replication")
    print(f"  base timeline    : {cfg.base}")
    print(f"  rng_seed (struct): {cfg.rng_seed} (held constant)")
    print(f"  master_seeds     : {cfg.seeds}")
    print(f"  force            : {cfg.force}")
    print(f"  dry-run          : {cfg.dry_run}")
    print("=" * 72)

    for seed in cfg.seeds:
        print(f"\n--- seed={seed} ---")
        try:
            tl_json = step_generate_timeline(cfg, seed)
            step_run_sim(cfg, seed, tl_json)
            for pl in PIPELINES:
                step_run_pipeline(cfg, seed, pl)
        except Exception as exc:
            failures.append((seed, str(exc)))
            print(f"[seed={seed}] FAIL: {exc}", file=sys.stderr, flush=True)
            continue

        if not cfg.dry_run:
            rows.append(extract_metrics_for_seed(cfg, seed))

    elapsed = time.perf_counter() - t0
    print(f"\n=== Done in {elapsed/60:.1f} min ({len(rows)} seeds succeeded, "
          f"{len(failures)} failed) ===")

    if not cfg.dry_run:
        csv_out = OUTPUT_DIR / f"seed_replication_{cfg.base}.csv"
        merged = merge_with_existing_csv(rows, csv_out)
        write_summary_csv(merged, csv_out)
        n_new = len(rows)
        n_total = len(merged)
        n_carried = n_total - n_new
        print(f"\nWide metrics table: {csv_out} "
              f"({n_total} total seeds: {n_new} from this run, "
              f"{n_carried} carried over)")
        print("\nNext step:")
        print(f"  python -m analysis.external_validation.seed_robustness "
              f"--in {csv_out}")

    if failures:
        print("\nFailed seeds:", file=sys.stderr)
        for s, err in failures:
            print(f"  seed={s}: {err}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    cfg = _parse_args(argv)
    return run_all(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
