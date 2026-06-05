function c = constants()
%CONSTANTS  Physical + default simulation constants.
%
% Single source of truth for numeric constants used across the simulator.
% Returns a struct so callers can `c = utils.constants(); c.speed_of_light`.
%
% References:
%   [1] 3GPP TS 38.214 v17.x.x, "NR; Physical layer procedures for data"
%   [2] 3GPP TR 38.901 v17.1.0, "Study on channel model for 0.5–100 GHz"
%   [3] 3GPP TS 38.104 v17.x.x, "NR; Base Station (BS) radio transmission and reception"

c = struct();

% --- physical ---
c.speed_of_light       = 299792458;        % m/s
c.boltzmann            = 1.380649e-23;     % J/K
c.kelvin_0c            = 273.15;           % 0 °C in K

% --- thermal noise (typical 5G) ---
c.thermal_noise_dbm_hz = -174;             % -174 dBm/Hz at 290 K (kT)

% --- default carrier / bandwidth ---
c.default_fc_ghz       = 3.5;              % n78 band center, common 5G NR FR1
c.default_bw_mhz       = 100;              % 100 MHz channel BW
c.default_n_rb         = 273;              % 100 MHz @ 30 kHz SCS, per TS 38.104
c.default_scs_khz      = 30;               % subcarrier spacing

% --- effective signal bandwidth for RSRP/SINR computations ---
% RSRP is measured per RE; for SINR conversion we use full BW noise.
c.default_noise_bw_hz  = c.default_bw_mhz * 1e6;

% --- default base station parameters (macro UMa) ---
% Per 3GPP TR 38.913 v17.x §6.1.6.1 (UMa, urban macro deployment):
c.default_h_bs_m       = 25;               % BS antenna height
c.default_p_tx_dbm     = 49;               % per TR 38.913 (high-power macro 5G NR)
c.default_g_tx_dbi     = 8;                % omnidirectional macro antenna gain (modest)
c.default_g_rx_dbi     = 0;                % UE omnidirectional
c.default_nf_db        = 7;                % UE noise figure (typical handset)

% --- default UE parameters ---
c.default_h_ut_m       = 1.5;              % UE antenna height (pedestrian)
c.default_ue_speed_mps = 5;                % default mobility speed (≈ 18 km/h, urban)

% --- default deployment ---
c.default_isd_m        = 500;              % macro UMa ISD per TR 38.913
c.default_scenario     = "UMa";            % "UMa" | "UMi"

% --- TR 38.901 channel-model parameters (UMa) ---
% Path-loss shadowing std (Table 7.4.1-1, UMa)
c.sigma_los_db         = 4;                % LoS shadow std
c.sigma_nlos_db        = 6;                % NLoS shadow std
% Decorrelation distances (Table 7.5-6, UMa)
c.shadow_corr_m_uma    = 37;               % shadow fading correlation distance, UMa
c.los_corr_m_uma       = 50;               % LoS-state correlation distance (TR 38.901 §7.6.3.3)
% UMi equivalents (Table 7.5-6, UMi-Street Canyon)
c.shadow_corr_m_umi    = 10;
c.los_corr_m_umi       = 15;

% --- simulation timing ---
c.sim_dt_s             = 0.010;            % 10 ms internal tick, per ADR-5
c.meas_report_period_s = 0.240;            % default reporting period (3GPP allowed value)

% --- L3 filtering (TS 38.331 §5.5.3.2) ---
% Coefficient k; filter coefficient a = 1 / 2^(k/4).
% Default k = 4 → a = 0.5 (a common starting point).
c.default_l3_filter_k  = 4;

% --- A3 event default (TS 38.331 §5.5.4.4) ---
c.default_ttt_ms       = 256;              % 3GPP-allowed value
c.default_hyst_db      = 2;                % 3GPP allowed step 0..30 dB / 0.5 dB
c.default_a3_off_db    = 0;                % 3GPP allowed range -30..+30 dB / 0.5 dB

% --- RLF (TS 38.331 §5.3.10) ---
c.default_T310_ms      = 1000;             % T310 timer
c.default_N310         = 6;                % out-of-sync indications to start T310
c.default_N311         = 2;                % in-sync indications to stop T310
c.rlf_sinr_threshold_db= -8;               % Qout-equivalent threshold (typical)
c.in_sync_sinr_db      = -6;               % Qin-equivalent (typical)

% --- ping-pong (HO-then-HO-back) ---
% Default 5 s window aligns with 3GPP TR 36.839 §6.1 and academic
% conventions (e.g., Alhammadi 2023, Farooq 2022). The MTS / MRO
% specifications allow operator configuration in 1..30 s range.
c.ping_pong_window_s   = 5.0;
end
