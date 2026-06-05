function [events, history] = event_loop(ue_track, cells, meas, params)
%EVENT_LOOP  Per-tick orchestration of L3 filter + A3 event + HO execution
%            + RLF state machine + ping-pong detection, for a single UE.
%
% Pipeline (per tick):
%   1. L3-filter the raw RSRP/RSRQ/SINR samples for all cells.
%   2. If HO execution in progress → advance, evaluate completion.
%      During HO, RLF is still evaluated; if RLF fires, the HO is
%      retroactively marked HO_FAIL.
%   3. RLF state machine on serving-cell filtered SINR.
%   4. If not in HO and not just RLF'd, evaluate A3 entry condition on
%      every non-serving cell, advance per-neighbor TTT timers, and
%      trigger HO when TTT reached.
%   5. Append events to the log.
%
% References:
%   - L3 filter:  3GPP TS 38.331 v17.16.0 §5.5.3.2
%   - Event A3:   3GPP TS 38.331 v17.16.0 §5.5.4.4
%   - RLF / T310: 3GPP TS 38.331 v17.16.0 §5.3.10
%
% Args:
%   ue_track : struct from `mobility.waypoint_track` (.t_s, .x_m, .y_m)
%   cells    : struct array from `utils.hex_layout`
%   meas     : struct from `ho.measurements` (rsrp_dbm, rsrq_db, sinr_db, ...)
%   params   : config struct, required fields:
%       .ue_id            (int, default 1)
%       .dt_s             (s; should match ue_track sampling)
%       .l3_alpha         (filter coefficient, default 0.5)
%       .ttt_s            (TTT, seconds; e.g., 0.256)
%       .hyst_db          (A3 hysteresis, dB)
%       .a3_offset_db     (A3 offset, dB)
%       .ho_exec_s        (HO execution duration, default 0.050)
%       .rlf_qout_db      (Qout threshold, default -8)
%       .rlf_qin_db       (Qin threshold, default -6)
%       .n310             (default 6)
%       .n311             (default 2)
%       .t310_s           (default 1.000)
%       .ping_pong_s      (ping-pong window after HO_SUCCESS, default 1.0)
%
% Returns:
%   events  : struct array of HO/RLF/PING_PONG events
%   history : per-tick diagnostic record (serving cell, L3 RSRPs, ttt, in_ho, rlf_state)

arguments
    ue_track (1,1) struct
    cells    (:,1) struct
    meas     (1,1) struct
    params   (1,1) struct
end

% --- defaults ---
if ~isfield(params, 'ue_id'),        params.ue_id        = 1;     end
if ~isfield(params, 'dt_s'),         params.dt_s         = 0.010; end
if ~isfield(params, 'l3_alpha'),     params.l3_alpha     = 0.5;   end
if ~isfield(params, 'ho_exec_s'),    params.ho_exec_s    = 0.050; end
if ~isfield(params, 'rlf_qout_db'),  params.rlf_qout_db  = -8;    end
if ~isfield(params, 'rlf_qin_db'),   params.rlf_qin_db   = -6;    end
if ~isfield(params, 'n310'),         params.n310         = 6;     end
if ~isfield(params, 'n311'),         params.n311         = 2;     end
if ~isfield(params, 't310_s'),       params.t310_s       = 1.000; end
if ~isfield(params, 'ping_pong_s'),  params.ping_pong_s  = 1.0;   end

T = numel(ue_track.t_s);
K = numel(cells);
dt = params.dt_s;

% --- preallocate event log (upper bound) ---
max_events = max(1000, ceil(T * 0.05));
events = repmat(ho.empty_event(), max_events, 1);
n_events = 0;

% --- preallocate history (per-tick diagnostics) ---
history = struct( ...
    'serving_idx',   zeros(T, 1), ...
    'rsrp_l3_dbm',   zeros(T, K), ...
    'sinr_l3_db',    zeros(T, K), ...
    'in_ho',         false(T, 1), ...
    'rlf_state',     strings(T, 1), ...
    'ttt_elapsed_s', zeros(T, K));

% --- init state ---
state = ho.init_state(meas, cells, params.ue_id, params);

