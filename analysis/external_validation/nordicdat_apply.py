"""Phase 11 Iter C — NordicDat face-validity inference.

Applies the thesis detectors (ADWIN for fleet-level drift; PCA-based
reconstruction-error detector for window-level anomalies) to NordicDat
operational 5G-NSA telemetry **without ground-truth labels**. The
claim produced by this script is *plausibility*, not precision/recall.

Pipeline
--------
1. Load NordicDat per-sample feed via ``analysis.calibration.load_nordicdat``.
2. Restrict to one segment (default: ``operator_id=1`` ∩ ``ran_type='5G-NSA'``).
3. Resample to 1 s fleet-level streams (mean RSRP / mean SINR / handover
   proxy rate / segment-mean cell count).
4. Apply ADWIN to RSRP_mean and SINR_mean streams; record (stream, t)
   change points.
5. Train a ``PCAReconstructionDetector`` (Phase 5) on the first
   ``--train-s`` seconds of windowed features (clean-calibration
   assumption); apply inference to the remaining windows; flag any
   window whose score >= percentile-99 threshold.
6. Persist:

    * ``data/processed/external_validation/nordicdat_drift_events.csv``
    * ``data/processed/external_validation/nordicdat_anomaly_windows.csv``
    * ``data/processed/external_validation/nordicdat_face_validity.json``
      (G1..G3 acceptance check)
    * ``data/processed/external_validation/nordicdat_face_validity.png``
      (2-panel timeline: RSRP_mean stream + detected events overlay)
    * ``data/processed/external_validation/nordicdat_face_validity.md``
      (human-readable interpretation + caveats)

Acceptance (Phase 11 Iter C)
----------------------------
    G1 (volume sanity): anomaly detection rate < 10 % of windows.
    G2 (qualitative plausibility): >= 1 interpretable temporal
        pattern in the detected events (e.g. higher event rate during
        rush-hour buckets vs. off-peak; or correlation with known
        service-status code transitions).
    G3 (no production-claim overshoot): output write-up explicitly
        bounds the claim to "consistent with operational telemetry;
        precision/recall unknown without labels". Hard-coded in the
        ``.md`` template; a unit test verifies the disclaimer string
        is present.

Usage
-----
    python -m analysis.external_validation.nordicdat_apply \\
        --operator 1 --ran 5G-NSA --train-s 600

NB: the actual NordicDat CSV must be present at
``data/raw_public/nordicdat/nordicdat_readable.csv`` (see
``data/raw_public/README.md``).
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from analysis.calibration.load_nordicdat import (
    detect_cell_transitions,
    load_nordicdat,
)
from analysis.common.paths import DATA_PROC


OUTPUT_DIR: Path = DATA_PROC / "external_validation"
DISCLAIMER_STR = (
    "no ground-truth labels available for NordicDat; precision/recall "
    "are not reported and cannot be claimed without operator event logs."
)


# ---------------------------------------------------------------------------
# Stream construction
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FleetStreams:
    t_s: np.ndarray             # second-of-segment (zero-based)
    rsrp_mean: np.ndarray
    sinr_mean: np.ndarray
    ho_rate: np.ndarray         # HOs per second within sliding 60-s window
    n_unique_cells: np.ndarray  # |{serving_cell}| within sliding 60-s window
    bucket_hour: np.ndarray     # local hour (0..23) for each bucket — proxy for time-of-day
    n_samples: np.ndarray       # raw NordicDat samples per 1 s bucket (sanity)


def build_fleet_streams(
    df: pd.DataFrame,
    *,
    bucket_s: float = 1.0,
    ho_window_s: float = 60.0,
) -> FleetStreams:
    """Resample NordicDat per-sample feed into fleet-level 1 s streams.

    Args:
        df: DataFrame from ``load_nordicdat`` restricted to one segment.
        bucket_s: time-grid bucket width in seconds (default 1.0).
        ho_window_s: rolling window for HO-rate counter (default 60.0).

    Returns:
        ``FleetStreams`` with co-indexed numpy arrays.
    """
    if df.empty:
        return FleetStreams(
            t_s=np.array([]), rsrp_mean=np.array([]),
            sinr_mean=np.array([]), ho_rate=np.array([]),
            n_unique_cells=np.array([]), bucket_hour=np.array([]),
            n_samples=np.array([]),
        )

    df = df.sort_values("time_s").reset_index(drop=True)
    t0 = float(df["time_s"].iloc[0])
    t_seg = (df["time_s"] - t0).to_numpy()
    n_buckets = int(np.ceil((t_seg[-1] + 1e-9) / bucket_s))
    bucket_idx = np.minimum((t_seg / bucket_s).astype(int), n_buckets - 1)

    rsrp = df["rsrp_serving_dbm"].to_numpy()
    sinr = df["sinr_serving_db"].to_numpy()
    serving = pd.to_numeric(df["serving_cell_id"], errors="coerce").to_numpy()

    rsrp_mean = np.full(n_buckets, np.nan)
    sinr_mean = np.full(n_buckets, np.nan)
    n_unique = np.zeros(n_buckets, dtype=float)
    counts = np.zeros(n_buckets, dtype=int)

    # Pre-aggregate buckets via groupby for speed
    bdf = pd.DataFrame({"bucket": bucket_idx, "rsrp": rsrp,
                        "sinr": sinr, "cell": serving})
    grp = bdf.groupby("bucket", sort=True)
    agg = grp.agg(rsrp=("rsrp", "mean"), sinr=("sinr", "mean"),
                  n_cells=("cell", "nunique"), n=("rsrp", "size"))
    rsrp_mean[agg.index] = agg["rsrp"].to_numpy()
    sinr_mean[agg.index] = agg["sinr"].to_numpy()
    n_unique[agg.index] = agg["n_cells"].to_numpy()
    counts[agg.index] = agg["n"].to_numpy()

    # HO proxy: count cell-transition events from analysis.calibration
    ev = detect_cell_transitions(df)
    ho_per_bucket = np.zeros(n_buckets, dtype=float)
    if not ev.empty:
        ev_t = (ev["time_s"].to_numpy() - t0)
        ev_bucket = np.minimum((ev_t / bucket_s).astype(int), n_buckets - 1)
        for b in ev_bucket:
            ho_per_bucket[b] += 1
    # Rolling sum / window length -> rate per second.
    window_len = max(1, int(round(ho_window_s / bucket_s)))
    kernel = np.ones(window_len, dtype=float)
    ho_rolling = np.convolve(ho_per_bucket, kernel, mode="same") / ho_window_s

    # Time-of-day bucket label using unix epoch in input column.
    t_abs = t0 + np.arange(n_buckets) * bucket_s
    bucket_hour = (((t_abs / 3600.0) % 24.0)).astype(int)

    return FleetStreams(
        t_s=np.arange(n_buckets) * bucket_s,
        rsrp_mean=rsrp_mean,
        sinr_mean=sinr_mean,
        ho_rate=ho_rolling,
        n_unique_cells=n_unique,
        bucket_hour=bucket_hour,
        n_samples=counts.astype(float),
    )


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def apply_adwin_to_stream(
    stream: np.ndarray,
    *,
    delta: float = 0.002,
) -> list[int]:
    """Run river.ADWIN on a numeric stream; return change-point bucket indices."""
    try:
        from river.drift import ADWIN
    except Exception as exc:  # pragma: no cover - dep missing
        raise RuntimeError(f"river not installed: {exc}") from exc

    detector = ADWIN(delta=delta)
    change_points: list[int] = []
    for i, x in enumerate(stream):
        if x is None or (isinstance(x, float) and math.isnan(x)):
            continue
        detector.update(float(x))
        if detector.drift_detected:
            change_points.append(i)
    return change_points


def apply_pca_anomaly_detector(
    streams: FleetStreams,
    *,
    train_s: float,
    score_pct: float = 99.0,
    n_components: int = 2,
    window_s: float = 30.0,
    slide_s: float = 5.0,
) -> tuple[list[int], float, np.ndarray]:
    """Train a small PCA-AE on the head of the streams and flag tail anomalies.

    The "features" here are the sliding-window mean & std of the four
    fleet streams (RSRP / SINR / HO-rate / unique-cells), giving an
    8-dim vector per window. PCA is fit on the first ``train_s`` seconds;
    threshold is the score-percentile ``score_pct`` over the train set.

    Returns:
        ``(flagged_window_centers_in_seconds, threshold, all_scores)``.
    """
    from sklearn.decomposition import PCA

    if len(streams.t_s) == 0:
        return [], float("nan"), np.array([])

    bucket_s = float(streams.t_s[1] - streams.t_s[0]) if len(streams.t_s) >= 2 else 1.0
    w = max(1, int(round(window_s / bucket_s)))
    s = max(1, int(round(slide_s / bucket_s)))
    starts = list(range(0, max(1, len(streams.t_s) - w + 1), s))
    if not starts:
        return [], float("nan"), np.array([])

    feat: list[list[float]] = []
    centers: list[float] = []
    for st in starts:
        end = st + w
        rsrp = streams.rsrp_mean[st:end]
        sinr = streams.sinr_mean[st:end]
        ho = streams.ho_rate[st:end]
        nc = streams.n_unique_cells[st:end]
        vec = [
            float(np.nanmean(rsrp)) if np.any(~np.isnan(rsrp)) else 0.0,
            float(np.nanstd(rsrp)) if np.any(~np.isnan(rsrp)) else 0.0,
            float(np.nanmean(sinr)) if np.any(~np.isnan(sinr)) else 0.0,
            float(np.nanstd(sinr)) if np.any(~np.isnan(sinr)) else 0.0,
            float(np.nanmean(ho)),
            float(np.nanstd(ho)),
            float(np.nanmean(nc)),
            float(np.nanstd(nc)),
        ]
        feat.append(vec)
        centers.append(float(streams.t_s[st] + (window_s / 2.0)))

    X = np.asarray(feat, dtype=float)
    train_mask = np.array(centers) < train_s
    if train_mask.sum() < 5:
        # too little train data -> use the first half of windows
        n_half = max(5, len(centers) // 2)
        train_mask = np.zeros(len(centers), dtype=bool)
        train_mask[:n_half] = True
    X_train = X[train_mask]

    pca = PCA(n_components=min(n_components, X_train.shape[1] - 1))
    pca.fit(X_train)
    X_recon = pca.inverse_transform(pca.transform(X))
    scores = np.linalg.norm(X - X_recon, axis=1)
    train_scores = scores[train_mask]
    threshold = float(np.percentile(train_scores, score_pct))

    flagged = [int(c) for i, c in enumerate(centers)
               if not train_mask[i] and scores[i] >= threshold]
    return flagged, threshold, scores


# ---------------------------------------------------------------------------
# Acceptance
# ---------------------------------------------------------------------------

@dataclass
class FaceValidityCheck:
    code: str
    name: str
    passed: bool
    actual: str
    target: str


def check_acceptance(
    flagged_anomaly_times_s: list[int],
    n_eval_windows: int,
    hourly_event_counts: pd.Series,
    disclaimer_text: str,
) -> list[FaceValidityCheck]:
    out: list[FaceValidityCheck] = []

    # G1: detection rate sanity
    if n_eval_windows > 0:
        rate = len(flagged_anomaly_times_s) / n_eval_windows
    else:
        rate = 1.0
    out.append(FaceValidityCheck(
        code="G1",
        name="volume sanity",
        passed=rate < 0.10,
        actual=f"flagged {len(flagged_anomaly_times_s)} / {n_eval_windows} "
               f"eval windows ({rate * 100:.1f} %)",
        target="< 10 %",
    ))

    # G2: qualitative plausibility — non-uniform time-of-day distribution
    # (ratio of max-bucket count to mean count > 1.5 indicates pattern).
    if not hourly_event_counts.empty and hourly_event_counts.sum() > 0:
        m = float(hourly_event_counts.mean())
        peak = float(hourly_event_counts.max())
        ratio = peak / m if m > 0 else 1.0
        peak_hour = int(hourly_event_counts.idxmax())
        out.append(FaceValidityCheck(
            code="G2",
            name="qualitative time-of-day pattern",
            passed=ratio >= 1.5,
            actual=f"peak hour {peak_hour:02d}:00 has "
                   f"{peak:.1f} events vs mean {m:.1f} (ratio {ratio:.2f})",
            target="peak/mean >= 1.5",
        ))
    else:
        out.append(FaceValidityCheck(
            code="G2",
            name="qualitative time-of-day pattern",
            passed=False,
            actual="no detections to assess pattern",
            target="peak/mean >= 1.5",
        ))

    # G3: disclaimer present in markdown text
    out.append(FaceValidityCheck(
        code="G3",
        name="no production-claim overshoot",
        passed=DISCLAIMER_STR.lower() in (disclaimer_text or "").lower(),
        actual="disclaimer string present" if DISCLAIMER_STR.lower() in (
            disclaimer_text or "").lower() else "MISSING disclaimer",
        target=f'contains: "{DISCLAIMER_STR}"',
    ))

    return out


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def _drift_events_to_df(
    rsrp_cps: list[int], sinr_cps: list[int], bucket_s: float
) -> pd.DataFrame:
    rows = []
    for cp in rsrp_cps:
        rows.append({"stream": "rsrp_mean", "bucket_idx": cp,
                     "t_s": float(cp) * bucket_s})
    for cp in sinr_cps:
        rows.append({"stream": "sinr_mean", "bucket_idx": cp,
                     "t_s": float(cp) * bucket_s})
    if not rows:
        return pd.DataFrame(columns=["stream", "bucket_idx", "t_s"])
    return pd.DataFrame(rows).sort_values("t_s").reset_index(drop=True)


def _write_face_validity_md(
    out_path: Path,
    *,
    base_n_samples: int,
    n_eval_windows: int,
    n_flagged: int,
    rsrp_cps: int,
    sinr_cps: int,
    peak_hour: int | None,
    checks: list[FaceValidityCheck],
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# NordicDat face-validity report (Phase 11 Iter C)",
        "",
        "## Caveat",
        "",
        DISCLAIMER_STR,
        "",
        "## Setup",
        "",
        f"- NordicDat samples used: {base_n_samples:,}",
        f"- Evaluation windows produced: {n_eval_windows:,}",
        f"- Anomaly-flagged windows: {n_flagged:,} "
        f"({(n_flagged / max(1, n_eval_windows)) * 100:.1f} %)",
        f"- ADWIN change points: RSRP {rsrp_cps}, SINR {sinr_cps}",
        f"- Peak detection hour: "
        + (f"{peak_hour:02d}:00 local-clock proxy" if peak_hour is not None
           else "n/a"),
        "",
        "## Acceptance (G1..G3)",
        "",
        "| Code | Name | Pass | Actual | Target |",
        "|---|---|---|---|---|",
    ]
    for c in checks:
        lines.append(
            f"| {c.code} | {c.name} | {'PASS' if c.passed else 'FAIL'} "
            f"| {c.actual} | {c.target} |"
        )
    lines.append("")
    out_path.write_text("\n".join(lines))


def _write_face_validity_figure(
    streams: FleetStreams,
    rsrp_cps: list[int],
    sinr_cps: list[int],
    flagged_t_s: list[int],
    out_path: Path,
) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - optional dep
        print(f"WARN: matplotlib unavailable ({exc}); skipping figure.")
        return

    if len(streams.t_s) == 0:
        return
    t_h = streams.t_s / 3600.0

    fig, axes = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
    axes[0].plot(t_h, streams.rsrp_mean, lw=0.8, color="C0", label="RSRP mean")
    for cp in rsrp_cps:
        axes[0].axvline(streams.t_s[cp] / 3600.0, color="red",
                        alpha=0.5, lw=0.8)
    axes[0].set_ylabel("RSRP (dBm)")
    axes[0].legend(loc="lower right", fontsize=8)
    axes[0].grid(alpha=0.3)

    axes[1].plot(t_h, streams.sinr_mean, lw=0.8, color="C2", label="SINR mean")
    for cp in sinr_cps:
        axes[1].axvline(streams.t_s[cp] / 3600.0, color="red",
                        alpha=0.5, lw=0.8)
    for ft in flagged_t_s:
        axes[1].axvline(float(ft) / 3600.0, color="orange",
                        alpha=0.4, lw=0.6, linestyle="--")
    axes[1].set_ylabel("SINR (dB)")
    axes[1].set_xlabel("Time (hours from segment start)")
    axes[1].legend(loc="lower right", fontsize=8)
    axes[1].grid(alpha=0.3)

    fig.suptitle("Phase 11 Iter C — NordicDat face validity "
                 "(red = ADWIN change points; orange dashed = PCA-AE flag)",
                 fontsize=10)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--operator", type=int, default=1,
                   help="NordicDat operator_id to keep (default: 1).")
    p.add_argument("--ran", default="5G-NSA",
                   help="ran_type filter (default: '5G-NSA'). "
                        "Use 'all' to keep every ran_type.")
    p.add_argument("--bucket-s", type=float, default=1.0,
                   help="Fleet-stream bucket width in seconds.")
    p.add_argument("--train-s", type=float, default=600.0,
                   help="Initial seconds used to fit PCA-AE.")
    p.add_argument("--window-s", type=float, default=30.0,
                   help="PCA-AE feature-window width (seconds).")
    p.add_argument("--slide-s", type=float, default=5.0,
                   help="PCA-AE window slide step (seconds).")
    p.add_argument("--score-pct", type=float, default=99.0,
                   help="Percentile of train-set reconstruction error "
                        "used as anomaly threshold.")
    p.add_argument("--adwin-delta", type=float, default=0.002,
                   help="ADWIN delta parameter.")
    a = p.parse_args(argv)

    df = load_nordicdat()
    if a.ran.lower() != "all":
        df = df[df["ran_type"] == a.ran]
    df = df[df["operator_id"] == a.operator].reset_index(drop=True)
    print(f"NordicDat segment loaded: operator={a.operator}, ran={a.ran}, "
          f"rows={len(df):,}")
    if df.empty:
        print("Empty segment, nothing to do.")
        return 1

    streams = build_fleet_streams(df, bucket_s=a.bucket_s)
    print(f"Built {len(streams.t_s)} fleet buckets ({a.bucket_s}s each), "
          f"total {streams.t_s[-1] / 3600.0:.1f} h.")

    rsrp_cps = apply_adwin_to_stream(streams.rsrp_mean, delta=a.adwin_delta)
    sinr_cps = apply_adwin_to_stream(streams.sinr_mean, delta=a.adwin_delta)
    print(f"ADWIN change points: RSRP={len(rsrp_cps)}, SINR={len(sinr_cps)}")

    flagged_t_s, thr, scores = apply_pca_anomaly_detector(
        streams,
        train_s=a.train_s,
        score_pct=a.score_pct,
        window_s=a.window_s,
        slide_s=a.slide_s,
    )
    n_eval_windows = max(0, len(scores) - int(round(a.train_s / a.slide_s)))
    print(f"PCA-AE: threshold={thr:.3f}, flagged={len(flagged_t_s)} / "
          f"{n_eval_windows} eval windows.")

    # Hourly distribution of flagged events for G2
    if flagged_t_s:
        ev_buckets = np.minimum(
            np.asarray(flagged_t_s) / a.bucket_s,
            len(streams.bucket_hour) - 1,
        ).astype(int)
        ev_hours = streams.bucket_hour[ev_buckets]
        hourly = pd.Series(ev_hours).value_counts().reindex(
            range(24), fill_value=0
        )
    else:
        hourly = pd.Series(dtype=int)

    # Build markdown so G3 check can find disclaimer
    md_path = OUTPUT_DIR / "nordicdat_face_validity.md"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_face_validity_md(
        md_path,
        base_n_samples=len(df),
        n_eval_windows=n_eval_windows,
        n_flagged=len(flagged_t_s),
        rsrp_cps=len(rsrp_cps),
        sinr_cps=len(sinr_cps),
        peak_hour=int(hourly.idxmax()) if not hourly.empty
                  and hourly.sum() > 0 else None,
        checks=[],  # placeholder, re-written below with actual checks
    )

    md_text = md_path.read_text()
    checks = check_acceptance(
        flagged_t_s, n_eval_windows, hourly, md_text,
    )
    # Re-write md with checks now populated
    _write_face_validity_md(
        md_path,
        base_n_samples=len(df),
        n_eval_windows=n_eval_windows,
        n_flagged=len(flagged_t_s),
        rsrp_cps=len(rsrp_cps),
        sinr_cps=len(sinr_cps),
        peak_hour=int(hourly.idxmax()) if not hourly.empty
                  and hourly.sum() > 0 else None,
        checks=checks,
    )

    drift_df = _drift_events_to_df(rsrp_cps, sinr_cps, a.bucket_s)
    drift_df.to_csv(OUTPUT_DIR / "nordicdat_drift_events.csv", index=False)

    anomaly_df = pd.DataFrame({"t_s": flagged_t_s})
    anomaly_df.to_csv(OUTPUT_DIR / "nordicdat_anomaly_windows.csv", index=False)

    acc_json = {c.code: {"name": c.name, "passed": c.passed,
                         "actual": c.actual, "target": c.target}
                for c in checks}
    acc_json["_all_pass"] = all(c.passed for c in checks)
    (OUTPUT_DIR / "nordicdat_face_validity.json").write_text(
        json.dumps(acc_json, indent=2)
    )

    _write_face_validity_figure(
        streams, rsrp_cps, sinr_cps, flagged_t_s,
        OUTPUT_DIR / "nordicdat_face_validity.png",
    )

    print()
    print("Phase 11 Iter C acceptance:")
    for c in checks:
        mark = "PASS" if c.passed else "FAIL"
        print(f"  [{mark}] {c.code}  {c.name}: {c.actual}")
    return 0 if all(c.passed for c in checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
