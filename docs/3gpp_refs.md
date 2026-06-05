# 3GPP Specification References

Every spec clause cited in code or thesis must be listed here. Format: spec — clause — what we use — local PDF path.

> **Scope reminder (ADR-14):** the thesis is bounded to **5G NR Standalone, intra-RAT, inter-gNB Xn handover**. The TS 38-series and TR 38-series specs below are **normative** for the simulator and ML pipeline. The TS 36-series (LTE) specs are kept for **background only** — they are required to decode the Bangladesh dataset's encoded RSRP/RSRQ columns, but do **not** drive any design decision and are not cited as authority in the methodology chapter.

> **PDFs downloaded** to `data/external/3gpp/` from ETSI public mirror (<https://www.etsi.org/standards-search>). See that folder's README for download commands and file inventory. All pinned to **Release 17, latest maintenance** (Jun 2026).

---

## NR (5G) — TS 38 series (normative)

### TS 38.300 v17.10.0 — NR; Overall description; Stage 2
- **§9.2.3 Mobility in connected mode** — taxonomy of NR HO types. Defines **Xn-based HO** (§9.2.3.2 — used by our simulator, no AMF involvement; source gNB sends HO Request over Xn directly to target gNB) vs **N2-based HO** (§9.2.3.3 — explicitly out of scope per ADR-14). Defines inter-gNB vs intra-gNB cases.
- Cite when explaining why each cell in the 7-hex layout is treated as a distinct gNB ⇒ every HO event is inter-gNB Xn by construction.
- Local PDF: `data/external/3gpp/ts_138300v171000p.pdf` *(to download)*

### TS 38.331 v17.16.0 — NR RRC Protocol Specification
- **§5.3.10 Radio link failure related actions** — RLF detection (T310, N310, N311 counters).
- **§5.5.3.2 Layer 3 filtering** — L3 filter coefficient applied to RSRP/RSRQ samples before event evaluation.
- **§5.5.4.1 Event A1–A6 conditions** — measurement reporting event definitions.
- **§5.5.4.4 Event A3 (Neighbour becomes amount of offset better than SpCell)** — primary trigger logic in our simulator. Entry condition `Mn + Ofn + Ocn − Hys > Ms + Ofs + Ocs + Off` for TimeToTrigger duration.
- **§6.3.2 MeasurementInformationElements** — `MeasConfig`, `ReportConfig`, `Hysteresis`, `TimeToTrigger`, `A3-Offset` definitions (these are the config parameters we sweep).
- Local PDF: `data/external/3gpp/ts_138331v171600p.pdf`

### TS 38.133 v17.21.0 — NR Requirements for support of RRM
- **§9.2.4 RSRP measurement period requirements** — measurement timing and accuracy bounds.
- **§9.3 Cell identification requirements** — relevant for cell detection latency model.
- Local PDF: `data/external/3gpp/ts_138133v172100p.pdf`

### TS 38.214 — NR Physical layer procedures for data
- Only consulted if SINR model needs MCS/CQI cross-check. Likely out of scope.

### TS 32.425 — Performance measurements (LTE) and TS 32.426 (NR equivalent)
- **Used as evidence** that operator PM counters do not include per-event RRM config (TTT/hysteresis) → justifies why real datasets cannot fill those fields.
- Cite explicitly in thesis chapter 3 (Methodology) when explaining the structural gap.

---

## NR (5G) — TR (technical report) series

### TR 38.901 v17.1.0 — Study on channel model for frequencies from 0.5 to 100 GHz
- **§7.4.1 Path loss models** — Table 7.4.1-1 (LoS/NLoS formulas for UMa, UMi, RMa, InH).
- **Table 7.4.1-1, note in caption** — Shadow fading std deviation per scenario.
- **§7.5 Fast fading model parameters** — used for parameter consistency only; we do not implement fast-fading taps.
- **Table 7.5-6** — Correlation distance for large-scale parameters; controls spatial correlation of shadowing grid.
- **§7.6.3 Mobility-related modeling** — Gauss-Markov spirit for our `+mobility.gauss_markov`.
- Local PDF: `data/external/3gpp/tr_138901v170100p.pdf`

### TR 38.913 — Study on scenarios and requirements for next generation access technologies
- ISD recommendations per deployment scenario (UMa 500 m default).

---

## LTE (4G) — TS 36 series (BACKGROUND ONLY, not normative for thesis)

> Per ADR-14 these specs are out of the design scope. They are kept solely to interpret the Bangladesh public dataset, which is excluded from the calibration target list. Do not cite as authority in the methodology chapter.

### TS 36.331 v17.16.0 — E-UTRA RRC Protocol Specification
- **§5.5.4.4 Event A3** — LTE counterpart of NR A3 (identical structure). Reference only when explaining that the Bangladesh "Intra LTE-HO" events are *analogous* to our NR scope (chapter 2 "Related Work / Real-network context"). Do not cite for design.
- Local PDF: `data/external/3gpp/ts_136331v171600p.pdf`

### TS 36.133 v17.16.0 — E-UTRA Requirements for support of RRM
- **§9.1.4 RSRP measurement reporting range** — encoding `0..97` ↔ `-140..-44 dBm` step 1 dB. **Used by `load_bangladesh._decode_rsrp_ts36133` to decode the Bangladesh dataset's encoded RSRP column.** Background only — no design dependence.
- Local PDF: `data/external/3gpp/ts_136133v171600p.pdf`

---

## How we cite in thesis

- Inline: `(3GPP TS 38.331 v17.16.0, §5.5.4.4)`
- Bib entry style: standard IEEE / Elsevier — use the pinned Release/version (Rel-17, latest maintenance Jun 2026).
- When the spec text was rephrased rather than quoted, still cite. Reviewers will check.

---

## Phase 0 close-out checklist

- [x] Download TS 38.331, TS 38.133, TR 38.901, TS 36.331, TS 36.133 PDFs into `data/external/3gpp/`
- [x] Pin exact version per spec (see table above and folder README)
- [ ] Cross-reference each citation in code via comment header: `% Per 3GPP TS 38.331 v17.16.0 §5.5.4.4 Event A3` (done as code is written)
