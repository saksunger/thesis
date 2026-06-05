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

## ADR-12 — Calibration metric: KS-statistic + bootstrap CI, not p-value
*2026-06-05 · accepted (Phase 3 v0)*

**Context.** Two-sample Kolmogorov-Smirnov test (`scipy.stats.ks_2samp`) is the standard nonparametric goodness-of-fit metric for matching simulated to empirical KPI distributions. With `n_sim ≈ 144 k` and `n_real ≈ 43 k`, the asymptotic p-value of any non-zero KS-statistic underflows to 0 — making p-value a useless yardstick at this scale.

**Decision.**
1. Report **KS-statistic** as the primary calibration metric.
2. Report a **95 % bootstrap confidence interval** on the KS-statistic itself (200 paired resamples of sim and real arrays, each capped at 10 000 samples for cost control).
3. Report **Δp50 (sim median − real median)** as the physically-interpretable companion.
4. Do **not** report or interpret p-values at this sample size. They are computed only for completeness in the summary CSV.
5. Calibration claims are restricted to the **matched (RAN, band, operator) segment** — i.e. our 5G-NSA / 3.5 GHz simulator vs NordicDat op1/5G-NSA/LTE_B20. Cross-band / cross-RAN comparisons are reported for context only.

**Consequences.**
- (+) Defensible reporting at thesis review (Massey 1951 §3, Conover 1999 §6.3).
- (+) KS-statistic + bootstrap CI is reproducible and gives a clean acceptance threshold (e.g. KS < 0.20 for v0).
- (−) Cannot make "the distributions are statistically identical" claims. Mitigation: we never need that claim — the simulator is independent training data for ML; calibration only proves it samples a *compatible* distribution.

---

## ADR-13 — RSRQ residual mismatch deferred to Phase 4 traffic-load model
*2026-06-05 · accepted (Phase 3 v0)*

**Context.** In the 3 × 3 calibration sweep, RSRQ KS-statistic is constant at 0.467 across all (ISD, area) combinations — meaning the mismatch is insensitive to channel parameters. RSRP and SINR fits (KS ≤ 0.20) are tight against the same target segment. Inspection of the CDF shows the sim RSRQ is concentrated in `[−14, −10] dB` while real spans `[−20, −7] dB`. The Phase 3 simulator uses a deterministic load assumption (fixed PRB allocation, no neighbour-cell interference variability).

**Decision.** Accept the RSRQ residual mismatch as a known limitation of Phase 3. Document it openly in `docs/calibration_findings.md` §4 and the thesis chapter. Defer the fix to **Phase 4**, where we will add a stochastic traffic-load model so that neighbour-cell RSSI (and therefore the denominator of RSRQ) becomes stochastic.

**Consequences.**
- (+) Phase 3 ships on schedule with honest scope statement; reviewers can verify the gap is logical, not a bug.
- (+) Provides a concrete Phase 4 ML hook (load model is the natural place for traffic-shift drift scenario).
- (−) RSRQ-based detectors in Phase 5 will look optimistic on simulated data until Phase 4 lands. Mitigation: report Phase 5 detectors on Phase 4 timelines, not Phase 3 calibration data.

---

## ADR-14 — Scope: 5G intra-RAT inter-gNB Xn handover only
*2026-06-05 · accepted (supersedes implicit multi-RAT framing in Phase 0)*

**Context.** Through Phases 0–3 the simulator and documentation occasionally implied a multi-RAT scope (LTE comparisons, `ran_type` enum including `LTE`, Bangladesh dataset treated as a calibration source). The thesis statement, however, is bounded to **5G intra-RAT inter-gNB handover** (3GPP NR Standalone, Xn-based mobility per TS 38.300 §9.2.3 + TS 38.331 §5.5.4). Keeping the wider framing in the methodology section would inflate scope claims that the implementation does not actually back.

**Decision.** Lock the entire thesis pipeline to:

1. **Radio access:** 5G NR Standalone (`ran_type = "NR_SA"`), no NSA (LTE-anchored) signalling.
2. **HO type:** intra-RAT (NR ↔ NR only) and inter-gNB (each cell in the 7-hex layout is a distinct gNB; Xn-based HO per TS 38.300 §9.2.3.2). Intra-gNB intra-DU mobility, N2-based HO via 5GC, and any inter-RAT (NR ↔ LTE) handover are **out of scope**.
3. **3GPP normative references:** TS 38.331 v17.16.0 (RRC, A3, RLF), TS 38.133 v17.21.0 (RRM measurements), TR 38.901 v17.1.0 (channel). LTE specs (TS 36.331, TS 36.133) are demoted to **background only** — used solely to decode the Bangladesh dataset's encoded RSRP/RSRQ columns, and not as design references.
4. **Real-data calibration role:** NordicDat's `op1/5G-NSA/LTE_B20` segment is the closest publicly available 5G-flavored radio reference and is used for **radio-layer marginal distribution calibration only** (serving-cell RSRP/RSRQ/SINR shape). The HO control plane (A3 event timing, TTT, RLF state machine) is **not** calibrated against real HO event logs because no public 5G SA HO event dataset exists; it is implemented directly from the 3GPP spec and verified by unit tests (`simulator/tests/`).
5. **Bangladesh dataset:** **excluded from the calibration scope** of this track. It is LTE intra-RAT inter-eNB (the LTE analog of our scope), and is retained only as a *qualitative order-of-magnitude reference* for HO event rate sanity in the thesis chapter ("real LTE drive tests see ~310 HO attempts/hour" as context, not as a calibration target).

