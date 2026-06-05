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

# Default timeline (Phase 4+); override with: make sim TIMELINE=timeline_medium
TIMELINE      ?= timeline_short

.PHONY: help all matlab-check test demo demo-ho sweep-ttt sim calibrate anomaly drift adaptive surrogate end2end clean

help:
	@echo "Phase 0–2 (implemented):"
	@echo "  matlab-check    Verify MATLAB toolboxes are installed and licensed"
	@echo "  test            Run all MATLAB unit tests (simulator/tests/)"
	@echo "  demo            Run Phase 1 channel/measurement smoke test"
	@echo "  demo-ho         Run Phase 2 HO event-loop smoke test (single UE)"
	@echo "  sweep-ttt       Run Phase 2 small TTT × hysteresis sanity sweep"
	@echo ""
	@echo "Phase 3+ (placeholders):"
	@echo "  sim             Run simulator timeline (TIMELINE=$(TIMELINE))"
	@echo "  calibrate       KS-test simulator vs real datasets"
	@echo "  anomaly         Anomaly detection benchmark"
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
# Phase 4+ targets (placeholders, to be implemented as we progress)
# ---------------------------------------------------------------------------
sim:
	@mkdir -p $(DATA_SIM)/$(TIMELINE)
	$(MATLAB) $(MATLAB_FLAGS) "addpath(genpath('simulator')); \
		runners.run_timeline('simulator/+scenarios/timelines/$(TIMELINE).yml','$(DATA_SIM)/$(TIMELINE)')"

calibrate:
	$(PYTHON) -m analysis.calibration.run \
		--sim $(DATA_SIM)/$(TIMELINE) \
		--bangladesh $(DATA_RAW)/bangladesh \
		--nordicdat  $(DATA_RAW)/nordicdat

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

all: sim calibrate anomaly drift adaptive surrogate end2end

clean:
	rm -rf $(DATA_SIM)/* $(DATA_PROC)/*
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
