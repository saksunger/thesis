"""Single source of truth for repository data paths.

Importing this module guarantees that every downstream loader / analysis
script agrees on where the datasets live, even when they're launched from
different working directories (REPL, notebook, `make`, pytest, ...).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[2]

DATA_DIR: Path = REPO_ROOT / "data"
DATA_RAW: Path = DATA_DIR / "raw_public"
DATA_SIM: Path = DATA_DIR / "simulated"
DATA_PROC: Path = DATA_DIR / "processed"
DATA_EXT: Path = DATA_DIR / "external"

BANGLADESH_DIR: Path = DATA_RAW / "bangladesh"
NORDICDAT_CSV: Path = DATA_RAW / "nordicdat" / "nordicdat_readable.csv"

# convenience subpaths inside Bangladesh
BANGLADESH_PROCESSED: Path = BANGLADESH_DIR / "Processed Dataset"
BANGLADESH_PARENT: Path = BANGLADESH_DIR / "Parent Dataset"
BANGLADESH_EVENTS: Path = BANGLADESH_DIR / "Event Statistics"


def timeline_dir(name: str) -> Path:
    """Return the on-disk directory for a Phase 4 simulated timeline.

    Layout (produced by `make sim TIMELINE=<name>`):
        DATA_SIM / <name> / samples.parquet
        DATA_SIM / <name> / events.parquet
        DATA_SIM / <name> / ground_truth_drift.parquet
        DATA_SIM / <name> / ground_truth_anomaly.parquet
        DATA_SIM / <name> / run_metadata.json
    """
    return DATA_SIM / name


def assert_timeline_present(name: str) -> None:
    """Raise a helpful error if the named timeline has not been generated."""
    base = timeline_dir(name)
    required = [
        base / "samples.parquet",
        base / "events.parquet",
        base / "run_metadata.json",
    ]
    missing = [p for p in required if not p.is_file()]
    if missing:
        raise FileNotFoundError(
            f"Timeline '{name}' is missing required files:\n  "
            + "\n  ".join(str(p) for p in missing)
            + f"\n\nRegenerate via:\n  make sim TIMELINE={name}"
        )


def assert_real_data_present() -> None:
    """Raise a helpful error if the real datasets are not copied in."""
    missing: list[Path] = []
    if not BANGLADESH_PROCESSED.is_dir():
        missing.append(BANGLADESH_PROCESSED)
    if not NORDICDAT_CSV.is_file():
        missing.append(NORDICDAT_CSV)
    if missing:
        raise FileNotFoundError(
            "Required real datasets are missing:\n  "
            + "\n  ".join(str(p) for p in missing)
            + "\nSee data/raw_public/README.md for repopulation instructions."
        )
