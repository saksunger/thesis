%DEMO_PHASE2  Phase 2 smoke test: full HO event loop on a single UE.
%
% Reuses the Phase 1.1 channel + measurement pipeline, then runs the
% Phase 2 RRC-layer event loop:
%   L3 filter → A3 event (TTT, hyst, A3-offset) → HO execution → RLF → ping-pong
%
% Outputs:
%   - Console:
%       * end-of-run counters (HO_ATTEMPT / HO_SUCCESS / HO_FAIL / RLF / PING_PONG)
%       * event list (first ~20 rows)
%   - Figure 1: serving cell ID over time + HO event markers
%   - Figure 2: L3-filtered RSRP per cell + serving cell highlighted
%   - PNGs saved to data/simulated/phase2_demo/

addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));

c = utils.constants();

% --- scenario: same as Phase 1 demo (7-cell hex, UE walks east-west) ---
cells = utils.hex_layout(1, c.default_isd_m, c.default_h_bs_m);
wp = [-600,  0,   0;
       600,  0, 240];
ue_track = mobility.waypoint_track(wp, c.sim_dt_s);

% --- measurement (Phase 1.1) ---
meas_params = struct( ...
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
meas = ho.measurements(ue_track, cells, meas_params);

% --- HO event loop config ---
ho_params = struct( ...
    'ue_id',         1, ...
    'dt_s',          c.sim_dt_s, ...
    'l3_alpha',      0.5, ...                    % k = 4 default
    'ttt_s',         c.default_ttt_ms / 1000, ... % 256 ms
    'hyst_db',       c.default_hyst_db, ...      % 2 dB
    'a3_offset_db',  c.default_a3_off_db, ...    % 0 dB
    'ho_exec_s',     0.050, ...                  % 50 ms execution
    'rlf_qout_db',   c.rlf_sinr_threshold_db, ...
    'rlf_qin_db',    c.in_sync_sinr_db, ...
    'n310',          c.default_N310, ...
    'n311',          c.default_N311, ...
    't310_s',        c.default_T310_ms / 1000, ...
    'ping_pong_s',   c.ping_pong_window_s);

fprintf('Running HO event loop (T=%d ticks, TTT=%.0f ms, hyst=%.1f dB)...\n', ...
        numel(ue_track.t_s), ho_params.ttt_s*1000, ho_params.hyst_db);
tic;
[events, history] = ho.event_loop(ue_track, cells, meas, ho_params);
toc;

% --- end-of-run counters ---
fprintf('--- end-of-run counters ---\n');
event_types = ["HO_ATTEMPT","HO_SUCCESS","HO_FAIL","RLF","PING_PONG"];
for et = event_types
    cnt = sum(arrayfun(@(e) e.event_type == et, events));
    fprintf('  %-12s : %d\n', et, cnt);
end
n_attempt = sum(arrayfun(@(e) e.event_type == "HO_ATTEMPT", events));
n_success = sum(arrayfun(@(e) e.event_type == "HO_SUCCESS", events));
n_fail    = sum(arrayfun(@(e) e.event_type == "HO_FAIL",    events));
hosr = n_success / max(n_attempt, 1);
fprintf('  HOSR (success / attempts) : %.2f\n', hosr);

% --- event list (first 20) ---
fprintf('--- events (first %d) ---\n', min(20, numel(events)));
for k = 1:min(20, numel(events))
    e = events(k);
    fprintf('  [%2d] t=%6.2fs  %-11s  C%d -> C%d  (RSRP serv=%6.1f / tgt=%6.1f dBm)\n', ...
            e.event_id, e.event_time_s, e.event_type, ...
            e.source_cell_id, e.target_cell_id, ...
            e.rsrp_serv_pre_dbm, e.rsrp_tgt_pre_dbm);
end

% --- Figure 1: serving cell over time + event markers ---
f1 = figure('Name', 'Phase 2 — serving cell over time', 'Color', 'w', ...
            'Position', [100 100 1000 500]);
hold on; grid on;
serving_ids = arrayfun(@(idx) cells(idx).id, history.serving_idx);
stairs(ue_track.t_s, serving_ids, 'b-', 'LineWidth', 1.5);
% overlay event markers
markers = struct( ...
    'HO_ATTEMPT', struct('color', [1.0 0.6 0.0], 'sym', 'o'), ...
    'HO_SUCCESS', struct('color', [0.0 0.7 0.0], 'sym', 's'), ...
    'HO_FAIL',    struct('color', [0.8 0.0 0.0], 'sym', 'x'), ...
    'RLF',        struct('color', [0.6 0.0 0.6], 'sym', 'd'), ...
    'PING_PONG',  struct('color', [0.5 0.5 0.5], 'sym', 'v'));
labels = string.empty(); h_legend = [];
for et = fieldnames(markers).'
    idx = arrayfun(@(e) e.event_type == et{1}, events);
    if any(idx)
        e_sub = events(idx);
        h = scatter([e_sub.event_time_s], [e_sub.target_cell_id], 60, ...
                    markers.(et{1}).color, 'filled', ...
                    'Marker', markers.(et{1}).sym, 'MarkerEdgeColor', 'k');
        labels(end+1) = string(et{1}); %#ok<SAGROW>
        h_legend(end+1) = h;            %#ok<SAGROW>
    end
end
yticks(1:numel(cells)); ylim([0.5, numel(cells)+0.5]);
xlim([0 ue_track.t_s(end)]);
xlabel('time (s)'); ylabel('serving cell id');
title(sprintf('Phase 2 demo: serving cell + HO events (TTT %.0f ms, hyst %.1f dB)', ...
              ho_params.ttt_s*1000, ho_params.hyst_db));
legend([{'serving cell'}, cellstr(labels)], 'Location', 'eastoutside');

% --- Figure 2: L3-filtered RSRP, serving cell highlighted ---
f2 = figure('Name', 'Phase 2 — L3-filtered RSRP', 'Color', 'w', ...
            'Position', [100 100 1000 500]);
hold on; grid on;
colors = lines(numel(cells));
for k = 1:numel(cells)
    plot(ue_track.t_s, history.rsrp_l3_dbm(:, k), '-', ...
         'Color', [colors(k,:), 0.5], 'LineWidth', 0.8, ...
         'DisplayName', sprintf('cell %d (L3)', cells(k).id));
end
% overlay serving cell RSRP in bold
serving_rsrp = arrayfun(@(ti) history.rsrp_l3_dbm(ti, history.serving_idx(ti)), ...
                        (1:numel(ue_track.t_s)).');
plot(ue_track.t_s, serving_rsrp, 'k-', 'LineWidth', 2.0, ...
     'DisplayName', 'serving (L3)');
ylabel('RSRP (dBm, L3-filtered)'); xlabel('time (s)');
title('Per-cell L3 RSRP; serving cell highlighted (black)');
legend('Location', 'eastoutside'); xlim([0 ue_track.t_s(end)]);

% --- save PNGs ---
fig_dir = fullfile(fileparts(mfilename('fullpath')), '..', '..', 'data', 'simulated', 'phase2_demo');
if ~exist(fig_dir, 'dir'), mkdir(fig_dir); end
exportgraphics(f1, fullfile(fig_dir, 'phase2_serving.png'), 'Resolution', 150);
exportgraphics(f2, fullfile(fig_dir, 'phase2_l3rsrp.png'),  'Resolution', 150);
fprintf('Figures saved to %s\n', fig_dir);
