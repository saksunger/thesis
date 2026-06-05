function tests = test_build_timeline
%TEST_BUILD_TIMELINE  End-to-end timeline builder integration test.
%
% We build a tiny 2-phase timeline (baseline → drift_channel_swap) into a
% temp folder, then verify the four output artifacts (samples, events,
% ground_truth_drift, run_metadata) exist with the expected structure.
tests = functiontests(localfunctions);
end


function setupOnce(tc)
addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));
tc.TestData.tmp = fullfile(tempdir, sprintf('timeline_test_%d', randi(1e9)));
mkdir(tc.TestData.tmp);

% Write a minimal timeline JSON to disk
tl = struct();
tl.timeline_id = "test_tiny";
tl.global = struct('master_seed', 7, 'n_ue', 2, 'area_m', 1000, ...
                   'default_duration_s', 10);
phase1 = struct('phase_id', 1, 'scenario', "baseline",            'params', struct());
phase2 = struct('phase_id', 2, 'scenario', "drift_channel_swap",  'params', struct());
tl.phases = [phase1; phase2];
gtd = struct('drift_id', "D-2", 'phase_id_start', 2, 'phase_id_end', 2, ...
             'affected_kpis', "rsrp_serving_dbm,sinr_serving_db", ...
             'expected_direction', "shape", 'note', "test drift");
tl.ground_truth_drift = gtd;

tc.TestData.tl_path = fullfile(tc.TestData.tmp, 'tl.json');
fid = fopen(tc.TestData.tl_path, 'w');
fprintf(fid, '%s\n', jsonencode(tl, 'PrettyPrint', true));
fclose(fid);
end


function teardownOnce(tc)
if isfield(tc.TestData, 'tmp') && exist(tc.TestData.tmp, 'dir')
    rmdir(tc.TestData.tmp, 's');
end
end


function test_build_creates_all_four_artifacts(tc)
out_dir = fullfile(tc.TestData.tmp, 'out');
build_timeline(string(tc.TestData.tl_path), string(out_dir));

for name = ["samples.parquet", "events.parquet", ...
            "ground_truth_drift.parquet", "run_metadata.json"]
    fp = fullfile(out_dir, char(name));
    verifyTrue(tc, isfile(fp), sprintf('missing artifact: %s', name));
end

