# Public Raw Datasets

These datasets are **not** committed (large + license terms). They live outside the repo and are referenced by symlink or absolute path.

## Bangladesh (LTE drive-test)

- **Local path:** `/home/sensoy/ozgun/projects/ho_optimization/raw_public/bangladesh/`
- **Subfolders:** `DRM Files/`, `Event Statistics/`, `Measurement Reports/`, `Parent Dataset/`, `Processed Dataset/`
- **Structure:** 3 datasets × 7 files each
- **Tech:** LTE (Intra LTE-HO events)
- **Use in this track:** sanity check only — HO success/fail rate vs simulator, RSRP/CINR distribution shape
- **Schema:** see `docs/schema.md`
- **Source citation:** TBD — locate the originator paper / DOI before submission and add here.

## NordicDat (LTE + 5G-NSA measurements, Nordic operators)

- **Local path:** `/home/sensoy/ozgun/projects/ho_optimization/raw_public/nordicdat/`
- **File:** `nordicdat_readable.csv` (~14 MB, 91 455 rows, 23 columns)
- **Time span:** ~65 days (1558 h)
- **Tech mix:** 5G-NSA (46 911 rows) + LTE (44 544 rows)
- **Cells:** 216 unique `serving_cell_id`s; 4 operators
- **Use in this track:** sanity check — RSRP/RSRQ/SINR distribution KS-test vs simulator
- **Schema:** see `docs/schema.md`
- **Source citation:** TBD — locate the originator (likely an NTNU / UiT / SINTEF release) and add here.

## Linking these datasets

After cloning the repo:

```bash
ln -s /home/sensoy/ozgun/projects/ho_optimization/raw_public/bangladesh data/raw_public/bangladesh
ln -s /home/sensoy/ozgun/projects/ho_optimization/raw_public/nordicdat  data/raw_public/nordicdat
```

(Later: replace with DVC `dvc import` once a remote is configured.)
