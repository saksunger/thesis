function tests = test_logger
%TEST_LOGGER  Smoke tests for `utils.write_kpis_parquet` / `write_events_parquet`.
%
% We only verify the parquet round-trips with the right schema (file
% exists, row count matches, expected columns present). Distribution-level
% verification happens later in Python (`analysis/calibration/ks_test.py`).
tests = functiontests(localfunctions);
end

function setupOnce(tc)
addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));
tc.TestData.tmp = fullfile(tempdir, sprintf('logger_test_%d', randi(1e9)));
mkdir(tc.TestData.tmp);
end

function teardownOnce(tc)
if isfield(tc.TestData, 'tmp') && exist(tc.TestData.tmp, 'dir')
    rmdir(tc.TestData.tmp, 's');
end
end

function [cells, ue_track, meas, meta] = mk_minimal(T, K)
    cells = struct('id', num2cell(1:K), ...
                   'x_m', num2cell(zeros(1, K)), ...
                   'y_m', num2cell(((1:K)-1) * 100), ...
                   'z_m', num2cell(25 * ones(1, K)), ...
                   'tier', num2cell(zeros(1, K)));
    cells = cells(:);
    dt = 0.010;
    ue_track = struct( ...
        't_s',         (0:T-1).' * dt, ...
        'x_m',         zeros(T, 1), 'y_m', zeros(T, 1), ...
        'v_mps',       ones(T, 1) * 5, ...
        'heading_rad', zeros(T, 1));
    meas = struct( ...
        'rsrp_dbm', -70 + 10 * randn(T, K), ...
        'rsrq_db',  -12 + 2 * randn(T, K), ...
        'sinr_db',  5 + 3 * randn(T, K));
    meta = struct('fc_ghz', 3.5, 'isd_m', 500, 'seed', 42, ...
                  'h_bs_m', 25, 'h_ut_m', 1.5, ...
                  'scenario', 'UMa', 'ran_type', "NR_SA", 'band', "n78");
end


function test_write_kpis_creates_file(tc)
[cells, ue_track, meas, meta] = mk_minimal(100, 5);
out = fullfile(tc.TestData.tmp, 'kpis.parquet');
n = utils.write_kpis_parquet(out, 7, ue_track, cells, meas, meta);
verifyEqual(tc, n, 100);
verifyTrue(tc, isfile(out));
end


function test_write_kpis_schema_and_serving_argmax(tc)
[cells, ue_track, meas, meta] = mk_minimal(50, 4);
% force a deterministic serving cell (cell 3 always strongest)
meas.rsrp_dbm = -100 * ones(50, 4);
meas.rsrp_dbm(:, 3) = -50;
out = fullfile(tc.TestData.tmp, 'kpis2.parquet');
utils.write_kpis_parquet(out, 1, ue_track, cells, meas, meta);

% read back via parquetread (MATLAB-side roundtrip)
tbl = parquetread(out);
expected_cols = { ...
    'ue_id', 'time_s', 'serving_cell_id', 'neighbor_cell_id', ...
    'rsrp_serving_dbm', 'rsrq_serving_db', 'sinr_serving_db', ...
    'rsrp_neighbor_dbm', 'rsrq_neighbor_db', ...
    'ue_x_m', 'ue_y_m', 'ue_speed_mps', 'ue_heading_rad', ...
    'ran_type', 'band', 'meta_fc_ghz', 'meta_isd_m', 'meta_seed'};
for c = expected_cols
    verifyTrue(tc, ismember(c{1}, tbl.Properties.VariableNames), ...
        sprintf('missing column: %s', c{1}));
end
verifyEqual(tc, height(tbl), 50);
verifyTrue(tc, all(tbl.serving_cell_id == 3), ...
    'expected cell 3 to be serving for every row (it has +50 dB advantage)');
end


function test_write_events_empty_log(tc)
events = ho.empty_event();
events = events([]);
meta = struct('ttt_ms', 256, 'hyst_db', 2, 'a3_off_db', 0, 'seed', 1, ...
              'fc_ghz', 3.5, 'isd_m', 500);
out = fullfile(tc.TestData.tmp, 'events_empty.parquet');
n = utils.write_events_parquet(out, events, meta);
verifyEqual(tc, n, 0);
verifyTrue(tc, isfile(out), 'empty parquet must still be written');
end


function test_write_events_has_canonical_columns(tc)
% Fabricate two events
ev1 = ho.empty_event();
ev1.event_id = 1; ev1.event_time_s = 10.0; ev1.event_type = "HO_ATTEMPT";
ev1.ue_id = 1; ev1.source_cell_id = 1; ev1.target_cell_id = 2;
ev2 = ho.empty_event();
ev2.event_id = 2; ev2.event_time_s = 10.05; ev2.event_type = "HO_SUCCESS";
ev2.ue_id = 1; ev2.source_cell_id = 1; ev2.target_cell_id = 2;
events = [ev1; ev2];

meta = struct('ttt_ms', 256, 'hyst_db', 2, 'a3_off_db', 0, 'seed', 7, ...
              'fc_ghz', 3.5, 'isd_m', 500, 'ran_type', "NR_SA", 'band', "n78");
out = fullfile(tc.TestData.tmp, 'events_two.parquet');
utils.write_events_parquet(out, events, meta);

tbl = parquetread(out);
verifyEqual(tc, height(tbl), 2);
for c = {'event_id','event_type','cfg_ttt_ms','cfg_hyst_db','meta_seed', ...
         'meta_fc_ghz','meta_isd_m','meta_band'}
    verifyTrue(tc, ismember(c{1}, tbl.Properties.VariableNames), ...
        sprintf('missing column: %s', c{1}));
end
verifyEqual(tc, tbl.cfg_ttt_ms(1), single(256));
verifyEqual(tc, tbl.meta_seed(1), int32(7));
end
