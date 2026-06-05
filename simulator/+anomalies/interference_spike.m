function meas = interference_spike(meas, t_mask, cells, spec)
%INTERFERENCE_SPIKE  Anomaly A-3: per-cell SINR drop during a window.
%
% Subtracts `spec.delta_db` from `sinr_db` for the cells listed in
% `spec.affected_cell_ids` (or all cells if empty) over the time window.
% Mimics a localized interference source (microwave, radar, jammer)
% that degrades reception of specific cells from this UE's vantage.
%
% Required spec fields:
%   .delta_db                (negative number, e.g. -10)
%   .affected_cell_ids       vector of cell ids; empty = all cells

arguments
    meas   (1,1) struct
    t_mask (:,1) logical
    cells  (:,1) struct
    spec   (1,1) struct
end

if ~isfield(spec, 'delta_db')
    error('interference_spike:missing_field', 'spec.delta_db is required');
end

cell_ids = [cells.id].';
K        = numel(cell_ids);

if isfield(spec, 'affected_cell_ids') && ~isempty(spec.affected_cell_ids)
    cell_mask = ismember(cell_ids, spec.affected_cell_ids);
else
    cell_mask = true(K, 1);
end

% Outer product: rows = T-with-anomaly, cols = K-with-anomaly
delta_field = double(t_mask) * double(cell_mask).' * spec.delta_db;
meas.sinr_db = meas.sinr_db + delta_field;
meas.rsrq_db = meas.rsrq_db + delta_field / 2;
end
