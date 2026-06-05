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
