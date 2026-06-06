"""Unit tests for the Phase 11 Iter B cross-scenario comparator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis.external_validation import cross_scenario_compare as csc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_summary(root: Path, timeline: str, pipeline: str, doc: dict) -> Path:
    subdir, fname = csc.PIPELINE_SUMMARY_FILES[pipeline]
    out_dir = root / subdir.format(tl=timeline)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / fname
    path.write_text(json.dumps(doc))
    return path


def _write_full_pipeline_set(root: Path, timeline: str,
                             *, hosr_uplift: float, delta_fil_nai: float,
                             n_interventions: int,
                             winner: str, drift_winner: str,
                             n_detections: int) -> None:
    _write_summary(root, timeline, "anomaly", {
        "winning_detector_by_mean_pr_auc": winner,
        "elapsed_s": 10.0,
    })
    _write_summary(root, timeline, "drift", {
        "winning_detector": drift_winner,
        "n_detections": n_detections,
        "n_drifts": 8,
        "elapsed_s": 5.0,
    })
    _write_summary(root, timeline, "adaptive", {
        "strategies": [
            {"strategy": "static", "drift_phase_pr_auc": 0.05,
             "overall_pr_auc": 0.2, "cost__n_refits": 1,
             "cost__fit_cpu_s_sum": 0.01},
            {"strategy": "drift_triggered_naive",
             "drift_phase_pr_auc": 0.10, "overall_pr_auc": 0.3,
             "cost__n_refits": 3, "cost__fit_cpu_s_sum": 0.03},
            {"strategy": "drift_triggered_filtered",
             "drift_phase_pr_auc": 0.10 + delta_fil_nai,
             "overall_pr_auc": 0.4, "cost__n_refits": 3,
             "cost__fit_cpu_s_sum": 0.03},
        ],
    })
    _write_summary(root, timeline, "demo", {
        "cumulative_predicted_uplift": {"hosr": hosr_uplift,
                                        "rlf_rate": -0.005},
        "n_interventions": n_interventions,
        "n_retrains": n_interventions + 1,
        "acceptance": {"D5": {"pass": True}},
    })


# ---------------------------------------------------------------------------
# Summary loaders / metric extraction
# ---------------------------------------------------------------------------

class TestSummaryLoaders:
    def test_returns_none_for_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(csc, "DATA_PROC", tmp_path)
        out = csc.load_pipeline_summaries("no_such_tl")
        assert set(out) == {"anomaly", "drift", "adaptive", "demo"}
        assert all(v is None for v in out.values())

    def test_loads_present_summaries(self, tmp_path, monkeypatch):
        monkeypatch.setattr(csc, "DATA_PROC", tmp_path)
        _write_summary(tmp_path, "tl_x", "anomaly",
                       {"winning_detector_by_mean_pr_auc": "PCA-AE"})
        out = csc.load_pipeline_summaries("tl_x")
        assert out["anomaly"]["winning_detector_by_mean_pr_auc"] == "PCA-AE"
        assert out["drift"] is None


class TestExtractScalars:
    def test_full_extraction(self, tmp_path, monkeypatch):
        monkeypatch.setattr(csc, "DATA_PROC", tmp_path)
        _write_full_pipeline_set(tmp_path, "tl_x",
                                 hosr_uplift=0.5,
                                 delta_fil_nai=0.20,
                                 n_interventions=3,
                                 winner="LOF",
                                 drift_winner="ADWIN",
                                 n_detections=24)
        summaries = csc.load_pipeline_summaries("tl_x")
        scalars = csc.extract_scalars(summaries)
        assert scalars["demo__cum_uplift_hosr"] == pytest.approx(0.5)
        assert scalars["demo__n_interventions"] == 3
        assert scalars["adaptive__delta_filtered_minus_naive"] == pytest.approx(0.20)
        assert scalars["drift__n_detections"] == 24
        assert scalars["anomaly__winning_detector"] == "LOF"
        assert scalars["demo__acceptance__D5"] is True

    def test_missing_pipeline_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(csc, "DATA_PROC", tmp_path)
        _write_summary(tmp_path, "tl_x", "anomaly",
                       {"winning_detector_by_mean_pr_auc": "PCA-AE"})
        summaries = csc.load_pipeline_summaries("tl_x")
        scalars = csc.extract_scalars(summaries)
        assert scalars.get("drift__missing") is True
        assert scalars.get("adaptive__missing") is True
        assert scalars.get("demo__missing") is True


# ---------------------------------------------------------------------------
# compute_deltas
# ---------------------------------------------------------------------------

class TestComputeDeltas:
    def test_numeric_delta(self):
        base = {"x": 1.0, "y": 2.0}
        contrast = {"x": 1.5, "y": 1.5}
        deltas = csc.compute_deltas(base, contrast)
        x = next(d for d in deltas if d.metric == "x")
        assert x.delta == pytest.approx(0.5)
        assert x.relative_delta == pytest.approx(0.5)

    def test_delta_none_when_non_numeric(self):
        deltas = csc.compute_deltas({"x": "alpha"}, {"x": "beta"})
        x = next(d for d in deltas if d.metric == "x")
        assert x.delta is None
        assert x.relative_delta is None

    def test_relative_delta_zero_base(self):
        deltas = csc.compute_deltas({"x": 0.0}, {"x": 1.5})
        x = next(d for d in deltas if d.metric == "x")
        assert x.delta == pytest.approx(1.5)
        assert x.relative_delta is None  # avoid div-by-zero

    def test_handles_keys_in_only_one_side(self):
        deltas = csc.compute_deltas({"a": 1.0}, {"b": 2.0})
        keys = {d.metric for d in deltas}
        assert keys == {"a", "b"}

    def test_excludes_booleans_from_delta(self):
        # booleans shouldn't be subtracted (True - False = 1 is meaningless here)
        deltas = csc.compute_deltas({"flag": True}, {"flag": False})
        flag = next(d for d in deltas if d.metric == "flag")
        assert flag.delta is None


# ---------------------------------------------------------------------------
# Acceptance check (F1..F5)
# ---------------------------------------------------------------------------

class TestAcceptanceCheck:
    def _make_pass_metrics(self):
        base = {
            "adaptive__delta_filtered_minus_naive": 0.25,
            "demo__cum_uplift_hosr": 0.62,
            "demo__n_interventions": 7,
        }
        contrast = {
            "adaptive__delta_filtered_minus_naive": 0.18,
            "demo__cum_uplift_hosr": 0.40,
            "demo__n_interventions": 4,
        }
        return base, contrast

    def test_all_pass_path(self):
        base, contrast = self._make_pass_metrics()
        checks = csc.check_acceptance(
            "base_tl", "contrast_tl",
            base, contrast,
            base_top2=["PCA-AE", "LOF"],
            contrast_top2=["PCA-AE", "IsoForest"],
        )
        codes = [c.code for c in checks]
        assert codes == ["F1", "F2", "F3", "F4", "F5"]
        assert all(c.passed for c in checks), [
            (c.code, c.actual) for c in checks
        ]

    def test_f1_fails_when_pipeline_missing(self):
        base, contrast = self._make_pass_metrics()
        contrast["adaptive__missing"] = True
        checks = csc.check_acceptance(
            "b", "c", base, contrast,
            base_top2=["PCA-AE"], contrast_top2=["PCA-AE"],
        )
        f1 = next(c for c in checks if c.code == "F1")
        assert not f1.passed
        assert "adaptive" in f1.actual

    def test_f2_fails_when_top2_disjoint(self):
        base, contrast = self._make_pass_metrics()
        checks = csc.check_acceptance(
            "b", "c", base, contrast,
            base_top2=["PCA-AE", "LOF"],
            contrast_top2=["IsoForest", "OneClassSVM"],
        )
        f2 = next(c for c in checks if c.code == "F2")
        assert not f2.passed

    def test_f3_fails_when_sign_flips(self):
        base, contrast = self._make_pass_metrics()
        contrast["adaptive__delta_filtered_minus_naive"] = -0.10
        checks = csc.check_acceptance(
            "b", "c", base, contrast,
            base_top2=["PCA-AE"], contrast_top2=["PCA-AE"],
        )
        f3 = next(c for c in checks if c.code == "F3")
        assert not f3.passed

    def test_f4_fails_when_no_interventions(self):
        base, contrast = self._make_pass_metrics()
        contrast["demo__n_interventions"] = 0
        checks = csc.check_acceptance(
            "b", "c", base, contrast,
            base_top2=["PCA-AE"], contrast_top2=["PCA-AE"],
        )
        f4 = next(c for c in checks if c.code == "F4")
        assert not f4.passed

    def test_f5_fails_when_uplift_negative(self):
        base, contrast = self._make_pass_metrics()
        contrast["demo__cum_uplift_hosr"] = -0.10
        checks = csc.check_acceptance(
            "b", "c", base, contrast,
            base_top2=["PCA-AE"], contrast_top2=["PCA-AE"],
        )
        f5 = next(c for c in checks if c.code == "F5")
        assert not f5.passed


# ---------------------------------------------------------------------------
# load_anomaly_top2
# ---------------------------------------------------------------------------

class TestLoadAnomalyTop2:
    def test_uses_per_anomaly_csv_when_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(csc, "DATA_PROC", tmp_path)
        out_dir = tmp_path / "anomaly_benchmark_tl_x"
        out_dir.mkdir(parents=True)
        # PCA-AE wins on average (0.7), LOF second (0.5)
        (out_dir / "per_anomaly_with_ci.csv").write_text(
            "anomaly_type,detector,pr_auc\n"
            "A-1,PCA-AE,0.8\n"
            "A-2,PCA-AE,0.6\n"
            "A-1,LOF,0.5\n"
            "A-2,LOF,0.5\n"
            "A-1,IsoForest,0.3\n"
            "A-2,IsoForest,0.2\n"
        )
        top2 = csc.load_anomaly_top2("tl_x")
        assert top2 == ["PCA-AE", "LOF"]

    def test_falls_back_to_summary(self, tmp_path, monkeypatch):
        monkeypatch.setattr(csc, "DATA_PROC", tmp_path)
        _write_summary(tmp_path, "tl_x", "anomaly",
                       {"winning_detector_by_mean_pr_auc": "PCA-AE"})
        top2 = csc.load_anomaly_top2("tl_x")
        assert top2 == ["PCA-AE"]

    def test_returns_empty_when_no_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(csc, "DATA_PROC", tmp_path)
        top2 = csc.load_anomaly_top2("tl_x")
        assert top2 == []


# ---------------------------------------------------------------------------
# Writers (sanity-check structure)
# ---------------------------------------------------------------------------

class TestWriters:
    def test_long_table_has_rows_for_each_metric_x_timeline(self, tmp_path):
        base = {"x": 1.0, "y": 2.0}
        contrast = {"x": 1.5, "y": 1.0}
        out = tmp_path / "long.csv"
        csc.write_long_table("base_tl", "contrast_tl", base, contrast, out)
        lines = out.read_text().splitlines()
        # header + 2 metrics x 2 timelines = 5 lines
        assert len(lines) == 5
        assert lines[0] == "metric,timeline,value"

    def test_delta_table_columns(self, tmp_path):
        deltas = [
            csc.MetricDelta(metric="x", base_value=1.0, contrast_value=1.5,
                            delta=0.5, relative_delta=0.5),
        ]
        out = tmp_path / "delta.csv"
        csc.write_delta_table(deltas, out, "b", "c")
        text = out.read_text()
        assert "metric,b,c,delta_contrast_minus_base,relative_delta" in text
        assert "x,1,1.5,0.5,0.5" in text

    def test_acceptance_writer_sets_all_pass_flag(self, tmp_path):
        checks = [
            csc.AcceptanceCheck("F1", "t", True, "a", "t"),
            csc.AcceptanceCheck("F2", "t", False, "a", "t"),
        ]
        out = tmp_path / "acc.json"
        csc.write_acceptance(checks, out)
        doc = json.loads(out.read_text())
        assert doc["_all_pass"] is False
        assert doc["F1"]["passed"] is True
