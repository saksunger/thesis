# Public Raw Datasets

Two real LTE / LTE+5G-NSA drive-test datasets used **for calibration and sanity check only** (not for ML training, per the simulator-based track decision in `docs/design.md` ADR-1).

> The dataset files themselves are **gitignored** (large + license uncertainty) but are kept locally inside this folder so that all analysis pipelines work out-of-the-box without extra setup.

## Bangladesh (LTE drive-test)

- **Local path:** `data/raw_public/bangladesh/` (~535 MB on disk, gitignored)
- **Subfolders present:** `DRM Files/`, `Event Statistics/`, `Measurement Reports/`, `Parent Dataset/`, `Processed Dataset/`
- **Structure:** 3 dataset variants × multiple `.csv` per variant (see `Processed Dataset/Dataset_{1,2,3}_ver_{1,2}.csv`)
- **Tech:** LTE; `Event Statistics/` contains explicit `Event=Intra LTE-HO` rows with `Result ∈ {Attempt, Success, Fail}` — the only real-world HO outcome ground truth available to us
- **Use in this track:** sanity check only — HO success/fail rate vs simulator, RSRP/CINR distribution shape
- **Schema mapping:** see `docs/schema.md` §1–2 (note RSRP unit encoding caveat, §4.1)
- **Source citation:** TBD — locate originator paper / DOI before submission and add here.

## NordicDat (LTE + 5G-NSA measurements, Nordic operators)

- **Local path:** `data/raw_public/nordicdat/nordicdat_readable.csv` (~14 MB, gitignored)
- **Rows × cols:** 91 455 × 23
- **Time span:** ~65 days (1558 h) — usable as a **real-world drift baseline** in Phase 6
- **Tech mix:** 5G-NSA (46 911 rows) + LTE (44 544 rows)
- **Cells:** 216 unique `serving_cell_id`s; 4 operators
- **Use in this track:** sanity check — RSRP / RSRQ / SINR distribution KS-test vs simulator (per band)
- **Schema mapping:** see `docs/schema.md` §1
- **Source citation:** TBD — locate originator (likely NTNU / UiT / SINTEF release) and add here.

## How to populate this folder on a fresh clone

The CI / future-you needs to manually drop the datasets back in:

```bash
# adjust source paths to wherever the datasets live on the new machine
cp -r /path/to/bangladesh data/raw_public/bangladesh
cp -r /path/to/nordicdat  data/raw_public/nordicdat
```

(DVC `dvc import` is deferred until / unless we publish the datasets externally — see `docs/design.md` ADR-9.)

## Why these and not others?

Decided in `docs/design.md` ADR-1 / ADR-2:
- **Bangladesh** is the only public dataset with explicit per-event HO outcomes (Attempt / Success / Fail).
- **NordicDat** is the only public dataset with multi-month time span on the same cells (drift baseline).
- Neither contains the HO **control parameters** (TTT, hysteresis, A3-offset) — these can only come from the simulator. This is the structural reason this thesis cannot be "real-data only."
