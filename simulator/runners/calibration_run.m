function calibration_run(out_dir, opts)
%CALIBRATION_RUN  Generate a per-sample KPI parquet for Phase 3 KS-test.
%
% Runs ``opts.n_ue`` independent random straight-line trajectories
% through a multi-tier hex layout, each with its own seed. Per-sample
% KPIs are written to ``<out_dir>/kpis_ue<id>.parquet`` (one file per UE
% so each can be inspected individually) and the HO event-loop log to
% ``<out_dir>/events_ue<id>.parquet``.
%
% Default opts give ~24 k samples × 12 UEs ≈ 290 k rows — plenty for a
% stable KS-test against NordicDat's ~43 k-row largest segment.
%
% Args:
%   out_dir : char/string, output directory
%   opts    : struct (all fields optional, defaults shown):
%     .n_ue          (12)        number of independent UEs
%     .t_total_s     (240)       trajectory duration
%     .area_m        (1500)      half-width of the start/end sample box
%     .speed_mps     (10)        constant speed (m/s; ~36 km/h, urban)
%     .n_tiers       (2)         hex layout tiers (1=7 cells, 2=19, 3=37)
%     .isd_m         (default)   ISD override (m)
%     .fc_ghz        (default)   carrier override (GHz)
%     .scenario      ('UMa')     'UMa' or 'UMi'
%     .ran_type      ('NR_SA')   thesis scope: 5G intra-RAT inter-gNB Xn HO
%     .band          ('n78')     5G NR FR1 band, 3.5 GHz
%     .master_seed   (1)         per-UE seed = master_seed * 100 + ue_id

arguments
    out_dir (1,1) string
    opts    (1,1) struct = struct()
end

c = utils.constants();

% --- option defaults ---
opts = setfield_default(opts, 'n_ue',        12);
opts = setfield_default(opts, 't_total_s',   240);
opts = setfield_default(opts, 'area_m',      1500);
opts = setfield_default(opts, 'speed_mps',   10);
opts = setfield_default(opts, 'n_tiers',     2);
opts = setfield_default(opts, 'isd_m',       c.default_isd_m);
opts = setfield_default(opts, 'fc_ghz',      c.default_fc_ghz);
opts = setfield_default(opts, 'scenario',    c.default_scenario);
opts = setfield_default(opts, 'ran_type',    "NR_SA");
opts = setfield_default(opts, 'band',        "n78");
opts = setfield_default(opts, 'master_seed', 1);

if ~exist(out_dir, 'dir'), mkdir(out_dir); end

% --- layout (shared across UEs) ---
cells = utils.hex_layout(opts.n_tiers, opts.isd_m, c.default_h_bs_m);

% --- per-UE loop ---
ho_params_base = struct( ...
    'dt_s',          c.sim_dt_s, ...
    'l3_alpha',      0.5, ...
    'ttt_s',         c.default_ttt_ms / 1000, ...
    'hyst_db',       c.default_hyst_db, ...
    'a3_offset_db',  c.default_a3_off_db, ...
    'ho_exec_s',     0.050, ...
    'rlf_qout_db',   c.rlf_sinr_threshold_db, ...
    'rlf_qin_db',    c.in_sync_sinr_db, ...
    'n310',          c.default_N310, ...
    'n311',          c.default_N311, ...
    't310_s',        c.default_T310_ms / 1000, ...
    'ping_pong_s',   c.ping_pong_window_s);

meta = struct( ...
    'fc_ghz',   opts.fc_ghz, ...
    'isd_m',    opts.isd_m, ...
    'h_bs_m',   c.default_h_bs_m, ...
    'h_ut_m',   c.default_h_ut_m, ...
    'scenario', opts.scenario, ...
    'ran_type', opts.ran_type, ...
    'band',     opts.band, ...
    'ttt_ms',   c.default_ttt_ms, ...
    'hyst_db',  c.default_hyst_db, ...
    'a3_off_db',c.default_a3_off_db);

total_samples = 0;
total_events  = 0;
t0 = tic;

