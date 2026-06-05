function n_rows = write_kpis_parquet(out_path, ue_id, ue_track, cells, meas, meta)
%WRITE_KPIS_PARQUET  Sample-level KPI parquet writer (canonical schema).
%
% Writes one row per simulator tick × UE with serving + best-neighbor
% KPIs, UE kinematics, and run metadata. Schema matches
% `docs/schema.md` §1 so that the same Python loader can consume
% simulator and real-dataset outputs interchangeably.
%
% Args:
%   out_path : char/string, output .parquet path (parents auto-created)
%   ue_id    : integer UE identifier
%   ue_track : struct from `mobility.waypoint_track` (.t_s, .x_m, .y_m,
%              .v_mps, .heading_rad)
%   cells    : struct array from `utils.hex_layout`
%   meas     : struct from `ho.measurements`
%              (.rsrp_dbm T×K, .rsrq_db T×K, .sinr_db T×K)
%   meta     : struct with run metadata
%              .fc_ghz, .isd_m, .scenario, .seed, .h_bs_m, .h_ut_m
%              .ran_type ("NR_SA" — scope: 5G intra-RAT inter-gNB Xn HO),
%              .band     (e.g. "n78")
%
% Returns:
%   n_rows   : number of rows written

arguments
    out_path (1,1) string
    ue_id    (1,1) double {mustBeInteger, mustBeNonnegative}
    ue_track (1,1) struct
    cells    (:,1) struct
    meas     (1,1) struct
    meta     (1,1) struct
end

T = numel(ue_track.t_s);
K = numel(cells);

% --- argmax / runner-up across cells per tick ---
[rsrp_sorted, idx_sorted] = sort(meas.rsrp_dbm, 2, 'descend');
serving_idx  = idx_sorted(:, 1);
neighbor_idx = idx_sorted(:, 2);

% gather indices (vectorized)
lin_serv = sub2ind([T, K], (1:T).', serving_idx);
lin_nbr  = sub2ind([T, K], (1:T).', neighbor_idx);

% map idx → cell id
cell_ids = [cells.id].';

% --- defaults for optional UE kinematics ---
v_mps   = getfield_default(ue_track, 'v_mps',        zeros(T,1));
heading = getfield_default(ue_track, 'heading_rad',  zeros(T,1));

ran_type = getfield_default(meta, 'ran_type', "NR_SA");
band     = getfield_default(meta, 'band',     "n78");

tbl = table( ...
    repmat(uint32(ue_id), T, 1), ...
    double(ue_track.t_s(:)), ...
    int32(cell_ids(serving_idx)), ...
    int32(cell_ids(neighbor_idx)), ...
    rsrp_sorted(:, 1), ...
    meas.rsrq_db(lin_serv), ...
    meas.sinr_db(lin_serv), ...
    rsrp_sorted(:, 2), ...
    meas.rsrq_db(lin_nbr), ...
    double(ue_track.x_m(:)), ...
    double(ue_track.y_m(:)), ...
    double(v_mps(:)), ...
    double(heading(:)), ...
    repmat(string(ran_type), T, 1), ...
    repmat(string(band), T, 1), ...
    repmat(single(meta.fc_ghz), T, 1), ...
    repmat(single(meta.isd_m), T, 1), ...
    repmat(int32(meta.seed), T, 1), ...
    'VariableNames', { ...
        'ue_id', 'time_s', 'serving_cell_id', 'neighbor_cell_id', ...
        'rsrp_serving_dbm', 'rsrq_serving_db', 'sinr_serving_db', ...
        'rsrp_neighbor_dbm', 'rsrq_neighbor_db', ...
        'ue_x_m', 'ue_y_m', 'ue_speed_mps', 'ue_heading_rad', ...
        'ran_type', 'band', 'meta_fc_ghz', 'meta_isd_m', 'meta_seed'});

% ensure parent dir
parent = fileparts(out_path);
if strlength(parent) > 0 && ~isfolder(parent)
    mkdir(parent);
end

parquetwrite(out_path, tbl);
n_rows = height(tbl);
end


function v = getfield_default(s, fname, dflt)
    if isfield(s, fname)
        v = s.(fname);
    else
        v = dflt;
    end
end
