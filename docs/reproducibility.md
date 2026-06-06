# Reproducibility — How to re-derive every result in this thesis

This document is the binding reference for reproducing the simulator-driven
results (Chapters 5–9 + 11). It pins the exact software versions used to
produce the committed artifacts, and describes three reviewer profiles with
copy-paste recipes. The reproducibility package targets **Tier 1** of the ACM
artifact review classification (results, code, data, and environment fully
reproducible by an independent reviewer).

> **Status (2026-06-06).** Sections 1 (pinned stack), 2 (reviewer recipes
> for `make all-*` + `make verify-cache`), and the Docker recipe in Profile B
> are all actionable today. The Zenodo deposit tooling
> (`scripts/build_zenodo_bundle.py`, `scripts/fetch_zenodo_bundle.py`,
> `make zenodo-bundle`, `docs/zenodo_upload.md`) is also in place: the
> bundle builds reproducibly (980 MB compressed / 1.25 GB unpacked /
> 308 files / SHA256 round-trip verified). The only remaining blocker is
> minting the real DOI on Zenodo at thesis submission — wherever this doc
> says `10.5281/zenodo.XXXXXXX`, that placeholder gets replaced once the
> upload is published.

---

## 1. Pinned software stack

### 1.1 MATLAB (simulator side, Phases 1–4)

| Component | Pinned version | License feature | Notes |
|---|---|---|---|
| MATLAB | **R2023b (23.2.0.2365128)** GA release | — | GLNXA64; macOS / Windows builds of the same release are accepted but untested. |
| Communications Toolbox | v23.2 | `communication_toolbox` | Required (channel utilities, AWGN). |
| 5G Toolbox | v23.2 | `MATLAB_5G_Toolbox` | Required (`nrCarrierConfig` symbol used as functional probe). |
| Statistics and Machine Learning Toolbox | v23.2 | `statistics_toolbox` | Required (`fitcsvm` smoke). |
| Parallel Computing Toolbox | v23.2 | `distrib_computing_toolbox` | Required (`parfor` in `sweep_static`). |
| Deep Learning Toolbox | v23.2 | (none required) | Optional — only used if Phase 5 Iter B-2 (LSTM-AE) is re-run. Iter B-2 deferred in current results. |
| Antenna / Phased Array Toolbox | v23.2 | (none required) | Optional, not used by simulator code paths. |

Snapshot captured 2026-06-06 by `make matlab-check`. Re-run any time to verify
your local environment matches:

```bash
make matlab-check
# expected tail: "ALL REQUIRED TOOLBOXES PRESENT AND LICENSED."
```

**Why R2023b and not "R2023b or newer"?** All simulator results were produced
on this release. Later MATLAB releases (R2024a/b, R2025a) introduce breaking
changes in `nrCarrierConfig`, `comm.AWGNChannel`, and `parquetwrite` that
have not been regression-tested. If you must use a newer release, run
`make test` first — all 88 MATLAB unit tests must pass.

### 1.2 Python (analysis side, Phases 3 + 5–11)

| Component | Pinned version | Notes |
|---|---|---|
| Python | **3.12.3** | CI + Docker image. 3.11 source-installs too but results not validated. |
| Top-level deps | see `requirements.in` | 14 direct deps |
| Full lockfile | `requirements.txt` | **126 packages × SHA256 hash**, regenerated via `make pip-compile` |

Install reproducibly:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements.txt
```

The `--require-hashes` flag aborts the install if any wheel's SHA256 differs
from the lockfile. PyPI mirror outages do not affect reproducibility because
wheels are content-addressed.

### 1.3 Third-party data

| Dataset | Pinned source | Size | Required for |
|---|---|---|---|
| NordicDat (5G-NSA op1, LTE_B20) | Original upload at IEEE DataPort, see `data/raw_public/README.md` | 14 MB | Phase 3 calibration, Phase 11C face validity |
| Bangladesh LTE | IEEE DataPort original | 535 MB | Phase 3 EDA only (informational), not on any acceptance path |

Both are redistributed under the original authors' licenses; the
reproducibility Zenodo deposit (Section 3) does **not** rebundle them.
Reviewers download them once into `data/raw_public/` per the instructions
in that subdirectory's README.

---

## 2. Three reviewer profiles

Pick the profile that matches what you have available.

### Profile A — No MATLAB license (most common)

You can reproduce every Chapter 5–9 + 11 figure from cached simulator output
without running MATLAB at all. Simulator output is content-addressed parquet
distributed via Zenodo (Section 3).

```bash
git clone https://github.com/<owner>/thesis.git && cd thesis
python3.12 -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements.txt

# Pull the cached simulator artifacts (~1.2 GB) from Zenodo:
python -m scripts.fetch_zenodo_bundle --doi 10.5281/zenodo.XXXXXXX
# (until the DOI is minted, use --local dist/thesis_artifacts_<ver>.tar.gz
#  built upstream via `make zenodo-bundle`; see docs/zenodo_upload.md)

