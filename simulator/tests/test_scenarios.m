function tests = test_scenarios
%TEST_SCENARIOS  Phase-spec builders + override mechanism.
tests = functiontests(localfunctions);
end


function setupOnce(tc)
addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));
end


function test_baseline_has_required_fields(tc)
spec = scenarios.baseline();
required_top = {'scenario_name', 'is_drift', 'is_anomaly', ...
                'n_ue', 'area_m', 'duration_s', 'master_seed', 'phase_id', ...
                'layout_params', 'meas_params', 'ho_params', 'anomalies'};
for f = required_top
    verifyTrue(tc, isfield(spec, f{1}), ...
        sprintf('baseline missing top field: %s', f{1}));
end
verifyEqual(tc, spec.scenario_name, "baseline");
verifyFalse(tc, spec.is_drift);
verifyEqual(tc, spec.meas_params.scenario, "UMa");
verifyEqual(tc, spec.layout_params.h_bs_m, 25);
verifyEqual(tc, spec.anomalies, {});
end


function test_drift_channel_swap_switches_to_umi(tc)
spec = scenarios.drift_channel_swap();
verifyEqual(tc, spec.scenario_name, "drift_channel_swap");
verifyTrue(tc, spec.is_drift);
verifyEqual(tc, spec.drift_id, "D-2");
verifyEqual(tc, spec.meas_params.scenario, "UMi");
verifyEqual(tc, spec.layout_params.h_bs_m, 10);
verifyEqual(tc, spec.meas_params.h_bs_m,   10);
verifyEqual(tc, spec.meas_params.sigma_nlos_db, 7.82);
end


function test_overrides_apply_at_top_level(tc)
spec = scenarios.baseline(struct('n_ue', 24, 'duration_s', 120));
verifyEqual(tc, spec.n_ue, 24);
verifyEqual(tc, spec.duration_s, 120);
% other fields unchanged
verifyEqual(tc, spec.area_m, 1500);
end


function test_overrides_apply_at_nested_level(tc)
spec = scenarios.baseline(struct( ...
    'meas_params', struct('scenario', "UMi"), ...
    'ho_params',   struct('ttt_s', 0.512)));
verifyEqual(tc, spec.meas_params.scenario, "UMi");
verifyEqual(tc, spec.ho_params.ttt_s, 0.512);
% sibling fields unchanged
verifyEqual(tc, spec.meas_params.fc_ghz, 3.5);
verifyEqual(tc, spec.ho_params.hyst_db, 2);
end


function test_unknown_override_field_errors(tc)
verifyError(tc, ...
    @() scenarios.baseline(struct('this_does_not_exist', 1)), ...
    'apply_overrides:unknown_field');
verifyError(tc, ...
    @() scenarios.baseline(struct('meas_params', struct('nope', 1))), ...
    'apply_overrides:unknown_subfield');
end


% -------------------------------------------------------------------------
% Phase 4 Iter B drift scenarios
% -------------------------------------------------------------------------

function test_drift_traffic_shift_defaults(tc)
spec = scenarios.drift_traffic_shift();
verifyEqual(tc, spec.scenario_name, "drift_traffic_shift");
verifyTrue(tc, spec.is_drift);
verifyEqual(tc, spec.drift_id, "D-1");
verifyGreaterThan(tc, spec.n_ue, 12, ...
    'D-1 must bump n_ue above the baseline default 12');
end


function test_drift_traffic_shift_overrides_n_ue(tc)
spec = scenarios.drift_traffic_shift(struct('n_ue', 50));
verifyEqual(tc, spec.n_ue, 50);
verifyEqual(tc, spec.drift_id, "D-1");
end


function test_drift_mobility_changes_speed(tc)
spec = scenarios.drift_mobility();
verifyEqual(tc, spec.scenario_name, "drift_mobility");
verifyTrue(tc, spec.is_drift);
verifyEqual(tc, spec.drift_id, "D-3");
base = scenarios.baseline();
verifyGreaterThan(tc, spec.speed_mps, base.speed_mps, ...
    'D-3 must increase speed_mps above baseline');
end


function test_drift_mobility_speed_override(tc)
spec = scenarios.drift_mobility(struct('speed_mps', 33));
verifyEqual(tc, spec.speed_mps, 33);
end


function test_drift_reconfig_changes_ho_params(tc)
spec = scenarios.drift_reconfig();
verifyEqual(tc, spec.scenario_name, "drift_reconfig");
verifyEqual(tc, spec.drift_id, "D-4");
base = scenarios.baseline();
verifyNotEqual(tc, spec.ho_params.ttt_s, base.ho_params.ttt_s, ...
    'D-4 must change TTT relative to baseline');
verifyEqual(tc, spec.meas_params.scenario, base.meas_params.scenario, ...
    'D-4 changes RRM only — radio scenario must match baseline');
end


function test_drift_reconfig_nested_override(tc)
spec = scenarios.drift_reconfig(struct( ...
    'ho_params', struct('hyst_db', 6, 'a3_offset_db', 3)));
verifyEqual(tc, spec.ho_params.hyst_db, 6);
verifyEqual(tc, spec.ho_params.a3_offset_db, 3);
end
