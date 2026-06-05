function tests = test_sweep_static
%TEST_SWEEP_STATIC  Schema + smoke tests for the Phase 4 Iter C static sweep.
%
% Runs a deliberately tiny grid (2 × 2 × 2 × 2 = 16 runs) so the test stays
% under ~30 s wall-clock while still exercising:
%   - the flatten/parfor-or-serial dispatch
%   - the parquet writer schema
%   - the metadata JSON
%   - the rate-derivation logic (HOSR, HOFR, RLF rate, ping-pong rate)
tests = functiontests(localfunctions);
end


function setupOnce(tc)
addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));
tc.TestData.tmp = fullfile(tempdir, sprintf('sweep_test_%d', randi(1e9)));
mkdir(tc.TestData.tmp);
end


function teardownOnce(tc)
if isfield(tc.TestData, 'tmp') && exist(tc.TestData.tmp, 'dir')
    rmdir(tc.TestData.tmp, 's');
end
end


function test_sweep_emits_parquet_with_expected_schema(tc)
opts = struct( ...
    'ttt_ms_grid',    [256, 512], ...
    'hyst_db_grid',   [0, 2], ...
    'a3_off_db_grid', [0, 3], ...
    'seeds',          [1, 2], ...
    'n_ue',           2, ...
    'duration_s',     12, ...
    'use_parfor',     false);
out_dir = fullfile(tc.TestData.tmp, 'out_schema');
sweep_static(string(out_dir), opts);

parquet_fp = fullfile(out_dir, 'sweep_config_perf.parquet');
verifyTrue(tc, isfile(parquet_fp), 'sweep_config_perf.parquet must exist');

T = parquetread(parquet_fp);
% 2 × 2 × 2 × 2 = 16 runs
verifyEqual(tc, height(T), 16);

required_cols = { ...
    'run_id', 'ttt_ms', 'hyst_db', 'a3_off_db', 'seed', 'n_ue', 'duration_s', ...
    'ho_attempt_count', 'ho_success_count', 'ho_fail_count', ...
    'rlf_count', 'pp_count', ...
    'hosr', 'hofr_rate', 'rlf_rate', 'ping_pong_rate', ...
    'rsrp_p10_dbm', 'rsrp_p50_dbm', 'rsrp_p90_dbm', ...
    'sinr_p10_db',  'sinr_p50_db',  'sinr_p90_db', ...
    'rsrq_p10_db',  'rsrq_p50_db',  'rsrq_p90_db'};
for f = required_cols
    verifyTrue(tc, ismember(f{1}, T.Properties.VariableNames), ...
        sprintf('missing column: %s', f{1}));
end

