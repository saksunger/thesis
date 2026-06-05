"""Unit tests for ground-truth anomaly labelling."""

from __future__ import annotations

import pandas as pd

from analysis.anomaly.labels import _parse_id_list, anomaly_label_columns, label_windows


def _mk_features(ue_ids=(1, 2, 3), windows=None) -> pd.DataFrame:
    """Build a minimal feature-like table for label tests."""
    windows = windows or [(0, 5), (5, 10), (10, 15), (15, 20)]
    rows = []
    for ue in ue_ids:
        for w_idx, (t0, t1) in enumerate(windows):
            rows.append(
                {
                    "ue_id": ue,
                    "phase_id": 1,
                    "scenario_name": "baseline",
                    "window_id": w_idx,
                    "t_start_s": float(t0),
                    "t_end_s": float(t1),
                    "t_mid_s": (t0 + t1) / 2,
                }
            )
    return pd.DataFrame(rows)


def _mk_gta(rows) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_parse_id_list_handles_all_string():
    assert _parse_id_list("all") is None
    assert _parse_id_list("ALL") is None
    assert _parse_id_list("") is None
    assert _parse_id_list(None) is None


def test_parse_id_list_handles_comma_string():
    assert _parse_id_list("1,2,3") == {1, 2, 3}
    assert _parse_id_list("7") == {7}
    assert _parse_id_list(" 1 , 5 ") == {1, 5}


def test_label_windows_no_gt_marks_all_negative():
    feats = _mk_features()
    out = label_windows(feats, pd.DataFrame())
    assert (out["label_anomaly"] == 0).all()
    assert (out["active_anomaly_id"] == "").all()


def test_label_per_ue_anomaly_only_hits_target_ue():
    feats = _mk_features(ue_ids=(1, 2, 3))
    gta = _mk_gta(
        [
            {
                "anomaly_id": "A-1",
                "anomaly_type": "rlf_burst",
                "phase_id": 1,
                "t_start_s": 6.0,
                "t_end_s": 9.0,
                "affected_ue_ids": "2",
                "affected_cell_ids": "all",
                "severity": -15.0,
                "note": "",
            }
        ]
    )
    out = label_windows(feats, gta)
    # window (5,10) overlaps (6,9); UE 2 should be positive, UE 1/3 negative
    win_5_10 = out[(out["t_start_s"] == 5.0) & (out["t_end_s"] == 10.0)]
    assert win_5_10.loc[win_5_10.ue_id == 2, "label_anomaly"].iloc[0] == 1
    assert win_5_10.loc[win_5_10.ue_id == 1, "label_anomaly"].iloc[0] == 0
    assert win_5_10.loc[win_5_10.ue_id == 3, "label_anomaly"].iloc[0] == 0
    # Per-id column should track the same
    assert "label_A-1" in out.columns
    assert win_5_10.loc[win_5_10.ue_id == 2, "label_A-1"].iloc[0] == 1


def test_label_all_ues_anomaly_hits_every_ue():
    feats = _mk_features(ue_ids=(1, 2, 3))
    gta = _mk_gta(
        [
            {
                "anomaly_id": "A-3",
                "anomaly_type": "interference_spike",
                "phase_id": 1,
                "t_start_s": 11.0,
                "t_end_s": 14.0,
                "affected_ue_ids": "all",
                "affected_cell_ids": "1",
                "severity": -10.0,
                "note": "",
            }
        ]
    )
    out = label_windows(feats, gta)
    win = out[(out["t_start_s"] == 10.0) & (out["t_end_s"] == 15.0)]
    assert (win["label_anomaly"] == 1).all()


def test_half_open_interval_rule():
    feats = _mk_features(ue_ids=(1,), windows=[(0, 5), (5, 10)])
    # GT [5, 5.001] should NOT touch window [0,5) (right-exclusive), MUST touch [5,10)
    gta = _mk_gta(
        [
            {
                "anomaly_id": "A-X",
                "anomaly_type": "rlf_burst",
                "phase_id": 1,
                "t_start_s": 5.0,
                "t_end_s": 5.001,
                "affected_ue_ids": "1",
                "affected_cell_ids": "all",
                "severity": -10.0,
                "note": "",
            }
        ]
    )
    out = label_windows(feats, gta)
    win05 = out[out["t_start_s"] == 0.0].iloc[0]
    win510 = out[out["t_start_s"] == 5.0].iloc[0]
    assert win05["label_anomaly"] == 0
    assert win510["label_anomaly"] == 1


def test_multiple_anomalies_in_same_window_get_joined_id():
    feats = _mk_features(ue_ids=(1,), windows=[(0, 10)])
    gta = _mk_gta(
        [
            {
                "anomaly_id": "A-1",
                "anomaly_type": "rlf_burst",
                "phase_id": 1,
                "t_start_s": 2.0,
                "t_end_s": 4.0,
                "affected_ue_ids": "1",
                "affected_cell_ids": "all",
                "severity": -15.0,
                "note": "",
            },
            {
                "anomaly_id": "A-2",
                "anomaly_type": "meas_glitch",
                "phase_id": 1,
                "t_start_s": 6.0,
                "t_end_s": 8.0,
                "affected_ue_ids": "1",
                "affected_cell_ids": "all",
                "severity": -95.0,
                "note": "",
            },
        ]
    )
    out = label_windows(feats, gta)
    assert out["label_anomaly"].iloc[0] == 1
    assert out["active_anomaly_id"].iloc[0] == "A-1,A-2"
    assert out["label_A-1"].iloc[0] == 1
    assert out["label_A-2"].iloc[0] == 1


def test_anomaly_label_columns_helper():
    feats = _mk_features(ue_ids=(1,))
    gta = _mk_gta(
        [
            {
                "anomaly_id": "A-7",
                "anomaly_type": "rlf_burst",
                "phase_id": 1,
                "t_start_s": 1.0,
                "t_end_s": 2.0,
                "affected_ue_ids": "1",
                "affected_cell_ids": "all",
                "severity": -5.0,
                "note": "",
            }
        ]
    )
    out = label_windows(feats, gta)
    assert "label_A-7" in anomaly_label_columns(out)
    assert "label_anomaly" not in anomaly_label_columns(out)
