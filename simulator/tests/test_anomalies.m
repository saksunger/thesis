function tests = test_anomalies
%TEST_ANOMALIES  Anomaly injection modules + run_phase hook.
tests = functiontests(localfunctions);
end


function setupOnce(tc)
addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));
end


% =========================================================================
% Stand-alone unit tests for each anomaly module
% =========================================================================

function test_rlf_burst_drops_sinr_inside_window(tc)
meas   = mk_meas(60, 7);
t_mask = mk_window(meas.t_s, 10, 20);
before_sinr = meas.sinr_db;

a = struct('delta_db', -15);
out = anomalies.rlf_burst(meas, t_mask, a);

% Inside window: SINR dropped by exactly delta_db
verifyEqual(tc, out.sinr_db(t_mask, :), before_sinr(t_mask, :) - 15, ...
    'AbsTol', 1e-9);
% Outside window: unchanged
verifyEqual(tc, out.sinr_db(~t_mask, :), before_sinr(~t_mask, :));
% RSRQ also dropped (correlated)
verifyTrue(tc, all(out.rsrq_db(t_mask, :) < meas.rsrq_db(t_mask, :), 'all'));
end


function test_meas_glitch_freezes_rsrp(tc)
meas = mk_meas(60, 7);
t_mask = mk_window(meas.t_s, 5, 15);

a = struct('stuck_value_dbm', -95);
out = anomalies.meas_glitch(meas, t_mask, a);

vals_in = out.rsrp_dbm(t_mask, :);
verifyTrue(tc, all(vals_in(:) == -95), 'all in-window values should equal stuck value');
% Outside: unchanged
verifyEqual(tc, out.rsrp_dbm(~t_mask, :), meas.rsrp_dbm(~t_mask, :));
end


function test_meas_glitch_default_freezes_first_tick(tc)
meas = mk_meas(60, 7);
t_mask = mk_window(meas.t_s, 5, 15);
idx_first = find(t_mask, 1, 'first');
expected_per_cell = meas.rsrp_dbm(idx_first, :);

a = struct('stuck_value_dbm', NaN);   % NaN -> freeze
out = anomalies.meas_glitch(meas, t_mask, a);

n_in = sum(t_mask);
verifyEqual(tc, out.rsrp_dbm(t_mask, :), repmat(expected_per_cell, n_in, 1));
end


function test_slow_degrade_linear_ramp(tc)
meas = mk_meas(60, 7);
t_mask = mk_window(meas.t_s, 10, 60);
before = meas.sinr_db;

a = struct('rate_db_per_s', -0.5, 't_start_s', 10, 't_end_s', 60);
out = anomalies.slow_degrade(meas, t_mask, meas.t_s, a);

% At t=10s -> 0 dB drop; at t=60s -> -25 dB drop
delta = out.sinr_db - before;
% First in-window tick should be ~0
idx_first = find(t_mask, 1, 'first');
verifyEqual(tc, delta(idx_first, :), zeros(1, size(before, 2)), 'AbsTol', 1e-9);
% Last in-window tick
idx_last = find(t_mask, 1, 'last');
expected_drop = -0.5 * (meas.t_s(idx_last) - 10);
verifyEqual(tc, delta(idx_last, :), repmat(expected_drop, 1, size(before, 2)), ...
    'AbsTol', 1e-9);
% Outside: unchanged
verifyEqual(tc, delta(~t_mask, :), zeros(sum(~t_mask), size(before, 2)));
end


function test_interference_spike_targets_specific_cells(tc)
[meas, cells] = mk_meas_with_cells(60, 7);
t_mask = mk_window(meas.t_s, 0, 30);
target_cells = [cells(2).id, cells(5).id];

a = struct('delta_db', -10, 'affected_cell_ids', target_cells);
before = meas.sinr_db;
out = anomalies.interference_spike(meas, t_mask, cells, a);