% run_id unique 1..N
verifyEqual(tc, sort(T.run_id), int32((1:16).'));

% Grid values are all combinations
verifyEqual(tc, sort(unique(T.ttt_ms)),    single([256; 512]));
verifyEqual(tc, sort(unique(T.hyst_db)),   single([0; 2]));
verifyEqual(tc, sort(unique(T.a3_off_db)), single([0; 3]));
verifyEqual(tc, sort(unique(T.seed)),      int32([1; 2]));
end


function test_sweep_metadata_json_records_grid(tc)
opts = struct( ...
    'ttt_ms_grid',    [256], ...
    'hyst_db_grid',   [0, 4], ...
    'a3_off_db_grid', [3], ...
    'seeds',          [99], ...
    'n_ue',           2, ...
    'duration_s',     8, ...
    'use_parfor',     false);
out_dir = fullfile(tc.TestData.tmp, 'out_meta');
sweep_static(string(out_dir), opts);

meta_fp = fullfile(out_dir, 'sweep_metadata.json');
verifyTrue(tc, isfile(meta_fp), 'sweep_metadata.json must exist');

meta = jsondecode(fileread(meta_fp));
verifyEqual(tc, meta.runner, 'sweep_static');
verifyEqual(tc, meta.scenario, 'baseline');
verifyEqual(tc, meta.n_runs, 2);
verifyEqual(tc, meta.n_ue_per_run, 2);
verifyEqual(tc, meta.duration_s, 8);
verifyEqual(tc, meta.ttt_ms_grid(:), [256]);
verifyEqual(tc, meta.hyst_db_grid(:), [0; 4]);
verifyEqual(tc, meta.a3_off_db_grid(:), [3]);
verifyEqual(tc, meta.seeds(:), [99]);
end


function test_rates_finite_when_attempts_nonzero(tc)
% With 4 UEs over 20 s on a single phase, baseline (TTT 256ms, hyst 2dB)
% reliably triggers handovers. Verify the derived rate columns are
% finite and within plausible bounds.
opts = struct( ...
    'ttt_ms_grid',    [256], ...
    'hyst_db_grid',   [2], ...
    'a3_off_db_grid', [3], ...
    'seeds',          [42], ...
    'n_ue',           4, ...
    'duration_s',     20, ...
    'use_parfor',     false);
out_dir = fullfile(tc.TestData.tmp, 'out_rates');
sweep_static(string(out_dir), opts);
T = parquetread(fullfile(out_dir, 'sweep_config_perf.parquet'));

verifyEqual(tc, height(T), 1);
verifyGreaterThanOrEqual(tc, T.ho_attempt_count(1), 0);
% rates: NaN allowed only if attempts == 0
if T.ho_attempt_count(1) > 0
    verifyTrue(tc, isfinite(T.hosr(1)));
    verifyGreaterThanOrEqual(tc, T.hosr(1), 0);
    verifyLessThanOrEqual(tc,    T.hosr(1), 1);
    verifyTrue(tc, isfinite(T.ping_pong_rate(1)));
    verifyGreaterThanOrEqual(tc, T.ping_pong_rate(1), 0);
end
verifyTrue(tc, isfinite(T.rlf_rate(1)));
verifyGreaterThanOrEqual(tc, T.rlf_rate(1), 0);
% Distribution percentiles are finite (serving cell has guaranteed signal)
verifyTrue(tc, isfinite(T.rsrp_p50_dbm(1)));
verifyTrue(tc, isfinite(T.sinr_p50_db(1)));
verifyTrue(tc, isfinite(T.rsrq_p50_db(1)));
% Monotone p10 <= p50 <= p90
verifyLessThanOrEqual(tc, T.rsrp_p10_dbm(1), T.rsrp_p50_dbm(1));
verifyLessThanOrEqual(tc, T.rsrp_p50_dbm(1), T.rsrp_p90_dbm(1));
verifyLessThanOrEqual(tc, T.sinr_p10_db(1),  T.sinr_p50_db(1));
verifyLessThanOrEqual(tc, T.sinr_p50_db(1),  T.sinr_p90_db(1));
end


function test_sweep_picks_up_ho_param_changes(tc)
% Higher TTT (1024 ms) should reduce HO_ATTEMPT count vs short TTT
% (256 ms) at the same hyst, seed, duration. This is the same
% qualitative trend Phase 2 already verified, just exercised through
% the sweep_static dispatch path.
opts = struct( ...
    'ttt_ms_grid',    [256, 1024], ...
    'hyst_db_grid',   [2], ...
    'a3_off_db_grid', [3], ...
    'seeds',          [7], ...
    'n_ue',           4, ...
    'duration_s',     30, ...
    'use_parfor',     false);
out_dir = fullfile(tc.TestData.tmp, 'out_ttt');
sweep_static(string(out_dir), opts);
T = parquetread(fullfile(out_dir, 'sweep_config_perf.parquet'));

n_short = T.ho_attempt_count(T.ttt_ms == 256);
n_long  = T.ho_attempt_count(T.ttt_ms == 1024);
verifyEqual(tc, numel(n_short), 1);
verifyEqual(tc, numel(n_long),  1);
verifyLessThanOrEqual(tc, n_long, n_short, ...
    'TTT=1024 ms should produce <= HO_ATTEMPT than TTT=256 ms');
end
