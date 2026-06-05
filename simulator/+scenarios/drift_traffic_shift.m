function spec = drift_traffic_shift(overrides)
%DRIFT_TRAFFIC_SHIFT  Drift scenario D-1 — traffic load shift via UE-density change.
%
% v0 implementation: increase `n_ue` for this phase relative to baseline.
% More UEs in the same coverage area means more aggregate handovers per
% unit time and more frequent ping-pong events at boundaries. The
% simulator's per-cell load model is fixed (cells assumed fully loaded
% for interference; ADR-13 deferred RSRQ traffic-load to Phase 4c), so
% this drift mainly shifts the EVENT-RATE distribution rather than the
% per-UE radio distribution.
%
% Realism justification: diurnal load patterns + special events
% (concerts, sports) cause real RAN n_ue to swing 2-4x; the ML pipeline
% should learn to distinguish these from genuine anomaly bursts.

arguments
    overrides (1,1) struct = struct()
end

spec = scenarios.baseline(struct());

spec.scenario_name = "drift_traffic_shift";
spec.is_drift      = true;
spec.drift_id      = "D-1";

% Default behaviour: triple the UE count vs baseline. Override via the
% timeline JSON `params: { n_ue: <value> }`.
spec.n_ue = 36;

spec = scenarios.apply_overrides(spec, overrides);
end
