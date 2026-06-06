# Top-level Makefile for the thesis simulator + analysis pipeline.
# Each target should be self-contained and idempotent.

# ---------------------------------------------------------------------------
# MATLAB resolution
# ---------------------------------------------------------------------------
# Auto-detect MATLAB if it's not already on PATH. Override on the command line:
#   make MATLAB_DIR=/path/to/matlab/bin sim
MATLAB_DIR ?= /home/$(USER)/MATLAB/R2023b/bin
ifneq ($(wildcard $(MATLAB_DIR)/matlab),)
  export PATH := $(MATLAB_DIR):$(PATH)
endif
MATLAB        ?= matlab
MATLAB_FLAGS  ?= -batch

# ---------------------------------------------------------------------------
# Python resolution
# ---------------------------------------------------------------------------
PYTHON        ?= python3

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_SIM      := data/simulated
DATA_RAW      := data/raw_public
DATA_PROC     := data/processed
FIG_DEMO_DIR  := $(DATA_SIM)/phase1_demo
FIG_HO_DIR    := $(DATA_SIM)/phase2_demo
FIG_SWEEP_DIR := $(DATA_SIM)/phase2_sweep
CALIB_FINAL   := $(DATA_SIM)/calibration_final
CALIB_SWEEP   := $(DATA_SIM)/calibration_sweep
CALIB_OUT     := $(DATA_PROC)/calibration_ks
TIMELINE_DIR  := simulator/+scenarios/timelines

# Calibration scenario knobs (override on the command line if needed)
CALIB_N_UE      ?= 12
CALIB_T_TOTAL_S ?= 120
CALIB_ISD_M     ?= 500
CALIB_AREA_M    ?= 1500
CALIB_SEED      ?= 999

# Phase 4 timeline: override with: make sim TIMELINE=timeline_medium
TIMELINE      ?= timeline_short

.PHONY: help all all-full all-from-cache matlab-check test pytest demo demo-ho sweep-ttt sweep-static gen-timeline-medium gen-timeline-dense-urban eda calibrate-sim calibrate-sweep calibrate sim anomaly-smoke anomaly-benchmark drift-benchmark adaptive-benchmark surrogate-benchmark end2end-demo seed-replication seed-replication-aggregate seed-replication-all cross-scenario-compare nordicdat-face-validity anomaly drift adaptive surrogate end2end pip-compile pip-sync manifest verify-cache clean

# Phase 4 Iter C sweep_static output dir
SWEEP_STATIC_DIR := $(DATA_SIM)/sweep_static

