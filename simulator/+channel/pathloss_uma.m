function pl_db = pathloss_uma(d_2d_m, d_3d_m, h_bs_m, h_ut_m, fc_ghz, is_los)
%PATHLOSS_UMA  TR 38.901 Table 7.4.1-1 UMa path-loss (LoS / NLoS).
%
% Implements the Urban Macro (UMa) path-loss formulas defined in
%   3GPP TR 38.901 v17.1.0 §7.4.1 (Table 7.4.1-1).
%
% Args:
%   d_2d_m : 2-D horizontal distance UE↔BS (m), scalar or vector
%   d_3d_m : 3-D slant distance UE↔BS (m), same size as d_2d_m
%   h_bs_m : BS antenna height (m), scalar (typ. 25 m)
%   h_ut_m : UE antenna height (m), scalar (typ. 1.5 m for pedestrian)
%   fc_ghz : carrier frequency (GHz), scalar
%   is_los : logical, same size as d_2d_m (true → LoS branch)
%
% Returns:
%   pl_db  : path-loss in dB, same size as inputs
%
% Validity (per Table 7.4.1-1):
%   10 m ≤ d_2D ≤ 5000 m
%   1.5 m ≤ h_UT ≤ 22.5 m
%   h_BS = 25 m  (other heights: use UMi or generalize)
%   0.5 GHz ≤ fc ≤ 100 GHz
%
% LoS:
%   PL_LoS = PL1                              for 10 ≤ d_2D ≤ d_BP'
%   PL_LoS = PL2                              for d_BP' < d_2D ≤ 5 km
%   where
%     PL1 = 28.0 + 22*log10(d_3D) + 20*log10(fc)
%     PL2 = 28.0 + 40*log10(d_3D) + 20*log10(fc)
%           - 9*log10( (d_BP')^2 + (h_BS - h_UT)^2 )
%     d_BP' = 4 * h_BS' * h_UT' * fc * 1e9 / c
%     h_BS' = h_BS - h_E,   h_UT' = h_UT - h_E
%     h_E (effective env height) is 1 m with prob 1 / (1 + C(d_2D,h_UT))   (TR 38.901 NOTE 1 to Table 7.4.1-1)
%
%   For simplicity (and because h_UT < 13 m → C = 0 → h_E = 1 m deterministic),
%   we take h_E = 1 m here. This is the standard simplification used in academic
%   simulators and matches MATLAB 5G Toolbox `nrPathLoss` default.
%
% NLoS:
%   PL_NLoS = max( PL_LoS, PL'_NLoS )
%     PL'_NLoS = 13.54 + 39.08*log10(d_3D) + 20*log10(fc) - 0.6*(h_UT - 1.5)

arguments
    d_2d_m (:,:) double {mustBeNonnegative}
    d_3d_m (:,:) double {mustBeNonnegative}
    h_bs_m (1,1) double {mustBePositive}
    h_ut_m (1,1) double {mustBePositive}
    fc_ghz (1,1) double {mustBePositive}
    is_los logical
end

% --- effective env height (TR 38.901 Table 7.4.1-1 NOTE 1; simplified) ---
h_E    = 1.0;
h_bsp  = h_bs_m - h_E;
h_utp  = h_ut_m - h_E;

c0     = 299792458;                       % speed of light, m/s
fc_hz  = fc_ghz * 1e9;
d_BP_p = 4 * h_bsp * h_utp * fc_hz / c0;  % breakpoint distance, m

% --- LoS components ---
PL1 = 28.0 + 22*log10(d_3d_m) + 20*log10(fc_ghz);
PL2 = 28.0 + 40*log10(d_3d_m) + 20*log10(fc_ghz) ...
      - 9*log10(d_BP_p.^2 + (h_bs_m - h_ut_m)^2);
PL_LoS = PL1 .* (d_2d_m <= d_BP_p) + PL2 .* (d_2d_m > d_BP_p);

% --- NLoS component ---
PL_NLoS_prime = 13.54 + 39.08*log10(d_3d_m) + 20*log10(fc_ghz) ...
                - 0.6 * (h_ut_m - 1.5);
PL_NLoS = max(PL_LoS, PL_NLoS_prime);

% --- branch select ---
pl_db = PL_LoS .* is_los + PL_NLoS .* ~is_los;

% --- guard rails: NaN for distances below 10 m (out of validity) ---
pl_db(d_2d_m < 10) = NaN;
end
