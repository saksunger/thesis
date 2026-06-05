# Public Raw Datasets

> **Scope reminder (ADR-14):** the thesis is locked to **5G NR SA, intra-RAT, inter-gNB Xn HO**. The two datasets here are real-world LTE / LTE+5G-NSA drive-test logs. Only one is in scope for calibration; the other is kept for context only. See `docs/calibration_findings.md` §0 for the full calibration / non-calibration split.

> The dataset files themselves are **gitignored** (large + license uncertainty) but are kept locally inside this folder so that all analysis pipelines work out-of-the-box without extra setup.

## NordicDat (LTE + 5G-NSA measurements, Nordic operators) — **in scope**

- **Local path:** `data/raw_public/nordicdat/nordicdat_readable.csv` (~14 MB, gitignored)
- **Rows × cols:** 91 455 × 23
- **Time span:** ~65 days (1558 h, but bursty — see `docs/schema.md` §4.3) — usable as a **real-world drift baseline** in Phase 6
- **Tech mix:** 5G-NSA (46 911 rows) + LTE (44 544 rows). The 5G-NSA carrier reuses NR PHY, which makes its RSRP/RSRQ/SINR distributions a reasonable proxy for the 5G SA radio layer we simulate.
- **Cells:** 216 unique `serving_cell_id`s; 4 operators
- **Use in this track (ADR-14):** primary calibration target. Segment `op1 / 5G-NSA / LTE_B20` is the matched reference for KS-tests on serving-cell RSRP / RSRQ / SINR. The LTE segments are reported as cross-RAN sanity references only (not as calibration targets).
- **Schema mapping:** see `docs/schema.md` §1
- **Source citation:** TBD — locate originator (likely NTNU / UiT / SINTEF release) and add here.

## Bangladesh (LTE drive-test) — **out of scope** for calibration

- **Local path:** `data/raw_public/bangladesh/` (~535 MB on disk, gitignored)
- **Subfolders present:** `DRM Files/`, `Event Statistics/`, `Measurement Reports/`, `Parent Dataset/`, `Processed Dataset/`
- **Structure:** 3 dataset variants × multiple `.csv` per variant (see `Processed Dataset/Dataset_{1,2,3}_ver_{1,2}.csv`)
- **Tech:** LTE only; `Event Statistics/` contains explicit `Event=Intra LTE-HO` rows with `Result ∈ {Attempt, Success, Fail}` — the only public dataset we know of with per-event HO outcome ground truth, but it is **LTE intra-eNB**, the LTE analog of (not equal to) our NR scope.
- **Use in this track (ADR-14):** **not used for calibration.** Kept only as an order-of-magnitude reference (e.g., "real LTE drive tests see ~310 HO attempts/hour") in chapter 2 ("Related Work / Real-network context"). The loader at `analysis/calibration/load_bangladesh.py` is preserved for that context paragraph and for thesis-text figures; it does **not** feed `make calibrate`.
- **Schema mapping:** see `docs/schema.md` §1–2 (note RSRP unit encoding caveat, §4.1)
- **Source citation:** TBD — locate originator paper / DOI before submission and add here.

## How to populate this folder on a fresh clone

The CI / future-you needs to manually drop the datasets back in:

```bash
# adjust source paths to wherever the datasets live on the new machine
cp -r /path/to/bangladesh data/raw_public/bangladesh
cp -r /path/to/nordicdat  data/raw_public/nordicdat
```

(DVC `dvc import` is deferred until / unless we publish the datasets externally — see `docs/design.md` ADR-9.)

## Why these and not others?

Decided in `docs/design.md` ADR-1 / ADR-2 / ADR-14:
- **NordicDat** is the only public dataset we found that includes a 5G-flavored RAN segment (5G-NSA on a Norwegian operator) with usable RSRP/RSRQ/SINR distributions. That makes it the best available proxy for our 5G NR SA radio layer.
- **Bangladesh** is the only public dataset with explicit per-event HO outcomes (Attempt / Success / Fail), but it is LTE-only ⇒ out of calibration scope per ADR-14. It is retained as historical context.
- **Neither contains 5G SA HO event logs with the RRM control parameters** (TTT, hysteresis, A3-offset, T310, N310, N311). Operator PM counter specs (3GPP TS 32.425 / 32.426) confirm these are not exposed. This is the structural reason the thesis cannot be "real-data-only" and must use a simulator-centric methodology.
