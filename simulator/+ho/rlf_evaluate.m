function [state, rlf_declared] = rlf_evaluate(state, sinr_serv_db, dt_s, params)
%RLF_EVALUATE  3GPP TS 38.331 §5.3.10 Radio Link Failure state machine.
%
% Simplified BLER-equivalent model: the L1 lower-layer feeds out-of-sync
% (Qout) and in-sync (Qin) indications based on serving-cell SINR thresholds.
%
%   IDLE          : SINR >= Qin                    → stay IDLE
%                 : SINR < Qout                    → start counting N310
%   OUT_OF_SYNC   : after N310 out-of-sync         → start T310, go T310_RUNNING
%   T310_RUNNING  : SINR >= Qin → count N311       → after N311 in-sync, stop T310, go IDLE
%                 : T310 expires                   → declare RLF, reset
%
% Default thresholds (typical academic):
%   Qout = -8 dB   (SINR below → out-of-sync indication)
%   Qin  = -6 dB   (SINR above → in-sync indication)
%   N310 = 6, N311 = 2
%   T310 = 1000 ms
%
% Evaluation is done per tick (dt_s). N310/N311 are incremented per tick
% rather than per L1 measurement period — same response logic but finer
% granularity (acceptable per ADR-5).
%
% Args:
%   state         : per-UE HO state
%   sinr_serv_db  : current filtered SINR on serving cell (scalar, dB)
%   dt_s          : tick duration (s)
%   params        : struct with .rlf_qout_db, .rlf_qin_db, .n310, .n311, .t310_s
%
% Returns:
%   state         : updated state
%   rlf_declared  : true if RLF fires on this tick

arguments
    state         (1,1) struct
    sinr_serv_db  (1,1) double
    dt_s          (1,1) double {mustBePositive}
    params        (1,1) struct
end

rlf_declared = false;

out_of_sync = sinr_serv_db < params.rlf_qout_db;
in_sync     = sinr_serv_db >= params.rlf_qin_db;

switch state.rlf_state
    case "IDLE"
        if out_of_sync
            state.n310_count = state.n310_count + 1;
            if state.n310_count >= params.n310
                state.rlf_state        = "T310_RUNNING";
                state.t310_remaining_s = params.t310_s;
                state.n311_count       = 0;
            end
        else
            state.n310_count = 0;       % any non-out-of-sync sample resets
        end

    case "T310_RUNNING"
        state.t310_remaining_s = state.t310_remaining_s - dt_s;
        if in_sync
            state.n311_count = state.n311_count + 1;
            if state.n311_count >= params.n311
                % recovered: cancel T310, back to IDLE
                state.rlf_state        = "IDLE";
                state.t310_remaining_s = 0;
                state.n310_count       = 0;
                state.n311_count       = 0;
            end
        else
            state.n311_count = 0;
        end
        if state.t310_remaining_s <= 0 && state.rlf_state == "T310_RUNNING"
            rlf_declared           = true;
            state.rlf_state        = "IDLE";
            state.t310_remaining_s = 0;
            state.n310_count       = 0;
            state.n311_count       = 0;
        end

    otherwise
        error('rlf_evaluate:badState', 'Unknown RLF state "%s".', state.rlf_state);
end
end
