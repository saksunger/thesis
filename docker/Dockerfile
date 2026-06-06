# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Hybrid Python-only reproducibility image for the thesis.
#
# The image ships:
#   - Python 3.12.3 (matches host pin documented in docs/reproducibility.md),
#   - 126 hash-verified Python packages from requirements.txt,
#   - the analysis/ + tools/ + Makefile source needed to re-run every
#     Chapter 5–9 + 11 figure from cached simulator output (Profile A/B in
#     docs/reproducibility.md).
#
# The image does NOT ship:
#   - MATLAB (license-bound; see docs/reproducibility.md Section 5 for the
#     rationale and Profile C for the full from-scratch path),
#   - the cached simulator parquet (~1.2 GB; mounted at /app/data at run
#     time, fetched on demand from the Zenodo deposit by Phase 10 A5).
#
# Build:
#   docker build -t thesis-repro -f docker/Dockerfile .
#
# Run (drops you into a shell with the repo at /app and your host's
# data/ mounted at /app/data):
#   docker run --rm -it -v "$(pwd)/data:/app/data" thesis-repro
#
# Reviewer recipes live in docker/README.md.
# ---------------------------------------------------------------------------

# Exact Python pin matches docs/reproducibility.md §1.2. Bookworm-slim keeps
# the base under 60 MB while still shipping a glibc compatible with the
# manylinux2014 wheels for pyarrow/scipy/scikit-learn.
FROM python:3.12.3-slim-bookworm

# Build-time provenance. Override at build:
#   docker build --build-arg GIT_SHA=$(git rev-parse HEAD) ...
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="thesis-repro" \
      org.opencontainers.image.description="Hybrid Python-only reproducibility container for the 5G/6G HO ML thesis (Profiles A & B)." \
      org.opencontainers.image.source="https://github.com/<owner>/thesis" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.documentation="https://github.com/<owner>/thesis/blob/main/docs/reproducibility.md"

# Runtime env. MPLBACKEND=Agg keeps matplotlib happy in headless containers.
# PYTHONDONTWRITEBYTECODE keeps the image read-only-friendly and avoids
# .pyc files showing up in mounted volumes when /app is bind-mounted.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg \
    TZ=UTC

# System deps:
#   - make: the Makefile is the canonical entry point for every pipeline,
#   - git:  read-only for `git rev-parse` provenance and run_metadata.json,
#   - ca-certificates + curl: for the Phase 10 A5 Zenodo fetcher,
#   - tzdata: stable timestamps in event logs.
# Build-essential is intentionally omitted — every pinned wheel ships a
# manylinux build for x86_64. On ARM64 hosts pass `--platform=linux/amd64`
# (see docker/README.md "ARM64 hosts").
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        make \
        git \
        curl \
        ca-certificates \
        tzdata \
 && rm -rf /var/lib/apt/lists/*

# Non-root user for security + cleanly-owned mounted volumes. UID 1000
# matches the typical host primary user; override at run-time if your host
# UID differs:
#   docker run --user $(id -u):$(id -g) ...
RUN groupadd --gid 1000 repro \
 && useradd  --uid 1000 --gid repro --create-home --shell /bin/bash repro

# Create /app explicitly and chown to repro so pytest / matplotlib / any
# Python tool can write transient state (e.g. .pytest_cache) without
# needing root. COPY --chown=... chowns copied files, not the dir itself.
RUN mkdir -p /app && chown repro:repro /app
WORKDIR /app

# Layer caching: copy ONLY the lockfile first so subsequent code changes
# don't invalidate the (slow) dependency install layer.
COPY --chown=repro:repro requirements.txt requirements.in ./

# Hash-verified install of the full lockfile (126 packages). Any wheel that
# fails SHA256 verification aborts the build — this is the security barrier
# replacing TLS cert validation. We do NOT pass --no-deps: the lockfile
# already lists every transitive at top level, and pip's resolver consults
# them to verify consistency.
RUN pip install --require-hashes -r requirements.txt \
 && python -c "import pandas, numpy, scipy, sklearn, river, pyarrow, matplotlib; \
print('deps OK:', pandas.__version__, numpy.__version__, scipy.__version__, \
sklearn.__version__, river.__version__, pyarrow.__version__, matplotlib.__version__)"

# Now copy the source. .dockerignore strips data/, .venv/, .git/, agent
# state, paper build artifacts, MATLAB artefacts — everything you don't
# want in a 200 MB image.
COPY --chown=repro:repro . .

# Pre-create the data mount point so a bare `docker run` (no -v) still
# starts cleanly. Bind-mounted data overrides this dir.
RUN mkdir -p /app/data/raw_public /app/data/simulated /app/data/processed \
 && chown -R repro:repro /app/data

USER repro

# Entry point is a thin wrapper so we can print a banner + the GIT_SHA
# without losing the ability to `docker run ... bash -c "make pytest"`.
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["bash"]
