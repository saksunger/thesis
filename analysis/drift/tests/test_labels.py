"""Unit tests for analysis.drift.labels."""

from __future__ import annotations

import pandas as pd
import pytest

from analysis.drift.labels import drift_label_columns, label_streams
from analysis.drift.streams import STREAM_NAMES


def _mk_streams(time_grid=None) -> pd.DataFrame:
    time_grid = time_grid if time_grid is not None else list(range(0, 30))
    rows = []
    for sn in STREAM_NAMES:
        for t in time_grid:
            rows.append({
                "time_s": float(t),
                "stream_name": sn,
                "value": 0.0,
                "phase_id": 1,
                "scenario_name": "baseline",
            })
    return pd.DataFrame(rows)


def _gtd(rows) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_no_drifts_marks_all_negative():
    s = _mk_streams()
    out = label_streams(s, pd.DataFrame())
    assert (out["label_drift"] == 0).all()
    assert (out["active_drift_ids"] == "").all()


def test_d2_drift_labels_rsrp_sinr_rsrq_streams_not_event_streams():
    s = _mk_streams()
    gtd = _gtd([{
        "drift_id": "D-2",
        "start_time_s": 10.0,
        "end_time_s": 20.0,
        "affected_kpis": "rsrp_serving_dbm,sinr_serving_db,rsrq_serving_db",
        "phase_id_start": 1, "phase_id_end": 1,
        "expected_direction": "shape", "note": "",
    }])
    out = label_streams(s, gtd)
    # rsrp_serving_mean stream in t in [10, 20) is positive
    win = out[(out["stream_name"] == "rsrp_serving_mean")
              & (out["time_s"] >= 10.0) & (out["time_s"] < 20.0)]
    assert (win["label_drift"] == 1).all()
    # SINR stream same
    win_sinr = out[(out["stream_name"] == "sinr_serving_mean")
                   & (out["time_s"] >= 10.0) & (out["time_s"] < 20.0)]
    assert (win_sinr["label_drift"] == 1).all()
    # ho_rate_rolling stream NOT affected by D-2
    not_aff = out[(out["stream_name"] == "ho_rate_rolling")
                  & (out["time_s"] >= 10.0) & (out["time_s"] < 20.0)]
    assert (not_aff["label_drift"] == 0).all()


def test_d1_traffic_shift_targets_event_streams():
    s = _mk_streams()
    gtd = _gtd([{
        "drift_id": "D-1",
        "start_time_s": 5.0, "end_time_s": 15.0,
        "affected_kpis": "event_rate,ping_pong_rate",
        "phase_id_start": 1, "phase_id_end": 1,
        "expected_direction": "up", "note": "",
    }])
    out = label_streams(s, gtd)
    pp = out[(out["stream_name"] == "ping_pong_rate_rolling")
             & (out["time_s"] >= 5.0) & (out["time_s"] < 15.0)]
    assert (pp["label_drift"] == 1).all()
    ho = out[(out["stream_name"] == "ho_rate_rolling")
             & (out["time_s"] >= 5.0) & (out["time_s"] < 15.0)]
    # event_rate -> ho_rate_rolling (per STREAM_TO_KPI_TAGS mapping)
    assert (ho["label_drift"] == 1).all()
    # rsrp_serving_mean is unaffected by D-1
    rsrp = out[(out["stream_name"] == "rsrp_serving_mean")
               & (out["time_s"] >= 5.0) & (out["time_s"] < 15.0)]
    assert (rsrp["label_drift"] == 0).all()


def test_d4_reconfig_labels_hosr_and_pp_rate():
    s = _mk_streams()
    gtd = _gtd([{
        "drift_id": "D-4",
        "start_time_s": 0.0, "end_time_s": 10.0,
        "affected_kpis": "hosr,ping_pong_rate",
        "phase_id_start": 1, "phase_id_end": 1,
        "expected_direction": "shape", "note": "",
    }])
    out = label_streams(s, gtd)
    assert (out[(out["stream_name"] == "hosr_rolling")
                & (out["time_s"] < 10)]["label_drift"] == 1).all()
    assert (out[(out["stream_name"] == "ping_pong_rate_rolling")
                & (out["time_s"] < 10)]["label_drift"] == 1).all()
    assert (out[(out["stream_name"] == "rsrp_serving_mean")
                & (out["time_s"] < 10)]["label_drift"] == 0).all()


def test_half_open_interval_rule():
    s = _mk_streams(time_grid=[9, 10, 11, 19, 20, 21])
    gtd = _gtd([{
        "drift_id": "D-2",
        "start_time_s": 10.0, "end_time_s": 20.0,
        "affected_kpis": "rsrp_serving_dbm",
        "phase_id_start": 1, "phase_id_end": 1,
        "expected_direction": "shape", "note": "",
    }])
    out = label_streams(s, gtd)
    rsrp = out[out["stream_name"] == "rsrp_serving_mean"].set_index("time_s")
    assert rsrp.loc[9.0, "label_drift"] == 0
    assert rsrp.loc[10.0, "label_drift"] == 1
    assert rsrp.loc[19.0, "label_drift"] == 1
    assert rsrp.loc[20.0, "label_drift"] == 0  # right-exclusive
    assert rsrp.loc[21.0, "label_drift"] == 0


def test_per_drift_id_column_emitted():
    s = _mk_streams()
    gtd = _gtd([
        {"drift_id": "D-1", "start_time_s": 0.0, "end_time_s": 5.0,
         "affected_kpis": "ping_pong_rate",
         "phase_id_start": 1, "phase_id_end": 1, "expected_direction": "up", "note": ""},
        {"drift_id": "D-2", "start_time_s": 10.0, "end_time_s": 15.0,
         "affected_kpis": "rsrp_serving_dbm",
         "phase_id_start": 1, "phase_id_end": 1, "expected_direction": "shape", "note": ""},
    ])
    out = label_streams(s, gtd)
    assert "label_D-1" in out.columns
    assert "label_D-2" in out.columns
    assert "label_D-1" in drift_label_columns(out)
    assert "label_D-2" in drift_label_columns(out)
    assert "label_drift" not in drift_label_columns(out)


def test_active_drift_ids_joined_when_multiple_overlap():
    s = _mk_streams()
    gtd = _gtd([
        {"drift_id": "D-3", "start_time_s": 5.0, "end_time_s": 15.0,
         "affected_kpis": "ho_rate",
         "phase_id_start": 1, "phase_id_end": 1, "expected_direction": "shape", "note": ""},
        {"drift_id": "D-4", "start_time_s": 10.0, "end_time_s": 20.0,
         "affected_kpis": "ho_rate",
         "phase_id_start": 1, "phase_id_end": 1, "expected_direction": "shape", "note": ""},
    ])
    out = label_streams(s, gtd)
    overlap = out[(out["stream_name"] == "ho_rate_rolling") & (out["time_s"] == 12.0)]
    assert overlap.iloc[0]["active_drift_ids"] == "D-3,D-4"
