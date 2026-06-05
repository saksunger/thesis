function meas = measurements(ue_track, cells, params)
%MEASUREMENTS  Per-(UE, cell, time) RSRP / RSRQ / SINR.
%
% Pipeline:
%   1. LoS state per (t, k) from `channel.los_state` (correlated over arc length)
%   2. Shadow field per (t, k) from `channel.shadowing_field` (correlated 2-D GRF)
%      scaled by sigma_LoS / sigma_NLoS per LoS state
%   3. UMa path-loss per (t, k) from `channel.pathloss_uma`
%   4. RSRP_per_RE = EIRP - PL - shadow - 10*log10(12*N_RB)
%   5. Per-cell total carrier power, sum across cells -> wideband received power
%   6. Thermal noise: -174 + 10*log10(BW) + NF
%   7. RSSI = total received + noise
%   8. RSRQ = N_RB * RSRP / RSSI (linear), in dB
%   9. Wideband SINR per cell: P_cell / (P_others + noise)
%
% RSRP is defined per 3GPP TS 38.215 as the linear average over the REs
% carrying the secondary synchronization signal; in our abstraction it is
% the per-RE share of the per-cell EIRP minus PL minus shadowing.
%
% RSRQ definition (TS 38.215 / TS 36.214 §5.1.3): RSRQ = N * RSRP / RSSI,
% where N is the number of RBs in the measurement bandwidth and RSSI is
% the total received wideband power.
%
% Args:
%   ue_track : struct from `mobility.waypoint_track`
%   cells    : struct array from `utils.hex_layout`
%   params   : struct, fields below
%
% Required params:
%   .fc_ghz, .h_bs_m, .h_ut_m
%   .p_tx_dbm, .g_tx_dbi, .g_rx_dbi
%   .n_rb, .noise_bw_hz, .nf_db
%   .scenario      ("UMa" | "UMi")
%   .sigma_los_db, .sigma_nlos_db
%   .shadow_corr_m, .los_corr_m
%   .seed
%
% Returns:
%   meas : struct with fields (all T × K)
%       .rsrp_dbm   per-cell RSRP per RE, dBm
%       .rsrq_db    per-cell RSRQ, dB
%       .sinr_db    per-cell wideband SINR, dB
%       .rssi_dbm   wideband RSSI (broadcast across cells, T × 1), dBm
%       .los        per-cell LoS state, logical T × K
%       .pl_db      per-cell path loss, dB
%       .shadow_db  per-cell shadowing realization (dB)

arguments
    ue_track (1,1) struct
    cells    (:,1) struct
    params   (1,1) struct
end

T = numel(ue_track.t_s);
K = numel(cells);

% --- 1. LoS state ---
los = channel.los_state(ue_track, cells, params.h_ut_m, ...
                        params.scenario, params.los_corr_m, params.seed);

% --- 2. Shadow field (standard normal), scaled by sigma per LoS state ---
shadow_std = channel.shadowing_field(ue_track, cells, ...
                                     params.shadow_corr_m, params.seed + 1);
sigma_per_sample_db = params.sigma_los_db * los + params.sigma_nlos_db * ~los;
shadow_db = shadow_std .* sigma_per_sample_db;

% --- 3. Path loss per cell ---
% TR 38.901 Table 7.4.1-1 path-loss formulas are valid for d_2D >= 10 m.
% For closer distances (UE right under the BS), we clip d_2D to 10 m to
% avoid NaN propagation and to provide a sensible upper bound on PL.
% This matches the convention used by most academic NR simulators.
%
% Dispatch on scenario:
%   "UMa" → channel.pathloss_uma  (TR 38.901 Table 7.4.1-1 UMa LoS/NLoS)
%   "UMi" → channel.pathloss_umi  (TR 38.901 Table 7.4.1-1 UMi-Street Canyon)
switch upper(string(params.scenario))
    case "UMA"
        pl_fn = @channel.pathloss_uma;
    case "UMI"
        pl_fn = @channel.pathloss_umi;
    otherwise
        error('measurements:unknown_scenario', ...
              'unknown scenario "%s" (expected "UMa" or "UMi")', params.scenario);
end

pl_db = nan(T, K);
for k = 1:K
    dx = ue_track.x_m - cells(k).x_m;
    dy = ue_track.y_m - cells(k).y_m;
    dz = params.h_ut_m - cells(k).z_m;
    d_2d = hypot(dx, dy);
    d_2d_clip = max(d_2d, 10);                       % validity clip per TR 38.901
    d_3d = sqrt(d_2d_clip.^2 + dz.^2);
    pl_db(:, k) = pl_fn(d_2d_clip, d_3d, ...
                        cells(k).z_m, params.h_ut_m, ...
                        params.fc_ghz, los(:, k));
end

% --- 4. RSRP per RE ---
eirp_dbm   = params.p_tx_dbm + params.g_tx_dbi + params.g_rx_dbi;
re_norm_db = 10 * log10(12 * params.n_rb);
rsrp_dbm   = eirp_dbm - pl_db - shadow_db - re_norm_db;

% --- 5. Per-cell total carrier power, sum across cells ---
cell_total_dbm = rsrp_dbm + re_norm_db;
cell_total_mw  = 10.^(cell_total_dbm / 10);
% NaNs (d_2D < 10 m) treated as 0 mW for the sum (cell is "too close to evaluate")
cell_total_mw(isnan(cell_total_mw)) = 0;

total_rx_mw = sum(cell_total_mw, 2);   % T × 1

% --- 6. Thermal noise ---
c = utils.constants();
noise_dbm = c.thermal_noise_dbm_hz + 10*log10(params.noise_bw_hz) + params.nf_db;
noise_mw  = 10.^(noise_dbm / 10);

% --- 7. RSSI ---
rssi_mw  = total_rx_mw + noise_mw;
rssi_dbm = 10 * log10(rssi_mw);

% --- 8. RSRQ per cell ---
rsrq_db = 10*log10(params.n_rb) + rsrp_dbm - rssi_dbm;

% --- 9. Wideband SINR per cell ---
sinr_db = nan(T, K);
all_idx = 1:K;
for k = 1:K
    p_k    = cell_total_mw(:, k);
    p_int  = sum(cell_total_mw(:, all_idx ~= k), 2);
    sinr_db(:, k) = 10 * log10(p_k ./ (p_int + noise_mw));
end

meas = struct( ...
    'rsrp_dbm',  rsrp_dbm, ...
    'rsrq_db',   rsrq_db, ...
    'sinr_db',   sinr_db, ...
    'rssi_dbm',  rssi_dbm, ...
    'los',       los, ...
    'pl_db',     pl_db, ...
    'shadow_db', shadow_db);
end
