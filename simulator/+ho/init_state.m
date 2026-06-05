function state = init_state(meas, cells, ue_id, params)
%INIT_STATE  Initialize per-UE HO state at t = 0.
%
% Initial serving cell = the cell with the highest *raw* RSRP at the first
% sample. L3-filter outputs are initialized to the raw values at t=0.
%
% Args:
%   meas   : struct from `ho.measurements` (Phase 1)
%   cells  : struct array from `utils.hex_layout`
%   ue_id  : integer UE identifier
%   params : config struct (see `ho.event_loop` docstring for fields)
%
% Returns:
%   state : per-UE HO state struct

K = numel(cells);

% --- initial serving cell selection (camp on strongest at t=0) ---
rsrp0 = meas.rsrp_dbm(1, :);
[~, serving_idx] = max(rsrp0);

state = struct();
state.ue_id            = ue_id;

% serving cell
state.serving_idx      = serving_idx;
state.serving_id       = cells(serving_idx).id;

% L3-filtered measurements (init from raw at t=0)
state.rsrp_l3_dbm      = meas.rsrp_dbm(1, :).';        % K x 1
state.rsrq_l3_db       = meas.rsrq_db(1, :).';
state.sinr_l3_db       = meas.sinr_db(1, :).';
state.l3_initialized   = true(K, 1);

% A3 TTT accumulator per neighbor
state.ttt_elapsed_s    = zeros(K, 1);

% HO execution
state.in_ho            = false;
state.ho_remaining_s   = 0;
state.ho_target_idx    = 0;
state.ho_source_idx    = 0;
state.ho_attempt_t_s   = NaN;       % timestamp of last HO_ATTEMPT
state.ho_attempt_event_id = 0;      % id of last HO_ATTEMPT (for outcome backlink)

% RLF state machine (TS 38.331 §5.3.10)
state.rlf_state        = "IDLE";    % IDLE | OUT_OF_SYNC | T310_RUNNING
state.n310_count       = 0;
state.n311_count       = 0;
state.t310_remaining_s = 0;
state.rlf_count        = 0;

% Ping-pong tracking
state.last_ho_complete_t_s = -inf;  % time of last HO_SUCCESS
state.last_ho_source_id    = 0;     % source cell id of last HO_SUCCESS

% Counters (for end-of-run KPIs)
state.cnt_ho_attempt   = 0;
state.cnt_ho_success   = 0;
state.cnt_ho_fail      = 0;
state.cnt_rlf          = 0;
state.cnt_ping_pong    = 0;

% Snapshot of pre-event KPIs (used when assembling the next event row)
state.last_event_id    = 0;
state.last_event_t_s   = NaN;
end