help:
	@echo "Phase 0–3 (implemented):"
	@echo "  matlab-check    Verify MATLAB toolboxes are installed and licensed"
	@echo "  test            Run all MATLAB unit tests (simulator/tests/)"
	@echo "  demo            Run Phase 1 channel/measurement smoke test"
	@echo "  demo-ho         Run Phase 2 HO event-loop smoke test (single UE)"
	@echo "  sweep-ttt       Run Phase 2 small TTT × hysteresis sanity sweep"
	@echo "  eda             Generate Phase 3 EDA report (Bangladesh + NordicDat)"
	@echo "  calibrate-sim   Run calibration scenario (12 UEs × 120 s → parquet)"
	@echo "  calibrate-sweep Run 3×3 (ISD × area) tuning sweep"
	@echo "  calibrate       KS-test simulator vs NordicDat top-3 segments"
	@echo ""
	@echo "Phase 4 (timeline data generation — Iter B: 4 drifts + 4 anomaly types):"
	@echo "  sim             Build timeline (TIMELINE=$(TIMELINE) -> $(TIMELINE_DIR)/$$TIMELINE.json)"
	@echo "                  Available timelines: timeline_short (3-phase smoke), timeline_iter_b (6-phase full coverage),"
	@echo "                  timeline_medium (30-phase production timeline)"
	@echo "  sweep-static    Iter C static (TTT × hyst × A3 × seeds) sweep → sweep_config_perf.parquet for Phase 8 surrogate"
	@echo "  gen-timeline-medium  Iter C: (re)generate timeline_medium.json via tools/gen_timeline.py"
	@echo ""
	@echo "Phase 5 (anomaly benchmark — Iter A smoke + Iter B full implemented):"
	@echo "  pytest          Run Python unit tests (analysis/anomaly/tests/, requires .venv)"
	@echo "  anomaly-smoke   Iter A smoke: IsoForest + LOF + PCA-AE on TIMELINE; PR-AUC + FPR per phase + per anomaly"
	@echo "  anomaly-benchmark Iter B full: 5 detectors (IsoForest/LOF/OneClassSVM/PCA-AE/MLP-AE)"
	@echo "                  x cell-conditional A-3 labels x bootstrap CI x window sweep (2/5/10/30 s) x ablation"
	@echo "                  Default timeline: timeline_medium. Overrides: TIMELINE=<name>"
	@echo ""
	@echo "Phase 6 (drift benchmark - Iter A implemented):"
	@echo "  drift-benchmark Iter A: 7 detectors (ADWIN/KSWIN/PageHinkley/DDM/EDDM/MMD-batch/Energy-batch)"
	@echo "                  x 6 streams (RSRP/SINR fleet means + HO/HOSR/PP/RLF rolling rates)"
	@echo "                  x bootstrap CI on median latency. Default timeline: timeline_medium (~4.5 min)."
	@echo ""
	@echo "Phase 7 (adaptive framework - Iter A implemented):"
	@echo "  adaptive-benchmark Iter A: 3 retraining strategies (static / periodic-180s / drift-triggered)"
	@echo "                  x PCA-AE base detector x ADWIN drift trigger on HOSR/RLF streams."
	@echo "                  Outputs Table 7.1, sliding PR-AUC + cost ledger, 2-panel figure."
	@echo "                  Default timeline: timeline_medium."
	@echo ""
	@echo "Phase 8 (config-perf surrogate - Iter A implemented):"
	@echo "  surrogate-benchmark Iter A: HistGB (point) + QuantileGB (q=0.1/0.5/0.9) x 3 mobility KPIs"
	@echo "                  (HOSR/RLF_rate/ping_pong_rate) on sweep_config_perf.parquet (360 rows)."
	@echo "                  Grouped 5-fold CV + inverse query @ median deployment. ~30 s wall clock."
	@echo ""
	@echo "Phase 9 (end-to-end demo - Iter A implemented):"
	@echo "  end2end-demo    Iter A: full pipeline replay - drift detect -> filtered retrain ->"
	@echo "                  surrogate query -> counterfactual KPI uplift. PCA-AE + ADWIN + HistGB +"
	@echo "                  ConformalQuantileGB on timeline_medium. Outputs intervention_log.csv +"
	@echo "                  4-panel end_to_end_moneyshot.png (thesis defense plate)."
	@echo "                  Default timeline: timeline_medium."
	@echo ""
	@echo "Phase 11 (external validation):"
	@echo "  seed-replication           Iter A: regen 5 seeded timelines + run 4 pipelines each (~2.5 h)"
	@echo "  seed-replication-aggregate Iter A: bootstrap CI + boxplot + E1..E5 acceptance check"
	@echo "  seed-replication-all       Chained: orchestrator -> aggregator"
	@echo "                  Override: SEEDS='42 100 200' SEED_BASE=timeline_medium"
	@echo "  gen-timeline-dense-urban   Iter B: build timeline_dense_urban.json (37 cells, 24 UEs)"
	@echo "  cross-scenario-compare     Iter B: compare base vs contrast timeline pipelines"
	@echo "                             Override: CROSS_BASE=tl1 CROSS_CONTRAST=tl2"
	@echo "  nordicdat-face-validity    Iter C: ADWIN+PCA-AE on NordicDat (~1 min, qualitative)"
	@echo ""
	@echo "Phase 10 (reproducibility — Tier 1):"
	@echo "  all-full        Phase 1->11 from scratch (MATLAB required, ~3.5-4 h)"
	@echo "  all-from-cache  Python-only chain on cached deposit (~30 min). Profile A/B."
	@echo "  manifest        Regenerate data/manifest.sha256 (sha256sum-compatible)"
	@echo "  verify-cache    Verify cached parquets/CSV against manifest.sha256"
	@echo "  pip-compile     Regenerate hash-locked requirements.txt from requirements.in"
	@echo "  pip-sync        Sync venv exactly with requirements.txt (uninstalls extras)"
	@echo ""
	@echo "  all             Alias for all-full (kept for back-compat)"
	@echo "  clean           Remove generated artifacts (keeps raw)"
	@echo ""
	@echo "Legacy placeholders (point at non-existent modules, kept for old docs):"
	@echo "  anomaly / drift / adaptive / surrogate / end2end"
	@echo "  -> use anomaly-benchmark / drift-benchmark / adaptive-benchmark /"
	@echo "     surrogate-benchmark / end2end-demo instead."
	@echo ""
	@echo "Env (auto-detected):"
	@echo "  MATLAB_DIR=$(MATLAB_DIR)"
	@echo "  MATLAB=$(MATLAB)  (will be on PATH after make sets it)"

