function tests = test_measurements
%TEST_MEASUREMENTS  Unit tests for `ho.measurements` (Phase 1.1).
tests = functiontests(localfunctions);
end

function setupOnce(testCase) %#ok<INUSD>
addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));
end

function p = default_params()
c = utils.constants();
p = struct( ...
    'fc_ghz',        c.default_fc_ghz, ...
    'h_bs_m',        c.default_h_bs_m, ...
    'h_ut_m',        c.default_h_ut_m, ...
    'p_tx_dbm',      c.default_p_tx_dbm, ...
    'g_tx_dbi',      c.default_g_tx_dbi, ...
    'g_rx_dbi',      c.default_g_rx_dbi, ...
    'n_rb',          c.default_n_rb, ...
    'noise_bw_hz',   c.default_noise_bw_hz, ...
    'nf_db',         c.default_nf_db, ...
    'scenario',      c.default_scenario, ...
    'sigma_los_db',  c.sigma_los_db, ...
    'sigma_nlos_db', c.sigma_nlos_db, ...
    'shadow_corr_m', c.shadow_corr_m_uma, ...
    'los_corr_m',    c.los_corr_m_uma, ...
    'seed',          1);
end

function test_output_shape(tc)
cells = utils.hex_layout(1, 500, 25);
wp = [-300, 0, 0; 300, 0, 120];
tr = mobility.waypoint_track(wp, 0.01);
m  = ho.measurements(tr, cells, default_params());
T = numel(tr.t_s); K = numel(cells);
verifyEqual(tc, size(m.rsrp_dbm), [T K]);
verifyEqual(tc, size(m.rsrq_db),  [T K]);
verifyEqual(tc, size(m.sinr_db),  [T K]);
verifyEqual(tc, size(m.los),      [T K]);
verifyEqual(tc, size(m.rssi_dbm), [T 1]);
end

function test_serving_cell_near_center(tc)
% When UE is near cell 1 center, cell 1 should be serving (max RSRP).
cells = utils.hex_layout(1, 500, 25);
wp = [-10, 0, 0; 10, 0, 4];      % UE wiggles ±10 m around origin
tr = mobility.waypoint_track(wp, 0.01);
m  = ho.measurements(tr, cells, default_params());
[~, best] = max(m.rsrp_dbm, [], 2);
% At least 80% of ticks should select cell 1 as serving
frac_c1 = mean(best == 1);
verifyTrue(tc, frac_c1 > 0.8, ...
    sprintf('Expected cell 1 to dominate near origin, got %.2f fraction.', frac_c1));
end

function test_rsrq_within_3gpp_range(tc)
% RSRQ valid-range checks:
%   (a) global upper bound is structural: RSRQ_max = -10*log10(12) ≈ -10.79 dB
%       (single-cell-dominant + no noise), so RSRQ must be < -10 dB for all cells.
%   (b) serving cell (per tick) should sit in the "normal" range [-20, -10] dB.
%       Per-cell across the whole trajectory can go very negative for cells
%       far from the UE (interferer-dominated), which is physical.
cells = utils.hex_layout(1, 500, 25);
wp = [-600, 0, 0; 600, 0, 240];
tr = mobility.waypoint_track(wp, 0.05);
m  = ho.measurements(tr, cells, default_params());

% (a) structural upper bound
v_all = m.rsrq_db(~isnan(m.rsrq_db));
verifyTrue(tc, all(v_all < -10), ...
    sprintf('RSRQ exceeds structural max -10.79 dB: max=%.2f', max(v_all)));

% (b) serving cell band
serving_rsrq = max(m.rsrq_db, [], 2);
v_srv = serving_rsrq(~isnan(serving_rsrq));
verifyTrue(tc, median(v_srv) > -16 && median(v_srv) < -10, ...
    sprintf('Median serving RSRQ outside [-16, -10] dB: %.2f', median(v_srv)));
end

function test_sinr_positive_at_cell_center(tc)
% Right at a cell center the serving SINR should be very high (>= 20 dB).
cells = utils.hex_layout(1, 500, 25);
wp = [0, 0, 0; 0, 0, 0.1];
tr = mobility.waypoint_track(wp, 0.01);
m  = ho.measurements(tr, cells, default_params());
[best_sinr, ~] = max(m.sinr_db, [], 2);
v = best_sinr(~isnan(best_sinr));
verifyTrue(tc, median(v) > 20, ...
    sprintf('Median serving SINR at cell center too low: %.2f dB', median(v)));
end

function test_shadow_correlation_smooths_rsrp(tc)
% With correlated shadowing, per-sample RSRP differences should be small
% in absolute value (no >5 dB jumps over 10 ms ticks for serving cell).
cells = utils.hex_layout(1, 500, 25);
wp = [-300, 0, 0; 300, 0, 120];     % 5 m/s
tr = mobility.waypoint_track(wp, 0.01);
m  = ho.measurements(tr, cells, default_params());
serving_rsrp = max(m.rsrp_dbm, [], 2);
d = diff(serving_rsrp);
% allow some outliers due to serving-cell switching, check 99th percentile
verifyTrue(tc, prctile(abs(d), 99) < 5, ...
    sprintf('99th-pct tick-to-tick |dRSRP| too large: %.2f dB', prctile(abs(d), 99)));
end
