function tests = test_pathloss
%TEST_PATHLOSS  MATLAB unit tests for `channel.pathloss_uma`.
%
% Run with:
%   addpath(genpath('simulator'));
%   results = runtests('simulator/tests/test_pathloss.m');
%   disp(results.table());
tests = functiontests(localfunctions);
end

function setupOnce(testCase) %#ok<INUSD>
addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));
end

function test_below_breakpoint_los_grows_with_distance(tc)
% PL_LoS branch 1: monotone increasing with d_3D before the breakpoint
d_2d = [20; 50; 100; 200];
d_3d = sqrt(d_2d.^2 + (25-1.5)^2);
pl   = channel.pathloss_uma(d_2d, d_3d, 25, 1.5, 3.5, true(size(d_2d)));
verifyTrue(tc, all(diff(pl) > 0), ...
           'LoS PL must be monotonically increasing with distance below breakpoint.');
end

function test_nlos_geq_los(tc)
% By construction (NLoS = max(LoS, NLoS')), NLoS ≥ LoS always.
d_2d = (20:50:1500).';
d_3d = sqrt(d_2d.^2 + (25-1.5)^2);
pl_los  = channel.pathloss_uma(d_2d, d_3d, 25, 1.5, 3.5, true(size(d_2d)));
pl_nlos = channel.pathloss_uma(d_2d, d_3d, 25, 1.5, 3.5, false(size(d_2d)));
verifyTrue(tc, all(pl_nlos >= pl_los - 1e-9), ...
           'NLoS PL must be ≥ LoS PL at all distances.');
end

function test_nan_below_10m(tc)
% Validity guard: d_2D < 10 m → NaN
d_2d = [1; 5; 9; 10];
d_3d = sqrt(d_2d.^2 + (25-1.5)^2);
pl   = channel.pathloss_uma(d_2d, d_3d, 25, 1.5, 3.5, true(size(d_2d)));
verifyTrue(tc, all(isnan(pl(1:3))), 'PL must be NaN for d_2D < 10 m.');
verifyFalse(tc, isnan(pl(4)), 'PL must be defined at d_2D = 10 m.');
end

function test_freq_scaling(tc)
% Higher frequency → higher path loss (the +20*log10(fc) term dominates).
d_2d = 100;  d_3d = sqrt(d_2d^2 + (25-1.5)^2);
pl_low  = channel.pathloss_uma(d_2d, d_3d, 25, 1.5, 2.0, true);
pl_high = channel.pathloss_uma(d_2d, d_3d, 25, 1.5, 28.0, true);
% expected delta: 20*log10(28/2) ≈ 22.9 dB
delta = pl_high - pl_low;
expected = 20*log10(28/2);
verifyEqual(tc, delta, expected, 'AbsTol', 0.5, ...
            'Frequency scaling must follow 20*log10(fc) term.');
end

function test_reference_value_3p5ghz_100m(tc)
% Sanity reference: at 100 m, 3.5 GHz, h_BS=25, h_UT=1.5, LoS.
%   d_3D = sqrt(100^2 + 23.5^2) ≈ 102.73 m
%   PL1 = 28.0 + 22*log10(102.73) + 20*log10(3.5)
%       = 28.0 + 22 * 2.0117 + 20 * 0.5441
%       = 28.0 + 44.26 + 10.88
%       ≈ 83.14 dB
d_2d = 100;  d_3d = sqrt(d_2d^2 + (25-1.5)^2);
pl   = channel.pathloss_uma(d_2d, d_3d, 25, 1.5, 3.5, true);
verifyEqual(tc, pl, 83.14, 'AbsTol', 0.5, ...
            'Reference LoS PL at 100 m / 3.5 GHz mismatch.');
end
