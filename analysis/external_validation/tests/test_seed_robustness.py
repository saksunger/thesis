"""Unit tests for the Phase 11 Iter A cross-seed aggregator."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from analysis.external_validation.seed_robustness import (
    AcceptanceCheck,
    BootstrapStat,
    DetectorRankingStability,
    bootstrap_mean_ci,
    check_acceptance,
    compute_bootstrap_table,
    compute_detector_stability,
    load_replication_csv,
    write_acceptance_json,
    write_bootstrap_table,
    write_detector_table,
)


# ---------------------------------------------------------------------------
# bootstrap_mean_ci
# ---------------------------------------------------------------------------

class TestBootstrapMeanCI:
    def test_returns_nan_for_empty_input(self):
        out = bootstrap_mean_ci([])
        assert all(math.isnan(x) for x in out)

    def test_returns_nan_for_single_value(self):
        out = bootstrap_mean_ci([1.0])
        assert all(math.isnan(x) for x in out)

    def test_constant_input_zero_std(self):
        mean, std, lo, hi = bootstrap_mean_ci([5.0] * 6)
        assert mean == pytest.approx(5.0)
        assert std == pytest.approx(0.0)
        # All bootstrap resamples of constants are 5.0, so CI is degenerate.
        assert lo == pytest.approx(5.0)
        assert hi == pytest.approx(5.0)

    def test_recovers_known_mean(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        mean, std, lo, hi = bootstrap_mean_ci(vals, n_bootstrap=5000, rng_seed=0)
        assert mean == pytest.approx(3.0)
        assert lo < mean < hi
        # Std of a 5-point arithmetic progression with mean 3, step 1.
        assert std == pytest.approx(1.5811388, rel=1e-3)

    def test_ci_contains_population_mean_under_resampling(self):
        # With 100 normal draws, 95% CI should be tight around 0.
        import numpy as np

        rng = np.random.default_rng(1)
        vals = rng.normal(0, 1, size=100).tolist()
        mean, std, lo, hi = bootstrap_mean_ci(vals, n_bootstrap=2000, rng_seed=42)
        assert abs(mean) < 0.3
        assert lo < 0 < hi  # CI brackets the true population mean.

    def test_ignores_nan_values(self):
        mean, std, lo, hi = bootstrap_mean_ci([1.0, float("nan"), 3.0],
                                              rng_seed=0)
        assert mean == pytest.approx(2.0)
        assert not math.isnan(lo)


# ---------------------------------------------------------------------------
# compute_bootstrap_table
# ---------------------------------------------------------------------------

class TestComputeBootstrapTable:
    def _make_rows(self):
        return [
            {"seed": 42, "timeline": "tl_seed42",
             "demo__cum_uplift_hosr": 0.5,
             "adaptive__delta_filtered_minus_naive": 0.10,
             "drift__winning_detector": "ADWIN",
             "demo__acceptance__D4": True},
            {"seed": 43, "timeline": "tl_seed43",
             "demo__cum_uplift_hosr": 0.6,
             "adaptive__delta_filtered_minus_naive": 0.15,
             "drift__winning_detector": "ADWIN",
             "demo__acceptance__D4": True},
            {"seed": 44, "timeline": "tl_seed44",
             "demo__cum_uplift_hosr": 0.4,
             "adaptive__delta_filtered_minus_naive": 0.05,
             "drift__winning_detector": "KSWIN",
             "demo__acceptance__D4": False},
        ]

    def test_skips_non_metric_columns(self):
        stats = compute_bootstrap_table(self._make_rows())
        metric_names = [s.metric for s in stats]
        # winning_detector / seed / timeline are excluded
        assert "seed" not in metric_names
        assert "timeline" not in metric_names
        assert "drift__winning_detector" not in metric_names

    def test_includes_numeric_columns(self):
        stats = compute_bootstrap_table(self._make_rows())
        metric_names = [s.metric for s in stats]
        assert "demo__cum_uplift_hosr" in metric_names
        assert "adaptive__delta_filtered_minus_naive" in metric_names

    def test_boolean_columns_coerced_to_float(self):
        stats = compute_bootstrap_table(self._make_rows())
        b = next(s for s in stats if s.metric == "demo__acceptance__D4")
        # 2 True + 1 False = 2/3 ≈ 0.667
        assert b.mean == pytest.approx(2 / 3)
        assert b.n == 3

    def test_recovers_known_mean(self):
        stats = compute_bootstrap_table(self._make_rows())
        upl = next(s for s in stats if s.metric == "demo__cum_uplift_hosr")
        assert upl.mean == pytest.approx((0.5 + 0.6 + 0.4) / 3)
        assert upl.n == 3
        assert upl.raw == (0.5, 0.6, 0.4)

    def test_handles_missing_values(self):
        rows = self._make_rows()
        rows.append({"seed": 99, "timeline": "tl_seed99",
                     "demo__cum_uplift_hosr": None,
                     "adaptive__delta_filtered_minus_naive": 0.20})
        stats = compute_bootstrap_table(rows)
        upl = next(s for s in stats if s.metric == "demo__cum_uplift_hosr")
        # missing rows excluded
        assert upl.n == 3

    def test_empty_rows_returns_empty(self):
        assert compute_bootstrap_table([]) == []


# ---------------------------------------------------------------------------
# compute_detector_stability
# ---------------------------------------------------------------------------

class TestDetectorStability:
    def test_stable_when_4_of_5_seeds_agree(self):
        rows = [
            {"anomaly__winning_detector": "IsoForest"},
            {"anomaly__winning_detector": "IsoForest"},
            {"anomaly__winning_detector": "IsoForest"},
            {"anomaly__winning_detector": "IsoForest"},
            {"anomaly__winning_detector": "LOF"},
        ]
        out = compute_detector_stability(rows)
        a = out["anomaly"]
        assert a.modal_share == pytest.approx(4 / 5)
        assert a.stable
        assert a.winners["IsoForest"] == 4
        assert a.winners["LOF"] == 1

    def test_unstable_when_3_of_5_seeds_agree(self):
        rows = [{"anomaly__winning_detector": x}
                for x in ["IsoForest", "IsoForest", "IsoForest", "LOF", "PCA-AE"]]
        out = compute_detector_stability(rows)
        assert not out["anomaly"].stable

    def test_handles_multiple_pipelines(self):
        rows = [
            {"anomaly__winning_detector": "IsoForest",
             "drift__winning_detector": "ADWIN"},
            {"anomaly__winning_detector": "IsoForest",
             "drift__winning_detector": "ADWIN"},
        ]
        out = compute_detector_stability(rows)
        assert set(out) == {"anomaly", "drift"}
        assert out["drift"].modal_share == pytest.approx(1.0)

    def test_empty_winner_skipped(self):
        rows = [{"anomaly__winning_detector": None}]
        out = compute_detector_stability(rows)
        assert "anomaly" not in out

    def test_custom_threshold(self):
        # 3/5 = 0.6 stable at threshold 0.5 but unstable at 0.8
        rows = [{"anomaly__winning_detector": x}
                for x in ["A", "A", "A", "B", "C"]]
        loose = compute_detector_stability(rows, threshold=0.5)
        strict = compute_detector_stability(rows, threshold=0.8)
        assert loose["anomaly"].stable
        assert not strict["anomaly"].stable


# ---------------------------------------------------------------------------
# check_acceptance
# ---------------------------------------------------------------------------

class TestCheckAcceptance:
    def _build_full_pass_input(self):
        rows = [
            {"seed": s,
             "demo__cum_uplift_hosr": 0.5 + s / 100,
             "adaptive__delta_filtered_minus_naive": 0.10 + s / 1000,
             "drift__n_detections": 20 + s,
             "drift__winning_detector": "ADWIN",
             "anomaly__winning_detector": "PCA-AE"}
            for s in range(5)
        ]
        stats = compute_bootstrap_table(rows)
        rankings = compute_detector_stability(rows)
        return rows, stats, rankings

    def test_all_pass_path(self):
        rows, stats, rankings = self._build_full_pass_input()
        # Also surface the new per-type-mean / overall winner columns so
        # both E2a and E2b have data to evaluate.
        for r in rows:
            r["anomaly__winning_detector_per_type_mean"] = "PCA-AE"
            r["anomaly__winning_detector_overall"] = "IsoForest"
        rankings = compute_detector_stability(rows)
        checks = check_acceptance(rows, stats, rankings)
        assert {c.code for c in checks} == {
            "E1", "E2a", "E2b", "E3", "E4", "E5"
        }
        assert all(c.passed for c in checks), [
            (c.code, c.passed, c.actual) for c in checks
        ]

    def test_e1_fails_when_pipeline_missing(self):
        rows, stats, rankings = self._build_full_pass_input()
        rows[0]["anomaly__missing"] = True
        checks = check_acceptance(rows, stats, rankings)
        e1 = next(c for c in checks if c.code == "E1")
        assert not e1.passed

    def test_e3_fails_when_delta_centered_negative(self):
        rows = [
            {"seed": s,
             "adaptive__delta_filtered_minus_naive": -0.10,
             "demo__cum_uplift_hosr": 0.5,
             "drift__n_detections": 20,
             "drift__winning_detector": "ADWIN",
             "anomaly__winning_detector": "PCA-AE"}
            for s in range(5)
        ]
        stats = compute_bootstrap_table(rows)
        rankings = compute_detector_stability(rows)
        checks = check_acceptance(rows, stats, rankings)
        e3 = next(c for c in checks if c.code == "E3")
        assert not e3.passed

    def test_e4_fails_when_uplift_ci_brackets_zero(self):
        rows = [
            {"seed": s,
             "demo__cum_uplift_hosr": val,
             "adaptive__delta_filtered_minus_naive": 0.1,
             "drift__n_detections": 20,
             "drift__winning_detector": "ADWIN",
             "anomaly__winning_detector": "PCA-AE"}
            for s, val in zip(range(5), [-0.5, -0.2, 0.0, 0.2, 0.5])
        ]
        stats = compute_bootstrap_table(rows)
        rankings = compute_detector_stability(rows)
        checks = check_acceptance(rows, stats, rankings)
        e4 = next(c for c in checks if c.code == "E4")
        assert not e4.passed  # CI brackets zero

    def test_e5_fails_when_cv_high(self):
        rows = [
            {"seed": s,
             "demo__cum_uplift_hosr": 0.5,
             "adaptive__delta_filtered_minus_naive": 0.1,
             "drift__n_detections": val,   # huge CV
             "drift__winning_detector": "ADWIN",
             "anomaly__winning_detector": "PCA-AE"}
            for s, val in zip(range(5), [10, 50, 100, 200, 500])
        ]
        stats = compute_bootstrap_table(rows)
        rankings = compute_detector_stability(rows)
        checks = check_acceptance(rows, stats, rankings)
        e5 = next(c for c in checks if c.code == "E5")
        assert not e5.passed

    def test_e2a_fails_when_per_type_winners_split(self):
        rows = [
            {"seed": s,
             "demo__cum_uplift_hosr": 0.5,
             "adaptive__delta_filtered_minus_naive": 0.1,
             "drift__n_detections": 20,
             "drift__winning_detector": "ADWIN",
             "anomaly__winning_detector_per_type_mean": (
                 "PCA-AE" if s % 2 == 0 else "LOF"
             ),
             "anomaly__winning_detector_overall": "IsoForest"}
            for s in range(5)
        ]
        stats = compute_bootstrap_table(rows)
        rankings = compute_detector_stability(rows)
        checks = check_acceptance(rows, stats, rankings)
        e2a = next(c for c in checks if c.code == "E2a")
        e2b = next(c for c in checks if c.code == "E2b")
        assert not e2a.passed
        assert e2b.passed  # overall winner is rock-solid IsoForest

    def test_e2b_isolated_from_e2a(self):
        """E2b PASS while E2a FAIL is the Phase 11 Iter A reality
        on timeline_medium — make sure the acceptance logic preserves
        that asymmetry."""
        rows = [
            {"seed": s,
             "demo__cum_uplift_hosr": 0.5,
             "adaptive__delta_filtered_minus_naive": 0.1,
             "drift__n_detections": 20,
             "drift__winning_detector": "ADWIN",
             "anomaly__winning_detector_per_type_mean": [
                 "LOF", "OCSVM", "MLP-AE", "LOF", "OCSVM"
             ][s],
             "anomaly__winning_detector_overall": "IsoForest"}
            for s in range(5)
        ]
        stats = compute_bootstrap_table(rows)
        rankings = compute_detector_stability(rows)
        checks = check_acceptance(rows, stats, rankings)
        verdicts = {c.code: c.passed for c in checks}
        assert verdicts["E2a"] is False
        assert verdicts["E2b"] is True


# ---------------------------------------------------------------------------
# I/O round-trips
# ---------------------------------------------------------------------------

class TestIO:
    def test_load_csv_round_trip(self, tmp_path: Path):
        rows_in = [
            {"seed": 42, "timeline": "tl_seed42", "x": 1.5, "flag": True,
             "winner": "IsoForest"},
            {"seed": 43, "timeline": "tl_seed43", "x": 2.0, "flag": False,
             "winner": "LOF"},
        ]
        path = tmp_path / "rep.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["seed", "timeline", "x", "flag", "winner"])
            w.writerow([42, "tl_seed42", 1.5, "true", "IsoForest"])
            w.writerow([43, "tl_seed43", 2.0, "false", "LOF"])
        rows = load_replication_csv(path)
        assert len(rows) == 2
        assert rows[0]["seed"] == 42
        assert rows[0]["x"] == pytest.approx(1.5)
        assert rows[0]["flag"] is True
        assert rows[1]["flag"] is False
        assert rows[0]["winner"] == "IsoForest"

    def test_write_bootstrap_table(self, tmp_path: Path):
        stats = [
            BootstrapStat(metric="m1", n=3, mean=1.0, std=0.5,
                          ci_lo=0.2, ci_hi=1.8, raw=(0.5, 1.0, 1.5)),
        ]
        path = tmp_path / "out.csv"
        write_bootstrap_table(stats, path)
        text = path.read_text()
        assert "metric,n_seeds,mean,std,ci_lo_95,ci_hi_95,raw_values" in text
        assert "m1,3,1,0.5,0.2,1.8,0.5;1;1.5" in text

    def test_write_detector_table(self, tmp_path: Path):
        rankings = {
            "anomaly": DetectorRankingStability(
                pipeline="anomaly",
                winners=__import__("collections").Counter(
                    {"PCA-AE": 4, "LOF": 1}
                ),
                n_seeds=5,
                modal_share=0.8,
                stable=True,
            )
        }
        path = tmp_path / "det.csv"
        write_detector_table(rankings, path)
        text = path.read_text()
        assert "anomaly,PCA-AE,4,5,0.8,true" in text
        assert "anomaly,LOF,1,5,0.8,true" in text

    def test_write_acceptance_json_summary_flag(self, tmp_path: Path):
        checks = [
            AcceptanceCheck("E1", "test E1", True, "ok", "ok"),
            AcceptanceCheck("E2", "test E2", False, "no", "must be yes"),
        ]
        path = tmp_path / "acc.json"
        write_acceptance_json(checks, path)
        doc = json.loads(path.read_text())
        assert doc["E1"]["passed"] is True
        assert doc["E2"]["passed"] is False
        assert doc["_all_pass"] is False
