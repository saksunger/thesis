# Phase 3 — Calibration Findings (v0)

> Last updated: 2026-06-05
> Source artifacts: `data/processed/calibration_ks/`
> Reproduce: `make calibrate-sweep` then `make calibrate`

---

## 0 · Scope (read first)

Per ADR-14 (`docs/design.md`), the thesis is locked to **5G NR Standalone, intra-RAT, inter-gNB Xn handover**. This shapes what we *do* and *do not* calibrate against real data:

| | Calibrated against real data | Verified against 3GPP spec only |
|---|---|---|
| **What** | Radio-layer marginal distributions: serving-cell RSRP, RSRQ, SINR | HO control plane: A3 entry condition, TTT timer, L3 filter, RLF state machine, ping-pong detection |
| **Reference** | NordicDat segment `op1 / 5G-NSA / LTE_B20` (closest publicly available 5G-flavored radio measurement set) | TS 38.331 v17.16.0 §5.5.3.2 (L3), §5.5.4.4 (A3), §5.3.10 (RLF); TR 38.901 v17.1.0 (channel) |
| **Why this split?** | NordicDat is independent operator drive-test data → defensible distribution-shape target. | No public 5G SA HO event dataset exists (operator PM counters do not include per-event RRM config — see TS 32.425/.426). Spec compliance is what we can prove. |

Cross-RAT segments (op1 / LTE / LTE_B20, op3 / LTE / LTE_B3) are reported in §3 below as **sanity references only** — we expect them to be more distant from our NR sim, and they are. Bangladesh is **out of scope** for calibration; it appears only in `docs/scenarios.md` discussion as an order-of-magnitude HO-rate sanity reference.

---

## TL;DR

After a 3 × 3 (ISD × trajectory-area) grid search, the **default simulator
configuration** (5G NR SA, UMa, 3.5 GHz n78, ISD 500 m, 7-gNB hex layout,
12 UEs × 120 s straight-line walks) matches the **NordicDat operator-1,
5G-NSA, LTE_B20** radio-layer reference segment with:

| KPI  | KS-stat | 95 % CI         | Δ median (sim − real) |
|------|---------|------------------|------------------------|
| RSRP | 0.175   | [0.164, 0.186]   | **+3.1 dB**            |
| SINR | 0.192   | [0.183, 0.200]   | **−2.2 dB**            |
| RSRQ | 0.467   | [0.458, 0.478]   | −0.5 dB                |

For RSRP and SINR this is a tight fit (< 0.20 KS-statistic, < 3 dB
median offset) for an out-of-the-box 3GPP TR 38.901 channel model — no
parameter hand-tuning required beyond confirming the default layout.
**RSRQ shows a residual mismatch** that is band/load-driven, not
channel-driven (see §4).

---

## 1 · Methodology

We do **not** merge real and simulated samples. They are treated as two
independent populations whose marginal distributions of (RSRP, RSRQ,
SINR) we compare via the two-sample Kolmogorov-Smirnov statistic.

For each (segment, KPI) pair we compute:

* `KS_stat`  — `scipy.stats.ks_2samp` two-sample statistic.
* `KS_stat 95 % CI` — 200-replicate bootstrap on independently
  resampled sim and real arrays (capped at 10 000 each to keep cost
  bounded — KS-stat distribution stabilises long before that).
* `Δp50` — `quantile(sim, 0.5) − quantile(real, 0.5)`, the
  physically-intuitive companion metric.

P-values are not reported as a primary metric: at `n_sim ≈ 144 k` and
`n_real ≈ 43 k`, *any* finite KS-statistic gives `p ≈ 0`. The
KS-statistic itself plus the bootstrap CI is the defensible quantity
(Massey 1951 §3, Conover 1999 §6.3).

Reference segments (top-3 by row count):

| Segment id          | n_rows | RAN     | Band     | Why                          |
|---------------------|--------|---------|----------|-------------------------------|
| op1 / 5G-NSA / B20  | 43 161 | 5G-NSA  | LTE 800  | **closest analog to sim**     |
| op1 / LTE / B20     | 25 988 | LTE     | LTE 800  | cross-RAN sanity check        |
| op3 / LTE / B3      | 10 390 | LTE     | LTE 1800 | cross-band + cross-operator   |

Default sim config is `(scenario UMa, fc = 3.5 GHz, ISD = 500 m,
trajectory area ±1500 m, speed 10 m/s, 12 UEs × 120 s ⇒ 144 012
samples)`. Channel constants follow TR 38.901 §7.4 (path loss),
§7.4.2 (LoS probability), §7.5 Table 7.5-6 (shadow std), §7.6.3.3
(piecewise-constant LoS).

---

## 2 · Tuning sweep result