matlab-check:
	$(MATLAB) $(MATLAB_FLAGS) "addpath('simulator/scripts'); check_toolboxes"

test:
	$(MATLAB) $(MATLAB_FLAGS) "addpath(genpath('simulator')); \
		r = runtests('simulator/tests','UseParallel',false); \
		fprintf('\\n=== %d/%d PASS ===\\n', sum([r.Passed]), numel(r)); \
		exit(double(any([r.Failed])))"

pytest:
	$(PYTHON) -m pytest analysis/anomaly/tests/ analysis/drift/tests/ analysis/adaptive/tests/ analysis/config_perf/tests/ analysis/demo/tests/ analysis/external_validation/tests/ tools/tests/ -v

demo:
	@mkdir -p $(FIG_DEMO_DIR)
	$(MATLAB) $(MATLAB_FLAGS) "addpath(genpath('simulator')); demo_phase1"
	@echo "PNGs in $(FIG_DEMO_DIR):"
	@ls -lh $(FIG_DEMO_DIR)

demo-ho:
	@mkdir -p $(FIG_HO_DIR)
	$(MATLAB) $(MATLAB_FLAGS) "addpath(genpath('simulator')); demo_phase2"
	@echo "PNGs in $(FIG_HO_DIR):"
	@ls -lh $(FIG_HO_DIR)

sweep-ttt:
	@mkdir -p $(FIG_SWEEP_DIR)
	$(MATLAB) $(MATLAB_FLAGS) "addpath(genpath('simulator')); sweep_ttt_hyst"
	@echo "PNGs + MAT in $(FIG_SWEEP_DIR):"
	@ls -lh $(FIG_SWEEP_DIR)

# ---------------------------------------------------------------------------
# Phase 3 calibration pipeline
# ---------------------------------------------------------------------------
eda:
	$(PYTHON) -m analysis.calibration.eda_report
	@ls -lh $(DATA_PROC)/calibration_eda

calibrate-sim:
	@mkdir -p $(CALIB_FINAL)
	@rm -f $(CALIB_FINAL)/kpis_ue*.parquet $(CALIB_FINAL)/events_ue*.parquet
	$(MATLAB) $(MATLAB_FLAGS) "addpath(genpath('simulator')); \
		calibration_run('$(CALIB_FINAL)', struct( \
			'n_ue', $(CALIB_N_UE), \
			't_total_s', $(CALIB_T_TOTAL_S), \
			'isd_m', $(CALIB_ISD_M), \
			'area_m', $(CALIB_AREA_M), \
			'master_seed', $(CALIB_SEED)))"
	@echo "Sim parquet in $(CALIB_FINAL):"
	@ls -lh $(CALIB_FINAL) | head -20

calibrate-sweep:
	@mkdir -p $(CALIB_SWEEP)
	$(MATLAB) $(MATLAB_FLAGS) "addpath(genpath('simulator')); \
		calibration_sweep('$(CALIB_SWEEP)')"
	# `--segment 1/5G-NSA/LTE_B20` is the NordicDat segment identifier
	# (operator_id / ran / band as printed in the raw CSV). It is the
	# closest 5G-flavored real reference for our NR_SA simulator and is
	# locked as the primary calibration target by ADR-14.
	$(PYTHON) -m analysis.calibration.ks_sweep_compare \
		--root $(CALIB_SWEEP) \
		--kpi rsrp_serving_dbm \
		--segment 1/5G-NSA/LTE_B20

calibrate: calibrate-sim
	$(PYTHON) -m analysis.calibration.ks_test \
		--sim $(CALIB_FINAL) --label final
	@echo "Calibration artifacts in $(CALIB_OUT):"
	@ls -lh $(CALIB_OUT)

