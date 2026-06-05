function [samples_tbl, events_tbl, ue_state_out] = run_phase(phase_spec, ue_state_in)
%RUN_PHASE  Run one timeline phase, return sample-level + event-level tables.
%
% This is the single-phase execution kernel that the timeline builder
% calls once per phase. It is a thin orchestration over the existing
% modules (`utils.hex_layout`, `mobility.waypoint_track`, `ho.measurements`,
% `ho.event_loop`). The output tables match `docs/schema.md` §1/§2 so
% they can be concatenated across phases by the caller.
%
% Args:
%   phase_spec : struct (typically produced by a `+scenarios/*.m` function),
%                fields:
%     .phase_id       (int)            sequential phase number in the timeline
%     .scenario_name  (string)         e.g. "baseline", "drift_channel_swap"
%     .duration_s     (double)         phase length (s); ticks @ utils.constants().sim_dt_s
%     .n_ue           (int)            number of independent UEs to simulate
%     .area_m         (double)         UE start/end sample half-width (m)
%     .master_seed    (int)            per-UE seed = master_seed + phase_id*100 + ue_id
%     .ho_params      (struct)         passed to ho.event_loop (TTT, hyst, ...)
%     .meas_params    (struct)         passed to ho.measurements (channel scenario, fc, ...)
%     .layout_params  (struct)         (.n_tiers, .isd_m, .h_bs_m) for utils.hex_layout
%     .speed_mps      (double)         constant UE speed for straight-line tracks
%   ue_state_in : (optional, Phase 4 Iter C) cell array indexed by ue_id.
%                 Each element is either empty (UE has no prior state ->
%                 fresh random init) or a struct with field `x_end_m`,
%                 `y_end_m`. When supplied, the UE starts at its previous
%                 phase's end position; we DELIBERATELY re-randomise the
%                 direction per phase (Random-Direction mobility model)
%                 because preserving direction caused UEs to walk straight
%                 off the cell footprint after a handful of phases (per-UE
%                 RSRP degraded by > 60 dB across a 30-phase production
%                 timeline). UEs with carry-over endpoints OUTSIDE
%                 [-area_m, area_m] fall back to random init (bounded
%                 mobility). UEs whose ue_id exceeds numel(ue_state_in)
%                 (new UEs added by D-1 traffic shift) also get random init.
%
% Returns:
%   samples_tbl  : table matching canonical sample-level schema (one row per
%                  (ue, tick)) with phase_id and scenario_name columns added.
%   events_tbl   : table matching canonical event-level schema (one row per
%                  HO/RLF/PING_PONG event) with phase_id and scenario_name
%                  columns added.
%   ue_state_out : cell array of length phase_spec.n_ue. Each cell holds the
%                  end-of-phase state for that UE (x_end_m, y_end_m, theta_rad,
%                  speed_mps_last). Caller can thread this into the next
%                  phase's `ue_state_in` to keep UE trajectories continuous.

arguments
    phase_spec   (1,1) struct
    ue_state_in        cell = {}
end

c = utils.constants();

% Build cell layout for this phase (BS height may differ for UMi etc.)
cells = utils.hex_layout(phase_spec.layout_params.n_tiers, ...
                         phase_spec.layout_params.isd_m, ...
                         phase_spec.layout_params.h_bs_m);

samples_chunks = cell(phase_spec.n_ue, 1);
events_chunks  = cell(phase_spec.n_ue, 1);
ue_state_out   = cell(phase_spec.n_ue, 1);

for ue_id = 1:phase_spec.n_ue
    seed = phase_spec.master_seed + phase_spec.phase_id * 100 + ue_id;
    rng(seed);

    % Speed-driven straight-line trajectory. Direction is always freshly
    % randomised per phase (Random-Direction mobility model): if we kept
    % `theta` across phases the UE would walk in a single straight line for
    % the entire timeline and exit the cell footprint within a handful of
    % phases. Position is carried over from the prior phase's end-of-phase
    % state IFF (a) the caller supplied `ue_state_in` and (b) the stored
    % endpoint is still inside [-area_m, area_m]. Otherwise we fall back to
    % a fresh random spawn inside the area box. This keeps motion bounded
    % while still giving per-UE trajectories some inter-phase continuity for
    % the drift-aware ML pipeline.
    %
    % Constant speed matches `phase_spec.speed_mps` exactly (displacement =
    % speed × duration), so D-3 "mobility shift" gives the requested speed
    % without any emergent slop.
    theta = rand() * 2 * pi;
    if numel(ue_state_in) >= ue_id && ~isempty(ue_state_in{ue_id})
        st = ue_state_in{ue_id};
        if abs(st.x_end_m) <= phase_spec.area_m && abs(st.y_end_m) <= phase_spec.area_m
            p0 = [st.x_end_m, st.y_end_m];
        else
            % Endpoint drifted outside the area box; re-init to keep
            % per-UE mobility bounded.
            p0 = (rand(1, 2) * 2 - 1) * phase_spec.area_m;
        end
    else
        p0 = (rand(1, 2) * 2 - 1) * phase_spec.area_m;
    end
    disp_m = phase_spec.speed_mps * phase_spec.duration_s;
    p1     = p0 + disp_m * [cos(theta), sin(theta)];
    waypoints = [p0(1), p0(2), 0; p1(1), p1(2), phase_spec.duration_s];
    ue_track  = mobility.waypoint_track(waypoints, c.sim_dt_s);
    ue_track.v_mps = repmat(phase_spec.speed_mps, numel(ue_track.t_s), 1);

    meas_params      = phase_spec.meas_params;
    meas_params.seed = seed;
    meas             = ho.measurements(ue_track, cells, meas_params);

    % --- Anomaly injection (Phase 4 Iter B) ---
    % Applied AFTER channel-level measurement so the HO event loop reacts
    % to the corrupted view (e.g. RLF-burst injection actually triggers
    % real RLF events in the log).
    if isfield(phase_spec, 'anomalies') && ~isempty(phase_spec.anomalies)
        meas = anomalies.apply_all(meas, ue_track, ue_id, ...
                                   cells, phase_spec.anomalies);
    end

    ho_params       = phase_spec.ho_params;
    ho_params.ue_id = ue_id;
    [events, ~]     = ho.event_loop(ue_track, cells, meas, ho_params);

    % Build a sample-level chunk via the parquet helpers' shared logic.
    samples_chunks{ue_id} = build_samples_chunk(ue_id, ue_track, cells, meas, ...
                                                phase_spec);
    events_chunks{ue_id}  = build_events_chunk(events, phase_spec);

    % End-of-phase state for the next phase (only used when the caller has
    % opted into carry-over by threading `ue_state_in` -> `ue_state_out`).
    % We record theta + speed for diagnostics only; the next phase will
    % re-randomise theta either way (see Random-Direction comment above).
    ue_state_out{ue_id} = struct( ...
        'x_end_m',        p1(1), ...
        'y_end_m',        p1(2), ...
        'theta_rad',      theta, ...
        'speed_mps_last', phase_spec.speed_mps);
