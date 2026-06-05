%SWEEP_TTT_HYST  Small (TTT × hysteresis × seed) sweep for sanity check
%                against published trends (Alhammadi 2023, Farooq 2022).
%
% Expected qualitative trends:
%   - Increasing TTT (with hyst fixed) → fewer HO attempts, fewer ping-pongs,
%     potentially more RLF beyond an optimum.
%   - Increasing hyst (with TTT fixed) → similar effect, fewer attempts,
%     fewer ping-pongs.
%
% This is a v0 sanity sweep, not the full thesis sweep (Phase 4).
% Trajectory: same Phase-1 east-west walk, shortened to 120 s. 3 seeds per
% (TTT, hyst) cell. ~120 single-UE runs ≈ 60 seconds wall-clock.

addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));

c = utils.constants();
cells = utils.hex_layout(1, c.default_isd_m, c.default_h_bs_m);
wp = [-600, 0, 0; 600, 0, 240];
ue_track = mobility.waypoint_track(wp, c.sim_dt_s);

ttt_grid_ms = [40, 64, 128, 256, 480, 1024];
hyst_grid_db = [0, 1, 2, 3];
seeds = 1:3;

meas_params_base = struct( ...
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
    'los_corr_m',    c.los_corr_m_uma);

ho_params_base = struct( ...
    'ue_id',         1, ...
    'dt_s',          c.sim_dt_s, ...
    'l3_alpha',      0.5, ...
    'a3_offset_db',  c.default_a3_off_db, ...
    'ho_exec_s',     0.050, ...
    'rlf_qout_db',   c.rlf_sinr_threshold_db, ...
    'rlf_qin_db',    c.in_sync_sinr_db, ...
    'n310',          c.default_N310, ...
    'n311',          c.default_N311, ...
    't310_s',        c.default_T310_ms / 1000, ...
    'ping_pong_s',   c.ping_pong_window_s);

n_ttt  = numel(ttt_grid_ms);
n_hyst = numel(hyst_grid_db);
n_seed = numel(seeds);

% --- result tensors (n_ttt × n_hyst × n_seed) ---
ho_attempt = zeros(n_ttt, n_hyst, n_seed);
ho_success = zeros(n_ttt, n_hyst, n_seed);
ho_fail    = zeros(n_ttt, n_hyst, n_seed);
rlf_count  = zeros(n_ttt, n_hyst, n_seed);
pp_count   = zeros(n_ttt, n_hyst, n_seed);

fprintf('Running sweep: %d TTT × %d hyst × %d seed = %d runs\n', ...
        n_ttt, n_hyst, n_seed, n_ttt*n_hyst*n_seed);
t0 = tic;
for is = 1:n_seed
    meas_params = meas_params_base;
    meas_params.seed = seeds(is);
    meas = ho.measurements(ue_track, cells, meas_params);
    for it = 1:n_ttt
        for ih = 1:n_hyst
            ho_params = ho_params_base;
            ho_params.ttt_s   = ttt_grid_ms(it) / 1000;
            ho_params.hyst_db = hyst_grid_db(ih);

            [events, ~] = ho.event_loop(ue_track, cells, meas, ho_params);

            ho_attempt(it, ih, is) = sum(arrayfun(@(e) e.event_type == "HO_ATTEMPT", events));
            ho_success(it, ih, is) = sum(arrayfun(@(e) e.event_type == "HO_SUCCESS", events));
            ho_fail(it, ih, is)    = sum(arrayfun(@(e) e.event_type == "HO_FAIL",    events));
            rlf_count(it, ih, is)  = sum(arrayfun(@(e) e.event_type == "RLF",        events));
            pp_count(it, ih, is)   = sum(arrayfun(@(e) e.event_type == "PING_PONG",  events));
        end
    end
    fprintf('  seed %d done (%.1f s elapsed)\n', seeds(is), toc(t0));
end

% --- mean over seeds ---
ho_attempt_mean = mean(ho_attempt, 3);
ho_success_mean = mean(ho_success, 3);
hosr_mean       = ho_success_mean ./ max(ho_attempt_mean, 1);
pp_rate_mean    = mean(pp_count, 3) ./ max(ho_attempt_mean, 1);
rlf_mean        = mean(rlf_count, 3);