# (optional) Pull the NordicDat segment for Phase 11C:
#   follow data/raw_public/README.md

make all-from-cache                          # Phase 10 A3 — Python-only chain
# → 30 min wall clock, regenerates every Python-side figure & CSV
```

You cannot regenerate the MATLAB simulator outputs themselves. You **can**:
- re-run the Python analysis on the cached parquet (Chapters 5–9, 11B, 11C),
- re-run the seed-replication aggregator on the cached per-seed CSV
  (Chapter 11A — analysis verification, not full simulator re-run),
- re-derive every figure and table in those chapters.

### Profile B — Docker, host-tooling-agnostic

If you do not want to install anything locally:

```bash
# Build once (~5 min, ~1.5 GB image):
docker build -t thesis-repro -f docker/Dockerfile .

# Run the analysis end-to-end on the host's data/ directory:
docker run --rm -it \
  -v "$(pwd)/data:/app/data" \
  thesis-repro \
  bash -c "python -m scripts.fetch_zenodo_bundle --doi 10.5281/zenodo.XXXXXXX && make all-from-cache"
```

The container ships Python 3.12 + the full hash-locked lockfile. It does
**not** ship MATLAB (licensing makes that infeasible).

### Profile C — Full from-scratch regeneration (requires MATLAB)

Reviewers with a MATLAB R2023b license + the four required toolboxes can
regenerate every artifact from source:

```bash
git clone https://github.com/<owner>/thesis.git && cd thesis
python3.12 -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements.txt

# Download NordicDat per data/raw_public/README.md.

make matlab-check                            # verify licenses + functional probe
make test                                    # 88/88 MATLAB unit tests
make pytest                                  # 356/356 Python unit tests

make all-full                                # Phase 1 → 11, ~3–4 h wall clock
```

`make all-full` chains: `calibrate` → `sim TIMELINE=timeline_medium` →
`sweep-static` → `anomaly-benchmark` → `drift-benchmark` →
`adaptive-benchmark` → `surrogate-benchmark` → `end2end-demo` →
`gen-timeline-dense-urban` + the same pipeline on `dense_urban` →
`seed-replication-all` → `cross-scenario-compare` → `nordicdat-face-validity`.

---

## 3. Zenodo deposit (cached artifacts + DOI)

A Zenodo deposit accompanies this thesis to provide:
- citable DOI for all simulator-generated parquet + processed CSV + figures,
- byte-identical artifacts so Profile A reviewers do not need MATLAB,
- frozen snapshot of the repository at the defense commit.

**Deposit URL:** `https://doi.org/10.5281/zenodo.XXXXXXX` *(placeholder —
real DOI reserved + activated at thesis submission per the workflow in
[`docs/zenodo_upload.md`](zenodo_upload.md). Once the DOI is live, the
placeholder is replaced repo-wide via `grep -rn XXXXXXX`.)*.

**Bundle build & verify (maintainer + reviewer)**:

```bash
# Maintainer side — pack the tarball from the committed manifest:
make zenodo-bundle
# → dist/thesis_artifacts_v1.0.0.tar.gz (~0.98 GB, 308 files)

# Reviewer side — fetch + verify the published deposit:
python -m scripts.fetch_zenodo_bundle --doi 10.5281/zenodo.XXXXXXX
make verify-cache                              # 308/308 OK
```

The build script (`scripts/build_zenodo_bundle.py`) is reproducible: file
order, mtimes, and ownership are normalised so two builds of the same
`data/manifest.sha256` produce byte-identical tarballs (covered by
`scripts/tests/test_build_zenodo_bundle.py::TestBuild::test_is_reproducible`).
A `BUNDLE_INFO.txt` provenance header at the top of the tarball records
the source git commit, build time (UTC), file count, total bytes, and the
embedded manifest's SHA256.

**Bundle contents** (~1.19 GB unpacked, ~0.98 GB compressed):

| Path inside bundle | Size | Source phase | Why included |
|---|---|---|---|
| `simulated/timeline_medium/` | 131 MB | Phase 4c | Primary 30-phase production timeline (Chapter 5–9, 11A baseline) |
| `simulated/timeline_medium_seed{42..46}/` | 5 × 131 MB | Phase 11 Iter A | Cross-seed full re-runnability without MATLAB |
| `simulated/timeline_dense_urban/` | 260 MB | Phase 11 Iter B | Cross-scenario validation |
| `simulated/sweep_static/sweep_config_perf.parquet` | 36 KB | Phase 4c | Phase 8 surrogate training matrix (360 rows) |
| `simulated/calibration_final/` | 11 MB | Phase 3 | Source data for KS-test vs NordicDat |
| `simulated/phase1_demo/`, `phase2_demo/`, `phase2_sweep/` | ~0.5 MB | Phase 1–2 | Chapter 1–2 methodology illustration figures |
| `processed/` (entire tree) | 135 MB | Phase 3, 5–11 | All tables/CSV/JSON/PNGs for every benchmark, seed variant, dense_urban, external validation |
| `data/manifest.sha256` | 67 KB | tooling | per-file SHA256 integrity check (`make verify-cache`, `sha256sum -c`) |
| `docs/zenodo_metadata.json` | 3 KB | tooling | self-describing deposit metadata (also uploaded to the Zenodo form) |
| `BUNDLE_INFO.txt` | < 1 KB | tooling | provenance header (git commit, build UTC, file count, manifest sha) |