# ---------------------------------------------------------------------------
# Phase 4 — Timeline data generation (Iter B: 4 drifts + 4 anomaly injectors)
# Phase 4 Iter C — Production timelines + static sweep for Phase 8 surrogate
# ---------------------------------------------------------------------------
sim:
	@mkdir -p $(DATA_SIM)/$(TIMELINE)
	$(MATLAB) $(MATLAB_FLAGS) "addpath(genpath('simulator')); \
		build_timeline('$(TIMELINE_DIR)/$(TIMELINE).json','$(DATA_SIM)/$(TIMELINE)')"
	@echo "Timeline parquet + ground truth in $(DATA_SIM)/$(TIMELINE):"
	@ls -lh $(DATA_SIM)/$(TIMELINE)

# Iter C: static (TTT × hyst × A3 × seeds) sweep producing the
# config-performance training matrix consumed by the Phase 8 surrogate.
# Default grid is 3 × 4 × 3 × 10 = 360 runs ≈ 5–10 min wall clock with parfor.
sweep-static:
	@mkdir -p $(SWEEP_STATIC_DIR)
	$(MATLAB) $(MATLAB_FLAGS) "addpath(genpath('simulator')); \
		sweep_static('$(SWEEP_STATIC_DIR)')"
	@echo "Static sweep parquet + metadata in $(SWEEP_STATIC_DIR):"
	@ls -lh $(SWEEP_STATIC_DIR)

# Iter C: parametric timeline generator (used to (re)build production timelines).
# Defaults to regenerating timeline_medium.json; override TIMELINE_GEN_TARGET to
# build a different file, or call `python -m tools.gen_timeline` directly for
# custom configs.
TIMELINE_GEN_TARGET ?= simulator/+scenarios/timelines/timeline_medium.json
gen-timeline-medium:
	$(PYTHON) -m tools.gen_timeline \
		--out $(TIMELINE_GEN_TARGET) \
		--timeline-id timeline_medium \
		--description "Phase 4 Iter C production timeline: 30 phases x 60s. 2 instances per drift, 10 anomalies per type. UE positions carry over." \
		--n-phases 30 --phase-duration-s 60 \
		--master-seed 42 --n-ue 12 --area-m 1500 \
		--variant default \
		--carry-over-ues true \
		--drifts-per-type 2 --anomalies-per-type 10 \
		--head-baseline-phases 3 --tail-baseline-phases 3 \
		--rng-seed 42

# Phase 11 Iter B: dense-urban variant (3-tier hex, 24 UEs, 750 m area,
# isd_m=350). Cross-scenario benchmark — see plan.md Phase 11.
DENSE_URBAN_GEN_TARGET ?= simulator/+scenarios/timelines/timeline_dense_urban.json
gen-timeline-dense-urban:
	$(PYTHON) -m tools.gen_timeline \
		--out $(DENSE_URBAN_GEN_TARGET) \
		--timeline-id timeline_dense_urban \
		--description "Phase 11 Iter B cross-scenario regime: 3-tier hex (37 cells), 24 UEs, 750 m area, isd_m=350. Same eval horizon as timeline_medium (30 x 60s)." \
		--n-phases 30 --phase-duration-s 60 \
		--master-seed 42 --n-ue 24 --area-m 750 \
		--variant dense_urban \
		--carry-over-ues true \
		--drifts-per-type 2 --anomalies-per-type 10 \
		--head-baseline-phases 3 --tail-baseline-phases 3 \
		--rng-seed 42

# ---------------------------------------------------------------------------
# Phase 5 — Anomaly detection (Iter A smoke + Iter B full benchmark landed)
# ---------------------------------------------------------------------------
anomaly-smoke:
	$(PYTHON) -m analysis.anomaly.smoke_eval --timeline $(TIMELINE)

# Iter B full benchmark (5 detectors, cell-conditional A-3, bootstrap CI,
# window sweep, per-feature ablation). Default TIMELINE for this target
# is timeline_medium (30-phase Iter C production timeline).
# ANOMALY_BENCH_TIMELINE wins over TIMELINE so callers can still pin the
# anomaly benchmark to medium even while running other pipelines on a
# different sim. If only TIMELINE is set we honor it (Phase 11 Iter B
# style: `make ... TIMELINE=timeline_dense_urban` should reach every
# pipeline by default).
ANOMALY_BENCH_TIMELINE ?= $(if $(filter-out timeline_short,$(TIMELINE)),$(TIMELINE),timeline_medium)
anomaly-benchmark:
	$(PYTHON) -m analysis.anomaly.benchmark_eval --timeline $(ANOMALY_BENCH_TIMELINE)