end

samples_tbl = vertcat(samples_chunks{:});
events_tbl  = vertcat(events_chunks{:});

% Apply scenario / phase tags as constant columns
ns = height(samples_tbl);
samples_tbl.phase_id      = repmat(int32(phase_spec.phase_id),      ns, 1);
samples_tbl.scenario_name = repmat(string(phase_spec.scenario_name), ns, 1);

ne = height(events_tbl);
if ne > 0
    events_tbl.phase_id      = repmat(int32(phase_spec.phase_id),      ne, 1);
    events_tbl.scenario_name = repmat(string(phase_spec.scenario_name), ne, 1);
end
end


% =========================================================================
% Local helpers — kept here so the canonical schema lives in one place.
% =========================================================================
function tbl = build_samples_chunk(ue_id, ue_track, cells, meas, phase_spec)
T = numel(ue_track.t_s);
K = numel(cells);

[rsrp_sorted, idx_sorted] = sort(meas.rsrp_dbm, 2, 'descend');
serving_idx  = idx_sorted(:, 1);
neighbor_idx = idx_sorted(:, 2);

lin_serv = sub2ind([T, K], (1:T).', serving_idx);
lin_nbr  = sub2ind([T, K], (1:T).', neighbor_idx);

cell_ids = [cells.id].';

tbl = table( ...
    repmat(uint32(ue_id), T, 1), ...
    double(ue_track.t_s(:)), ...
    int32(cell_ids(serving_idx)), ...
    int32(cell_ids(neighbor_idx)), ...
    rsrp_sorted(:, 1), ...
    meas.rsrq_db(lin_serv), ...
    meas.sinr_db(lin_serv), ...
    rsrp_sorted(:, 2), ...
    meas.rsrq_db(lin_nbr), ...
    double(ue_track.x_m(:)), ...
    double(ue_track.y_m(:)), ...
    double(ue_track.v_mps(:)), ...
    repmat(single(phase_spec.meas_params.fc_ghz), T, 1), ...
    repmat(single(phase_spec.layout_params.isd_m), T, 1), ...
    repmat(string(phase_spec.meas_params.scenario), T, 1), ...
    'VariableNames', { ...
        'ue_id', 'time_s', 'serving_cell_id', 'neighbor_cell_id', ...
        'rsrp_serving_dbm', 'rsrq_serving_db', 'sinr_serving_db', ...
        'rsrp_neighbor_dbm', 'rsrq_neighbor_db', ...
        'ue_x_m', 'ue_y_m', 'ue_speed_mps', ...
        'meta_fc_ghz', 'meta_isd_m', 'meta_channel_scenario'});
end


function tbl = build_events_chunk(events, phase_spec)
% Always emit the same schema (including for zero-event UEs) so vertcat
% across UEs / phases doesn't blow up. We build the table from a
% single-row template (`ho.empty_event`) then truncate / fill.
if isempty(events)
    template = ho.empty_event();
    tbl_one  = struct2table(template, 'AsArray', true);
    tbl      = tbl_one(false, :);   % zero rows, full schema preserved
else
    tbl = struct2table(events, 'AsArray', true);
end

% Type-cast for parquet stability
tbl.event_id         = int32(tbl.event_id);
tbl.event_time_s     = double(tbl.event_time_s);
tbl.event_type       = string(tbl.event_type);
tbl.ue_id            = uint32(tbl.ue_id);
tbl.source_cell_id   = int32(tbl.source_cell_id);
tbl.target_cell_id   = int32(tbl.target_cell_id);
tbl.outcome          = string(tbl.outcome);
tbl.trigger_quantity = string(tbl.trigger_quantity);

n = height(tbl);
tbl.cfg_ttt_ms    = repmat(single(phase_spec.ho_params.ttt_s * 1000), n, 1);
tbl.cfg_hyst_db   = repmat(single(phase_spec.ho_params.hyst_db),      n, 1);
tbl.cfg_a3_off_db = repmat(single(phase_spec.ho_params.a3_offset_db), n, 1);
end
