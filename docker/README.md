# Docker — Hybrid Python-only reproducibility image

This image lets any reviewer reproduce every Chapter 5–9 + 11 figure of the
thesis without installing MATLAB or a single Python package on their host.
It corresponds to **Profile B** in [`docs/reproducibility.md`](../docs/reproducibility.md);
the underlying Python pipelines are identical to Profile A.

For the rationale ("why no MATLAB Compiler Runtime route?") and the full
pinned stack, see the reproducibility doc. This README is the operational
quick-reference.

---

## 1. Build the image

From the **repo root** (not from `docker/`):

```bash
docker build \
  --build-arg GIT_SHA=$(git rev-parse HEAD) \
  --build-arg BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  -t thesis-repro:dev \
  -f docker/Dockerfile \
  .
```

Expected build behaviour (measured 2026-06-06 on a Debian 12 x86_64 host,
gigabit network):

- First build: **~3.5 min**, dominated by the hash-verified `pip install` of
  126 packages (~250 MB of wheels).
- Final image size: **~1.1 GB** (Python 3.12-slim-bookworm base + scientific
  Python stack; pyarrow + scipy + matplotlib dominate).
- Subsequent builds with no `requirements.txt` change: pip layer cached,
  rebuild completes in **~5 sec**.

The `--build-arg GIT_SHA=...` is optional but recommended — it bakes the
repo revision into the image labels and the entrypoint banner so reviewers
know exactly which commit produced their figures.

---

## 2. Run recipes

All recipes mount the host `data/` directory at `/app/data` so generated
artifacts persist outside the container.

### 2.1 Interactive shell (default)

```bash
docker run --rm -it \
  -v "$(pwd)/data:/app/data" \
  thesis-repro:dev
```

You land at `/app` as user `repro`. The entrypoint banner prints the image
revision, build date, Python version, and a hint at the most useful Makefile
targets. From here every documented `make` target works exactly as on the
host (modulo MATLAB-only targets, which fail fast with a clear error).

### 2.2 Smoke test the install (CI-style)

```bash
docker run --rm \
  -v "$(pwd)/data:/app/data" \
  thesis-repro:dev \
  make pytest
```

Expected output tail: `379 passed, 2 warnings in ~35s`. The 2 warnings are
the documented sklearn PCA divide-by-zero on a degenerate Phase 11C
NordicDat smoke fixture; harmless.

### 2.3 Regenerate Chapter 5 figures from cached parquet

