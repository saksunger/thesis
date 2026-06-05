function n_rows = write_events_parquet(out_path, events, meta)
%WRITE_EVENTS_PARQUET  Event-level (HO / RLF / ping-pong) parquet writer.
%
% Args:
%   out_path : output .parquet path
%   events   : struct array from `ho.event_loop`
%   meta     : run metadata struct (.ttt_ms, .hyst_db, .a3_off_db, .seed,
%              .fc_ghz, .isd_m, .ran_type, .band)
%
% Returns:
%   n_rows   : number of events written

arguments
    out_path (1,1) string
    events   (:,1) struct
    meta     (1,1) struct
end

if isempty(events)
    % still write an empty parquet with the right schema, so downstream
    % code doesn't blow up on missing files when a run had no events
    events = ho.empty_event();
    events = events([]);
end

% Stack struct array → table (MATLAB does this implicitly with struct2table)
tbl = struct2table(events, 'AsArray', true);

% Cast types for parquet stability
n = height(tbl);
tbl.event_id       = int32(tbl.event_id);
tbl.event_time_s   = double(tbl.event_time_s);
tbl.event_type     = string(tbl.event_type);
tbl.ue_id          = uint32(tbl.ue_id);
tbl.source_cell_id = int32(tbl.source_cell_id);
tbl.target_cell_id = int32(tbl.target_cell_id);
tbl.outcome        = string(tbl.outcome);
tbl.trigger_quantity = string(tbl.trigger_quantity);

% Add per-event configuration metadata as constant columns (so events
% from different runs can be concatenated and grouped by config later).
ttt_ms      = single(getfield_default(meta, 'ttt_ms',      NaN));
hyst_db     = single(getfield_default(meta, 'hyst_db',     NaN));
a3_off_db   = single(getfield_default(meta, 'a3_off_db',   NaN));
seed        = int32(getfield_default(meta,  'seed',        -1));
fc_ghz      = single(getfield_default(meta, 'fc_ghz',      NaN));
isd_m       = single(getfield_default(meta, 'isd_m',       NaN));
ran_type    = string(getfield_default(meta, 'ran_type',    "NR_SA"));
band        = string(getfield_default(meta, 'band',        "n78"));

tbl.cfg_ttt_ms      = repmat(ttt_ms,   n, 1);
tbl.cfg_hyst_db     = repmat(hyst_db,  n, 1);
tbl.cfg_a3_off_db   = repmat(a3_off_db, n, 1);
tbl.meta_seed       = repmat(seed,     n, 1);
tbl.meta_fc_ghz     = repmat(fc_ghz,   n, 1);
tbl.meta_isd_m      = repmat(isd_m,    n, 1);
tbl.meta_ran_type   = repmat(ran_type, n, 1);
tbl.meta_band       = repmat(band,     n, 1);

parent = fileparts(out_path);
if strlength(parent) > 0 && ~isfolder(parent)
    mkdir(parent);
end

parquetwrite(out_path, tbl);
n_rows = n;
end


function v = getfield_default(s, fname, dflt)
    if isfield(s, fname)
        v = s.(fname);
    else
        v = dflt;
    end
end
