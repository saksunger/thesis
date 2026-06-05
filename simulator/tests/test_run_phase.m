function tests = test_run_phase
%TEST_RUN_PHASE  Single-phase runner integration test.
tests = functiontests(localfunctions);
end


function setupOnce(tc)
addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));
end


function test_baseline_phase_produces_samples_and_events(tc)
spec = scenarios.baseline(struct('n_ue', 3, 'duration_s', 30));
[samples, events] = utils.run_phase(spec);

% sample count = n_ue × T (T = duration / dt + 1)
c = utils.constants();
T_per_ue = round(spec.duration_s / c.sim_dt_s) + 1;
verifyEqual(tc, height(samples), spec.n_ue * T_per_ue);

% required canonical columns
required = {'ue_id', 'time_s', 'serving_cell_id', 'neighbor_cell_id', ...
            'rsrp_serving_dbm', 'sinr_serving_db', 'rsrq_serving_db', ...
            'phase_id', 'scenario_name'};
for f = required
    verifyTrue(tc, ismember(f{1}, samples.Properties.VariableNames), ...
        sprintf('samples missing column: %s', f{1}));
end

% phase_id + scenario_name tagged
verifyTrue(tc, all(samples.phase_id == int32(spec.phase_id)));
verifyTrue(tc, all(samples.scenario_name == "baseline"));

% time ranges 0..duration_s for each UE
for ue = unique(samples.ue_id).'
    s = samples(samples.ue_id == ue, :);
    verifyEqual(tc, s.time_s(1),   0,                'AbsTol', 1e-9);
    verifyEqual(tc, s.time_s(end), spec.duration_s,  'AbsTol', 1e-3);
end

% events table is either empty or has the expected columns
if height(events) > 0
    for f = {'event_id', 'event_time_s', 'event_type', 'ue_id', ...
             'source_cell_id', 'target_cell_id', 'cfg_ttt_ms', 'cfg_hyst_db'}
        verifyTrue(tc, ismember(f{1}, events.Properties.VariableNames), ...
            sprintf('events missing column: %s', f{1}));
    end
    verifyTrue(tc, all(events.phase_id == int32(spec.phase_id)));
end
end


function test_drift_phase_has_umi_metadata(tc)
spec = scenarios.drift_channel_swap(struct('n_ue', 2, 'duration_s', 10));
[samples, ~] = utils.run_phase(spec);
verifyTrue(tc, all(samples.meta_channel_scenario == "UMi"));
verifyTrue(tc, all(samples.scenario_name == "drift_channel_swap"));
end


function test_seeds_are_deterministic(tc)
% Same spec → same samples (bit-identical).
spec = scenarios.baseline(struct('n_ue', 2, 'duration_s', 10, 'master_seed', 777));
[s1, ~] = utils.run_phase(spec);
[s2, ~] = utils.run_phase(spec);
verifyEqual(tc, s1.rsrp_serving_dbm, s2.rsrp_serving_dbm);
verifyEqual(tc, s1.sinr_serving_db,  s2.sinr_serving_db);
end


function test_different_seeds_produce_different_samples(tc)
spec1 = scenarios.baseline(struct('n_ue', 2, 'duration_s', 10, 'master_seed', 1));
spec2 = scenarios.baseline(struct('n_ue', 2, 'duration_s', 10, 'master_seed', 2));
[s1, ~] = utils.run_phase(spec1);
[s2, ~] = utils.run_phase(spec2);
verifyFalse(tc, isequal(s1.rsrp_serving_dbm, s2.rsrp_serving_dbm));
end


% -------------------------------------------------------------------------
% Iter C: UE state carry-over across phases
% -------------------------------------------------------------------------

function test_run_phase_returns_ue_state_out(tc)
% Without carry-over input, the function still returns a populated
% ue_state_out cell array of length n_ue.
spec = scenarios.baseline(struct('n_ue', 4, 'duration_s', 10));
[~, ~, st] = utils.run_phase(spec);

verifyEqual(tc, numel(st), 4);
for k = 1:numel(st)
    verifyClass(tc, st{k}, 'struct');
    for f = {'x_end_m', 'y_end_m', 'theta_rad', 'speed_mps_last'}
        verifyTrue(tc, isfield(st{k}, f{1}), ...
            sprintf('ue_state_out{%d} missing field %s', k, f{1}));
    end
    verifyTrue(tc, isfinite(st{k}.x_end_m));
    verifyTrue(tc, isfinite(st{k}.y_end_m));
