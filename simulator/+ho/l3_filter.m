function state = l3_filter(state, raw_rsrp_dbm, raw_rsrq_db, raw_sinr_db, alpha)
%L3_FILTER  Apply 3GPP TS 38.331 §5.5.3.2 Layer-3 filter to raw L1 samples.
%
% Filter update (per sample) for each measurement quantity Q:
%
%   F_n = (1 - alpha) * F_{n-1} + alpha * M_n
%
% where `alpha = 1 / 2^(k / 4)` and k is the RRC `filterCoefficient`
% (range 0..19 per TS 38.331 §6.3.2). For the common k = 4 default,
% alpha = 1 / 2 = 0.5.
%
% The 3GPP spec applies the filter to L1 measurements obtained at the L1
% measurement period (~200 ms). Our simulator ticks at dt = 10 ms (ADR-5)
% and applies the filter at every tick; the effective time constant is
% therefore shorter than the spec, which yields a faster (but
% architecturally faithful) response. We document this in thesis Ch. 4.
%
% Args:
%   state         : per-UE HO state (see `ho.init_state`)
%   raw_rsrp_dbm  : 1 x K raw RSRP samples at this tick (dBm)
%   raw_rsrq_db   : 1 x K raw RSRQ samples
%   raw_sinr_db   : 1 x K raw SINR samples
%   alpha         : filter coefficient in (0, 1]
%
% Returns:
%   state         : updated state with `.rsrp_l3_dbm`, `.rsrq_l3_db`, `.sinr_l3_db`

arguments
    state         (1,1) struct
    raw_rsrp_dbm  (1,:) double
    raw_rsrq_db   (1,:) double
    raw_sinr_db   (1,:) double
    alpha         (1,1) double {mustBePositive, mustBeLessThanOrEqual(alpha, 1)}
end

% Note: dB filtering is technically not equivalent to linear-power
% averaging, but 3GPP defines the filter explicitly on the dB-domain
% measurement quantity (see TS 38.331 §5.5.3.2 NOTE). We follow the spec.

% Replace any NaN samples with the previous filter value (NaN-skip).
mask_rsrp = isnan(raw_rsrp_dbm);
mask_rsrq = isnan(raw_rsrq_db);
mask_sinr = isnan(raw_sinr_db);

raw_rsrp_dbm(mask_rsrp) = state.rsrp_l3_dbm(mask_rsrp).';
raw_rsrq_db(mask_rsrq)  = state.rsrq_l3_db(mask_rsrq).';
raw_sinr_db(mask_sinr)  = state.sinr_l3_db(mask_sinr).';

state.rsrp_l3_dbm = (1 - alpha) * state.rsrp_l3_dbm + alpha * raw_rsrp_dbm.';
state.rsrq_l3_db  = (1 - alpha) * state.rsrq_l3_db  + alpha * raw_rsrq_db.';
state.sinr_l3_db  = (1 - alpha) * state.sinr_l3_db  + alpha * raw_sinr_db.';
end
