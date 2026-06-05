function tests = test_l3_filter
%TEST_L3_FILTER  Unit tests for `ho.l3_filter`.
tests = functiontests(localfunctions);
end

function setupOnce(testCase) %#ok<INUSD>
addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));
end

function s = mk_state(K, init_val)
    s = struct();
    s.rsrp_l3_dbm = init_val * ones(K, 1);
    s.rsrq_l3_db  = init_val * ones(K, 1);
    s.sinr_l3_db  = init_val * ones(K, 1);
end

function test_alpha_one_passes_raw(tc)
% alpha = 1 → filter output equals raw input
s = mk_state(3, -100);
raw = [-80, -85, -90];
s = ho.l3_filter(s, raw, raw, raw, 1.0);
verifyEqual(tc, s.rsrp_l3_dbm.', raw, 'AbsTol', 1e-9);
end

function test_alpha_small_low_pass(tc)
% Small alpha → output close to previous filter value
s = mk_state(3, -100);
raw = [-80, -85, -90];
s = ho.l3_filter(s, raw, raw, raw, 0.1);
expected = 0.9 * (-100) + 0.1 * raw;
verifyEqual(tc, s.rsrp_l3_dbm.', expected, 'AbsTol', 1e-9);
end

function test_convergence_to_step_input(tc)
% Apply constant input M for N steps; output should converge geometrically:
%   F_N = M - (M - F_0) * (1 - alpha)^N
s = mk_state(1, -100);
M = -80;
alpha = 0.5;
for n = 1:20
    s = ho.l3_filter(s, M, M, M, alpha);
end
expected = M - (M - (-100)) * (1 - alpha)^20;
verifyEqual(tc, s.rsrp_l3_dbm, expected, 'AbsTol', 1e-6);
end

function test_nan_skip(tc)
% NaN samples should not corrupt the filter state.
s = mk_state(3, -90);
raw = [NaN, -85, -80];
s = ho.l3_filter(s, raw, raw, raw, 0.5);
% cell 1 unchanged, cells 2/3 updated
verifyEqual(tc, s.rsrp_l3_dbm(1), -90, 'AbsTol', 1e-9);
verifyEqual(tc, s.rsrp_l3_dbm(2), 0.5*-90 + 0.5*-85, 'AbsTol', 1e-9);
verifyEqual(tc, s.rsrp_l3_dbm(3), 0.5*-90 + 0.5*-80, 'AbsTol', 1e-9);
end
