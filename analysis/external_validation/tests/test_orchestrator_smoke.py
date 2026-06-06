"""Smoke tests for the cross-seed orchestrator's metric-extraction logic.

These tests do NOT spawn MATLAB or any Python pipeline; they verify the
file-system contract (what the orchestrator reads, what it writes) by
populating a fake DATA_PROC layout in a temp directory and patching the
module-level paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments import run_seed_replication as rsr


@pytest.fixture
def cfg(tmp_path: Path) -> rsr.RunConfig:
    return rsr.RunConfig(
        seeds=[42, 43],
        base="timeline_smoke",
        rng_seed=42,
        n_phases=8,
        phase_duration_s=10.0,
        n_ue=4,
        area_m=100.0,
        drifts_per_type=1,
        anomalies_per_type=1,
        head_baseline_phases=1,
        tail_baseline_phases=1,
        carry_over_ues=True,
        matlab_bin="/usr/bin/false",
        python_bin="/usr/bin/false",
        matlab_flags=("-batch",),
        force=False,
        dry_run=True,
        skip_sim=False,
        skip_pipelines=(),
    )


class TestPathHelpers:
    def test_timeline_name(self, cfg):
        assert rsr._timeline_name(cfg, 42) == "timeline_smoke_seed42"
        assert rsr._timeline_name(cfg, 7) == "timeline_smoke_seed7"

    def test_timeline_json_path(self, cfg):
        p = rsr._timeline_json_path(cfg, 42)
        assert p.name == "timeline_smoke_seed42.json"
        assert "timelines" in p.parts


class TestExtractMetrics:
    def _write_fake_summaries(self, cfg, seed, monkeypatch, tmp_path: Path):
        """Populate a fake DATA_PROC layout for one seed."""
        # Monkey-patch DATA_PROC -> tmp_path so the orchestrator looks here.
        monkeypatch.setattr(rsr, "DATA_PROC", tmp_path)

        tl = rsr._timeline_name(cfg, seed)
        a_dir = tmp_path / f"anomaly_benchmark_{tl}"
        d_dir = tmp_path / f"drift_benchmark_{tl}"
        ad_dir = tmp_path / f"adaptive_benchmark_{tl}"
        demo_dir = tmp_path / f"end_to_end_demo_{tl}"
        for p in (a_dir, d_dir, ad_dir, demo_dir):
            p.mkdir(parents=True)

        (a_dir / "benchmark_summary.json").write_text(json.dumps({
            "winning_detector_by_mean_pr_auc": "PCA-AE",
            "winning_detector_by_per_type_mean_pr_auc": "PCA-AE",
            "winning_detector_by_overall_pr_auc": "IsoForest",
            "elapsed_s": 120.5,
        }))
        (d_dir / "benchmark_summary.json").write_text(json.dumps({
            "winning_detector": "ADWIN",
            "n_detections": 24,
            "n_drifts": 8,
            "elapsed_s": 30.0,
        }))
        (ad_dir / "benchmark_summary.json").write_text(json.dumps({
            "strategies": [
                {"strategy": "static", "overall_pr_auc": 0.2,
                 "drift_phase_pr_auc": 0.05, "cost__n_refits": 1,
                 "cost__fit_cpu_s_sum": 0.01},
                {"strategy": "periodic", "overall_pr_auc": 0.3,
                 "drift_phase_pr_auc": 0.15, "cost__n_refits": 5,
                 "cost__fit_cpu_s_sum": 0.05},
                {"strategy": "drift_triggered_naive", "overall_pr_auc": 0.25,
                 "drift_phase_pr_auc": 0.10, "cost__n_refits": 3,
                 "cost__fit_cpu_s_sum": 0.03},
                {"strategy": "drift_triggered_filtered", "overall_pr_auc": 0.40,
                 "drift_phase_pr_auc": 0.35, "cost__n_refits": 3,
                 "cost__fit_cpu_s_sum": 0.03},
            ],
        }))
        (demo_dir / "demo_summary.json").write_text(json.dumps({
            "cumulative_predicted_uplift": {"hosr": 0.62, "rlf_rate": -0.005},
            "n_interventions": 7,
            "n_retrains": 8,
            "acceptance": {
                "D2": {"pass": True, "msg": "..."},
                "D5": {"pass": True, "msg": "..."},
            },
        }))

    def test_extract_full_summary(self, cfg, tmp_path, monkeypatch):
        self._write_fake_summaries(cfg, 42, monkeypatch, tmp_path)
        row = rsr.extract_metrics_for_seed(cfg, 42)
        assert row["seed"] == 42
        assert row["timeline"] == "timeline_smoke_seed42"
        assert row["anomaly__winning_detector"] == "PCA-AE"  # back-compat
        assert row["anomaly__winning_detector_per_type_mean"] == "PCA-AE"
        assert row["anomaly__winning_detector_overall"] == "IsoForest"
        assert row["drift__winning_detector"] == "ADWIN"
        assert row["drift__n_detections"] == 24
        assert row["adaptive__static__drift_phase_pr_auc"] == pytest.approx(0.05)
        assert row["adaptive__drift_triggered_filtered__drift_phase_pr_auc"] \
            == pytest.approx(0.35)
        assert row["adaptive__delta_filtered_minus_naive"] == pytest.approx(
            0.35 - 0.10
        )
        assert row["demo__cum_uplift_hosr"] == pytest.approx(0.62)
        assert row["demo__n_interventions"] == 7
        assert row["demo__acceptance__D2"] is True

    def test_extract_legacy_summary_fallbacks_to_overall_csv(
        self, cfg, tmp_path, monkeypatch
    ):
        """Older anomaly_benchmark runs (Phase 5) only have the legacy
        ``winning_detector_by_mean_pr_auc`` field. Make sure the
        orchestrator still surfaces an overall winner by reading
        ``overall_pr_auc_with_ci.csv`` from disk."""
        monkeypatch.setattr(rsr, "DATA_PROC", tmp_path)
        tl = rsr._timeline_name(cfg, 42)
        a_dir = tmp_path / f"anomaly_benchmark_{tl}"
        a_dir.mkdir(parents=True)
        (a_dir / "benchmark_summary.json").write_text(json.dumps({
            "winning_detector_by_mean_pr_auc": "LOF",  # legacy only
            "elapsed_s": 1.0,
        }))
        (a_dir / "overall_pr_auc_with_ci.csv").write_text(
            "detector,pr_auc,pr_auc_ci_low,pr_auc_ci_high,ci_str,n_pos,n_neg\n"
            "IsoForest,0.43,0.40,0.46,...,500,3000\n"
            "PCA-AE,0.27,0.25,0.29,...,500,3000\n"
            "LOF,0.16,0.14,0.18,...,500,3000\n"
        )
        row = rsr.extract_metrics_for_seed(cfg, 42)
        # Back-compat column still reflects the legacy field
        assert row["anomaly__winning_detector"] == "LOF"
        assert row["anomaly__winning_detector_per_type_mean"] == "LOF"
        # Overall winner derived from the CSV fallback
        assert row["anomaly__winning_detector_overall"] == "IsoForest"

    def test_extract_marks_missing_pipelines(self, cfg, tmp_path, monkeypatch):
        # Create only the anomaly summary; the rest should be marked __missing.
        monkeypatch.setattr(rsr, "DATA_PROC", tmp_path)
        tl = rsr._timeline_name(cfg, 42)
        a_dir = tmp_path / f"anomaly_benchmark_{tl}"
        a_dir.mkdir(parents=True)
        (a_dir / "benchmark_summary.json").write_text(json.dumps({
            "winning_detector_by_mean_pr_auc": "PCA-AE",
            "elapsed_s": 1.0,
        }))
        row = rsr.extract_metrics_for_seed(cfg, 42)
        assert row["anomaly__winning_detector"] == "PCA-AE"
        assert row["drift__missing"] is True
        assert row["adaptive__missing"] is True
        assert row["demo__missing"] is True

    def test_extract_handles_missing_acceptance(self, cfg, tmp_path, monkeypatch):
        monkeypatch.setattr(rsr, "DATA_PROC", tmp_path)
        tl = rsr._timeline_name(cfg, 42)
        demo_dir = tmp_path / f"end_to_end_demo_{tl}"
        demo_dir.mkdir(parents=True)
        (demo_dir / "demo_summary.json").write_text(json.dumps({
            "cumulative_predicted_uplift": {"hosr": 0.5},
            "n_interventions": 0,
            "n_retrains": 0,
        }))
        row = rsr.extract_metrics_for_seed(cfg, 42)
        assert row["demo__cum_uplift_hosr"] == pytest.approx(0.5)
        # acceptance flags absent -> no demo__acceptance__* keys
        assert not any(k.startswith("demo__acceptance__") for k in row)


class TestMergeWithExistingCsv:
    def test_returns_new_rows_when_no_existing_csv(self, tmp_path):
        new = [{"seed": 42, "x": 1.5}, {"seed": 43, "x": 2.0}]
        merged = rsr.merge_with_existing_csv(new, tmp_path / "missing.csv")
        assert merged == new

    def test_carries_over_unrelated_seeds(self, tmp_path):
        # First run: write 3 seeds.
        first = [{"seed": 42, "x": 1.0}, {"seed": 43, "x": 2.0},
                 {"seed": 44, "x": 3.0}]
        csv_path = tmp_path / "rep.csv"
        rsr.write_summary_csv(first, csv_path)
        # Second run: only retry seed=44 with a new value.
        new = [{"seed": 44, "x": 99.0}]
        merged = rsr.merge_with_existing_csv(new, csv_path)
        by_seed = {int(r["seed"]): r for r in merged}
        assert by_seed[42]["x"] == 1.0       # carried over
        assert by_seed[43]["x"] == 2.0       # carried over
        assert by_seed[44]["x"] == 99.0      # replaced
        assert len(merged) == 3

    def test_results_are_sorted_by_seed(self, tmp_path):
        first = [{"seed": 46, "x": 4.0}, {"seed": 42, "x": 1.0}]
        csv_path = tmp_path / "rep.csv"
        rsr.write_summary_csv(first, csv_path)
        merged = rsr.merge_with_existing_csv(
            [{"seed": 43, "x": 2.0}, {"seed": 44, "x": 3.0}],
            csv_path,
        )
        seeds = [int(r["seed"]) for r in merged]
        assert seeds == sorted(seeds) == [42, 43, 44, 46]

    def test_handles_string_values_and_bools_in_existing(self, tmp_path):
        first = [{"seed": 42, "label": "alpha", "flag": True, "x": 1.5}]
        csv_path = tmp_path / "rep.csv"
        rsr.write_summary_csv(first, csv_path)
        merged = rsr.merge_with_existing_csv(
            [{"seed": 43, "label": "beta", "flag": False, "x": 2.0}],
            csv_path,
        )
        by_seed = {int(r["seed"]): r for r in merged}
        assert by_seed[42]["label"] == "alpha"
        assert by_seed[42]["flag"] is True
        assert by_seed[43]["label"] == "beta"
        assert by_seed[43]["flag"] is False


class TestWriteSummaryCsv:
    def test_round_trip_value_types(self, tmp_path):
        rows = [
            {"seed": 42, "x": 1.5, "label": "alpha", "ok": True},
            {"seed": 43, "x": 2.0, "label": "beta", "ok": False, "extra": 99},
        ]
        out_path = tmp_path / "out.csv"
        rsr.write_summary_csv(rows, out_path)
        text = out_path.read_text().splitlines()
        # Header includes seed + every column seen across rows
        header = text[0].split(",")
        assert "seed" in header
        assert "x" in header
        assert "label" in header
        assert "ok" in header
        assert "extra" in header
        # First-row missing value emitted as empty field
        row42 = text[1].split(",")
        assert row42[header.index("seed")] == "42"
        assert row42[header.index("ok")] == "true"
        assert row42[header.index("extra")] == ""
        # Second row has the extra column populated
        row43 = text[2].split(",")
        assert row43[header.index("extra")] == "99"

    def test_handles_empty_rows(self, tmp_path):
        out_path = tmp_path / "empty.csv"
        rsr.write_summary_csv([], out_path)
        assert out_path.read_text() == "seed\n"

    def test_quotes_commas_in_strings(self, tmp_path):
        rows = [{"label": "a,b,c", "n": 1}]
        out_path = tmp_path / "out.csv"
        rsr.write_summary_csv(rows, out_path)
        text = out_path.read_text()
        assert '"a,b,c"' in text
