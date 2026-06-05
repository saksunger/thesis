function tests = test_event_loop
%TEST_EVENT_LOOP  Integration tests for `ho.event_loop` with synthetic
%                 measurements (no channel model — full control over inputs).
tests = functiontests(localfunctions);
end

function setupOnce(testCase) %#ok<INUSD>
addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));
end

% Build a minimal cells/track/meas triple for controlled tests.
function [cells, ue_track, meas, params] = mk_scenario(rsrp_serv, rsrp_neigh, sinr_serv, T)
    % 2 cells, K=2. Cell 1 = serving forever (initial best).
    cells = struct('id', {1, 2}, ...
                   'x_m', {0, 100}, 'y_m', {0, 0}, 'z_m', {25, 25}, ...
                   'tier', {0, 1});
    cells = cells(:);
    dt = 0.010;
    ue_track = struct( ...
        't_s',     (0:T-1).' * dt, ...
        'x_m',     zeros(T, 1), 'y_m', zeros(T, 1), ...
        'v_mps',   zeros(T, 1), 'heading_rad', zeros(T, 1));
    meas = struct( ...
        'rsrp_dbm', [rsrp_serv, rsrp_neigh], ...   % T x 2
        'rsrq_db',  [-10*ones(T,1), -12*ones(T,1)], ...
        'sinr_db',  [sinr_serv,  zeros(T, 1)]);
    params = struct( ...
        'ue_id', 1, 'dt_s', dt, ...
        'l3_alpha', 1.0, ...           % no smoothing — easier to reason
        'ttt_s', 0.05, ...             % 5 ticks
        'hyst_db', 2, 'a3_offset_db', 0, ...
        'ho_exec_s', 0.020, ...        % 2 ticks
        'rlf_qout_db', -8, 'rlf_qin_db', -6, ...
        'n310', 6, 'n311', 2, 't310_s', 1.0, ...
        'ping_pong_s', 5.0);
end

function test_no_events_when_no_neighbor_threat(tc)
% Serving stays best for all T ticks → no events.
T = 100;
rsrp_serv  = -70 * ones(T, 1);
rsrp_neigh = -90 * ones(T, 1);
sinr_serv  = 10  * ones(T, 1);
[cells, ue_track, meas, params] = mk_scenario(rsrp_serv, rsrp_neigh, sinr_serv, T);
[events, ~] = ho.event_loop(ue_track, cells, meas, params);
verifyEmpty(tc, events);
end

function test_a3_triggers_ho_attempt_and_success(tc)
% Neighbor much better starting at tick 30 → A3 fires after TTT,
% then HO_SUCCESS after ho_exec_s.
T = 100;
rsrp_serv  = -70 * ones(T, 1);
rsrp_neigh = -90 * ones(T, 1);
rsrp_neigh(30:end) = -60;
sinr_serv  = 10  * ones(T, 1);
[cells, ue_track, meas, params] = mk_scenario(rsrp_serv, rsrp_neigh, sinr_serv, T);
[events, hist] = ho.event_loop(ue_track, cells, meas, params);

types = arrayfun(@(e) e.event_type, events);
verifyEqual(tc, sum(types == "HO_ATTEMPT"), 1);
verifyEqual(tc, sum(types == "HO_SUCCESS"), 1);
verifyEqual(tc, sum(types == "RLF"),        0);

% Condition first becomes true at tick 30 (t = 0.29 s, 0-based clock).
% On that same tick the TTT timer increments to dt; after 5 such ticks
% it reaches ttt_s = 0.05 → HO_ATTEMPT logged at tick 34 (t = 0.33 s).
attempt_t = events(types == "HO_ATTEMPT").event_time_s;
verifyEqual(tc, attempt_t, 0.33, 'AbsTol', 0.015);

% After HO_SUCCESS the serving cell index should be 2
final_serv = hist.serving_idx(end);
verifyEqual(tc, final_serv, 2);
end

function test_rlf_during_ho_marks_ho_fail(tc)
% Force RLF (very bad serving SINR) during HO execution.
T = 200;
rsrp_serv  = -70 * ones(T, 1);
rsrp_neigh = -90 * ones(T, 1);
rsrp_neigh(20:end) = -60;     % triggers A3 around tick 25 + TTT
sinr_serv  = 10  * ones(T, 1);
sinr_serv(20:end) = -20;      % well below Qout → N310 accumulates fast
[cells, ue_track, meas, params] = mk_scenario(rsrp_serv, rsrp_neigh, sinr_serv, T);
% lengthen HO execution so RLF can preempt
params.ho_exec_s = 1.5;
[events, ~] = ho.event_loop(ue_track, cells, meas, params);
types = arrayfun(@(e) e.event_type, events);
verifyGreaterThanOrEqual(tc, sum(types == "HO_FAIL"), 1);
verifyGreaterThanOrEqual(tc, sum(types == "RLF"),     1);
end

function test_ping_pong_detected(tc)
% Hand over to cell 2, then within ping_pong_s = 5 s switch back to cell 1.
T = 1200;   % 12 s @ 10 ms
rsrp_serv  = -70 * ones(T, 1);
rsrp_neigh = -90 * ones(T, 1);
% phase A: neighbor (cell 2) becomes best
rsrp_neigh(50:300) = -60;
% phase B: cell 1 becomes best again (within 5 s of phase-A HO)
rsrp_serv(301:end) = -50;
sinr_serv = 10 * ones(T, 1);
[cells, ue_track, meas, params] = mk_scenario(rsrp_serv, rsrp_neigh, sinr_serv, T);
[events, ~] = ho.event_loop(ue_track, cells, meas, params);
types = arrayfun(@(e) e.event_type, events);
verifyEqual(tc, sum(types == "HO_SUCCESS"), 2);
verifyEqual(tc, sum(types == "PING_PONG"),  1);
end

function test_serving_initial_selection(tc)
% Initial serving = strongest raw RSRP at t=0.
T = 20;
rsrp_serv  = -90 * ones(T, 1);
rsrp_neigh = -70 * ones(T, 1);
sinr_serv  = 10  * ones(T, 1);
[cells, ue_track, meas, params] = mk_scenario(rsrp_serv, rsrp_neigh, sinr_serv, T);
% swap so cell 2 is best at t=0
[~, hist] = ho.event_loop(ue_track, cells, meas, params);
verifyEqual(tc, hist.serving_idx(1), 2);
end