end
end


function test_carry_over_continues_ue_position(tc)
% Phase 1 runs alone -> capture end-of-phase positions. Phase 2 runs with
% carry-over state -> first sample at end position (provided the endpoint
% is still inside [-area_m, area_m]; pick small duration*speed so this
% holds for every UE deterministically).
spec1 = scenarios.baseline(struct('n_ue', 3, 'duration_s', 5, ...
                                  'speed_mps', 2, 'area_m', 500, ...
                                  'master_seed', 5, 'phase_id', 1));
[s1, ~, st1] = utils.run_phase(spec1);  %#ok<ASGLU>

spec2 = scenarios.baseline(struct('n_ue', 3, 'duration_s', 5, ...
                                  'speed_mps', 2, 'area_m', 500, ...
                                  'master_seed', 5, 'phase_id', 2));
[s2, ~, ~] = utils.run_phase(spec2, st1);

for ue = 1:3
    % Sanity check: carry-over endpoint is inside the area box for this
    % deliberately-small displacement (500 + 2*5 = 510 max).
    verifyLessThanOrEqual(tc, abs(st1{ue}.x_end_m), 510);
    verifyLessThanOrEqual(tc, abs(st1{ue}.y_end_m), 510);

    ue_s2 = s2(s2.ue_id == ue, :);
    if abs(st1{ue}.x_end_m) <= 500 && abs(st1{ue}.y_end_m) <= 500
        % Carry-over applies: phase 2 starts at phase 1's endpoint.
        verifyEqual(tc, ue_s2.ue_x_m(1), st1{ue}.x_end_m, 'AbsTol', 1e-6, ...
            sprintf('UE %d start-of-phase-2 x should match end-of-phase-1', ue));
        verifyEqual(tc, ue_s2.ue_y_m(1), st1{ue}.y_end_m, 'AbsTol', 1e-6, ...
            sprintf('UE %d start-of-phase-2 y should match end-of-phase-1', ue));
    else
        % UE was out-of-area: random re-init; just check finiteness.
        verifyTrue(tc, isfinite(ue_s2.ue_x_m(1)));
        verifyTrue(tc, isfinite(ue_s2.ue_y_m(1)));
    end
end
end


function test_carry_over_displacement_scales_with_speed(tc)
% Same UE start positions (carry-over), different speeds (D-3 mobility
% shift): displacement scales linearly with speed. Note: under the
% Random-Direction mobility model the heading is randomised PER PHASE, so
% we only check the displacement magnitude, not the direction.
spec1 = scenarios.baseline(struct('n_ue', 2, 'duration_s', 5, ...
                                  'speed_mps', 2, 'area_m', 500, ...
                                  'master_seed', 7, 'phase_id', 1));
[~, ~, st1] = utils.run_phase(spec1);

spec2_slow = scenarios.baseline(struct('n_ue', 2, 'duration_s', 5, ...
                                       'speed_mps', 5,  'area_m', 500, ...
                                       'master_seed', 7, 'phase_id', 2));
spec2_fast = scenarios.baseline(struct('n_ue', 2, 'duration_s', 5, ...
                                       'speed_mps', 20, 'area_m', 500, ...
                                       'master_seed', 7, 'phase_id', 2));
[~, ~, st2_slow] = utils.run_phase(spec2_slow, st1);
[~, ~, st2_fast] = utils.run_phase(spec2_fast, st1);

for ue = 1:2
    % Only check displacement if the phase-1 endpoint was inside the box
    % (i.e. carry-over actually fired and we know the new p0).
    if abs(st1{ue}.x_end_m) <= 500 && abs(st1{ue}.y_end_m) <= 500
        disp_slow = norm([st2_slow{ue}.x_end_m - st1{ue}.x_end_m, ...
                          st2_slow{ue}.y_end_m - st1{ue}.y_end_m]);
        disp_fast = norm([st2_fast{ue}.x_end_m - st1{ue}.x_end_m, ...
                          st2_fast{ue}.y_end_m - st1{ue}.y_end_m]);
        verifyEqual(tc, disp_slow,  5 * 5,  'AbsTol', 1e-6);
        verifyEqual(tc, disp_fast, 20 * 5,  'AbsTol', 1e-6);
    end
