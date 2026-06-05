# Design Decisions (ADR-style)

Every binding architecture decision goes here. Format:
**ADR-N — Title** · *date* · status (proposed / accepted / superseded by ADR-X) · context · decision · consequences.

---

## ADR-1 — Custom MATLAB micro-simulator over ns-3 / Simu5G
*2026-06-05 · accepted*

**Context.** Three contenders for the simulator: ns-3 (5G-LENA), Simu5G (OMNeT++), MATLAB-based custom. Author has strong MATLAB experience, zero ns-3 / OMNeT++. Thesis has a hard schedule constraint, so build-from-scratch with unfamiliar tooling is high risk. The HO-modeling scope is bounded: A3 event, RLF, ping-pong, no need for full PDCP/RLC/MAC fidelity.

**Decision.** Build a custom MATLAB micro-simulator that implements only the layers strictly required by the thesis: TR 38.901 channel (UMa/UMi), L3 measurement filtering, A3 event (TS 38.331 §5.5.4), RLF (TS 38.331 §5.3.10), ping-pong detection. Use the MATLAB **Communications Toolbox** for channel utilities where possible. Do **not** invest in a full 5G stack.

**Consequences.**
- (+) Familiar tooling, fast iteration, `parfor` for sweeps.
- (+) Full control over the event log schema → clean parquet for ML pipeline.
- (−) Cannot claim end-to-end RAN fidelity (PHY/MAC abstracted). Mitigation: explicit "scope: control-plane mobility events only" framing in thesis methodology.
- (−) Reviewer may compare to ns-3 5G-LENA. Mitigation: cite Patriciello 2019 explicitly, note as "alternative considered, rejected due to schedule + scope."

---

## ADR-2 — Hex cell layout, 7-cell (1 + 6 tier), wrap-around optional
*2026-06-05 · accepted*

**Context.** 3GPP study item layouts often use 19 or 21 cells. For HO experiments at moderate cell density, 7 cells (center + first ring) is sufficient and standard in academic papers.

**Decision.** Default scenario: 7-cell hex grid, ISD configurable (default 500 m macro), antennas at cell center, omnidirectional baseline. Wrap-around optional via image cells if edge effects matter.

**Consequences.**
- (+) Cheap simulation, clean center-cell statistics.
- (−) Edge effects unless wrap-around enabled. Mitigation: only use center cell + first ring boundary crossings for KPI accounting.

---

## ADR-3 — Channel model: TR 38.901 UMa as default, UMi as alternative
*2026-06-05 · accepted*

**Context.** TR 38.901 is the 3GPP-standard channel modeling document for above-6-GHz study items, applied widely in <6 GHz too. UMa = Urban Macro, UMi = Urban Micro. Drift scenario "channel model swap" needs at least two distinct models.

**Decision.** Implement TR 38.901 Table 7.4.1-1 UMa LoS/NLoS path-loss + log-normal shadowing (sigma per Table 7.4.1-2). Implement UMi as alternative for drift scenarios. Spatially correlated shadowing via 2D grid sampling (decorrelation distance per Table 7.5-6).

**Consequences.**
- (+) Standard, defensible, well-documented.
- (−) Time-selective fading abstracted. We do **not** model fast fading at the symbol level (out of scope for HO event timing). Mitigation: state explicitly in chapter 4.

---

## ADR-4 — Mobility: scripted waypoint + Gauss-Markov alternative
*2026-06-05 · accepted*

**Context.** HO experiments need deterministic, reproducible mobility for clean ground truth. Random Waypoint introduces issues (border effect, no momentum).

**Decision.** Two mobility models:
1. **Scripted waypoint** — polyline trajectories per UE, configurable speed segments. Deterministic, suitable for "drive-test-like" sanity.
2. **Gauss-Markov** — per TR 38.901 §7.6.3.2 spirit (correlated random direction + speed), seeded for reproducibility. Suitable for population-level KPI statistics.

Default = waypoint for unit tests + small-scale demos, GM for sweep population.

**Consequences.**
- (+) Reproducibility via seed.
- (−) Neither is realistic at city scale. Acceptable for thesis scope (HO event statistics, not traffic engineering).

---

## ADR-5 — Time step: 10 ms RRC-level, no PHY symbol-level
*2026-06-05 · accepted*

**Context.** A3 event TTT values start at 40 ms (TS 38.331). L3 filter has a quantization period of ~200 ms. PHY-symbol-level simulation (≤1 ms) would be overkill and 100× slower.

**Decision.** Internal simulation time step = 10 ms. All event timings (TTT, T310, ReportInterval) are quantized to this grid. Measurement reports issued per scheduled interval (default 240 ms).

**Consequences.**
- (+) ~100× speedup over PHY-step.
- (−) TTT < 10 ms cannot be tested (none in 3GPP-supported values anyway).
- (−) Sub-frame timing details (HARQ retransmission) absent. Acceptable.

---

## ADR-6 — Per-event log + 1 s per-cell aggregate, both written to parquet
*2026-06-05 · accepted*

