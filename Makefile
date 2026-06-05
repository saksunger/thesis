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

.PHONY: help all matlab-check test pytest demo demo-ho sweep-ttt sweep-static gen-timeline-medium eda calibrate-sim calibrate-sweep calibrate sim anomaly-smoke anomaly-benchmark drift-benchmark adaptive-benchmark anomaly drift adaptive surrogate end2end clean

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
	@echo "Phase 7+ (placeholders):"
	@echo "  drift           Drift detection benchmark"
	@echo "  adaptive        Drift-aware adaptive framework"
	@echo "  surrogate       Configuration-performance surrogate"
	@echo "  end2end         End-to-end timeline demo"
	@echo "  all             Run everything in order"
	@echo "  clean           Remove generated artifacts (keeps raw)"
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
	$(PYTHON) -m pytest analysis/anomaly/tests/ analysis/drift/tests/ analysis/adaptive/tests/ tools/tests/ -v

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
ANOMALY_BENCH_TIMELINE ?= timeline_medium
anomaly-benchmark:
	$(PYTHON) -m analysis.anomaly.benchmark_eval --timeline $(ANOMALY_BENCH_TIMELINE)

# ---------------------------------------------------------------------------
# Phase 6 - Drift benchmark (Iter A landed)
# ---------------------------------------------------------------------------
# 7 detectors x 6 streams; ground_truth_drift -> per-(stream, t) labels.
# Outputs Tables 6.1 (latency CI), 6.2 (miss rate), 6.3 (baseline FPR),
# plus drift_detection_heatmap.png (Chapter 6 moneyshot).
DRIFT_BENCH_TIMELINE ?= timeline_medium
drift-benchmark:
	PYTHONUNBUFFERED=1 $(PYTHON) -m analysis.drift.benchmark_eval --timeline $(DRIFT_BENCH_TIMELINE)

# ---------------------------------------------------------------------------
# Phase 7 - Adaptive framework (Iter A landed)
# ---------------------------------------------------------------------------
# 3 strategies (static / periodic / drift-triggered) x PCA-AE base detector
# x ADWIN drift trigger on HOSR/RLF streams. Outputs sliding PR-AUC + cost
# ledger + 2-panel figure (Chapter 7 moneyshot).
ADAPTIVE_BENCH_TIMELINE ?= timeline_medium
adaptive-benchmark:
	PYTHONUNBUFFERED=1 $(PYTHON) -m analysis.adaptive.benchmark_eval --timeline $(ADAPTIVE_BENCH_TIMELINE)

# ---------------------------------------------------------------------------
# Phase 7+ targets (placeholders)
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

all: sim sweep-static calibrate anomaly drift adaptive surrogate end2end

clean:
	rm -rf $(DATA_SIM)/* $(DATA_PROC)/*
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
