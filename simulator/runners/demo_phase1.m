%DEMO_PHASE1  Phase 1 smoke test: 1 UE walks across a 7-cell hex layout.
%
% Phase 1.1 (this version) features:
%   - Spatially-correlated shadowing (TR 38.901 Table 7.5-6)
%   - Piecewise-constant LoS state (TR 38.901 §7.6.3.3)
%   - RSRP, RSRQ, SINR per (UE, cell, t)
%
% Run:
%   addpath(genpath('simulator'));
%   demo_phase1
%
% Produces:
%   - Figure 1: cell layout + UE trajectory
%   - Figure 2: RSRP / RSRQ / SINR vs time per cell (3 subplots)
%   - Console: sanity stats
%
% Acceptance: RSRP curves are smooth (no per-tick zigzag); serving cell is
% well-defined per (t); SINR > 10 dB near each cell center.

addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));

c = utils.constants();

% --- scenario: 7-cell hex (1 tier), ISD 500 m, BS at 25 m ---
cells = utils.hex_layout(1, c.default_isd_m, c.default_h_bs_m);
fprintf('Built %d cells in 1 tier (ISD %d m, BS height %.0f m).\n', ...
        numel(cells), c.default_isd_m, c.default_h_bs_m);

% --- UE trajectory: straight east-west line, 5 m/s, 240 s ---
wp = [-600,  0,   0;
       600,  0, 240];
ue_track = mobility.waypoint_track(wp, c.sim_dt_s);
fprintf('UE trajectory: %d samples, %.0f s, mean %.1f m/s.\n', ...
        numel(ue_track.t_s), ue_track.t_s(end), mean(ue_track.v_abs));

% --- measurement params (UMa baseline) ---
params = struct( ...
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
    'seed',          42);

% --- compute measurements ---
meas = ho.measurements(ue_track, cells, params);

% --- sanity numbers ---
fprintf('--- summary ---\n');
fprintf('  Noise floor: %.2f dBm (BW %.0f MHz, NF %.0f dB)\n', ...
        c.thermal_noise_dbm_hz + 10*log10(params.noise_bw_hz) + params.nf_db, ...
        params.noise_bw_hz/1e6, params.nf_db);
[best_rsrp, best_cell] = max(meas.rsrp_dbm, [], 2);
serving_id = arrayfun(@(k) cells(k).id, best_cell);
fprintf('  Serving cell distribution:\n');
for k = 1:numel(cells)
    cnt = sum(serving_id == cells(k).id);
    fprintf('     cell %d : %5d ticks (%.1f%% of trajectory)\n', ...
            cells(k).id, cnt, 100*cnt/numel(serving_id));
end
fprintf('  Per-cell RSRP / RSRQ / SINR (median):\n');
for k = 1:numel(cells)
    fprintf('     cell %d : RSRP %6.1f dBm  RSRQ %5.1f dB  SINR %5.1f dB  (LoS frac %.2f)\n', ...
            cells(k).id, ...
            median(meas.rsrp_dbm(:, k), 'omitnan'), ...
            median(meas.rsrq_db(:, k),  'omitnan'), ...
            median(meas.sinr_db(:, k),  'omitnan'), ...
            mean(meas.los(:, k)));
end

% --- plots ---

% Figure 1: layout
f1 = figure('Name', 'Phase 1 demo — layout', 'Color', 'w', ...
            'Position', [100 100 800 600]);
hold on; axis equal; grid on;
theta = linspace(0, 2*pi, 64);
r_cell = c.default_isd_m / sqrt(3);
h_cell = []; h_bs = [];
for k = 1:numel(cells)
    h_tmp = fill(cells(k).x_m + r_cell*cos(theta), ...
                 cells(k).y_m + r_cell*sin(theta), ...
                 [0.92 0.94 1.0], 'EdgeColor', [0.5 0.5 0.7], ...
                 'HandleVisibility', 'off');
    if k == 1, h_cell = h_tmp; set(h_cell, 'HandleVisibility', 'on'); end
    h_tmp = plot(cells(k).x_m, cells(k).y_m, 'k^', ...
                 'MarkerSize', 8, 'MarkerFaceColor', 'k', ...
                 'HandleVisibility', 'off');
    if k == 1, h_bs = h_tmp; set(h_bs, 'HandleVisibility', 'on'); end
    text(cells(k).x_m, cells(k).y_m + 30, sprintf('C%d', cells(k).id), ...
         'HorizontalAlignment', 'center', 'FontWeight', 'bold');
end
h_ue    = plot(ue_track.x_m, ue_track.y_m, 'r-', 'LineWidth', 1.5);
h_start = plot(ue_track.x_m(1),   ue_track.y_m(1),   'go', ...
               'MarkerSize', 10, 'MarkerFaceColor', 'g');
h_end   = plot(ue_track.x_m(end), ue_track.y_m(end), 'rs', ...
               'MarkerSize', 10, 'MarkerFaceColor', 'r');
xlabel('x (m)'); ylabel('y (m)');
title(sprintf('Phase 1.1: 7-cell hex (ISD %d m) + UE track', c.default_isd_m));
legend([h_cell, h_bs, h_ue, h_start, h_end], ...
       {'cell area', 'BS', 'UE path', 'start', 'end'}, ...
       'Location', 'eastoutside');
hold off;

% Figure 2: RSRP / RSRQ / SINR
f2 = figure('Name', 'Phase 1 demo — RSRP / RSRQ / SINR', 'Color', 'w', ...
            'Position', [100 100 900 750]);
colors = lines(numel(cells));

subplot(3,1,1);
hold on; grid on;
for k = 1:numel(cells)
    plot(ue_track.t_s, meas.rsrp_dbm(:, k), 'Color', colors(k, :), 'LineWidth', 1.0);
end
ylabel('RSRP (dBm)'); xlim([0 ue_track.t_s(end)]);
title('Per-cell RSRP at the UE (Phase 1.1: correlated shadowing + LoS state)');
legend(arrayfun(@(c_) sprintf('cell %d', c_.id), cells, 'UniformOutput', false), ...
       'Location', 'eastoutside');

subplot(3,1,2);
hold on; grid on;
for k = 1:numel(cells)
    plot(ue_track.t_s, meas.rsrq_db(:, k), 'Color', colors(k, :), 'LineWidth', 1.0);
end
ylabel('RSRQ (dB)'); xlim([0 ue_track.t_s(end)]);
title('Per-cell RSRQ');

subplot(3,1,3);
hold on; grid on;
for k = 1:numel(cells)
    plot(ue_track.t_s, meas.sinr_db(:, k), 'Color', colors(k, :), 'LineWidth', 1.0);
end
ylabel('wideband SINR (dB)'); xlabel('time (s)'); xlim([0 ue_track.t_s(end)]);
title('Per-cell wideband SINR');

% --- save figures (for headless runs / thesis appendix) ---
fig_dir = fullfile(fileparts(mfilename('fullpath')), '..', '..', 'data', 'simulated', 'phase1_demo');
if ~exist(fig_dir, 'dir'), mkdir(fig_dir); end
exportgraphics(f1, fullfile(fig_dir, 'phase1_layout.png'), 'Resolution', 150);
exportgraphics(f2, fullfile(fig_dir, 'phase1_kpis.png'),   'Resolution', 150);
fprintf('Figures saved to %s\n', fig_dir);
fprintf('Demo done. Expect: smooth RSRP curves, serving-cell switches near\n');
fprintf('  the geometric mid-points between cells, SINR > 10 dB near cell centers.\n');