Requires `data/simulated/timeline_medium/` to be populated (either by your
host's prior `make sim`, or by the Zenodo fetcher — see §3).

```bash
docker run --rm \
  -v "$(pwd)/data:/app/data" \
  thesis-repro:dev \
  make anomaly-benchmark
```

Outputs land under `data/processed/anomaly_benchmark_timeline_medium/` on
the host. Same shape for `drift-benchmark`, `adaptive-benchmark`,
`surrogate-benchmark`, `end2end-demo`.

### 2.4 Full Python-side chain (`make all-from-cache`)

The canonical one-liner once `data/` has been populated with the Zenodo
deposit:

```bash
docker run --rm \
  -v "$(pwd)/data:/app/data" \
  thesis-repro:dev \
  make all-from-cache
```

This will: verify cache integrity (SHA256 against `data/manifest.sha256`)
→ run pytest → run anomaly / drift / adaptive / surrogate / end-to-end demo
on both `timeline_medium` + `timeline_dense_urban` → re-aggregate the
seed-replication CSV → optionally re-run NordicDat face validity if the
dataset is mounted → regenerate every Chapter 5–9 + 11 figure. Expected
wall clock: ~30 min on a 4-core x86_64.

---

## 3. Fetching the cached simulator output

The container does not ship the ~600 MB cached simulator parquet (that would
bloat the image past Docker Hub limits and re-locks reviewers to a single
deposit version). Instead, pull it on demand once per host:

```bash
# Phase 10 A5 lands this script. Until then, fetch manually:
#   https://doi.org/10.5281/zenodo.XXXXXXX → unpack into data/
docker run --rm \
  -v "$(pwd)/data:/app/data" \
  thesis-repro:dev \
  python scripts/fetch_zenodo_bundle.py
```

The fetcher streams the deposit tarball, verifies the SHA256, and unpacks
into `data/simulated/` + `data/processed/`. Re-running is a no-op (cache
check before download).

---

## 4. Common operational notes

### 4.1 ARM64 hosts (Apple Silicon, AWS Graviton)

The pinned scientific wheels (`pyarrow`, `scipy`, `scikit-learn`) ship
`manylinux2014_x86_64` builds, not `aarch64`. Two options:

- **Recommended**: cross-build for x86_64 using QEMU (slower but
  byte-identical to CI):

  ```bash
  docker build --platform=linux/amd64 \
    -t thesis-repro:dev \
    -f docker/Dockerfile .
  docker run --platform=linux/amd64 --rm -it \
    -v "$(pwd)/data:/app/data" thesis-repro:dev
  ```

- **Native ARM64**: re-run `make pip-compile` on an ARM64 host to pull the
  matching `aarch64` wheels; commit the resulting `requirements-arm64.txt`
  to a side branch. Not part of the supported repro path because we cannot
  guarantee byte-identical numerical results across CPU architectures.

### 4.2 UID/GID mismatches

If the container writes files under `data/` with `uid=1000` and your host
user has a different UID, the files will appear owned by that UID on the
host. Override at runtime:

```bash
docker run --rm -it \
  --user $(id -u):$(id -g) \
  -v "$(pwd)/data:/app/data" \
  thesis-repro:dev
```

The `repro` user inside the image has UID 1000 by default; passing
`--user` overrides ownership of newly-created files without changing the
image.

### 4.3 Pinning the image for archival

For the thesis defense plate, tag the image with the defense commit:

```bash
docker build --build-arg GIT_SHA=<defense-commit-sha> \
  -t thesis-repro:v1.0.0 -f docker/Dockerfile .
docker save thesis-repro:v1.0.0 | gzip > dist/thesis-repro-v1.0.0.tar.gz
# Upload the tarball alongside the Zenodo bundle (Phase 10 A5).
```

The Zenodo deposit cites this tarball so reviewers can always recover the
exact analysis container, even if the upstream Python base image is yanked.

---

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Build fails on `pip install --require-hashes` | Local `requirements.txt` edited without regenerating hashes | Run `make pip-compile` on host, rebuild |
| Build fails on apt-get | Corporate proxy / mirror | Pass `--build-arg HTTP_PROXY=...` |
| Container exits immediately | Bad command after `--`. Banner suppressed when non-TTY | Run with `-it` to confirm entrypoint banner |
| `make sim` errors with "matlab: command not found" | Expected — this image is Profile B (no MATLAB) | Use Profile C on a host with MATLAB R2023b |
| Output figures look slightly different (pixel-level) | Matplotlib backend / font rasteriser differs from host | Compare CSV outputs (deterministic) instead of PNG pixels |
| Image > 2 GB | `data/` ended up baked in (.dockerignore not respected) | Verify `data/raw_public/**` etc. exclusions are active; rebuild |

---

## 6. CI / hosted-image plan

Once a thesis-defense tag is cut, the maintainer pushes the image to GitHub
Container Registry so reviewers do not need to build locally:

```bash
docker tag thesis-repro:v1.0.0 ghcr.io/<owner>/thesis-repro:v1.0.0
docker push ghcr.io/<owner>/thesis-repro:v1.0.0
```

Reviewer one-liner becomes:

```bash
docker run --rm -it \
  -v "$(pwd)/data:/app/data" \
  ghcr.io/<owner>/thesis-repro:v1.0.0
```

This is purely operational — the local-build path above remains the supported
ground truth.
