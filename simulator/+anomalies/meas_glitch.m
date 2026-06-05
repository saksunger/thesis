function meas = meas_glitch(meas, t_mask, spec)
%MEAS_GLITCH  Anomaly A-2: stuck-at-value measurement glitch.
%
% Replaces `rsrp_dbm` on ALL cells with a constant value over the time
% window — mimicking a UE firmware bug or sensor fault where the
% measurement report is frozen. Default stuck-at value is the value at
% the start of the window for each cell (so the freeze is plausible).
%
% Required spec fields (optional, defaults shown):
%   .stuck_value_dbm  (-95)   constant value to inject; if NaN, freeze
%                              at the first-tick value within window

arguments
    meas   (1,1) struct
    t_mask (:,1) logical
    spec   (1,1) struct
end

if ~any(t_mask), return; end

idx_first = find(t_mask, 1, 'first');
K         = size(meas.rsrp_dbm, 2);

if isfield(spec, 'stuck_value_dbm') && isfinite(spec.stuck_value_dbm)
    stuck_per_cell = repmat(spec.stuck_value_dbm, 1, K);
else
    stuck_per_cell = meas.rsrp_dbm(idx_first, :);
end

meas.rsrp_dbm(t_mask, :) = repmat(stuck_per_cell, sum(t_mask), 1);
end