# ---------------------------------------------------------------------------
# Phase 6 - Drift benchmark (Iter A landed)
# ---------------------------------------------------------------------------
# 7 detectors x 6 streams; ground_truth_drift -> per-(stream, t) labels.
# Outputs Tables 6.1 (latency CI), 6.2 (miss rate), 6.3 (baseline FPR),
# plus drift_detection_heatmap.png (Chapter 6 moneyshot).
DRIFT_BENCH_TIMELINE ?= $(if $(filter-out timeline_short,$(TIMELINE)),$(TIMELINE),timeline_medium)
drift-benchmark:
	PYTHONUNBUFFERED=1 $(PYTHON) -m analysis.drift.benchmark_eval --timeline $(DRIFT_BENCH_TIMELINE)

# ---------------------------------------------------------------------------
# Phase 7 - Adaptive framework (Iter A landed)
# ---------------------------------------------------------------------------
# 3 strategies (static / periodic / drift-triggered) x PCA-AE base detector
# x ADWIN drift trigger on HOSR/RLF streams. Outputs sliding PR-AUC + cost
# ledger + 2-panel figure (Chapter 7 moneyshot).
ADAPTIVE_BENCH_TIMELINE ?= $(if $(filter-out timeline_short,$(TIMELINE)),$(TIMELINE),timeline_medium)
adaptive-benchmark:
	PYTHONUNBUFFERED=1 $(PYTHON) -m analysis.adaptive.benchmark_eval --timeline $(ADAPTIVE_BENCH_TIMELINE)

# ---------------------------------------------------------------------------
# Phase 8 - Config-performance surrogate (Iter A landed)
# ---------------------------------------------------------------------------
# HistGB (point estimate) + QuantileGB (q=0.1/0.5/0.9) on the 360-row
# sweep_config_perf.parquet (training matrix from Phase 4c sweep_static).
# Outputs Tables 8.1 (per-target CV MAE/R^2/RMSE) + 8.2 (PI coverage)
# + reliability_diagram.png + inverse_query.png + mae_per_target.png.
surrogate-benchmark:
	PYTHONUNBUFFERED=1 $(PYTHON) -m analysis.config_perf.benchmark_eval

# ---------------------------------------------------------------------------
# Phase 9 - End-to-end drift-aware adaptive demo (Iter A landed)
# ---------------------------------------------------------------------------
# Combines Phase 6 drift detection (ADWIN), Phase 7 filtered retraining
# (PCA-AE + bottom-q filter), and Phase 8 surrogate query (HistGB +
# ConformalQuantileGB) into a single walk-forward replay on
# `timeline_medium`. Emits intervention_log + per_window + retrain_log
# CSVs + 4-panel end_to_end_moneyshot.png (the thesis defense plate).
DEMO_TIMELINE ?= $(if $(filter-out timeline_short,$(TIMELINE)),$(TIMELINE),timeline_medium)
end2end-demo:
	PYTHONUNBUFFERED=1 $(PYTHON) -m analysis.demo.run_demo --timeline $(DEMO_TIMELINE)

# ---------------------------------------------------------------------------
# Phase 11 — External validation
# ---------------------------------------------------------------------------
# Iter A: cross-seed replication. Runs every pipeline on 5 master_seed
# variants of timeline_medium (structure held fixed). Idempotent and
# resumable: outputs already present on disk are skipped.
#
# Wall clock for the full run is ~2.5 h (5 × 30 min ≈ MATLAB sim 6 min +
# Python pipelines 24 min per seed). Override SEEDS to use fewer.
SEEDS         ?= 42 43 44 45 46
SEED_BASE     ?= timeline_medium
seed-replication:
	PYTHONUNBUFFERED=1 $(PYTHON) -m experiments.run_seed_replication \
		--base $(SEED_BASE) --seeds $(SEEDS)

seed-replication-aggregate:
	PYTHONUNBUFFERED=1 $(PYTHON) -m analysis.external_validation.seed_robustness \
		--in $(DATA_PROC)/external_validation/seed_replication_$(SEED_BASE).csv