% --- print tables ---
fprintf('\n=== HO_ATTEMPT count (mean over %d seeds), rows TTT, cols hyst ===\n', n_seed);
print_table(ho_attempt_mean, ttt_grid_ms, hyst_grid_db, '%6.2f');
fprintf('\n=== HOSR (success / attempt) ===\n');
print_table(hosr_mean, ttt_grid_ms, hyst_grid_db, '%6.3f');
fprintf('\n=== Ping-pong rate (pp / attempt) ===\n');
print_table(pp_rate_mean, ttt_grid_ms, hyst_grid_db, '%6.3f');
fprintf('\n=== RLF count (mean over %d seeds) ===\n', n_seed);
print_table(rlf_mean, ttt_grid_ms, hyst_grid_db, '%6.2f');

% --- heatmaps ---
f1 = figure('Name', 'Phase 2 sweep — HO_ATTEMPT count', 'Color', 'w', ...
            'Position', [100 100 800 500]);
plot_heatmap(ho_attempt_mean, ttt_grid_ms, hyst_grid_db, ...
             'HO_ATTEMPT count (mean over seeds)');

f2 = figure('Name', 'Phase 2 sweep — Ping-pong rate', 'Color', 'w', ...
            'Position', [100 100 800 500]);
plot_heatmap(pp_rate_mean, ttt_grid_ms, hyst_grid_db, ...
             'Ping-pong rate (pp / attempt)');

% --- save ---
fig_dir = fullfile(fileparts(mfilename('fullpath')), '..', '..', 'data', 'simulated', 'phase2_sweep');
if ~exist(fig_dir, 'dir'), mkdir(fig_dir); end
exportgraphics(f1, fullfile(fig_dir, 'sweep_ho_attempt.png'), 'Resolution', 150);
exportgraphics(f2, fullfile(fig_dir, 'sweep_ping_pong.png'),  'Resolution', 150);
% also save tensors for later analysis
save(fullfile(fig_dir, 'sweep_results.mat'), ...
     'ttt_grid_ms', 'hyst_grid_db', 'seeds', ...
     'ho_attempt', 'ho_success', 'ho_fail', 'rlf_count', 'pp_count');
fprintf('\nFigures + MAT saved to %s\n', fig_dir);
fprintf('Total wall time: %.1f s\n', toc(t0));


% =========================================================================
% helpers
% =========================================================================
function print_table(M, ttt_grid_ms, hyst_grid_db, fmt)
    n_h = numel(hyst_grid_db);
    fprintf('  TTT(ms) |');
    for h = hyst_grid_db, fprintf(' hyst=%-2d dB', h); end
    fprintf('\n');
    fprintf('  --------|');
    for ih = 1:n_h, fprintf('-----------'); end
    fprintf('\n');
    for it = 1:size(M, 1)
        fprintf('  %7d |', ttt_grid_ms(it));
        for ih = 1:n_h
            fprintf('  '); fprintf(fmt, M(it, ih)); fprintf('   ');
        end
        fprintf('\n');
    end
end

function plot_heatmap(M, ttt_grid_ms, hyst_grid_db, ttl)
    imagesc(M); colorbar;
    xticks(1:numel(hyst_grid_db));
    xticklabels(arrayfun(@(x) sprintf('%d dB', x), hyst_grid_db, 'UniformOutput', false));
    yticks(1:numel(ttt_grid_ms));
    yticklabels(arrayfun(@(x) sprintf('%d ms', x), ttt_grid_ms, 'UniformOutput', false));
    xlabel('hysteresis'); ylabel('TTT');
    title(ttl);
    % annotate cells
    for i = 1:size(M,1)
        for j = 1:size(M,2)
            txt = sprintf('%.2f', M(i, j));
            text(j, i, txt, 'HorizontalAlignment', 'center', ...
                 'Color', 'w', 'FontWeight', 'bold');
        end
    end
    colormap(parula);
end
