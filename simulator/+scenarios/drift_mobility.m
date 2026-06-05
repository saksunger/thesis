function spec = drift_mobility(overrides)
%DRIFT_MOBILITY  Drift scenario D-3 — UE mobility profile shift.
%
% Bumps UE speed from the baseline 10 m/s (≈ 36 km/h, urban traffic) up
% to 25 m/s (≈ 90 km/h, highway). Per Phase 4 Iter B implementation,
% `+utils.run_phase` builds straight-line trajectories with displacement
% = speed × duration, so this directly changes the per-tick UE position
% increment and therefore the cell-boundary crossing rate.
%
% Realism justification: rush-hour onset, vehicle launches in a coverage
% area, train-line activation. ML detectors should pick up the increase
% in HO frequency and the change in serving-cell dwell-time distribution.

arguments
    overrides (1,1) struct = struct()
end

spec = scenarios.baseline(struct());

spec.scenario_name = "drift_mobility";
spec.is_drift      = true;
spec.drift_id      = "D-3";

% Default: bump from 10 m/s -> 25 m/s. Override via timeline JSON
% `params: { speed_mps: <value> }`.
spec.speed_mps = 25;

spec = scenarios.apply_overrides(spec, overrides);
end
