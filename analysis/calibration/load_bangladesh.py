"""Loader for the Bangladesh LTE drive-test dataset.

Two consumer-facing entry points:

- `load_bangladesh_samples()` — per-sample feed (RSRP/RSRQ/CINR/timestamp +
  serving + best neighbor + per-row HO trigger flag). Built from the
  `Processed Dataset/` CSVs which are the cleanest representation.
- `load_bangladesh_events()` — explicit HO Attempt / Success / Fail event
  log from the `Event Statistics/` CSVs (filter to `Intra LTE-HO`).

Both return canonical-schema DataFrames matching `docs/schema.md` §1
(samples) and §2 (events). The loaders normalize the messy column names,
decode encoded RSRP / RSRQ values per 3GPP TS 36.133, and parse the two
different time formats present in the dataset.

References:
    3GPP TS 36.133 v17.x §9.1.4 (RSRP encoding 0..97 → -140..-44 dBm)
    3GPP TS 36.133 v17.x §9.1.7 (RSRQ encoding 0..34 → -19.5..-3 dB,
                                  0.5-step)
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.common.paths import (
    BANGLADESH_EVENTS,
    BANGLADESH_PROCESSED,
    assert_real_data_present,
)


# =============================================================================
# Public API
# =============================================================================
def load_bangladesh_samples(
    decode_encoded: bool = True,
    drop_missing_neighbor: bool = False,
) -> pd.DataFrame:
    """Load all Bangladesh per-sample CSVs into one DataFrame.

    Source: `data/raw_public/bangladesh/Processed Dataset/*.csv` (6 files
    = 3 dataset variants × 2 reorderings — we deduplicate on
    `(source_file, time_s, serving_cell_id, neighbor_cell_id)`).

    Args:
        decode_encoded: If True, apply TS 36.133 RSRP/RSRQ decoding so
            outputs are in dBm/dB. Detection is automatic via the value
            range — see `_detect_rsrp_encoding`.
        drop_missing_neighbor: If True, drop rows whose neighbor RSRP is
            recorded as ``--`` (a few hundred rows). Default keeps them
            with NaN so callers can decide.

    Returns:
        DataFrame with canonical columns (subset of `docs/schema.md` §1):
        ``time_s, serving_cell_id, neighbor_cell_id, rsrp_serving_dbm,
        rsrq_serving_db, cinr_serving_db, rsrp_neighbor_dbm,
        rsrq_neighbor_db, handover_trigger, dataset_id, file_id``.
    """
    assert_real_data_present()

    files = sorted(BANGLADESH_PROCESSED.glob("Dataset_*_ver_*.csv"))
    if not files:
        raise FileNotFoundError(
            f"No Processed Dataset CSVs found under {BANGLADESH_PROCESSED}"
        )

    frames: list[pd.DataFrame] = []
    for fp in files:
        df = _read_processed_csv(fp)
        df["source_file"] = fp.name
        frames.append(df)

    df = pd.concat(frames, ignore_index=True)

    # _ver_1 and _ver_2 hold the same logical rows in different column
    # orderings → deduplicate on the natural key
    dedupe_key = [
        "dataset_id",
        "time_s",
        "serving_cell_id",
        "neighbor_cell_id",
        "rsrp_serving_raw",
        "rsrp_neighbor_raw",
    ]
    before = len(df)
    df = df.drop_duplicates(subset=dedupe_key).reset_index(drop=True)
    after = len(df)
    if after < before:
        # informational only; not a warning since dedup is the intent
        pass

    if decode_encoded:
        encoding = _detect_rsrp_encoding(df["rsrp_serving_raw"])
        if encoding == "ts36133":
            df["rsrp_serving_dbm"] = _decode_rsrp_ts36133(df["rsrp_serving_raw"])
            df["rsrp_neighbor_dbm"] = _decode_rsrp_ts36133(df["rsrp_neighbor_raw"])
            df["rsrq_serving_db"] = _decode_rsrq_ts36133(df["rsrq_serving_raw"])
            df["rsrq_neighbor_db"] = _decode_rsrq_ts36133(df["rsrq_neighbor_raw"])
        elif encoding == "raw_dbm":
            df["rsrp_serving_dbm"] = df["rsrp_serving_raw"].astype("float64")
            df["rsrp_neighbor_dbm"] = df["rsrp_neighbor_raw"].astype("float64")
            df["rsrq_serving_db"] = df["rsrq_serving_raw"].astype("float64")
            df["rsrq_neighbor_db"] = df["rsrq_neighbor_raw"].astype("float64")
        else:
            warnings.warn(
                f"Could not auto-detect RSRP encoding (range "
                f"{df['rsrp_serving_raw'].min()}..{df['rsrp_serving_raw'].max()}); "
                "raw values kept. Pass decode_encoded=False to silence.",
                stacklevel=2,
            )

    if drop_missing_neighbor:
        df = df.dropna(subset=["rsrp_neighbor_raw"]).reset_index(drop=True)

    cols = [
        "dataset_id",
        "source_file",
        "time_s",
        "serving_cell_id",
        "neighbor_cell_id",
        "rsrp_serving_raw",
        "rsrp_neighbor_raw",
        "rsrq_serving_raw",
        "rsrq_neighbor_raw",
        "cinr_serving_db",
        "handover_trigger",
        "ue_lat_deg",
        "ue_lon_deg",
        "ue_speed_kmh",
    ]
    if decode_encoded:
        cols.extend(
            [
                "rsrp_serving_dbm",
                "rsrp_neighbor_dbm",
                "rsrq_serving_db",
                "rsrq_neighbor_db",
            ]
        )
    return df.reindex(columns=cols)


def load_bangladesh_events(
    intra_lte_only: bool = True,
) -> pd.DataFrame:
    """Load all Event Statistics CSVs into one event-level DataFrame.

    Source: `data/raw_public/bangladesh/Event Statistics/*/*.CSV`.

    Args:
        intra_lte_only: If True (default), filter to ``Event == 'Intra LTE-HO'``
            which is the only event type used for HOSR calibration.

    Returns:
        DataFrame with canonical event-level columns (subset of
        `docs/schema.md` §2): ``event_time_s, event_type, result,
        source_earfcn, source_pci, target_earfcn, target_pci,
        attempt, success, fail, dataset_id, source_file``.

        ``event_time_s`` is seconds-of-day from the ``HH:MM:SS.ms``
        timestamp (the raw files have no date component).
    """
    assert_real_data_present()

    files = sorted(BANGLADESH_EVENTS.glob("Event Statistics_Dataset_*/*.CSV"))
    if not files:
        raise FileNotFoundError(
            f"No Event Statistics CSVs found under {BANGLADESH_EVENTS}"
        )

    frames: list[pd.DataFrame] = []
    for fp in files:
        df = _read_event_csv(fp)
        df["source_file"] = fp.name
        # parent folder name: "Event Statistics_Dataset_1" → 1
        m = re.search(r"Dataset_(\d+)", fp.parent.name)
        df["dataset_id"] = int(m.group(1)) if m else -1
        frames.append(df)

    df = pd.concat(frames, ignore_index=True)
    if intra_lte_only:
        df = df[df["event_type"] == "Intra LTE-HO"].reset_index(drop=True)

    return df.reindex(
        columns=[
            "dataset_id",
            "source_file",
            "event_time_s",
            "event_type",
            "result",
            "source_earfcn",
            "source_pci",
            "target_earfcn",
            "target_pci",
            "attempt",
            "success",
            "fail",
            "count",
        ]
    )


# =============================================================================
# CSV parsing helpers
# =============================================================================
# Column aliases — the three Bangladesh dataset variants use slightly
# different headings (trailing whitespace, the typo `RSRPQ` in
# Dataset_3_ver_2, presence/absence of Lat/Lon/Speed).
_COL_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp":         ("Timestamp",),
    "serving_cell_id":   ("Serving Cell ID",),
    "neighbor_cell_id":  ("Neighbor Cell ID",),
    "rsrp_serving":      ("Serving Cell RSRP",),
    "rsrq_serving":      ("Serving Cell RSRQ", "Serving Cell RSRPQ"),
    "cinr_serving":      ("Serving Cell CINR",),
    "rsrp_neighbor":     ("NeighCell RSRP",),
    "rsrq_neighbor":     ("NeighCell RSRQ",),
    "handover_trigger":  ("Handover Trigger",),
    # optional, only present in Dataset_2 and Dataset_3
    "ue_lat_deg":        ("Latitude",),
    "ue_lon_deg":        ("Longitude",),
    "ue_speed_kmh":      ("Speed (km/h)",),
}

_REQUIRED = (
    "timestamp",
    "serving_cell_id",
    "neighbor_cell_id",
    "rsrp_serving",
    "rsrq_serving",
    "cinr_serving",
    "rsrp_neighbor",
    "rsrq_neighbor",
    "handover_trigger",
)


def _read_processed_csv(fp: Path) -> pd.DataFrame:
    """Read one `Processed Dataset/*.csv`, normalize messy headers.

    Handles three known schema variants across Dataset_1/2/3 and the
    column-reorderings between `_ver_1` and `_ver_2`. Tolerates the
    `RSRPQ` typo in Dataset_3_ver_2 and the optional Lat/Lon/Speed
    columns in Dataset_2 and Dataset_3.
    """
    df = pd.read_csv(fp)
    df.columns = [c.strip() for c in df.columns]

    # Build canonical_name → source_name lookup, raising for required ones.
    resolved: dict[str, str | None] = {}
    for canon, candidates in _COL_ALIASES.items():
        hit = next((c for c in candidates if c in df.columns), None)
        if hit is None and canon in _REQUIRED:
            raise ValueError(
                f"{fp.name}: missing required column "
                f"(tried: {', '.join(candidates)})"
            )
        resolved[canon] = hit

    # Dataset id from filename: "Dataset_3_ver_2.csv" → 3
    m = re.match(r"Dataset_(\d+)_ver_\d+\.csv", fp.name)
    dataset_id = int(m.group(1)) if m else -1

    def _num(canon: str) -> pd.Series:
        col = resolved[canon]
        if col is None:
            return pd.Series([float("nan")] * len(df))
        return pd.to_numeric(df[col], errors="coerce")

    out = pd.DataFrame(
        {
            "dataset_id":         dataset_id,
            "time_s":             _parse_any_time(df[resolved["timestamp"]]),
            "serving_cell_id":    _num("serving_cell_id").astype("Int64"),
            "neighbor_cell_id":   _num("neighbor_cell_id").astype("Int64"),
            "rsrp_serving_raw":   _num("rsrp_serving"),
            "rsrp_neighbor_raw":  _num("rsrp_neighbor"),
            "rsrq_serving_raw":   _num("rsrq_serving"),
            "rsrq_neighbor_raw":  _num("rsrq_neighbor"),
            # CINR (combined report) is in dB across all 3 datasets (values
            # include negatives like -11). CINR-1 / CINR-2 are per-antenna.
            "cinr_serving_db":    _num("cinr_serving"),
            "handover_trigger":   _num("handover_trigger").fillna(0).astype("int8"),
            "ue_lat_deg":         _num("ue_lat_deg"),
            "ue_lon_deg":         _num("ue_lon_deg"),
            "ue_speed_kmh":       _num("ue_speed_kmh"),
        }
    )
    return out


def _read_event_csv(fp: Path) -> pd.DataFrame:
    """Read one Event Statistics CSV; parse Freq into source/target EARFCN+PCI."""
    df = pd.read_csv(fp)
    df.columns = [c.strip() for c in df.columns]

    # Some rows have empty Time (continuation rows); coerce errors to NaT
    event_time_s = _parse_hhmmss_to_seconds(df["Time"])

    src_earfcn, src_pci, tgt_earfcn, tgt_pci = _parse_freq_field(df["Freq"])

    out = pd.DataFrame(
        {
            "event_time_s": event_time_s,
            "event_type": df["Event"].astype("string").str.strip(),
            "result": df["Result"].astype("string").str.strip(),
            "source_earfcn": src_earfcn,
            "source_pci": src_pci,
            "target_earfcn": tgt_earfcn,
            "target_pci": tgt_pci,
            "attempt": pd.to_numeric(df["Attempt"], errors="coerce").fillna(0).astype("int32"),
            "success": pd.to_numeric(df["Success"], errors="coerce").fillna(0).astype("int32"),
            "fail": pd.to_numeric(df["Fail"], errors="coerce").fillna(0).astype("int32"),
            "count": pd.to_numeric(df["Count"], errors="coerce"),
        }
    )
    return out


# =============================================================================
# Timestamp parsing
# =============================================================================
# Three distinct timestamp string formats observed across the dataset:
#
#   A. "MM:SS.s"      e.g. "33:33.7"     (Dataset_1, Dataset_2)
#   B. "HH:MM:SS.s"   e.g. "16:47:14.242" (Event Statistics)
#   C. "HH:MM:SSmmm"  e.g. "18:22:06981" (Dataset_3 — last colon dropped,
#                                          milliseconds glued to seconds
#                                          as a 3-4-digit suffix)
#
# We return seconds-of-day (formats B, C) or seconds-of-hour (format A);
# downstream code only cares about *relative* offsets so the absolute base
# is immaterial.
_MMSS_RE   = re.compile(r"^\s*(\d+):(\d+(?:\.\d+)?)\s*$")
_HHMMSS_RE = re.compile(r"^\s*(\d+):(\d+):(\d+(?:\.\d+)?)\s*$")
_HMS_NODOT = re.compile(r"^\s*(\d+):(\d+):(\d{2})(\d{1,4})\s*$")


def _parse_any_time(s: pd.Series) -> pd.Series:
    """Best-effort parse of the three known Bangladesh time formats into
    floating-point seconds. Unparseable strings become NaN."""
    def _one(v: object) -> float:
        if pd.isna(v):
            return float("nan")
        sv = str(v).strip()
        m = _HHMMSS_RE.match(sv)
        if m:
            return (
                float(m.group(1)) * 3600.0
                + float(m.group(2)) * 60.0
                + float(m.group(3))
            )
        m = _HMS_NODOT.match(sv)
        if m:
            ms_str = m.group(4)
            ms = float(ms_str) / 10.0 ** len(ms_str)
            return (
                float(m.group(1)) * 3600.0
                + float(m.group(2)) * 60.0
                + float(m.group(3))
                + ms
            )
        m = _MMSS_RE.match(sv)
        if m:
            return float(m.group(1)) * 60.0 + float(m.group(2))
        return float("nan")

    return s.map(_one).astype("float64")


def _parse_hhmmss_to_seconds(s: pd.Series) -> pd.Series:
    """Parse the strict ``HH:MM:SS.ms`` Event Stats format."""
    return _parse_any_time(s)


# Freq field format: "(-/-) to (40490/259)" — source unknown, target EARFCN/PCI
_FREQ_RE = re.compile(
    r"\(\s*([\d-]+|-)\s*/\s*([\d-]+|-)\s*\)\s*to\s*\(\s*([\d-]+|-)\s*/\s*([\d-]+|-)\s*\)"
)


def _parse_freq_field(s: pd.Series):
    """Return four parallel Int64 Series (src_earfcn, src_pci, tgt_earfcn, tgt_pci)."""
    src_e, src_p, tgt_e, tgt_p = [], [], [], []
    for v in s.fillna("").astype(str):
        m = _FREQ_RE.search(v)
        if not m:
            src_e.append(pd.NA); src_p.append(pd.NA)
            tgt_e.append(pd.NA); tgt_p.append(pd.NA)
            continue
        for lst, raw in zip((src_e, src_p, tgt_e, tgt_p), m.groups()):
            if raw == "-" or raw == "":
                lst.append(pd.NA)
            else:
                try:
                    lst.append(int(raw))
                except ValueError:
                    lst.append(pd.NA)
    return (
        pd.array(src_e, dtype="Int64"),
        pd.array(src_p, dtype="Int64"),
        pd.array(tgt_e, dtype="Int64"),
        pd.array(tgt_p, dtype="Int64"),
    )


# =============================================================================
# TS 36.133 encoding helpers
# =============================================================================
def _detect_rsrp_encoding(s: pd.Series) -> str:
    """Return 'ts36133' (encoded 0..97), 'raw_dbm' (already negative), or 'unknown'."""
    arr = s.dropna()
    if len(arr) == 0:
        return "unknown"
    lo, hi = arr.min(), arr.max()
    if lo >= 0 and hi <= 97:
        return "ts36133"
    if lo < 0 and hi <= 0:
        return "raw_dbm"
    return "unknown"


def _decode_rsrp_ts36133(s: pd.Series) -> pd.Series:
    """Decode TS 36.133 §9.1.4 RSRP_meas → RSRP in dBm.

    Mapping: ``encoded → encoded - 140`` for encoded ∈ [0, 96];
    encoded 97 maps to "above -44 dBm" (we clip to -44).
    """
    enc = pd.to_numeric(s, errors="coerce")
    out = enc - 140.0
    out = out.where(enc < 97, -44.0)
    out = out.where((enc >= 0) & (enc <= 97), np.nan)
    return out


def _decode_rsrq_ts36133(s: pd.Series) -> pd.Series:
    """Decode TS 36.133 §9.1.7 RSRQ_meas → RSRQ in dB.

    Two reporting ranges per the spec (and Rel-13 extension):

    - Original (Table 9.1.7-1): ``enc ∈ [0, 33] → enc * 0.5 - 19.5`` dB
      (covers -19.5 .. -3 dB in 0.5-dB steps); encoded 34 = "above -3 dB".
    - Rel-13 extended (Table 9.1.7-2): ``enc ∈ [34, 46] → (enc - 34) *
      0.5 - 3.0`` dB (covers -3 .. +3 dB in 0.5-dB steps).
    - Encoded > 46 are out-of-spec and clipped to +3 dB (physically the
      maximum achievable RSRQ given the L1 formula).
    """
    enc = pd.to_numeric(s, errors="coerce")
    out = pd.Series(np.nan, index=s.index, dtype="float64")
    m_orig = (enc >= 0) & (enc <= 33)
    out[m_orig] = enc[m_orig] * 0.5 - 19.5
    m_ext = (enc >= 34) & (enc <= 46)
    out[m_ext] = (enc[m_ext] - 34) * 0.5 - 3.0
    m_clip = enc > 46
    out[m_clip] = 3.0
    return out


# =============================================================================
# Convenience: one-shot summary for the CLI
# =============================================================================
if __name__ == "__main__":
    pd.options.display.width = 160
    pd.options.display.max_columns = 30

    print("=== Bangladesh per-sample feed ===")
    samples = load_bangladesh_samples()
    print(f"rows: {len(samples):,}  files: {samples['source_file'].nunique()}  "
          f"datasets: {sorted(samples['dataset_id'].unique())}")
    print(samples.head(3))
    print()
    print("--- raw RSRP/RSRQ value ranges ---")
    print(samples[
        ["rsrp_serving_raw", "rsrp_neighbor_raw",
         "rsrq_serving_raw", "rsrq_neighbor_raw"]
    ].describe(percentiles=[0.05, 0.5, 0.95]))
    print()
    print("--- decoded RSRP/RSRQ value ranges (dBm/dB) ---")
    print(samples[
        ["rsrp_serving_dbm", "rsrp_neighbor_dbm",
         "rsrq_serving_db", "rsrq_neighbor_db", "cinr_serving_db"]
    ].describe(percentiles=[0.05, 0.5, 0.95]))

    print()
    print("=== Bangladesh event log (Intra LTE-HO) ===")
    events = load_bangladesh_events()
    print(f"events: {len(events):,}  result counts:")
    print(events["result"].value_counts())
    # The dataset uses "Failure" (not "Fail"). Accept both for safety.
    n_attempt = int(events["result"].isin(["Attempt"]).sum())
    n_success = int(events["result"].isin(["Success"]).sum())
    n_fail    = int(events["result"].isin(["Fail", "Failure"]).sum())
    hosr = n_success / max(n_attempt, 1)
    print(f"Attempt={n_attempt}  Success={n_success}  Fail={n_fail}  "
          f"HOSR ≈ {hosr:.3f}")
