function tests = test_rlf_evaluate
%TEST_RLF_EVALUATE  Unit tests for the T310/N310/N311 RLF state machine.
tests = functiontests(localfunctions);
end

function setupOnce(testCase) %#ok<INUSD>
addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));
end

function s = mk_state()
    s = struct( ...
        'rlf_state',        "IDLE", ...
        'n310_count',       0, ...
        'n311_count',       0, ...
        't310_remaining_s', 0);
end

function p = mk_params(qout, qin, n310, n311, t310_s)
    p = struct('rlf_qout_db', qout, 'rlf_qin_db', qin, ...
               'n310', n310, 'n311', n311, 't310_s', t310_s);
end

function test_idle_stays_idle_when_in_sync(tc)
s = mk_state(); p = mk_params(-8, -6, 6, 2, 1.0);
for k = 1:50
    [s, rlf] = ho.rlf_evaluate(s, 0, 0.01, p);  % SINR = 0 dB, in-sync
    verifyFalse(tc, rlf);
end
verifyEqual(tc, s.rlf_state, "IDLE");
end

function test_n310_resets_on_recovery_before_threshold(tc)
% 3 out-of-sync samples, then in-sync → N310 must reset (no T310 entry)
s = mk_state(); p = mk_params(-8, -6, 6, 2, 1.0);
for k = 1:3
    [s, ~] = ho.rlf_evaluate(s, -10, 0.01, p);  % out-of-sync
end
verifyEqual(tc, s.n310_count, 3);
[s, ~] = ho.rlf_evaluate(s, 0, 0.01, p);
verifyEqual(tc, s.n310_count, 0);
verifyEqual(tc, s.rlf_state, "IDLE");
end

function test_t310_starts_after_n310_threshold(tc)
s = mk_state(); p = mk_params(-8, -6, 6, 2, 1.0);
for k = 1:6
    [s, rlf] = ho.rlf_evaluate(s, -10, 0.01, p);
    verifyFalse(tc, rlf);
end
verifyEqual(tc, s.rlf_state, "T310_RUNNING");
verifyEqual(tc, s.t310_remaining_s, 1.0, 'AbsTol', 1e-9);
end

function test_t310_recovers_after_n311_in_sync(tc)
s = mk_state(); p = mk_params(-8, -6, 6, 2, 1.0);
for k = 1:6,  [s, ~] = ho.rlf_evaluate(s, -10, 0.01, p); end
verifyEqual(tc, s.rlf_state, "T310_RUNNING");
% deliver 2 in-sync samples → recover
for k = 1:2,  [s, ~] = ho.rlf_evaluate(s, 0, 0.01, p); end
verifyEqual(tc, s.rlf_state, "IDLE");
verifyEqual(tc, s.n310_count, 0);
end

function test_t310_expires_to_rlf(tc)
s = mk_state(); p = mk_params(-8, -6, 6, 2, 0.10);  % short T310 for test
for k = 1:6,  [s, ~] = ho.rlf_evaluate(s, -10, 0.01, p); end
verifyEqual(tc, s.rlf_state, "T310_RUNNING");
% keep out-of-sync for >100 ms (>= 10 ticks of 10 ms)
fired = false;
for k = 1:15
    [s, rlf] = ho.rlf_evaluate(s, -10, 0.01, p);
    if rlf, fired = true; break; end
end
verifyTrue(tc, fired, 'Expected RLF to be declared after T310 expiry');
verifyEqual(tc, s.rlf_state, "IDLE");
verifyEqual(tc, s.n310_count, 0);
end
