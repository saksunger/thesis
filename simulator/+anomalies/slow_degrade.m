function meas = slow_degrade(meas, t_mask, t_s, spec)
%SLOW_DEGRADE  Anomaly A-5: linear SINR degradation on the affected UE.
%
% Adds a linear ramp to `sinr_db` (all cells) over the time window:
%
%     ramp_db(t) = rate_db_per_s * (t - t_start_s)
%
% so SINR ends at `rate_db_per_s * (t_end_s - t_start_s)` dB lower than
% baseline. Typical use: rate_db_per_s = -0.01 over a 1000 s window
% means a 10 dB drop. Mimics antenna damage / water ingress.
%
% Required spec fields:
%   .rate_db_per_s   (negative number, e.g. -0.01)

arguments
    meas   (1,1) struct
    t_mask (:,1) logical
    t_s    (:,1) double
    spec   (1,1) struct
end

if ~isfield(spec, 'rate_db_per_s')
    error('slow_degrade:missing_field', 'spec.rate_db_per_s is required');
end

ramp = zeros(size(t_s));
ramp(t_mask) = spec.rate_db_per_s * (t_s(t_mask) - spec.t_start_s);
meas.sinr_db = meas.sinr_db + ramp;
end
