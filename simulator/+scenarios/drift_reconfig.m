function spec = drift_reconfig(overrides)
%DRIFT_RECONFIG  Drift scenario D-4 — operator pushes new RRM config.
%
% Switches the HO control parameters (TTT, hysteresis, A3 offset) at
% the phase boundary. The radio environment is identical to baseline;
% only the SON/RRM decision logic changes. This isolates the effect of
% a config push on observable KPIs (HOSR, ping-pong rate, RLF rate) —
% a pattern operators face every time they roll out an OSS-driven
% parameter optimisation.
%
% Default: TTT 256 ms -> 1024 ms (more conservative, expected to reduce
% ping-pong but risk slower RLF response). Override via timeline JSON
% `params: { ho_params: { ttt_s: <s>, hyst_db: <db>, a3_offset_db: <db> } }`.

arguments
    overrides (1,1) struct = struct()
end

spec = scenarios.baseline(struct());

spec.scenario_name = "drift_reconfig";
spec.is_drift      = true;
spec.drift_id      = "D-4";

% Default RRM-push: longer TTT, larger hysteresis.
spec.ho_params.ttt_s   = 1.024;     % 1024 ms (3GPP-allowed value)
spec.ho_params.hyst_db = 4;         % 4 dB (more conservative)

spec = scenarios.apply_overrides(spec, overrides);
end
