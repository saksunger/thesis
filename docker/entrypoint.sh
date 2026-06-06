#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Thin entrypoint for the thesis-repro container.
#
# Prints a banner (image revision + Python/pandas/numpy/pyarrow versions),
# then execs whatever command was passed to `docker run` (default: bash).
# Keeps the image friendly for both interactive use and scripted pipelines.
# ---------------------------------------------------------------------------
set -euo pipefail

GIT_SHA="${GIT_SHA:-unknown}"
BUILD_DATE="${BUILD_DATE:-unknown}"

# Only print the banner when stdout is a TTY OR when the user explicitly
# asks for it via THESIS_REPRO_BANNER=1. Keeps non-interactive output
# (`docker run ... bash -c "make pytest"`) clean for log parsing.
if [[ -t 1 || "${THESIS_REPRO_BANNER:-0}" == "1" ]]; then
    cat <<BANNER
============================================================================
thesis-repro container (Profile A/B, see docs/reproducibility.md)
  image revision : ${GIT_SHA}
  build date     : ${BUILD_DATE}
  python         : $(python --version 2>&1)
  workdir        : $(pwd)
  data mount     : /app/data  ($(ls /app/data 2>/dev/null | wc -l) entries)

Common commands:
  make help                          # Makefile target catalog
  make pytest                        # 356 Python unit tests
  make anomaly-benchmark             # regenerate Chapter 5 figures from cache
  make all-from-cache                # Phase 10 A3 — full Python-side regen

If /app/data is empty, fetch the cached simulator artifacts:
  python scripts/fetch_zenodo_bundle.py     # Phase 10 A5, ~600 MB
============================================================================
BANNER
fi

# Default to interactive bash if no command was supplied. Otherwise exec
# whatever the user (or docker run) passed, preserving signal handling.
if [[ "$#" -eq 0 ]]; then
    exec bash
else
    exec "$@"
fi