# Convenience: orchestrator + aggregator chained.
seed-replication-all: seed-replication seed-replication-aggregate

# Phase 11 Iter B - cross-scenario comparison.
# Assumes both <base> and <contrast> timelines have been simulated +
# run through anomaly/drift/adaptive/end2end benchmarks (e.g. via the
# usual `make sim TIMELINE=...` + per-pipeline targets, or by reusing
# `seed-replication` with `--seeds 42 --base <name>`).
CROSS_BASE     ?= timeline_medium
CROSS_CONTRAST ?= timeline_dense_urban
cross-scenario-compare:
	PYTHONUNBUFFERED=1 $(PYTHON) -m analysis.external_validation.cross_scenario_compare \
		--base $(CROSS_BASE) --contrast $(CROSS_CONTRAST)

# Phase 11 Iter C - NordicDat face validity.
NORDIC_OPERATOR ?= 1
NORDIC_RAN      ?= 5G-NSA
NORDIC_TRAIN_S  ?= 600
nordicdat-face-validity:
	PYTHONUNBUFFERED=1 $(PYTHON) -m analysis.external_validation.nordicdat_apply \
		--operator $(NORDIC_OPERATOR) --ran $(NORDIC_RAN) \
		--train-s $(NORDIC_TRAIN_S)

# ---------------------------------------------------------------------------
# Phase 9+ targets (placeholders)
# ---------------------------------------------------------------------------

anomaly:
	$(PYTHON) -m analysis.anomaly.run --data $(DATA_SIM)/$(TIMELINE)

drift:
	$(PYTHON) -m analysis.drift.run --data $(DATA_SIM)/$(TIMELINE)

adaptive:
	$(PYTHON) -m analysis.adaptive.run --data $(DATA_SIM)/$(TIMELINE)

surrogate:
	$(PYTHON) -m analysis.config_perf.run --data $(DATA_SIM)/$(TIMELINE)

end2end:
	$(PYTHON) -m analysis.demo.run --data $(DATA_SIM)/$(TIMELINE)

# `make all` is now an alias for `all-full` (Profile C). For the no-MATLAB
# reviewer flow use `make all-from-cache` (Profile A/B).
all: all-full

# ---------------------------------------------------------------------------
# Phase 10 — End-to-end pipeline chains (Tier 1 reproducibility)
# ---------------------------------------------------------------------------
# `make all-full`: Phase 1 -> 11 regeneration from scratch. Requires MATLAB
# R2023b + all 4 toolboxes. Wall clock ~3.5-4 h dominated by the 5-seed
# replication (~2.5 h MATLAB). Produces every parquet, CSV, JSON, PNG that
# the thesis cites and a fresh SHA256 manifest.
#
# `make all-from-cache`: Python-only regeneration. Requires the cached
# simulator parquets (Zenodo deposit) under data/simulated/ and
# data/processed/ — verified against the deposited SHA256 manifest first.
# Wall clock ~30 min on a 4-core x86_64. This is the Profile A/B path
# (docs/reproducibility.md).
#
# Both targets are tested by re-running `pytest` first. If pytest fails,
# we abort before kicking off any expensive sim/analysis work.

all-full: matlab-check pytest
	$(MAKE) calibrate
	$(MAKE) sim TIMELINE=timeline_medium
	$(MAKE) sweep-static
	$(MAKE) gen-timeline-dense-urban
	$(MAKE) sim TIMELINE=timeline_dense_urban
	$(MAKE) anomaly-benchmark TIMELINE=timeline_medium
	$(MAKE) drift-benchmark    TIMELINE=timeline_medium
	$(MAKE) adaptive-benchmark TIMELINE=timeline_medium
	$(MAKE) surrogate-benchmark
	$(MAKE) end2end-demo       TIMELINE=timeline_medium
	$(MAKE) anomaly-benchmark  TIMELINE=timeline_dense_urban
	$(MAKE) drift-benchmark    TIMELINE=timeline_dense_urban
	$(MAKE) adaptive-benchmark TIMELINE=timeline_dense_urban
	$(MAKE) end2end-demo       TIMELINE=timeline_dense_urban
	$(MAKE) cross-scenario-compare \
		CROSS_BASE=timeline_medium \
		CROSS_CONTRAST=timeline_dense_urban
	$(MAKE) seed-replication-all
	@if [ -d data/raw_public/nordicdat ]; then \
	    $(MAKE) nordicdat-face-validity; \
	else \
	    echo "[all-full] skipping nordicdat-face-validity: data/raw_public/nordicdat/ not present (see data/raw_public/README.md)"; \
	fi
	$(MAKE) manifest
	@echo "[all-full] complete. SHA256 manifest -> data/manifest.sha256"

