function [state, fired_target_idx] = a3_evaluate(state, dt_s, params)
%A3_EVALUATE  Evaluate 3GPP TS 38.331 §5.5.4.4 Event A3 entry condition
%             on each neighbor cell, advancing the per-neighbor TTT timer.
%
% Event A3 (NR): "Neighbour becomes amount of offset better than SpCell"
%
% Entry condition (per spec):
%     Mn + Ofn + Ocn - Hys  >  Ms + Ofs + Ocs + Off
%
% where (we map and adopt simplifying defaults for v0):
%   Mn  : neighbor measurement quantity (filtered RSRP, dBm) — `state.rsrp_l3_dbm(k)`
%   Ms  : serving measurement quantity                       — `state.rsrp_l3_dbm(serving)`
%   Ofn : measurement-object-specific freq offset (neighbor) — 0
%   Ofs : measurement-object-specific freq offset (serving)  — 0
%   Ocn : cell-specific offset (neighbor, CIO)               — 0
%   Ocs : cell-specific offset (serving)                     — 0
%   Hys : hysteresis                                         — `params.hyst_db`
%   Off : a3-Offset                                          — `params.a3_offset_db`
%
% Behaviour:
%   - For each non-serving cell `k`, if entry condition true → accumulate TTT.
%   - If TTT >= `params.ttt_s` → fire HO toward `k` (return fired_target_idx).
%   - If condition false → reset that cell's TTT to 0.
%   - At most one HO fires per tick (the first cell to cross TTT, in cell-id order).
%
% Args:
%   state  : per-UE HO state
%   dt_s   : tick duration (s)
%   params : config struct with .hyst_db, .a3_offset_db, .ttt_s
%
% Returns:
%   state            : updated state (TTT timers advanced/reset)
%   fired_target_idx : index into cells of HO target, or 0 if none

arguments
    state  (1,1) struct
    dt_s   (1,1) double {mustBePositive}
    params (1,1) struct
end

K = numel(state.rsrp_l3_dbm);
fired_target_idx = 0;

% --- compute A3 entry condition per neighbor (vectorized) ---
m_s     = state.rsrp_l3_dbm(state.serving_idx);
m_n     = state.rsrp_l3_dbm;                    % K x 1
% Ofn = Ocn = Ofs = Ocs = 0 in v0; spec terms kept for clarity.
cond    = (m_n - params.hyst_db) > (m_s + params.a3_offset_db);
cond(state.serving_idx) = false;                % never trigger HO to serving

% --- update TTT accumulators ---
state.ttt_elapsed_s(cond)  = state.ttt_elapsed_s(cond) + dt_s;
state.ttt_elapsed_s(~cond) = 0;

% --- find first cell to hit TTT (deterministic: cell-id order) ---
hit = find(state.ttt_elapsed_s >= params.ttt_s, 1, 'first');
if ~isempty(hit)
    fired_target_idx = hit;
end
end