% Targeted cells in window: dropped
col_idx = ismember([cells.id], target_cells);
verifyEqual(tc, out.sinr_db(t_mask, col_idx), before(t_mask, col_idx) - 10, ...
    'AbsTol', 1e-9);
% Non-targeted cells in window: unchanged
verifyEqual(tc, out.sinr_db(t_mask, ~col_idx), before(t_mask, ~col_idx));
% Outside window: all unchanged
verifyEqual(tc, out.sinr_db(~t_mask, :), before(~t_mask, :));
end


function test_interference_spike_empty_cell_list_hits_all(tc)
[meas, cells] = mk_meas_with_cells(30, 5);
t_mask = mk_window(meas.t_s, 0, 10);
before = meas.sinr_db;

a = struct('delta_db', -5, 'affected_cell_ids', []);
out = anomalies.interference_spike(meas, t_mask, cells, a);

verifyEqual(tc, out.sinr_db(t_mask, :), before(t_mask, :) - 5, 'AbsTol', 1e-9);
end


% =========================================================================
% apply_all dispatcher tests
% =========================================================================

function test_apply_all_filters_by_ue_id(tc)
[meas, cells] = mk_meas_with_cells(30, 5);
ue_track = mk_track(meas.t_s);

% Anomaly affects only UE 7. We call apply_all for UE 1 -> no-op.
a = struct('type', "rlf_burst", 't_start_s', 0, 't_end_s', 30, ...
           'affected_ue_ids', [7], 'delta_db', -15);
out = anomalies.apply_all(meas, ue_track, 1, cells, {a});
verifyEqual(tc, out.sinr_db, meas.sinr_db, ...
    'UE 1 must not be affected by an anomaly scoped to UE 7');
end


function test_apply_all_dispatches_multiple_types(tc)
[meas, cells] = mk_meas_with_cells(60, 5);
ue_track = mk_track(meas.t_s);

a1 = struct('type', "rlf_burst",        't_start_s',  5, 't_end_s', 10, ...
            'affected_ue_ids', [1], 'delta_db', -15);
a2 = struct('type', "meas_glitch",      't_start_s', 20, 't_end_s', 30, ...
            'affected_ue_ids', [1], 'stuck_value_dbm', -90);
out = anomalies.apply_all(meas, ue_track, 1, cells, {a1, a2});

% Window 1: SINR dropped
m1 = mk_window(meas.t_s, 5, 10);
verifyTrue(tc, all(out.sinr_db(m1, :) < meas.sinr_db(m1, :), 'all'));
% Window 2: RSRP frozen
m2 = mk_window(meas.t_s, 20, 30);
vals = out.rsrp_dbm(m2, :);
verifyTrue(tc, all(vals(:) == -90));
end


function test_apply_all_empty_ue_list_hits_all(tc)
[meas, cells] = mk_meas_with_cells(20, 5);
ue_track = mk_track(meas.t_s);

a = struct('type', "rlf_burst", 't_start_s', 0, 't_end_s', 10, ...
           'affected_ue_ids', [], 'delta_db', -10);
for ue_id = 1:5
    out = anomalies.apply_all(meas, ue_track, ue_id, cells, {a});
    m = mk_window(meas.t_s, 0, 10);
    verifyEqual(tc, out.sinr_db(m, :), meas.sinr_db(m, :) - 10, 'AbsTol', 1e-9, ...
        sprintf('UE %d should be affected when affected_ue_ids is empty', ue_id));
end
end


function test_apply_all_unknown_type_errors(tc)
[meas, cells] = mk_meas_with_cells(10, 5);
ue_track = mk_track(meas.t_s);

a = struct('type', "totally_made_up", 't_start_s', 0, 't_end_s', 5, ...
           'affected_ue_ids', []);
verifyError(tc, ...
    @() anomalies.apply_all(meas, ue_track, 1, cells, {a}), ...
    'apply_all:unknown_type');
end


% =========================================================================
% Integration: run_phase with an anomaly produces visible effect
% =========================================================================

