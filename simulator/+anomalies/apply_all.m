function meas = apply_all(meas, ue_track, ue_id, cells, anomaly_list)
%APPLY_ALL  Dispatch all anomalies that apply to this (UE, phase).
%
% Iterates the anomaly_list (cell array of structs), filtering by
% `affected_ue_ids` (empty = all UEs) and the time window
% `[t_start_s, t_end_s)` over `ue_track.t_s`. For each match, dispatches
% to the matching `anomalies.<type>` function.
%
% Args:
%   meas         : meas struct from `ho.measurements` (modified in place)
%   ue_track     : UE track struct
%   ue_id        : integer UE id (1..n_ue)
%   cells        : cell array (passed through to per-anomaly fns)
%   anomaly_list : cell array of anomaly spec structs (see baseline.m)
%
% Returns:
%   meas         : possibly modified meas struct

arguments
    meas         (1,1) struct
    ue_track     (1,1) struct
    ue_id        (1,1) double
    cells        (:,1) struct
    anomaly_list (:,1) cell
end

t_s = ue_track.t_s;

for i = 1:numel(anomaly_list)
    a = anomaly_list{i};

    % --- filter by UE ---
    if isfield(a, 'affected_ue_ids') && ~isempty(a.affected_ue_ids)
        if ~ismember(ue_id, a.affected_ue_ids)
            continue
        end
    end

    % --- compute time mask ---
    t_mask = (t_s >= a.t_start_s) & (t_s < a.t_end_s);
    if ~any(t_mask)
        continue
    end

    % --- dispatch ---
    switch lower(string(a.type))
        case "rlf_burst"
            meas = anomalies.rlf_burst(meas, t_mask, a);
        case "meas_glitch"
            meas = anomalies.meas_glitch(meas, t_mask, a);
        case "slow_degrade"
            meas = anomalies.slow_degrade(meas, t_mask, t_s, a);
        case "interference_spike"
            meas = anomalies.interference_spike(meas, t_mask, cells, a);
        otherwise
            error('apply_all:unknown_type', ...
                  'unknown anomaly type "%s" (UE %d, anomaly_id %s)', ...
                  a.type, ue_id, getfield_default(a, 'anomaly_id', '?'));
    end
end
end


function v = getfield_default(s, f, dflt)
if isfield(s, f), v = s.(f); else, v = dflt; end
end