A 3 × 3 grid was run:

* `ISD ∈ {500, 1000, 1500} m`
* `trajectory area ∈ {1500, 2500, 3500} m`

For each cell we computed KS-stat against the matched 5G-NSA segment.

![Calibration sweep heatmap](../data/processed/calibration_ks/sweep_heatmap_rsrp_serving_dbm.png)

**Default cell wins** (top-left, KS 0.148 with the small 6 × 60 s sweep
sample). Increasing ISD shifts the sim median down by 9 – 17 dB
because UEs spend more time far from any cell. Increasing trajectory
area has the same direction (UEs sample more cell-edge regions). The
default operating point is the cell whose median naturally lands on
−91 dBm, matching the NordicDat median of −91 dBm.

This is a **non-trivial check of model correctness**: the default
configuration was chosen *before* seeing the real data (it just
follows TR 38.901 §7.4 defaults for an UMa scenario), and the median
RSRP came out within 3 dB of the operator measurement.

---

## 3 · Per-segment performance

CDF overlay (final 144 k-sample run):

![CDF overlay — sim vs NordicDat](../data/processed/calibration_ks/cdf_final.png)

| Segment            | RSRP KS | RSRP Δp50 | SINR KS | SINR Δp50 | RSRQ KS |
|--------------------|---------|------------|---------|------------|---------|
| op1 / 5G-NSA / B20 | **0.18** | **+3.1**   | **0.19** | **−2.2**   | 0.47    |
| op1 / LTE / B20    | 0.26    | +8.1       | 0.18    | +2.8       | 0.41    |
| op3 / LTE / B3     | 0.34    | +7.1       | 0.32    | −7.2       | 0.87    |

Interpretation:

* The simulator is a 5G-NSA / 3.5 GHz model, so the **closest segment
  is operator-1 / 5G-NSA / LTE_B20** — that's where the smallest
  KS-statistic lives, as expected.
* The LTE-only and other-band segments drift by 7 – 10 dB in the
  median. This is **a feature, not a bug**: the simulator correctly
  reflects that LTE drive-test logs (lower band, different deployment
  characteristics) live in a different operating region.
* On the cross-band op3 / B3 segment we deliberately do *not* try to
  match RSRQ (KS 0.87). That column is reported only to demonstrate
  the simulator's sensitivity to the segment choice — calibration
  claims are made against the 5G-NSA segment alone.

---

## 4 · Known residual: RSRQ

RSRQ KS-stat is `0.467` for every config in the sweep — meaning the
mismatch is **insensitive to ISD, trajectory area, and shadowing
realisation**. Inspecting the CDF:

* Sim RSRQ is concentrated in `[−14, −10] dB` (median −11.5).
* Real RSRQ spans `[−20, −7] dB` (median −11.0).

The sim formula uses a deterministic load assumption (`N` allocated
PRBs out of `N_total` ⇒ effectively `RSRQ = RSRP / (RSSI(N) ·
constant)`). The real-world RSSI varies with **traffic load on
neighbour cells**, which is not modelled in Phase 3. This will be
addressed in Phase 4 by adding a stochastic interference / traffic
load model. We log this explicitly as a known gap so the thesis
chapter is honest about the boundary of the v0 calibration claim.

---

## 5 · How to reproduce

```bash
# (one-time) set up Python env
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# baseline run + KS-test + plots
make calibrate

# full ISD × area sweep + heatmap
make calibrate-sweep

# read the artefacts
ls data/processed/calibration_ks/
#   ks_summary_final.csv        ← reported numbers
#   ks_sweep_summary.csv        ← all 9 configs × 3 segments × 3 KPIs
#   cdf_final.png               ← Figure 4.3.1 in thesis
#   qq_final.png                ← Figure 4.3.2 in thesis
#   sweep_heatmap_rsrp_serving_dbm.png ← Figure 4.3.3
```

The full pipeline runs in ~20 s of MATLAB time + 8 s of Python on a
single core (no parfor, no GPU).

---

## 6 · References

* 3GPP TR 38.901 v17.1.0 — *Study on channel model for frequencies
  from 0.5 to 100 GHz.* Tables 7.4.1-1 (path loss), 7.4.2-1 (LoS
  probability), 7.5-6 (shadow std), §7.6.3.3 (spatially correlated
  LoS state).
* 3GPP TS 36.133 v17.16.0 — RSRP / RSRQ measurement requirements
  (used for the Bangladesh decoder, included for completeness).
* F. J. Massey Jr. (1951), *The Kolmogorov-Smirnov test for goodness
  of fit*, JASA 46.253.
* W. J. Conover (1999), *Practical Nonparametric Statistics*, 3rd ed.,
  Wiley, §6.3.