**Context.** ML pipeline needs both event-level data (for HO outcome classification) and time-series KPI data (for drift detection on rolling windows).

**Decision.** Two output streams:
1. `events.parquet` — one row per HO attempt / RLF / ping-pong, columns include source/target cell, config, outcome, pre/post KPI snapshot.
2. `kpis_1s.parquet` — one row per (cell, 1-second bin), aggregated KPIs (avg RSRP, RSRP percentiles, SINR percentiles, HO attempts count, RLF count, ping-pong count).

Both written via `parquet.write` from MATLAB Communications Toolbox / `parquetwrite`.

**Consequences.**
- (+) Anomaly detection can use either stream depending on model.
- (+) Drift detection consumes the 1s stream directly.
- (−) Doubles I/O. Acceptable.

---

## ADR-7 — ML stack: scikit-learn + River + PyTorch
*2026-06-05 · accepted*

**Context.** Anomaly (mostly sklearn), drift (River for ADWIN/KSWIN/PH/DDM, plus custom MMD), deep models (PyTorch for AE/LSTM-AE/Transformer-AE).

**Decision.** Pin minimum versions in `requirements.txt` (added Phase 1):
- scikit-learn ≥ 1.4
- river ≥ 0.21
- torch ≥ 2.2
- pandas ≥ 2.2, pyarrow ≥ 15
- lightgbm ≥ 4.3, scikit-optimize / GPy for GP
- matplotlib ≥ 3.8, seaborn for plotting

**Consequences.**
- (+) Industry-standard, large community, easy to cite.
- (−) Three frameworks to keep in sync. Mitigation: thin wrapper layer in `analysis/common/`.

---

## ADR-8 — Repo language is English (code + commits + docs in repo)
*2026-06-05 · accepted*

**Context.** Thesis is in English. Optional workshop paper requires English. Reviewers may be non-Polish-speakers.

**Decision.** All committed text in English. Personal conversation / planning (this file, plan.md) can include Turkish snippets in informal sections only. Code identifiers, commit messages, README, design docs: English only.

**Consequences.** Forces consistent terminology, eases later paper writing.

---

## ADR-9 — Versioning: git for code + DVC for data
*2026-06-05 · accepted*

**Context.** Simulated parquet files run from MB to multi-GB. Git LFS quota concerns. Real datasets are too large to commit and have licenses.

**Decision.** Code → git. Datasets → DVC with local or cloud remote (TBD; SSH to a lab server is simplest first step). `data/raw_public/` and `data/simulated/` contents are .gitignored except README and .gitkeep.

**Consequences.**
- (+) Reproducibility via DVC pipeline (later).
- (−) DVC dependency. Mitigation: provide direct download script as fallback.

---

## ADR-10 — Reproducibility unit: `make <phase>` + Docker
*2026-06-05 · accepted*

**Context.** Reviewers and future readers must be able to rerun.

**Decision.** Top-level Makefile with one target per phase (`make sim`, `make calibrate`, etc.) plus `make all`. Dockerfile for the Python analysis side (MATLAB cannot be containerized open-source; document host MATLAB version in README).

**Consequences.** Clean entry points, predictable outputs. MATLAB step requires host install — accepted limitation.

---

## ADR-11 — Communications Toolbox Wireless Network Simulator (CTWNS) not used
*2026-06-05 · accepted*

**Context.** MATLAB R2023a+ ships an add-on (`Communications Toolbox Wireless Network Simulator`) that provides node-level NR network simulation with built-in PHY + MAC abstraction and limited HO support. Not installed in our environment by default.

**Decision.** Do **not** adopt CTWNS for the primary simulator. Rationale:
1. CTWNS lives at PHY/MAC abstraction; our scope (ADR-5) is RRC mobility event-level (10 ms tick). Wrong abstraction layer.
2. Drift/anomaly injection requires direct access to channel/measurement/cell state, which is opaque in CTWNS objects.
3. CTWNS is new (R2023a+), documentation and community-resources are sparse, breaking-change risk per MATLAB release is high; incompatible with thesis schedule.
4. Custom implementation maps directly to 3GPP spec clauses, which improves defensibility (reviewer can verify implementation against TS 38.331 §5.5.4 line-by-line).

**Consequences.**
- (+) Full control over event log, drift injection, sweep parameterization.
- (+) Smaller dependency surface, no add-on installation required.
- (−) Reimplementation effort for A3 + RLF + measurement filtering. Mitigation: thin functions, 3GPP spec citations in code, unit tests.

**Future work / optional validation.** If schedule permits after Phase 9, install CTWNS and run a default HO scenario, compare HOSR/RLF rates against our simulator. Cross-validation strengthens defensibility but is not on the critical path.

---

## Pending ADRs (to add as phases progress)

- ADR-12 — Sweep grid resolution (resolved end of Phase 4)
- ADR-13 — Anomaly injection rate + severity grid (Phase 4)
- ADR-14 — Drift labeling protocol (Phase 4)
- ADR-15 — Online retrain batch size + warmup (Phase 7)
- ADR-16 — Surrogate uncertainty calibration method (Phase 8)