for ue_id = 1:opts.n_ue
    seed = opts.master_seed * 100 + ue_id;
    rng(seed);                       % deterministic per UE

    % Random straight-line trajectory across the layout
    p0 = (rand(1, 2) * 2 - 1) * opts.area_m;     % start (x,y)
    p1 = (rand(1, 2) * 2 - 1) * opts.area_m;     % end (x,y)
    % enforce min displacement so we actually sample distance variation
    if norm(p1 - p0) < 200
        p1 = p0 + (p1 - p0) / max(norm(p1 - p0), 1) * 200;
    end
    waypoints = [p0(1), p0(2), 0; p1(1), p1(2), opts.t_total_s];
    ue_track = mobility.waypoint_track(waypoints, c.sim_dt_s);

    meas_params = struct( ...
        'fc_ghz',        opts.fc_ghz, ...
        'h_bs_m',        c.default_h_bs_m, ...
        'h_ut_m',        c.default_h_ut_m, ...
        'p_tx_dbm',      c.default_p_tx_dbm, ...
        'g_tx_dbi',      c.default_g_tx_dbi, ...
        'g_rx_dbi',      c.default_g_rx_dbi, ...
        'n_rb',          c.default_n_rb, ...
        'noise_bw_hz',   c.default_noise_bw_hz, ...
        'nf_db',         c.default_nf_db, ...
        'scenario',      opts.scenario, ...
        'sigma_los_db',  c.sigma_los_db, ...
        'sigma_nlos_db', c.sigma_nlos_db, ...
        'shadow_corr_m', c.shadow_corr_m_uma, ...
        'los_corr_m',    c.los_corr_m_uma, ...
        'seed',          seed);
    meas = ho.measurements(ue_track, cells, meas_params);

    % HO event loop (for events.parquet)
    ho_params = ho_params_base;
    ho_params.ue_id = ue_id;
    [events, ~] = ho.event_loop(ue_track, cells, meas, ho_params);

    % Write parquet (per-UE files; one big merge happens in Python)
    kpi_fp = fullfile(out_dir, sprintf('kpis_ue%02d.parquet', ue_id));
    ev_fp  = fullfile(out_dir, sprintf('events_ue%02d.parquet', ue_id));
    meta_run = meta;
    meta_run.seed = seed;
    n_s = utils.write_kpis_parquet(kpi_fp, ue_id, ue_track, cells, meas, meta_run);
    n_e = utils.write_events_parquet(ev_fp, events, meta_run);
    total_samples = total_samples + n_s;
    total_events  = total_events  + n_e;

    fprintf('  UE %2d  seed=%d  samples=%d  events=%d\n', ...
            ue_id, seed, n_s, n_e);
end

% Run metadata JSON sidecar
meta_path = fullfile(out_dir, 'run_metadata.json');
meta_out = struct();
meta_out.fc_ghz   = opts.fc_ghz;
meta_out.isd_m    = opts.isd_m;
meta_out.n_tiers  = opts.n_tiers;
meta_out.n_cells  = numel(cells);
meta_out.n_ue     = opts.n_ue;
meta_out.t_total_s= opts.t_total_s;
meta_out.area_m   = opts.area_m;
meta_out.speed_mps= opts.speed_mps;
meta_out.scenario = char(opts.scenario);
meta_out.ran_type = char(opts.ran_type);
meta_out.band     = char(opts.band);
meta_out.master_seed   = opts.master_seed;
meta_out.total_samples = total_samples;
meta_out.total_events  = total_events;
meta_out.elapsed_s     = toc(t0);
fid = fopen(meta_path, 'w');
fprintf(fid, '%s\n', jsonencode(meta_out, 'PrettyPrint', true));
fclose(fid);

fprintf('\n%d UEs, %d samples, %d events written to %s in %.1f s.\n', ...
        opts.n_ue, total_samples, total_events, out_dir, toc(t0));
end


function s = setfield_default(s, fname, dflt)
    if ~isfield(s, fname), s.(fname) = dflt; end
end
