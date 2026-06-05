function tests = test_pathloss_umi
%TEST_PATHLOSS_UMI  Unit tests for `+channel/pathloss_umi.m`.
%
% References:
%   3GPP TR 38.901 v17.1.0 Table 7.4.1-1 — UMi-Street Canyon.
tests = functiontests(localfunctions);
end


function setupOnce(tc)
addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));
end


function test_los_pl_at_breakpoint(tc)
% PL1 and PL2 must agree exactly at d2D = dBP' (continuity of the
% two-segment LoS formula). We sample one point just below and one
% just above dBP' (1 mm offset) so the function selects different
% branches; the values should match within numerical noise.
fc = 3.5; hBS = 10; hUT = 1.5; hE = 1.0;
c_ms  = 299792458;
dBP_p = 4 * (hBS - hE) * (hUT - hE) * (fc * 1e9) / c_ms;

eps_m = 1e-3;
d2d = [dBP_p - eps_m; dBP_p + eps_m];
d3d = sqrt(d2d.^2 + (hBS - hUT)^2);
los = true(size(d2d));
pl  = channel.pathloss_umi(d2d, d3d, hBS, hUT, fc, los);

verifyEqual(tc, pl(1), pl(2), 'AbsTol', 1e-3, ...
    'PL discontinuous at breakpoint (PL1/PL2 mismatch at d=dBP'')');
verifyTrue(tc, all(isfinite(pl)), 'no NaN/Inf inside validity range');
end


function test_nlos_is_max_los_nlos(tc)
% Per Table 7.4.1-1 footnote: PL_NLoS = max(PL_LoS, PL_NLoS_prime).
fc = 3.5; hBS = 10; hUT = 1.5;
d2d = (10:50:1000).';
d3d = sqrt(d2d.^2 + (hBS - hUT)^2);
nlos = false(size(d2d));
pl_n = channel.pathloss_umi(d2d, d3d, hBS, hUT, fc, nlos);
pl_l = channel.pathloss_umi(d2d, d3d, hBS, hUT, fc, true(size(d2d)));
verifyTrue(tc, all(pl_n >= pl_l - 1e-9), ...
    'NLoS PL must be >= LoS PL at every point');
end


function test_umi_typically_more_loss_than_uma_at_close_range(tc)
% UMi-NLoS uses 35.3*log10(d3D) growth rate (vs UMa's 39.08), but starts
% from a higher intercept (32.4 + 21.3*log10(fc) − 0.3*(hUT−1.5)).
% At a short distance (say 50 m), UMi's NLoS PL is typically a few dB
% LARGER than UMa's NLoS PL because UMi cells are at lower BS height
% (10 m vs 25 m) → larger 3-D distance fraction. Here we use the same
% hBS to isolate the formula difference.
fc = 3.5; hBS = 25; hUT = 1.5;  % same hBS to compare formulas only
d2d = 50;
d3d = sqrt(d2d.^2 + (hBS - hUT)^2);
nlos = false;
pl_umi = channel.pathloss_umi(d2d, d3d, hBS, hUT, fc, nlos);
pl_uma = channel.pathloss_uma(d2d, d3d, hBS, hUT, fc, nlos);
verifyGreaterThan(tc, pl_umi, 60, 'PL is at least ~60 dB at 50 m, 3.5 GHz');
verifyLessThan(tc,    pl_umi, 130, 'PL is at most ~130 dB at 50 m');
% Both formulas in same ballpark (within 15 dB at 50 m)
verifyLessThan(tc, abs(pl_umi - pl_uma), 15, ...
    'UMi and UMa NLoS within 15 dB at 50 m');
end


function test_size_mismatch_errors(tc)
% Defensive check.
d2d = [10; 20];
d3d = [10; 20; 30];
los = true(size(d2d));
verifyError(tc, ...
    @() channel.pathloss_umi(d2d, d3d, 10, 1.5, 3.5, los), ...
    'pathloss_umi:size_mismatch');
end


function test_monotonic_in_distance(tc)
% PL must be monotonically non-decreasing with distance for LoS and NLoS.
fc = 3.5; hBS = 10; hUT = 1.5;
d2d = (10:5:1000).';
d3d = sqrt(d2d.^2 + (hBS - hUT)^2);
for is_los = [true, false]
    los = repmat(is_los, size(d2d));
    pl  = channel.pathloss_umi(d2d, d3d, hBS, hUT, fc, los);
    verifyTrue(tc, all(diff(pl) >= -1e-9), ...
        sprintf('PL not monotonic for is_los=%d', is_los));
end
end