Repo source is **not** bundled — the canonical source archive is the git
tag (`vX.Y.Z`) on the GitHub repository linked in
`docs/zenodo_metadata.json::related_identifiers`. This keeps the bundle
focused on data and decouples deposit revisions from source-code commits.

**Not included** (deliberately):
- Third-party raw datasets (NordicDat, Bangladesh) — redistributed by their
  original authors under their own licenses. Reviewers download once via
  `data/raw_public/README.md`.
- Superseded / intermediate simulator runs: `timeline_iter_b/`,
  `timeline_short/`, `calibration_baseline/`, `calibration_sweep/`,
  `anomaly_smoke_*`, `*_quickcheck/`. These do not back any thesis figure.
- `data/external/` — out of scope (3GPP PDFs etc., obtained elsewhere).

For the per-release Zenodo workflow (DOI reservation → upload → publication
→ DOI back-fill into the repo), see
[`docs/zenodo_upload.md`](zenodo_upload.md).

**Manifest commit cadence (maintainer note).** `data/manifest.sha256` is
committed to the repo (not gitignored) so reviewers see the canonical SHA
table without needing to download anything first. The file should be
regenerated and committed:

1. After every `make all-full` run that is intended to back a thesis figure,
2. Immediately before cutting a Zenodo deposit (Phase 10 A5) so the
   in-repo manifest matches the deposit byte-for-byte,
3. At every thesis defense tag (`git tag v1.0.0` etc.).

Between these checkpoints the local manifest may diverge from the
committed one (e.g. while iterating on a single phase) — that is fine; just
do not commit those intermediate manifests. The provenance header inside
the file (`# Source commit:`) makes it obvious whether the committed
manifest is in sync with the current data tree.

---

## 4. Verifying a successful reproduction

After running Profile A/B/C, the following must hold:

```bash
# Python unit tests (no simulator required):
make pytest                                  # 396 passed

# MATLAB unit tests (Profile C only):
make test                                    # 88/88 PASS

# Cache integrity (Profile A/B):
make verify-cache                            # all SHA256 hashes match manifest.sha256
# Equivalent without Python:
sha256sum -c data/manifest.sha256 --quiet

# Headline figures regenerate byte-equivalent (modulo MPL backend) PNGs:
ls -lh data/processed/end_to_end_demo_timeline_medium/end_to_end_moneyshot.png
ls -lh data/processed/anomaly_benchmark_timeline_medium/drift_degradation.png
ls -lh data/processed/external_validation/seed_robustness_boxplot_timeline_medium.png
```

If any of these fail, file an issue with the full output of `make pytest` and
the matplotlib backend in use (`python -c "import matplotlib; print(matplotlib.get_backend())"`).

---

## 5. Why no MATLAB Compiler Runtime route?

We considered shipping a MATLAB Compiler Runtime (MCR) container that would
let reviewers run the simulator without a MATLAB license. We rejected this
because:

1. **5G Toolbox is not Compiler-deployable** in R2023b (verified via MathWorks'
   deployment matrix). `nrCarrierConfig` and related symbols are excluded from
   the `Communications Toolbox Wireless Network Simulator` support package's
   compiled targets.
2. The MCR image (~3 GB) plus the deployed binary (~500 MB) more than
   doubles the Docker image size with no functional benefit over Profile A
   (which uses cached parquet anyway).
3. The Python pipelines that consume simulator output (Phases 5–9, 11)
   represent ≥ 90 % of the analytical contribution. Caching the simulator
   output as parquet preserves full reproducibility of that work.

For full simulator regeneration, a MATLAB R2023b license is required
(Profile C). Universities, MathWorks academic licenses, and trial licenses
all suffice; the simulator does not exercise any commercial-only feature.

---

## 6. Provenance & changelog

| Date | Reproducibility version | Notes |
|---|---|---|
| 2026-06-06 | v0.1 | Initial Tier 1 reproducibility package: lockfile (A2), MATLAB pin (A4). Docker (A1), `make all-*` (A3), and Zenodo bundle (A5) tracked separately in `docs/plan.md` Phase 10. |
| 2026-06-06 | v0.2 (this doc) | All five Tier 1 sub-items complete: Dockerfile + `make all-from-cache` + `make all-full` + `data/manifest.sha256` + `scripts/build_zenodo_bundle.py` + `scripts/fetch_zenodo_bundle.py` + `docs/zenodo_metadata.json` + `docs/zenodo_upload.md`. Only outstanding item is minting the real Zenodo DOI at thesis submission. 396 Python tests passing. |

This document supersedes any version-pin language in earlier ADRs (ADR-1,
ADR-7) for the purposes of reproducibility.
