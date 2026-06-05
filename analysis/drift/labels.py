"""Per-(stream, t) ground-truth drift labels for Phase 6 benchmark.

Consumes `ground_truth_drift.parquet` from `make sim` and the long-form
streams table from :func:`analysis.drift.streams.build_streams` and
emits a `label_drift` column per stream and per timestamp.

Labelling rule
--------------
A row `(stream, t)` is labelled `1` iff some drift entry overlaps `t`
AND its `affected_kpis` list intersects the stream's KPI tags
(:data:`analysis.drift.streams.STREAM_TO_KPI_TAGS`). Time intervals are
half-open `[start_time_s, end_time_s)` on both sides — same convention
as anomaly labels and the simulator's `+drifts.apply_all`.

A per-drift-id column is also emitted (`label_<drift_id>`, e.g.
`label_D-2`) so per-drift-type analysis (Table 6.1 / 6.2) is easy.

Per stream we also emit `active_drift_ids` (comma-joined drift_id list)
for diagnostic plots.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from analysis.drift.streams import STREAM_TO_KPI_TAGS


def label_streams(
    streams_long: pd.DataFrame,
    ground_truth_drift: pd.DataFrame,
) -> pd.DataFrame:
    """Attach `label_drift` + per-drift-id columns to a long-form streams table.

    Args:
        streams_long       : output of :func:`build_streams`. Must contain
                             columns `time_s`, `stream_name`, `phase_id`,
                             `value`.
        ground_truth_drift : DataFrame from `ground_truth_drift.parquet`
                             with columns `drift_id`, `start_time_s`,
                             `end_time_s`, `affected_kpis`.

    Returns:
        Same DataFrame as `streams_long` with these added columns:
        - `label_drift` (int 0/1): 1 iff any GT drift overlaps and
          affects this stream's KPI tag.
        - `label_<drift_id>` (int 0/1) for every drift_id seen in GT.
        - `active_drift_ids` (str): comma-joined drift_ids, empty for
          negative rows.
    """
    out = streams_long.copy()
    out["label_drift"] = 0
    out["active_drift_ids"] = ""

    if ground_truth_drift is None or ground_truth_drift.empty:
        return out

    drift_ids = sorted(ground_truth_drift["drift_id"].astype(str).unique().tolist())
    for did in drift_ids:
        out[f"label_{did}"] = 0

    t_arr = out["time_s"].to_numpy()
    stream_arr = out["stream_name"].to_numpy()

    # Precompute kpi tag set per stream
    stream_kpis = {sn: set(STREAM_TO_KPI_TAGS.get(sn, ())) for sn in out["stream_name"].unique()}

    for _, dr in ground_truth_drift.iterrows():
        d_t0 = float(dr["start_time_s"])
        d_t1 = float(dr["end_time_s"])
        did = str(dr["drift_id"])
        affected = _parse_kpi_list(dr["affected_kpis"])
        # Time match (half-open: [d_t0, d_t1))
        time_match = (t_arr >= d_t0) & (t_arr < d_t1)
        # Stream match: stream's KPI tags intersect affected list
        kpi_match = np.array(
            [bool(stream_kpis.get(sn, set()) & affected) for sn in stream_arr]
        )
        match = time_match & kpi_match
        if not match.any():
            continue
        out.loc[match, "label_drift"] = 1
        col = f"label_{did}"
        # Multiple instances of same drift_id (e.g. D-2 fires twice) share the column
        out.loc[match, col] = 1
        # Append active_drift_ids
        for idx in match.nonzero()[0]:
            current = out.iat[idx, out.columns.get_loc("active_drift_ids")]
            ids = set(filter(None, current.split(",")))
            ids.add(did)
            out.iat[idx, out.columns.get_loc("active_drift_ids")] = ",".join(sorted(ids))
    return out


def drift_label_columns(labelled: pd.DataFrame) -> list[str]:
    """Per-drift-id label column names present in `labelled`."""
    return sorted(
        c for c in labelled.columns
        if c.startswith("label_") and c != "label_drift"
    )


def _parse_kpi_list(raw: str | float) -> set[str]:
    """Parse `affected_kpis` (comma-string) into a set of KPI tags."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return set()
    return {x.strip() for x in str(raw).split(",") if x.strip()}
