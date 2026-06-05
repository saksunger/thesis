function tests = test_a3_evaluate
%TEST_A3_EVALUATE  Unit tests for `ho.a3_evaluate`.
tests = functiontests(localfunctions);
end

function setupOnce(testCase) %#ok<INUSD>
addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));
end

function s = mk_state(rsrp_dbm, serving_idx)
    K = numel(rsrp_dbm);
    s = struct();
    s.rsrp_l3_dbm   = rsrp_dbm(:);
    s.serving_idx   = serving_idx;
    s.ttt_elapsed_s = zeros(K, 1);
end

function p = mk_params(ttt_s, hyst_db, a3_off_db)
    p = struct('ttt_s', ttt_s, 'hyst_db', hyst_db, 'a3_offset_db', a3_off_db);
end

function test_no_trigger_when_serving_best(tc)
% Serving is highest → no neighbor satisfies A3 entry → no TTT increment
s = mk_state([-80, -90, -95, -100], 1);
p = mk_params(0.256, 2, 0);
[s, fired] = ho.a3_evaluate(s, 0.01, p);
verifyEqual(tc, fired, 0);
verifyTrue(tc, all(s.ttt_elapsed_s == 0));
end

function test_ttt_accumulates_then_fires(tc)
% Neighbor 2 is 5 dB better than serving 1 (well above hyst+offset = 2 dB).
% After ceil(0.256/0.01) = 26 ticks of dt=10ms, TTT should fire.
s = mk_state([-90, -85, -95, -100], 1);
p = mk_params(0.256, 2, 0);
fired_at = 0;
for k = 1:30
    [s, fired] = ho.a3_evaluate(s, 0.01, p);
    if fired > 0 && fired_at == 0, fired_at = k; end
end
verifyEqual(tc, fired_at, 26, ...
    sprintf('Expected TTT fire at tick 26, got %d', fired_at));
end

function test_reset_on_condition_drop(tc)
% Condition true for some ticks, then false, must reset TTT to 0
s = mk_state([-90, -85, -95, -100], 1);
p = mk_params(1.0, 2, 0);  % TTT 1 s, won't fire in 20 ticks
% accumulate
for k = 1:20
    [s, ~] = ho.a3_evaluate(s, 0.01, p);
end
verifyTrue(tc, s.ttt_elapsed_s(2) > 0);
% serving suddenly becomes best
s.rsrp_l3_dbm(1) = -50;
[s, fired] = ho.a3_evaluate(s, 0.01, p);
verifyEqual(tc, fired, 0);
verifyEqual(tc, s.ttt_elapsed_s(2), 0, 'AbsTol', 1e-9);
end

function test_hysteresis_blocks_marginal(tc)
% Neighbor only 1 dB better than serving, hyst=2 → condition fails
s = mk_state([-90, -89, -95, -100], 1);
p = mk_params(0.256, 2, 0);
[s, fired] = ho.a3_evaluate(s, 0.01, p);
verifyEqual(tc, fired, 0);
verifyTrue(tc, all(s.ttt_elapsed_s == 0));
end

function test_a3_offset_lifts_threshold(tc)
% Neighbor 3 dB better than serving, but A3 offset = 5 → condition fails
s = mk_state([-90, -87, -95, -100], 1);
p = mk_params(0.256, 0, 5);
[s, fired] = ho.a3_evaluate(s, 0.01, p);
verifyEqual(tc, fired, 0);
end

function test_negative_a3_offset_lowers_threshold(tc)
% A3 offset = -3 → easier for neighbor to satisfy
s = mk_state([-90, -88, -95, -100], 1);   % neighbor only 2 dB better
p = mk_params(0.256, 0, -3);              % effective threshold lowered by 3
[s, ~] = ho.a3_evaluate(s, 0.01, p);
verifyTrue(tc, s.ttt_elapsed_s(2) > 0);
end