**Consequences.**
- (+) Clean, defensible scope statement for the thesis abstract and methodology chapter — every claim maps to an implemented artifact.
- (+) Removes the temptation to over-claim "calibrated against Bangladesh LTE" when the spec being implemented is NR.
- (+) Justifies why we do not pursue inter-RAT drift scenarios in Phase 4 (D-2's `UMa → UMi` swap stays in scope because both are 5G channel models per TR 38.901).
- (−) Loses the ability to cite Bangladesh's HOSR/HOFR figures as calibration metrics. Mitigation: those figures live in the thesis "Related Work / Real-network context" subsection only.
- (−) Reviewer may ask why we didn't use a 5G SA dataset. Mitigation: we explicitly survey the public dataset landscape in chapter 2 and document the absence of public 5G SA HO event logs.

**Code-level enforcement.**
- Simulator metadata default `ran_type = "NR_SA"` (writers + calibration runner). 
- Schema enum `ran_type ∈ {LTE, NR_SA, NR_NSA}`: sim emits only `NR_SA`; the `LTE` and `NR_NSA` values exist only to label real-data rows when they appear in the calibration pipeline for diagnostic comparison.
- Phase 4 drift catalog (`docs/scenarios.md`) reviewed — no inter-RAT scenario; D-2 (UMa ↔ UMi) is intra-NR.

---

## ADR-15 — KPI scope: mobility KPIs only (TS 28.554 §6.3.1–2)
*2026-06-05 · accepted (orthogonal to ADR-14; tightens the KPI axis that ADR-14 left implicit)*

**Context.** ADR-14 locked the *HO-type / RAT* axis (NR SA, intra-RAT, inter-gNB Xn) but did not address *which KPI family* the thesis monitors over those handovers. The earlier conference paper (`paper.tex`) listed downlink throughput alongside RSRP/SINR as a monitored KPI, implying a service-quality (3GPP TS 28.554 §6.3.6) monitoring scope. The current simulator implements PHY + RRC (L1 + L3) faithfully but **does not implement L2 MAC scheduling, MCS feedback, BLER curves, or any offered-load model** — so throughput, packet drop rate, and latency cannot be reported in a 3GPP-faithful way without weeks of additional scope. An examiner could legitimately ask "you have NR Xn HO modelled, why no throughput?" so the scope decision needs a written gerekçe rather than mental note.

**Decision.** Lock the thesis monitoring scope to **mobility KPIs as defined in 3GPP TS 28.554 §6.3.1–6.3.2**:

1. **In scope** (computed from `events.parquet`): `HOSR` (HO success rate), `HOFR` (HO failure rate), `RLF` rate, `ping-pong` rate; plus the underlying radio measurements `RSRP_serving`, `RSRQ_serving`, `SINR_serving`, `RSRP_neighbor` (computed in `samples.parquet`) which the detectors aggregate into window features.
2. **Out of scope** (TS 28.554 §6.3.6 service quality): downlink/uplink throughput, packet drop rate, end-to-end latency, jitter. The simulator does not produce these and the thesis does not claim them.
3. **Implication for Phase 5+ feature design**: anomaly/drift detectors consume only mobility-layer features; the surrogate model in Phase 8 targets only mobility-layer KPIs.

**Consequences.**
- (+) Clean alignment between simulator capabilities and claimed KPIs — no over-claim risk.
- (+) Defensible against "why not throughput?": *throughput requires an L2 MAC stack we explicitly do not model; mobility KPIs per TS 28.554 §6.3.1–2 are the canonical mobility-management metrics and are sufficient for the drift-aware-monitoring research question.*
- (+) Throughput is monotone-correlated with `SINR_serving` (Shannon bound), so no detector information is lost — any throughput-driven anomaly is already visible in SINR.
- (−) The earlier paper (`paper.tex`) mentions "lower downlink throughput" in the preliminary findings sentence. This sentence will be rewritten in the thesis Data Strategy chapter as "higher HO failure rate and poorer average signal conditions". Paper itself stays as-is (conference snapshot).
- (−) Loses the ability to evaluate user-experience metrics directly. Mitigation: the thesis explicitly frames the contribution as *mobility-layer* monitoring; user-experience evaluation is named as future work.

**Code-level enforcement.**
- Phase 5 `analysis/anomaly/features.py` window aggregator MUST consume only the columns enumerated above.
- Phase 8 surrogate target enum is `{HOSR, HOFR_rate, RLF_rate, ping_pong_rate}`; no throughput target.
- Sample-level schema (`docs/schema.md` §1) stays mobility-focused; no `throughput_*` columns are added.

---

## Pending ADRs (to add as phases progress)

- ADR-16 — Sweep grid resolution (resolved end of Phase 4 Iter C)
- ADR-17 — Anomaly injection rate + severity grid (Phase 5)
- ADR-18 — Drift labeling protocol — *partially captured in `docs/scenarios.md`, formalise after Phase 6*
- ADR-19 — Online retrain batch size + warmup (Phase 7)
- ADR-20 — Surrogate uncertainty calibration method (Phase 8)