samples = parquetread(fullfile(out_dir, 'samples.parquet'));
% Two phases × 2 UEs × T_per_ue samples
c = utils.constants();
T_per_ue = round(10 / c.sim_dt_s) + 1;
verifyEqual(tc, height(samples), 2 * 2 * T_per_ue);
verifyEqual(tc, sort(unique(samples.phase_id))', int32([1 2]));
end


function test_event_ids_unique_across_phases(tc)
out_dir = fullfile(tc.TestData.tmp, 'out2');
build_timeline(string(tc.TestData.tl_path), string(out_dir));
events = parquetread(fullfile(out_dir, 'events.parquet'));
if height(events) > 0
    verifyEqual(tc, numel(unique(events.event_id)), height(events), ...
        'event_id values must be unique across the whole timeline');
end
end


function test_time_offset_applied_between_phases(tc)
out_dir = fullfile(tc.TestData.tmp, 'out3');
build_timeline(string(tc.TestData.tl_path), string(out_dir));
samples = parquetread(fullfile(out_dir, 'samples.parquet'));

ts1 = samples.time_s(samples.phase_id == 1);
ts2 = samples.time_s(samples.phase_id == 2);
verifyLessThanOrEqual(tc, max(ts1), 10 + 1e-6);
verifyGreaterThanOrEqual(tc, min(ts2), 10 - 1e-6);
verifyLessThanOrEqual(tc, max(ts2), 20 + 1e-6);
end


function test_ground_truth_time_bounds_align(tc)
out_dir = fullfile(tc.TestData.tmp, 'out4');
build_timeline(string(tc.TestData.tl_path), string(out_dir));
gtd = parquetread(fullfile(out_dir, 'ground_truth_drift.parquet'));
verifyEqual(tc, height(gtd), 1);
verifyEqual(tc, gtd.drift_id(1),       "D-2");
verifyEqual(tc, gtd.start_time_s(1),   10, 'AbsTol', 1e-9);
verifyEqual(tc, gtd.end_time_s(1),     20, 'AbsTol', 1e-9);
end


% -------------------------------------------------------------------------
% Iter B: ground_truth_anomaly routing + parquet artifact
% -------------------------------------------------------------------------

function test_anomaly_routed_and_visible_in_samples(tc)
% Write an extended timeline JSON with one anomaly in phase 2.
tl = struct();
tl.timeline_id = "test_anom";
tl.global = struct('master_seed', 11, 'n_ue', 3, 'area_m', 1000, ...
                   'default_duration_s', 10);
phase1 = struct('phase_id', 1, 'scenario', "baseline", 'params', struct());
phase2 = struct('phase_id', 2, 'scenario', "baseline", 'params', struct());
tl.phases = [phase1; phase2];
tl.ground_truth_drift = [];

anom = struct( ...
    'anomaly_id',         "A-1", ...
    'type',               "rlf_burst", ...
    'phase_id',           2, ...
    't_start_s',          2, ...
    't_end_s',            7, ...
    'affected_ue_ids',    [1], ...
    'delta_db',           -15, ...
    'note',               "test burst");
tl.ground_truth_anomaly = anom;

tl_path = fullfile(tc.TestData.tmp, 'tl_anom.json');
fid = fopen(tl_path, 'w');
fprintf(fid, '%s\n', jsonencode(tl, 'PrettyPrint', true));
fclose(fid);

out_dir = fullfile(tc.TestData.tmp, 'out_anom');
build_timeline(string(tl_path), string(out_dir));

% Artifact exists
gta_fp = fullfile(out_dir, 'ground_truth_anomaly.parquet');
verifyTrue(tc, isfile(gta_fp), 'ground_truth_anomaly.parquet must exist');

gta = parquetread(gta_fp);
verifyEqual(tc, height(gta), 1);
verifyEqual(tc, gta.anomaly_id(1),   "A-1");
verifyEqual(tc, gta.anomaly_type(1), "rlf_burst");
% Phase 2 starts at t=10 globally; anomaly t_start=2 -> global 12
verifyEqual(tc, gta.t_start_s(1), 12, 'AbsTol', 1e-9);
verifyEqual(tc, gta.t_end_s(1),   17, 'AbsTol', 1e-9);

% Samples: UE 1 in [12, 17) should have lower SINR than UE 2 in same window
samples = parquetread(fullfile(out_dir, 'samples.parquet'));
in_win  = samples.time_s >= 12 & samples.time_s < 17;
ue1_med = median(samples.sinr_serving_db(in_win & samples.ue_id == 1));
ue2_med = median(samples.sinr_serving_db(in_win & samples.ue_id == 2));
verifyLessThan(tc, ue1_med, ue2_med - 3, ...
    'UE 1 SINR must be visibly lower than UE 2 inside the burst window');
end


% -------------------------------------------------------------------------
% Iter C: UE state carry-over flag in global config
% -------------------------------------------------------------------------

function test_carry_over_ues_flag_threads_state_across_phases(tc)
% Build a 2-phase timeline with carry_over_ues=true. Verify that the
% first sample of phase 2 for each UE equals the last sample of phase 1
% (when carry-over fires; UEs whose phase-1 endpoint left the area box
% are random-re-init'd and skipped from this assertion).
%
% NOTE: scenarios.baseline overrides only support fields that exist in
% the spec; `speed_mps` exists but `area_m` is set at the timeline-global
% level (not per-phase). Keep area generous + speed/duration small so
% every UE stays inside the box for deterministic position equality.
tl = struct();
tl.timeline_id = "test_carryover";
tl.global = struct('master_seed', 13, 'n_ue', 3, 'area_m', 800, ...
                   'default_duration_s', 5, 'carry_over_ues', true);
phase1 = struct('phase_id', 1, 'scenario', "baseline", ...
                'params', struct('speed_mps', 2));
phase2 = struct('phase_id', 2, 'scenario', "baseline", ...
                'params', struct('speed_mps', 2));
tl.phases = [phase1; phase2];
tl.ground_truth_drift   = [];
tl.ground_truth_anomaly = [];

tl_path = fullfile(tc.TestData.tmp, 'tl_carry.json');
fid = fopen(tl_path, 'w');
fprintf(fid, '%s\n', jsonencode(tl, 'PrettyPrint', true));
fclose(fid);

out_dir = fullfile(tc.TestData.tmp, 'out_carry');
build_timeline(string(tl_path), string(out_dir));

samples = parquetread(fullfile(out_dir, 'samples.parquet'));

n_carried = 0;
for ue = 1:3
    s_p1 = samples(samples.phase_id == 1 & samples.ue_id == ue, :);
    s_p2 = samples(samples.phase_id == 2 & samples.ue_id == ue, :);
    s_p1 = sortrows(s_p1, 'time_s');
    s_p2 = sortrows(s_p2, 'time_s');
    if abs(s_p1.ue_x_m(end)) <= 800 && abs(s_p1.ue_y_m(end)) <= 800
        verifyEqual(tc, s_p2.ue_x_m(1), s_p1.ue_x_m(end), 'AbsTol', 1e-6, ...
            sprintf('UE %d phase-2 start x should equal phase-1 end x', ue));
        verifyEqual(tc, s_p2.ue_y_m(1), s_p1.ue_y_m(end), 'AbsTol', 1e-6, ...
            sprintf('UE %d phase-2 start y should equal phase-1 end y', ue));
        n_carried = n_carried + 1;
    end
end
% At least one UE must have carried over (else the test is vacuous).
verifyGreaterThan(tc, n_carried, 0);

% Metadata records the flag
meta = jsondecode(fileread(fullfile(out_dir, 'run_metadata.json')));
verifyTrue(tc, meta.carry_over_ues);
end


function test_carry_over_ues_default_is_false(tc)
% Without the flag, behaviour is unchanged (each phase resets UE positions
% randomly per seed). Metadata reflects the default.
tl = struct();
tl.timeline_id = "test_noncarry";
tl.global = struct('master_seed', 19, 'n_ue', 2, 'area_m', 500, ...
                   'default_duration_s', 6);
phase1 = struct('phase_id', 1, 'scenario', "baseline", 'params', struct());
phase2 = struct('phase_id', 2, 'scenario', "baseline", 'params', struct());
tl.phases = [phase1; phase2];

tl_path = fullfile(tc.TestData.tmp, 'tl_nocarry.json');
fid = fopen(tl_path, 'w');
fprintf(fid, '%s\n', jsonencode(tl, 'PrettyPrint', true));
fclose(fid);

out_dir = fullfile(tc.TestData.tmp, 'out_nocarry');
build_timeline(string(tl_path), string(out_dir));

meta = jsondecode(fileread(fullfile(out_dir, 'run_metadata.json')));
verifyFalse(tc, logical(meta.carry_over_ues));

% At least one UE should have a discontinuity at the phase boundary,
% confirming positions did NOT carry over.
samples = parquetread(fullfile(out_dir, 'samples.parquet'));
discontinuous = false;
for ue = 1:2
    s_p1 = samples(samples.phase_id == 1 & samples.ue_id == ue, :);
    s_p2 = samples(samples.phase_id == 2 & samples.ue_id == ue, :);
    s_p1 = sortrows(s_p1, 'time_s');
    s_p2 = sortrows(s_p2, 'time_s');
    gap = norm([s_p2.ue_x_m(1) - s_p1.ue_x_m(end), ...
                s_p2.ue_y_m(1) - s_p1.ue_y_m(end)]);
    if gap > 1   % anything > 1 m is a non-trivial jump
        discontinuous = true;
        break
    end
end
verifyTrue(tc, discontinuous, ...
    'Without carry_over_ues, phase boundaries should show UE position jumps');
end


function test_anomaly_with_unknown_phase_errors(tc)
tl = struct();
tl.timeline_id = "test_bad";
tl.global = struct('master_seed', 1, 'n_ue', 1, 'area_m', 500, ...
                   'default_duration_s', 5);
tl.phases = struct('phase_id', 1, 'scenario', "baseline", 'params', struct());
tl.ground_truth_anomaly = struct( ...
    'anomaly_id', "A-x", 'type', "rlf_burst", 'phase_id', 999, ...
    't_start_s', 0, 't_end_s', 5, 'affected_ue_ids', [], 'delta_db', -5);

tl_path = fullfile(tc.TestData.tmp, 'tl_bad.json');
fid = fopen(tl_path, 'w');
fprintf(fid, '%s\n', jsonencode(tl, 'PrettyPrint', true));
fclose(fid);

out_dir = fullfile(tc.TestData.tmp, 'out_bad');
verifyError(tc, ...
    @() build_timeline(string(tl_path), string(out_dir)), ...
    'build_timeline:bad_anomaly_phase');
end
