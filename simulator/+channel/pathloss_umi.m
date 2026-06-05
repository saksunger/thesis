function pl_db = pathloss_umi(d_2d_m, d_3d_m, h_bs_m, h_ut_m, fc_ghz, is_los)
%PATHLOSS_UMI  TR 38.901 Table 7.4.1-1 — UMi-Street Canyon path loss.
%
% Args:
%   d_2d_m  : horizontal distance UE↔BS (m), array
%   d_3d_m  : 3-D distance UE↔BS (m), same shape as d_2d_m
%   h_bs_m  : BS antenna height (m), scalar; UMi default 10 m
%   h_ut_m  : UE antenna height (m), scalar (typically 1.5 m pedestrian
%             or 1.5–22.5 m per TR 38.901 §7.4.1 validity range)
%   fc_ghz  : carrier frequency (GHz), scalar
%   is_los  : logical array (same shape as d_2d_m); per-sample LoS state
%
% Returns:
%   pl_db   : path loss (dB), same shape as d_2d_m
%
% Notes:
%   * Implements UMi-Street Canyon LoS/NLoS exactly per TR 38.901
%     Table 7.4.1-1. Validity: 10 m ≤ d_2D ≤ 5000 m, hBS = 10 m,
%     1.5 m ≤ hUT ≤ 22.5 m, 0.5 GHz ≤ fc ≤ 100 GHz.
%   * Caller is responsible for clipping d_2D to >=10 m before calling
%     (we do not silently clip here so that boundary violations are visible
%     to the unit tests).
%   * For NLoS, returns max(PL_LoS, PL_NLoS_prime), as specified.

arguments
    d_2d_m (:,:) double
    d_3d_m (:,:) double
    h_bs_m (1,1) double {mustBePositive}
    h_ut_m (1,1) double {mustBePositive}
    fc_ghz (1,1) double {mustBePositive}
    is_los logical
end

if ~isequal(size(d_2d_m), size(d_3d_m), size(is_los))
    error('pathloss_umi:size_mismatch', ...
          'd_2d_m, d_3d_m, is_los must all be the same shape');
end

% --- breakpoint distance dBP' = 4 (hBS-hE)(hUT-hE) fc/c, with hE = 1 m for UMi ---
hE     = 1.0;
fc_hz  = fc_ghz * 1e9;
c_ms   = 299792458;
dBP_p  = 4 * (h_bs_m - hE) * (h_ut_m - hE) * fc_hz / c_ms;

% --- LoS PL (two-segment per TR 38.901 Table 7.4.1-1) ---
PL1 = 32.4 + 21*log10(d_3d_m) + 20*log10(fc_ghz);
PL2 = 32.4 + 40*log10(d_3d_m) + 20*log10(fc_ghz) ...
      - 9.5*log10(dBP_p^2 + (h_bs_m - h_ut_m)^2);
pl_los = PL1;
mask2  = d_2d_m > dBP_p;
pl_los(mask2) = PL2(mask2);

% --- NLoS PL = max(PL_LoS, PL_NLoS_prime) ---
% UMi NLoS does not include the UMa "hUT term"; instead a -0.3*(hUT-1.5) term.
PL_NLoS_prime = 35.3*log10(d_3d_m) + 22.4 + 21.3*log10(fc_ghz) ...
                - 0.3*(h_ut_m - 1.5);
pl_nlos = max(pl_los, PL_NLoS_prime);

pl_db = pl_los;
pl_db(~is_los) = pl_nlos(~is_los);
end
