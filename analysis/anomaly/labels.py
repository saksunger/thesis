"""Ground-truth labels for Phase 5 anomaly benchmark.

Converts the per-anomaly rows in `ground_truth_anomaly.parquet` (one row
per *injection*, with timeline-global start/end times and affected UE /
cell scopes) into per-window binary labels for the feature table
emitted by :func:`analysis.anomaly.features.aggregate_windows`.

Labelling rule (Iter A — pragmatic)
-----------------------------------
A window `(ue_id, t_start_s, t_end_s)` is labelled positive (`anomaly=1`)
iff any ground-truth anomaly entry overlaps the window's time range AND
the UE is in scope:

- `affected_ue_ids == "all"`     → every UE matches (used by
  `interference_spike` whose scoping is by cell, not UE).
- `affected_ue_ids == "<list>"`  → only listed UEs match.

This rule produces some labelling slack for `interference_spike` because
a UE whose serving cell is NOT the targeted one is *exposed but
unaffected*. Iter B will refine this by joining `serving_cell_id` of
each window against `affected_cell_ids`; Iter A keeps the simpler rule
to de-risk the detector pipeline first.

Per-anomaly-type label columns are also emitted (`label_A_1`, `label_A_2`,
…) so downstream code can compute PR-AUC per anomaly type.
"""

from __future__ import annotations

import pandas as pd


def _parse_id_list(raw: str | float) -> set[int] | None:
    """Parse the comma-string `affected_ue_ids` / `affected_cell_ids` column.

    Returns ``None`` for the literal string ``"all"`` (matches everything)
    and a set of ints otherwise. Empty strings are treated as ``None``
    (defensive default).
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    if s == "" or s.lower() == "all":
        return None
    return {int(x) for x in s.split(",") if x.strip()}


def label_windows(
    features: pd.DataFrame,
    ground_truth_anomaly: pd.DataFrame,
) -> pd.DataFrame:
    """Attach binary anomaly labels to a feature DataFrame.

    Args:
        features            : output of `analysis.anomaly.features.aggregate_windows`
                              (must contain `ue_id`, `t_start_s`, `t_end_s`).
        ground_truth_anomaly: DataFrame from `ground_truth_anomaly.parquet`
                              with columns `anomaly_id`, `anomaly_type`,
                              `t_start_s`, `t_end_s`, `affected_ue_ids`.

    Returns:
        Same DataFrame as `features` with these new columns:
        - `label_anomaly` (int 0/1): 1 iff any GT anomaly overlaps this window
        - `label_<anomaly_id>` (int 0/1) per anomaly_id seen in the GT table
          (e.g. `label_A-1`, `label_A-3`)
        - `active_anomaly_id` (string): comma-joined ids of overlapping
          anomalies (empty for negative windows). Useful for per-type
          PR-AUC breakdown.

    Note:
        Time intervals are treated half-open `[t_start_s, t_end_s)` on
        BOTH the GT side and the window side, matching the convention
        used by `+anomalies.apply_all` in the simulator.
    """
    out = features.copy()
    n = len(out)
    out["label_anomaly"] = 0
    out["active_anomaly_id"] = ""

    if ground_truth_anomaly is None or ground_truth_anomaly.empty:
        return out

    anomaly_ids = sorted(ground_truth_anomaly["anomaly_id"].unique().tolist())
    for aid in anomaly_ids:
        out[f"label_{aid}"] = 0

    win_ue = out["ue_id"].to_numpy()
    win_t0 = out["t_start_s"].to_numpy()
    win_t1 = out["t_end_s"].to_numpy()

    for _, anom in ground_truth_anomaly.iterrows():
        a_t0 = float(anom["t_start_s"])
        a_t1 = float(anom["t_end_s"])
        a_ues = _parse_id_list(anom["affected_ue_ids"])
        aid = str(anom["anomaly_id"])

        # Time overlap test (half-open intervals):
        #   overlap iff win_t0 < a_t1 AND a_t0 < win_t1
        time_match = (win_t0 < a_t1) & (a_t0 < win_t1)
        if a_ues is None:
            ue_match = pd.Series(True, index=out.index).to_numpy()
        else:
            ue_match = pd.Series(win_ue).isin(a_ues).to_numpy()

        match = time_match & ue_match
        out.loc[match, "label_anomaly"] = 1
        out.loc[match, f"label_{aid}"] = 1
        # Append the active anomaly_id to the comma-joined list, with
        # de-dup for windows that hit multiple anomalies.
        for idx in match.nonzero()[0]:
            current = out.iat[idx, out.columns.get_loc("active_anomaly_id")]
            ids = set(filter(None, current.split(",")))
            ids.add(aid)
            out.iat[idx, out.columns.get_loc("active_anomaly_id")] = ",".join(sorted(ids))

    return out


def anomaly_label_columns(labels_df: pd.DataFrame) -> list[str]:
    """Return the per-anomaly-id label column names present in `labels_df`."""
    return sorted(c for c in labels_df.columns if c.startswith("label_") and c != "label_anomaly")
