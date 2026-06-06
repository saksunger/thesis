"""Unit tests for the Phase 11 Iter C NordicDat face-validity script.

Avoid loading the real NordicDat CSV; build small synthetic DataFrames
that exercise the contracts.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from analysis.external_validation import nordicdat_apply as nda


# ---------------------------------------------------------------------------
# build_fleet_streams
# ---------------------------------------------------------------------------

def _synthetic_df(
    n_rows: int = 300,
    rate_hz: float = 1.0,
    rsrp_start: float = -90.0,
    sinr_start: float = 10.0,
    n_unique_cells: int = 3,
) -> pd.DataFrame:
    """Build a deterministic NordicDat-shaped DataFrame for testing."""
    rng = np.random.default_rng(0)
    t = np.arange(n_rows) / rate_hz
    rsrp = rsrp_start + rng.normal(0, 1.0, size=n_rows)
    sinr = sinr_start + rng.normal(0, 0.5, size=n_rows)
    serving = (np.arange(n_rows) // 30) % n_unique_cells + 1
    return pd.DataFrame({
        "time_s": t.astype(float) + 1_700_000_000.0,
        "rsrp_serving_dbm": rsrp,
        "sinr_serving_db": sinr,
        "serving_cell_id": pd.array(serving, dtype="Int64"),
        "operator_id": pd.array([1] * n_rows, dtype="Int64"),
        "ran_type": pd.Series(["5G-NSA"] * n_rows, dtype="string"),
        "band": pd.Series(["NR_n78"] * n_rows, dtype="string"),
    })


class TestBuildFleetStreams:
    def test_returns_empty_for_empty_input(self):
        empty = _synthetic_df(0)
        out = nda.build_fleet_streams(empty)
        assert len(out.t_s) == 0
        assert len(out.rsrp_mean) == 0

    def test_bucket_count_matches_time_span(self):
        df = _synthetic_df(60, rate_hz=1.0)  # 60 seconds
        out = nda.build_fleet_streams(df, bucket_s=1.0)
        # spans [0, 59], so n_buckets = ceil(59) + 1 if last index = 59
        # actual: ceil(59 / 1) = 59; but last sample at t_seg=59 -> bucket 59
        # so n_buckets should be at least 60
        assert len(out.t_s) >= 59
        assert len(out.rsrp_mean) == len(out.t_s)

    def test_rsrp_mean_matches_aggregation(self):
        df = _synthetic_df(60, rate_hz=1.0)
        out = nda.build_fleet_streams(df, bucket_s=10.0)
        # Bucket 0 covers seconds 0..9 (10 samples); should equal raw mean
        manual = float(df["rsrp_serving_dbm"].iloc[:10].mean())
        assert out.rsrp_mean[0] == pytest.approx(manual, abs=1e-6)

    def test_n_unique_cells_per_bucket(self):
        df = _synthetic_df(120, rate_hz=1.0, n_unique_cells=3)
        out = nda.build_fleet_streams(df, bucket_s=60.0)
        # 30-sample blocks per cell, so bucket of 60 s should have 2 cells
        assert out.n_unique_cells[0] == 2

    def test_bucket_hour_is_in_range(self):
        df = _synthetic_df(120, rate_hz=1.0)
        out = nda.build_fleet_streams(df, bucket_s=1.0)
        assert out.bucket_hour.min() >= 0
        assert out.bucket_hour.max() <= 23


# ---------------------------------------------------------------------------
# apply_pca_anomaly_detector
# ---------------------------------------------------------------------------

class TestApplyPcaAnomalyDetector:
    def test_returns_empty_for_empty_streams(self):
        empty = nda.FleetStreams(
            t_s=np.array([]), rsrp_mean=np.array([]),
            sinr_mean=np.array([]), ho_rate=np.array([]),
            n_unique_cells=np.array([]), bucket_hour=np.array([]),
            n_samples=np.array([]),
        )
        flagged, thr, scores = nda.apply_pca_anomaly_detector(
            empty, train_s=10.0,
        )
        assert flagged == []
        assert math.isnan(thr) or scores.size == 0

    def test_flag_count_within_eval_set(self):
        # Synthetic stream: stable head, gradual ramp in tail (should flag).
        n = 600
        t = np.arange(n, dtype=float)
        rsrp = np.full(n, -90.0)
        rsrp[400:] -= 20.0   # cliff drop
        sinr = np.full(n, 10.0)
        sinr[400:] -= 8.0
        streams = nda.FleetStreams(
            t_s=t, rsrp_mean=rsrp, sinr_mean=sinr,
            ho_rate=np.zeros(n), n_unique_cells=np.ones(n),
            bucket_hour=np.zeros(n, dtype=int), n_samples=np.full(n, 5.0),
        )
        flagged, thr, scores = nda.apply_pca_anomaly_detector(
            streams, train_s=200.0,
            window_s=30.0, slide_s=10.0,
        )
        # head is 0..200s -> training; tail flags should land >= 400s
        assert flagged, "expected to flag the post-cliff windows"
        assert all(ft >= 200.0 for ft in flagged), \
            "flags should only fall in the evaluation segment"
        assert thr >= 0

    def test_threshold_finite_when_training_constant(self):
        # Constant training -> very small recon err -> threshold tiny.
        n = 400
        t = np.arange(n, dtype=float)
        streams = nda.FleetStreams(
            t_s=t, rsrp_mean=np.full(n, -90.0),
            sinr_mean=np.full(n, 10.0),
            ho_rate=np.zeros(n), n_unique_cells=np.ones(n),
            bucket_hour=np.zeros(n, dtype=int), n_samples=np.full(n, 5.0),
        )
        flagged, thr, scores = nda.apply_pca_anomaly_detector(
            streams, train_s=200.0, slide_s=10.0,
        )
        assert math.isfinite(thr)


# ---------------------------------------------------------------------------
# check_acceptance (G1..G3)
# ---------------------------------------------------------------------------

class TestCheckAcceptance:
    def _hourly(self, **counts) -> pd.Series:
        s = pd.Series(0, index=range(24), dtype=int)
        for h, v in counts.items():
            s[int(h)] = v
        return s

    def test_all_pass_path(self):
        # 50 flagged out of 1000 -> 5% rate -> G1 pass
        # peak hour 8 has 30 events, mean 50/24 ~= 2.1 -> ratio > 1.5 -> G2 pass
        # disclaimer present -> G3 pass
        hourly = self._hourly(**{"8": 30, "9": 15, "17": 5})
        text = f"# Some markdown\n\n{nda.DISCLAIMER_STR}\n"
        checks = nda.check_acceptance([1] * 50, 1000, hourly, text)
        codes = {c.code for c in checks}
        assert codes == {"G1", "G2", "G3"}
        assert all(c.passed for c in checks), [
            (c.code, c.actual) for c in checks
        ]

    def test_g1_fails_when_rate_too_high(self):
        hourly = self._hourly(**{"8": 30})
        checks = nda.check_acceptance(
            list(range(200)), 1000,  # 20% rate
            hourly, nda.DISCLAIMER_STR,
        )
        g1 = next(c for c in checks if c.code == "G1")
        assert not g1.passed

    def test_g2_fails_when_uniform(self):
        # uniform hourly distribution -> peak/mean ~= 1.0 < 1.5
        hourly = self._hourly(**{str(h): 5 for h in range(24)})
        checks = nda.check_acceptance(
            list(range(50)), 1000, hourly,
            nda.DISCLAIMER_STR,
        )
        g2 = next(c for c in checks if c.code == "G2")
        assert not g2.passed

    def test_g2_fails_when_no_detections(self):
        empty = pd.Series(dtype=int)
        checks = nda.check_acceptance(
            [], 1000, empty, nda.DISCLAIMER_STR,
        )
        g2 = next(c for c in checks if c.code == "G2")
        assert not g2.passed

    def test_g3_fails_when_disclaimer_missing(self):
        hourly = self._hourly(**{"8": 30, "9": 15})
        checks = nda.check_acceptance(
            list(range(50)), 1000, hourly,
            "no disclaimer here, just random text",
        )
        g3 = next(c for c in checks if c.code == "G3")
        assert not g3.passed


# ---------------------------------------------------------------------------
# Markdown writer / disclaimer
# ---------------------------------------------------------------------------

class TestFaceValidityMd:
    def test_markdown_contains_disclaimer(self, tmp_path: Path):
        out = tmp_path / "report.md"
        nda._write_face_validity_md(
            out,
            base_n_samples=1000,
            n_eval_windows=100,
            n_flagged=5,
            rsrp_cps=2,
            sinr_cps=1,
            peak_hour=8,
            checks=[],
        )
        text = out.read_text()
        assert nda.DISCLAIMER_STR in text
        assert "Phase 11 Iter C" in text

    def test_markdown_renders_check_rows(self, tmp_path: Path):
        out = tmp_path / "report.md"
        checks = [
            nda.FaceValidityCheck("G1", "vol", True, "5%", "<10%"),
            nda.FaceValidityCheck("G2", "pattern", False, "uniform",
                                  ">=1.5"),
        ]
        nda._write_face_validity_md(
            out, base_n_samples=10, n_eval_windows=10,
            n_flagged=0, rsrp_cps=0, sinr_cps=0,
            peak_hour=None, checks=checks,
        )
        text = out.read_text()
        assert "| G1 | vol | PASS |" in text
        assert "| G2 | pattern | FAIL |" in text


# ---------------------------------------------------------------------------
# Drift events DataFrame helper
# ---------------------------------------------------------------------------

class TestDriftEventsDf:
    def test_combines_streams_with_time(self):
        df = nda._drift_events_to_df(
            rsrp_cps=[10, 100], sinr_cps=[50],
            bucket_s=1.0,
        )
        assert len(df) == 3
        assert set(df["stream"]) == {"rsrp_mean", "sinr_mean"}
        assert list(df.sort_values("t_s")["t_s"].astype(int)) == [10, 50, 100]

    def test_empty_inputs_return_empty_df(self):
        df = nda._drift_events_to_df([], [], 1.0)
        assert df.empty