all-from-cache: verify-cache pytest
	$(MAKE) anomaly-benchmark  TIMELINE=timeline_medium
	$(MAKE) drift-benchmark    TIMELINE=timeline_medium
	$(MAKE) adaptive-benchmark TIMELINE=timeline_medium
	$(MAKE) surrogate-benchmark
	$(MAKE) end2end-demo       TIMELINE=timeline_medium
	$(MAKE) anomaly-benchmark  TIMELINE=timeline_dense_urban
	$(MAKE) drift-benchmark    TIMELINE=timeline_dense_urban
	$(MAKE) adaptive-benchmark TIMELINE=timeline_dense_urban
	$(MAKE) end2end-demo       TIMELINE=timeline_dense_urban
	$(MAKE) cross-scenario-compare \
		CROSS_BASE=timeline_medium \
		CROSS_CONTRAST=timeline_dense_urban
	$(MAKE) seed-replication-aggregate
	@if [ -d data/raw_public/nordicdat ]; then \
	    $(MAKE) nordicdat-face-validity; \
	else \
	    echo "[all-from-cache] skipping nordicdat-face-validity: data/raw_public/nordicdat/ not present (see data/raw_public/README.md)"; \
	fi
	@echo "[all-from-cache] complete. Re-run 'make verify-cache' to confirm cached inputs untouched."

# ---------------------------------------------------------------------------
# Phase 10 — SHA256 manifest of cached artefacts (deposit + verify)
# ---------------------------------------------------------------------------
# `make manifest`: walk data/simulated/ + data/processed/ and write a fresh
# SHA256 manifest at data/manifest.sha256. Output is sha256sum-compatible so
# reviewers without Python can run `sha256sum -c data/manifest.sha256`.
#
# `make verify-cache`: check every entry in the manifest still exists with
# the deposited SHA. Extra files in data/ are ignored (out-of-scope for the
# deposit). Exits non-zero on any missing or mismatched file.
MANIFEST_PATH ?= data/manifest.sha256

manifest:
	$(PYTHON) -m tools.manifest --generate --manifest $(MANIFEST_PATH)

verify-cache:
	@if [ ! -f $(MANIFEST_PATH) ]; then \
	    echo "[verify-cache] manifest missing at $(MANIFEST_PATH)."; \
	    echo "[verify-cache] Either (a) fetch the Zenodo bundle (Phase 10 A5),"; \
	    echo "[verify-cache] or (b) run 'make manifest' to create a fresh one"; \
	    echo "[verify-cache] from your current data/ (only after 'make all-full')."; \
	    exit 2; \
	fi
	$(PYTHON) -m tools.manifest --verify --manifest $(MANIFEST_PATH)

# ---------------------------------------------------------------------------
# Phase 10 — Reproducibility tooling (pip-tools lockfile)
# ---------------------------------------------------------------------------
# `pip-compile` regenerates the hash-locked requirements.txt from
# requirements.in (edit deps there, then run this). We strip the
# machine-specific `--trusted-host` lines that pip-compile inherits
# from the user's local pip config so the lockfile is portable.
#
# `pip-sync` aligns the active venv with the lockfile exactly (uninstalls
# anything not in requirements.txt). Use after a fresh git pull.
pip-compile:
	$(PYTHON) -m pip install --quiet --upgrade pip-tools
	$(PYTHON) -m piptools compile --allow-unsafe --generate-hashes \
		--resolver=backtracking \
		--output-file=requirements.txt requirements.in
	sed -i '/^--trusted-host/d' requirements.txt
	@echo "Regenerated requirements.txt ($$(grep -cE '^[a-zA-Z]' requirements.txt) packages pinned)."
	@echo "Commit both requirements.in and requirements.txt."

pip-sync:
	$(PYTHON) -m pip install --quiet --upgrade pip-tools
	$(PYTHON) -m piptools sync requirements.txt

clean:
	rm -rf $(DATA_SIM)/* $(DATA_PROC)/*
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