function test_run_phase_with_rlf_burst_produces_lower_sinr(tc)
spec  = scenarios.baseline(struct('n_ue', 3, 'duration_s', 30, 'master_seed', 100));
% Baseline reference
[s_ref, ~] = utils.run_phase(spec);

% Same spec with an A-1 burst injected on UE 1, window [10, 20]s
spec.anomalies = { struct( ...
    'type',              "rlf_burst", ...
    't_start_s',         10, ...
    't_end_s',           20, ...
    'affected_ue_ids',   [1], ...
    'delta_db',          -15) };
[s_anom, ~] = utils.run_phase(spec);

% UE 1 inside window: median SINR strictly lower
ue1_ref  = s_ref(s_ref.ue_id == 1   & s_ref.time_s  >= 10 & s_ref.time_s  < 20, :);
ue1_anom = s_anom(s_anom.ue_id == 1 & s_anom.time_s >= 10 & s_anom.time_s < 20, :);
verifyLessThan(tc, median(ue1_anom.sinr_serving_db), ...
                   median(ue1_ref.sinr_serving_db) - 5, ...
    'UE 1 in-window SINR must drop significantly when RLF burst is injected');

% UE 2 same window: unaffected
ue2_ref  = s_ref(s_ref.ue_id == 2   & s_ref.time_s >= 10 & s_ref.time_s < 20, :);
ue2_anom = s_anom(s_anom.ue_id == 2 & s_anom.time_s >= 10 & s_anom.time_s < 20, :);
verifyEqual(tc, ue2_anom.sinr_serving_db, ue2_ref.sinr_serving_db, ...
    'AbsTol', 1e-9, ...
    'UE 2 must not see UE 1''s anomaly');
end


function test_run_phase_with_meas_glitch_flat_lines_rsrp(tc)
spec = scenarios.baseline(struct('n_ue', 2, 'duration_s', 30, 'master_seed', 42));
spec.anomalies = { struct( ...
    'type',              "meas_glitch", ...
    't_start_s',         10, ...
    't_end_s',           20, ...
    'affected_ue_ids',   [1], ...
    'stuck_value_dbm',   -95) };
[samples, ~] = utils.run_phase(spec);

ue1_in = samples(samples.ue_id == 1 & samples.time_s >= 10 & samples.time_s < 20, :);
% Serving cell RSRP may differ from -95 because serving = argmax across
% L3-filtered RSRPs and the glitch hits unfiltered values. Verify variance
% collapses (flat-lined input -> flat or near-flat L3 output).
verifyLessThan(tc, std(ue1_in.rsrp_serving_dbm), 1.0, ...
    'Stuck-at glitch should collapse RSRP variance in window');
end


% =========================================================================
% Helpers
% =========================================================================

function meas = mk_meas(T_sec, K_cells)
c    = utils.constants();
t_s  = (0:c.sim_dt_s:T_sec).';
T    = numel(t_s);
% Synthetic deterministic meas: ramp + offset per cell
rsrp = -80 - 10 * (1:K_cells) - 0.01 * t_s;
sinr =  10 - 0.5 * (1:K_cells) - 0.01 * t_s;
rsrq = -10 -        (1:K_cells) - 0.01 * t_s;
meas = struct();
meas.t_s      = t_s;
meas.rsrp_dbm = rsrp .* ones(T, 1);
meas.sinr_db  = sinr .* ones(T, 1);
meas.rsrq_db  = rsrq .* ones(T, 1);
end


function [meas, cells] = mk_meas_with_cells(T_sec, K_cells)
meas = mk_meas(T_sec, K_cells);
cells = struct('id', num2cell(1:K_cells).');
end


function m = mk_window(t_s, t0, t1)
m = (t_s >= t0) & (t_s < t1);
end


function tr = mk_track(t_s)
tr = struct();
tr.t_s   = t_s;
tr.x_m   = zeros(size(t_s));
tr.y_m   = zeros(size(t_s));
tr.v_mps = zeros(size(t_s));
end
