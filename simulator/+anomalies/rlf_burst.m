function meas = rlf_burst(meas, t_mask, spec)
%RLF_BURST  Anomaly A-1: transient SINR collapse on the affected UE.
%
% Drops `sinr_db` for ALL cells by `spec.delta_db` over the time window.
% A large enough drop (e.g. -15 dB) pushes the serving cell's SINR below
% the RLF Qout threshold, causing the HO event loop's RLF state machine
% to trigger T310 and (if Qin recovery does not happen in time) declare
% RLF. Severity is controlled by `delta_db` and window length.
%
% Required spec fields:
%   .delta_db    (negative number, e.g. -15)
%
% Side effects: also lowers `rsrq_db` proportionally since real SINR
% drops are correlated with RSRQ degradation.

arguments
    meas   (1,1) struct
    t_mask (:,1) logical
    spec   (1,1) struct
end

if ~isfield(spec, 'delta_db')
    error('rlf_burst:missing_field', 'spec.delta_db is required');
end

meas.sinr_db(t_mask, :) = meas.sinr_db(t_mask, :) + spec.delta_db;
meas.rsrq_db(t_mask, :) = meas.rsrq_db(t_mask, :) + spec.delta_db / 2;
end
