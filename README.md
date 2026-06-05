# ML-Based Anomaly and Drift-Aware Configuration–Performance Modeling for Adaptive Handover Optimization in 5G/6G Networks

Master's thesis — Wrocław University of Science and Technology, Department of Computer Engineering.

## TL;DR

1. A custom MATLAB micro-simulator implements the 3GPP NR HO procedure (TS 38.331 §5.5.4 A3 event, §5.3.10 RLF/T310, TR 38.901 channel model).
2. Parameter sweeps over (TTT, hysteresis, A3 offset) produce a controlled (config → KPI) dataset.
3. Drift scenarios (channel-model swap, traffic shift, mobility-profile change) and anomaly injections (RLF bursts, measurement glitches, slow degradation) are scripted with ground-truth labels.
4. ML benchmarks: anomaly detection (IsoForest, LOF, AE, LSTM-AE, Transformer-AE), drift detection (ADWIN, DDM, KSWIN, PH, MMD), drift-aware adaptive retraining, calibrated config–performance surrogate.
5. Sanity check: simulator output is compared against real-world Bangladesh and NordicDat distributions (KS test).

See [`docs/plan.md`](docs/plan.md) for the full phased plan, [`docs/design.md`](docs/design.md) for architecture decisions, [`docs/schema.md`](docs/schema.md) for the KPI dictionary.

## Repo layout

```
.
├── docs/                  # Plan, design ADRs, KPI schema, 3GPP refs, scenarios
├── simulator/             # MATLAB micro-simulator (3GPP A3 + RLF)
│   ├── +channel/          # TR 38.901 path-loss + shadowing
│   ├── +mobility/         # Waypoint + Gauss-Markov
│   ├── +ho/               # A3 event, RLF, ping-pong
│   ├── +scenarios/        # Drift + anomaly scripts
│   ├── +utils/            # Logging, IO, plotting helpers
│   ├── runners/           # Sweep runners (parfor)
│   ├── tests/             # MATLAB unit tests
│   └── scripts/           # Helper one-shots (toolbox check etc.)
├── data/
│   ├── raw_public/        # Bangladesh + NordicDat (local copies, gitignored)
│   ├── simulated/         # Generated parquet + demo PNGs (gitignored)
│   ├── processed/         # ML-ready features (gitignored)
│   └── external/          # Misc third-party (3GPP PDFs etc., gitignored)
├── analysis/              # Python: notebooks + scripts
│   ├── calibration/       # KS-test sim vs real
│   ├── anomaly/           # Benchmark
│   ├── drift/             # Benchmark
│   ├── adaptive/          # Drift-triggered retraining
│   ├── config_perf/       # Surrogate model
│   ├── demo/              # End-to-end timeline
│   └── common/            # Shared loaders + metrics
├── paper/                 # Thesis source (LaTeX)
├── docker/                # Reproducibility container
├── Makefile               # `make sim`, `make anomaly`, `make all`
└── README.md
```

## Quickstart

### Prerequisites

- **MATLAB R2023b or newer**, with these add-ons (all auto-checked by `make matlab-check`):
  - Communications Toolbox
  - 5G Toolbox
  - Statistics and Machine Learning Toolbox
  - Parallel Computing Toolbox
  - Deep Learning Toolbox (optional, for the LSTM-AE in Phase 5)
- Python 3.11+
- `make`, `git`

### MATLAB path

The Makefile auto-detects MATLAB at `/home/$USER/MATLAB/R2023b/bin/matlab`. Override by exporting `MATLAB_DIR` or passing on the command line:

```bash
make MATLAB_DIR=/opt/matlab/R2024a/bin matlab-check
# or
export MATLAB_DIR=/opt/matlab/R2024a/bin
make matlab-check
```

### Phase 0–2 targets (currently usable)

```bash
make help            # show all targets
make matlab-check    # verify required toolboxes are installed + licensed
make test            # run all MATLAB unit tests (Phase 1 + 2: 34 tests)
make demo            # Phase 1 channel/measurement smoke test
make demo-ho         # Phase 2 HO event loop smoke test (single UE)
make sweep-ttt       # Phase 2 small TTT × hysteresis sanity sweep (~30 s)
```

Expected output of `make test`:

```
=== 34/34 PASS ===
```

Expected artifacts:

```
data/simulated/phase1_demo/phase1_layout.png    # 7-cell hex + UE trajectory
data/simulated/phase1_demo/phase1_kpis.png      # RSRP / RSRQ / SINR vs time
data/simulated/phase2_demo/phase2_serving.png   # serving cell + HO events over time
data/simulated/phase2_demo/phase2_l3rsrp.png    # L3-filtered RSRP per cell
data/simulated/phase2_sweep/sweep_ho_attempt.png  # heatmap: TTT × hyst → HO count
data/simulated/phase2_sweep/sweep_ping_pong.png   # heatmap: TTT × hyst → pp rate
data/simulated/phase2_sweep/sweep_results.mat     # raw tensors for analysis
```

### Phase 3+ targets (placeholders)

Will be wired up as we implement the corresponding analysis pipelines.

```bash
make sim         # run simulator timeline (TIMELINE=<name>)
make calibrate   # KS-test simulator vs real datasets        ← Phase 3 (next)
make anomaly     # anomaly detection benchmark
make drift       # drift detection benchmark
make adaptive    # drift-aware adaptive framework
make surrogate   # config–performance surrogate
make end2end     # end-to-end timeline demo
make all         # everything end-to-end
```

### Python venv (analysis side, set up before Phase 3)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # added in Phase 3
```

## Real public datasets used for sanity check

| Dataset    | Local path (gitignored)             | Size  | Role in this track                                                       |
|------------|-------------------------------------|-------|--------------------------------------------------------------------------|
| Bangladesh | `data/raw_public/bangladesh/`       | 535 MB| HO success/fail rate sanity, RSRP / CINR distribution shape (LTE)        |
| NordicDat  | `data/raw_public/nordicdat/`        | 14 MB | RSRP / RSRQ / SINR distribution KS-test (LTE + 5G-NSA), 65-day time span |

Datasets are **not** used as training data on this track. They are external references for simulator calibration only. See `data/raw_public/README.md` for repopulation instructions on a fresh clone.

## License & citation

TBD — pick a license before public release. Cite 3GPP TS/TR documents and dataset originators per `docs/3gpp_refs.md` and `data/raw_public/README.md`.

## Status

| Phase                                           | Status                              |
|-------------------------------------------------|-------------------------------------|
| 0. Foundation (repo, docs, Makefile, datasets)  | **done**                            |
| 1.1. Simulator channel core (TR 38.901 UMa)     | **done** (14 unit tests)            |
| 2. HO mechanism v0 (TS 38.331 A3 + RLF)         | **done** (20 unit tests, sweep ok)  |
| 3. Calibration vs real data (KS-test)           | next                                |
| 4. Data generation (sweeps + drift + anomaly)   | planned                             |
| 5. Anomaly benchmark                            | planned                             |
| 6. Drift benchmark                              | planned                             |
| 7. Adaptive framework                           | planned                             |
| 8. Config–performance surrogate                 | planned                             |
| 9. End-to-end demo                              | planned                             |
| 10. Writing + reproducibility package           | planned                             |

See [`docs/plan.md`](docs/plan.md) for live, per-task status.