for ti = 1:T
    t = ue_track.t_s(ti);

    % --- 1. L3 filter ---
    state = ho.l3_filter(state, ...
        meas.rsrp_dbm(ti, :), meas.rsrq_db(ti, :), meas.sinr_db(ti, :), ...
        params.l3_alpha);

    % --- 3. RLF state machine on serving filtered SINR ---
    sinr_serv = state.sinr_l3_db(state.serving_idx);
    [state, rlf_declared] = ho.rlf_evaluate(state, sinr_serv, dt, params);

    if rlf_declared
        % If an HO was in progress, mark the HO as failed.
        if state.in_ho
            n_events = n_events + 1;
            ev = ho.empty_event();
            ev.event_id           = n_events;
            ev.event_time_s       = t;
            ev.event_type         = "HO_FAIL";
            ev.ue_id              = state.ue_id;
            ev.source_cell_id     = cells(state.ho_source_idx).id;
            ev.target_cell_id     = cells(state.ho_target_idx).id;
            ev.outcome            = "fail";
            ev.ttt_ms             = params.ttt_s * 1000;
            ev.hysteresis_db      = params.hyst_db;
            ev.a3_offset_db       = params.a3_offset_db;
            ev.rsrp_serv_pre_dbm  = state.rsrp_l3_dbm(state.ho_source_idx);
            ev.rsrp_tgt_pre_dbm   = state.rsrp_l3_dbm(state.ho_target_idx);
            ev.sinr_serv_pre_db   = sinr_serv;
            events(n_events) = ev;
            state.cnt_ho_fail = state.cnt_ho_fail + 1;
            state.in_ho = false;
        end

        % Log RLF event
        n_events = n_events + 1;
        ev = ho.empty_event();
        ev.event_id           = n_events;
        ev.event_time_s       = t;
        ev.event_type         = "RLF";
        ev.ue_id              = state.ue_id;
        ev.source_cell_id     = cells(state.serving_idx).id;
        ev.target_cell_id     = cells(state.serving_idx).id;
        ev.outcome            = "rlf";
        ev.rsrp_serv_pre_dbm  = state.rsrp_l3_dbm(state.serving_idx);
        ev.sinr_serv_pre_db   = sinr_serv;
        events(n_events) = ev;
        state.cnt_rlf = state.cnt_rlf + 1;

        % After RLF: re-establish on best RSRP cell (RRC re-establishment proxy)
        [~, best] = max(state.rsrp_l3_dbm);
        state.serving_idx       = best;
        state.serving_id        = cells(best).id;
        state.ttt_elapsed_s(:)  = 0;
        % Note: real RRC re-establishment takes seconds; we model it as instantaneous v0.
        history.serving_idx(ti) = state.serving_idx;
        history.rsrp_l3_dbm(ti, :) = state.rsrp_l3_dbm.';
        history.sinr_l3_db(ti, :)  = state.sinr_l3_db.';
        history.in_ho(ti)          = false;
        history.rlf_state(ti)      = state.rlf_state;
        history.ttt_elapsed_s(ti, :) = state.ttt_elapsed_s.';
        continue
    end

    % --- 2. Advance in-progress HO ---
    if state.in_ho
        state.ho_remaining_s = state.ho_remaining_s - dt;
        if state.ho_remaining_s <= 0
            % HO completes successfully
            new_src_idx = state.ho_source_idx;
            new_tgt_idx = state.ho_target_idx;

            n_events = n_events + 1;
            ev = ho.empty_event();
            ev.event_id           = n_events;
            ev.event_time_s       = t;
            ev.event_type         = "HO_SUCCESS";
            ev.ue_id              = state.ue_id;
            ev.source_cell_id     = cells(new_src_idx).id;
            ev.target_cell_id     = cells(new_tgt_idx).id;
            ev.outcome            = "success";
            ev.ttt_ms             = params.ttt_s * 1000;
            ev.hysteresis_db      = params.hyst_db;
            ev.a3_offset_db       = params.a3_offset_db;
            ev.rsrp_serv_pre_dbm  = state.rsrp_l3_dbm(new_src_idx);
            ev.rsrp_tgt_pre_dbm   = state.rsrp_l3_dbm(new_tgt_idx);
            ev.rsrp_serv_post_dbm = state.rsrp_l3_dbm(new_tgt_idx);
            ev.sinr_serv_pre_db   = state.sinr_l3_db(new_src_idx);
            events(n_events) = ev;
            state.cnt_ho_success = state.cnt_ho_success + 1;

            % Ping-pong: HO target = the cell we left in our previous HO,
            % and elapsed time since that HO_SUCCESS is below ping_pong_s.
            new_tgt_id = cells(new_tgt_idx).id;
            if (t - state.last_ho_complete_t_s) < params.ping_pong_s ...
                    && new_tgt_id == state.last_ho_source_id
                n_events = n_events + 1;
                ev = ho.empty_event();
                ev.event_id           = n_events;
                ev.event_time_s       = t;
                ev.event_type         = "PING_PONG";
                ev.ue_id              = state.ue_id;
                ev.source_cell_id     = cells(new_src_idx).id;
                ev.target_cell_id     = new_tgt_id;
                ev.outcome            = "pp_fail";
                events(n_events) = ev;
                state.cnt_ping_pong = state.cnt_ping_pong + 1;
            end
            state.last_ho_complete_t_s = t;
            state.last_ho_source_id    = cells(new_src_idx).id;

            % Adopt the new serving cell, reset TTT bookkeeping
            state.serving_idx      = new_tgt_idx;
            state.serving_id       = cells(new_tgt_idx).id;
            state.in_ho            = false;
            state.ho_target_idx    = 0;
            state.ho_source_idx    = 0;
            state.ttt_elapsed_s(:) = 0;
        end
        % during in_ho we skip new A3 evaluation
        history.serving_idx(ti) = state.serving_idx;
        history.rsrp_l3_dbm(ti, :) = state.rsrp_l3_dbm.';
        history.sinr_l3_db(ti, :)  = state.sinr_l3_db.';
        history.in_ho(ti)          = state.in_ho;
        history.rlf_state(ti)      = state.rlf_state;
        history.ttt_elapsed_s(ti, :) = state.ttt_elapsed_s.';
        continue
    end

    % --- 4. A3 event evaluation ---
    [state, fired_tgt] = ho.a3_evaluate(state, dt, params);
    if fired_tgt > 0
        % Fire HO_ATTEMPT immediately, mark in_ho for ho_exec_s
        n_events = n_events + 1;
        ev = ho.empty_event();
        ev.event_id           = n_events;
        ev.event_time_s       = t;
        ev.event_type         = "HO_ATTEMPT";
        ev.ue_id              = state.ue_id;
        ev.source_cell_id     = cells(state.serving_idx).id;
        ev.target_cell_id     = cells(fired_tgt).id;
        ev.outcome            = "attempt";
        ev.ttt_ms             = params.ttt_s * 1000;
        ev.hysteresis_db      = params.hyst_db;
        ev.a3_offset_db       = params.a3_offset_db;
        ev.rsrp_serv_pre_dbm  = state.rsrp_l3_dbm(state.serving_idx);
        ev.rsrp_tgt_pre_dbm   = state.rsrp_l3_dbm(fired_tgt);
        ev.sinr_serv_pre_db   = state.sinr_l3_db(state.serving_idx);
        events(n_events) = ev;
        state.cnt_ho_attempt = state.cnt_ho_attempt + 1;

        % Enter HO execution
        state.in_ho           = true;
        state.ho_remaining_s  = params.ho_exec_s;
        state.ho_source_idx   = state.serving_idx;
        state.ho_target_idx   = fired_tgt;
        state.ho_attempt_t_s  = t;
        state.ho_attempt_event_id = n_events;
        state.ttt_elapsed_s(:) = 0;
    end

    history.serving_idx(ti)      = state.serving_idx;
    history.rsrp_l3_dbm(ti, :)   = state.rsrp_l3_dbm.';
    history.sinr_l3_db(ti, :)    = state.sinr_l3_db.';
    history.in_ho(ti)            = state.in_ho;
    history.rlf_state(ti)        = state.rlf_state;
    history.ttt_elapsed_s(ti, :) = state.ttt_elapsed_s.';
end

% --- trim event log ---
events = events(1:n_events);

% --- backfill t_to_next_event_s ---
for k = 1:numel(events)-1
    events(k).t_to_next_event_s = events(k+1).event_time_s - events(k).event_time_s;
end
end
