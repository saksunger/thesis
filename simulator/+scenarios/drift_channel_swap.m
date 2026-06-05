function spec = drift_channel_swap(overrides)
%DRIFT_CHANNEL_SWAP  Drift scenario D-2 — UMa → UMi channel-model swap.
%
% Starts from `scenarios.baseline` and switches:
%   * path-loss model:       UMa  → UMi-Street Canyon (TR 38.901 Table 7.4.1-1)
%   * BS antenna height:     25 m → 10 m (TR 38.913 UMi reference)
%   * shadow std (LoS/NLoS): 4/6 → 4/7.82 dB
%   * correlation distances: 37/50 m → 10/15 m (TR 38.901 Table 7.5-6)
%
% Carrier frequency, ISD, layout, and HO control parameters are kept
% identical to baseline, so the observable distribution shift comes
% purely from the channel model (cleanest ground-truth label).
%
% Realism justification: this drift mimics what happens when an operator
% installs a denser layer of small cells on the same carrier — the same
% UE positions now see a different propagation environment.

arguments
    overrides (1,1) struct = struct()
end

c = utils.constants();
spec = scenarios.baseline(struct());

spec.scenario_name = "drift_channel_swap";
spec.is_drift      = true;
spec.drift_id      = "D-2";

spec.layout_params.h_bs_m  = c.h_bs_m_umi;

spec.meas_params.h_bs_m        = c.h_bs_m_umi;
spec.meas_params.scenario      = "UMi";
spec.meas_params.sigma_los_db  = c.sigma_los_db_umi;
spec.meas_params.sigma_nlos_db = c.sigma_nlos_db_umi;
spec.meas_params.shadow_corr_m = c.shadow_corr_m_umi;
spec.meas_params.los_corr_m    = c.los_corr_m_umi;

spec = scenarios.apply_overrides(spec, overrides);
end