end
end


function test_new_ues_get_random_init_when_carry_over_state_short(tc)
% Phase 1: 2 UEs. Phase 2: 4 UEs (D-1 traffic-shift style). The 2 extra
% UEs have no prior state -> random init, no error.
spec1 = scenarios.baseline(struct('n_ue', 2, 'duration_s', 5, ...
                                  'speed_mps', 2, 'area_m', 500, ...
                                  'master_seed', 11));
[~, ~, st1] = utils.run_phase(spec1);
verifyEqual(tc, numel(st1), 2);

spec2 = scenarios.baseline(struct('n_ue', 4, 'duration_s', 5, ...
                                  'speed_mps', 2, 'area_m', 500, ...
                                  'master_seed', 11, 'phase_id', 2));
[s2, ~, st2] = utils.run_phase(spec2, st1);
verifyEqual(tc, numel(st2), 4);

% UEs 1 and 2 carry over (assuming endpoint is in-area, which holds for
% the small displacement here); UEs 3 and 4 random-init.
for ue = 1:2
    if abs(st1{ue}.x_end_m) <= 500 && abs(st1{ue}.y_end_m) <= 500
        ue_s2 = s2(s2.ue_id == ue, :);
        verifyEqual(tc, ue_s2.ue_x_m(1), st1{ue}.x_end_m, 'AbsTol', 1e-6);
    end
end
for ue = 3:4
    ue_s2 = s2(s2.ue_id == ue, :);
    verifyTrue(tc, isfinite(ue_s2.ue_x_m(1)));
    verifyTrue(tc, isfinite(ue_s2.ue_y_m(1)));
end
end


function test_carry_over_out_of_area_triggers_reinit(tc)
% Hand-craft a ue_state_in entry whose stored endpoint is OUTSIDE the
% area box. The runner must fall back to random init and the resulting
% first sample must lie inside [-area_m, area_m].
spec = scenarios.baseline(struct('n_ue', 1, 'duration_s', 5, ...
                                 'speed_mps', 2, 'area_m', 500, ...
                                 'master_seed', 3, 'phase_id', 9));
runaway_state = {struct('x_end_m', 5000, 'y_end_m', 5000, ...
                        'theta_rad', 0, 'speed_mps_last', 10)};
[s, ~, ~] = utils.run_phase(spec, runaway_state);

x0 = s.ue_x_m(1);
y0 = s.ue_y_m(1);
% Random init places p0 in [-area, area], not at the runaway endpoint.
verifyLessThanOrEqual(tc, abs(x0), 500);
verifyLessThanOrEqual(tc, abs(y0), 500);
verifyNotEqual(tc, [x0, y0], [5000, 5000]);
end


function test_random_direction_changes_per_phase_with_carry_over(tc)
% Under Random-Direction mobility, even when position carries over, the
% heading should generally differ between consecutive phases (otherwise
% UEs would walk in straight lines across the whole timeline, draining
% out of the cell footprint — Iter C bug discovered when timeline_medium
% showed monotone RSRP degradation).
spec1 = scenarios.baseline(struct('n_ue', 5, 'duration_s', 5, ...
                                  'speed_mps', 2, 'area_m', 500, ...
                                  'master_seed', 42, 'phase_id', 1));
[~, ~, st1] = utils.run_phase(spec1);

spec2 = scenarios.baseline(struct('n_ue', 5, 'duration_s', 5, ...
                                  'speed_mps', 2, 'area_m', 500, ...
                                  'master_seed', 42, 'phase_id', 2));
[~, ~, st2] = utils.run_phase(spec2, st1);

% Compare per-UE thetas; expect at least one to differ.
n_diff = 0;
for ue = 1:5
    if abs(st1{ue}.theta_rad - st2{ue}.theta_rad) > 1e-6
        n_diff = n_diff + 1;
    end
end
verifyGreaterThanOrEqual(tc, n_diff, 4, ...
    'At least 4/5 UEs should have a different theta in phase 2 vs phase 1');
end
