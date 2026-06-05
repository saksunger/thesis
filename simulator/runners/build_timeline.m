function build_timeline(timeline_json_path, out_dir)
%BUILD_TIMELINE  Assemble a multi-phase 5G NR SA timeline → parquet bundle.
%
% Reads a timeline JSON (see `+scenarios/timelines/*.json` for examples),
% executes each phase via `utils.run_phase`, concatenates sample and
% event tables with phase-aligned time offsets, and writes:
%
%   <out_dir>/samples.parquet            sample-level KPI feed (per UE × tick)
%   <out_dir>/events.parquet             event log (HO_ATTEMPT/SUCCESS/FAIL/RLF/PING_PONG)
%   <out_dir>/ground_truth_drift.parquet one row per injected drift event
%   <out_dir>/ground_truth_anomaly.parquet one row per injected anomaly (Iter B)
%   <out_dir>/run_metadata.json          timeline config, seed, MATLAB version, ...
%
% Args:
%   timeline_json_path : path to timeline JSON (string/char)
%   out_dir            : output directory (created if missing)
%
% Schema of the parquet outputs matches `docs/schema.md` §1–2 with the
% additional columns `phase_id` and `scenario_name` so detectors can
% align ML features to the ground-truth phase structure.

arguments
    timeline_json_path (1,1) string
    out_dir            (1,1) string
end

if ~exist(out_dir, 'dir'), mkdir(out_dir); end

% --- 1. Load + validate timeline ---
raw = fileread(timeline_json_path);
tl  = jsondecode(raw);
% jsondecode returns a struct ARRAY when all element schemas match, but a
% CELL ARRAY when they differ (e.g. per-phase params with different keys).
% Normalize once up-front so downstream code can iterate uniformly.
tl.phases = normalize_array(tl.phases);
if isfield(tl, 'ground_truth_drift')
    tl.ground_truth_drift = normalize_array(tl.ground_truth_drift);
end
if isfield(tl, 'ground_truth_anomaly')
    tl.ground_truth_anomaly = normalize_array(tl.ground_truth_anomaly);
end
validate_timeline(tl);
fprintf('Timeline %s (%d phases) -> %s\n', ...
        tl.timeline_id, numel(tl.phases), out_dir);

% --- 2. Resolve per-phase specs + run each ---
default_dur = get_default(tl.global, 'default_duration_s', 60);
master_seed = get_default(tl.global, 'master_seed', 42);
n_ue_glob   = get_default(tl.global, 'n_ue', 12);
area_glob   = get_default(tl.global, 'area_m', 1500);

samples_chunks = cell(numel(tl.phases), 1);
events_chunks  = cell(numel(tl.phases), 1);

% Phase time-window precompute (needed to route anomalies whose times are
% specified in *timeline-global* seconds back to *phase-local* seconds).
[phase_t_start, phase_t_end] = phase_time_windows(tl.phases, default_dur);

% Anomalies indexed by phase_id so we can attach them to the right
% phase_spec without an O(N*M) scan inside the per-UE loop.
anomalies_by_phase = build_anomaly_index(tl, phase_t_start);

time_offset = 0;
t0 = tic;

for i = 1:numel(tl.phases)
    p = tl.phases{i};
    scenario_fn = str2func(['scenarios.' p.scenario]);

    overrides = struct();
    if isfield(p, 'params') && ~isempty(fieldnames(p.params))
        overrides = p.params;
    end

    phase_spec = scenario_fn(overrides);
    phase_spec.phase_id    = double(p.phase_id);
    phase_spec.master_seed = master_seed;
    if ~isfield(p, 'duration_s') || isempty(p.duration_s)
        phase_spec.duration_s = default_dur;
    else
        phase_spec.duration_s = double(p.duration_s);
    end
    % n_ue: timeline is the source of truth. Per-phase `params.n_ue` wins
    % (used by D-1 traffic shift); otherwise use the global default.
    % Scenario-file defaults for `n_ue` are only honoured when the
    % scenario is invoked standalone (outside this builder).
    if isfield(p, 'params') && isfield(p.params, 'n_ue')
        phase_spec.n_ue = double(p.params.n_ue);
    else
        phase_spec.n_ue = n_ue_glob;
    end
    phase_spec.area_m = area_glob;

    % Attach anomalies for this phase (may be empty)
    if isKey(anomalies_by_phase, phase_spec.phase_id)
        phase_spec.anomalies = anomalies_by_phase(phase_spec.phase_id);
    end

    fprintf('  [phase %d] scenario=%-22s duration=%4.0fs ...\n', ...
            phase_spec.phase_id, phase_spec.scenario_name, phase_spec.duration_s);

    [s_tbl, e_tbl] = utils.run_phase(phase_spec);

    % Apply phase-wide time offset (event_id uniqueness fixed below after
    % concat — each phase's per-UE event_loop restarts from 1, so per-phase
    % arithmetic is brittle; one renumber pass at the end is simpler).
    s_tbl.time_s = s_tbl.time_s + time_offset;
    if height(e_tbl) > 0
        e_tbl.event_time_s = e_tbl.event_time_s + time_offset;
    end

    samples_chunks{i} = s_tbl;
    events_chunks{i}  = e_tbl;

    time_offset = time_offset + phase_spec.duration_s;
end

% --- 3. Concat + global event_id renumber + write ---
samples_all = vertcat(samples_chunks{:});
events_all  = vertcat(events_chunks{:});
if height(events_all) > 0
    % Sort by (event_time_s, ue_id) for a chronologically consistent renumber
    events_all = sortrows(events_all, {'event_time_s', 'ue_id'});
    events_all.event_id = int32(1:height(events_all)).';
end

samples_fp = fullfile(out_dir, 'samples.parquet');
events_fp  = fullfile(out_dir, 'events.parquet');
parquetwrite(samples_fp, samples_all);
parquetwrite(events_fp,  events_all);

% --- 4. Ground truth drift + anomaly tables ---
if isfield(tl, 'ground_truth_drift') && ~isempty(tl.ground_truth_drift)
    gtd = build_ground_truth_drift(tl.ground_truth_drift, tl.phases, default_dur);
    gtd_fp = fullfile(out_dir, 'ground_truth_drift.parquet');
    parquetwrite(gtd_fp, gtd);
else
    gtd_fp = "(no drift events declared)";
end

if isfield(tl, 'ground_truth_anomaly') && ~isempty(tl.ground_truth_anomaly)
    gta = build_ground_truth_anomaly(tl.ground_truth_anomaly, phase_t_start);
    gta_fp = fullfile(out_dir, 'ground_truth_anomaly.parquet');
    parquetwrite(gta_fp, gta);
else
    gta_fp = "(no anomaly events declared)";
end

% --- 5. Run metadata sidecar ---
meta_out = struct();
meta_out.timeline_id     = tl.timeline_id;
meta_out.timeline_source = char(timeline_json_path);
meta_out.matlab_version  = version();
meta_out.master_seed     = master_seed;
meta_out.n_ue            = n_ue_glob;
meta_out.area_m          = area_glob;
meta_out.total_duration_s = time_offset;
meta_out.n_phases        = numel(tl.phases);
meta_out.n_samples       = height(samples_all);
meta_out.n_events        = height(events_all);
meta_out.elapsed_s       = toc(t0);
% best-effort git sha
[git_ok, sha] = system('git rev-parse --short HEAD 2>/dev/null');
if git_ok == 0
    meta_out.git_sha = strtrim(sha);
else
    meta_out.git_sha = 'unknown';
end
meta_fp = fullfile(out_dir, 'run_metadata.json');
fid = fopen(meta_fp, 'w');
fprintf(fid, '%s\n', jsonencode(meta_out, 'PrettyPrint', true));
fclose(fid);

fprintf('\nTimeline written: %d samples, %d events in %.1f s.\n', ...
        height(samples_all), height(events_all), toc(t0));
fprintf('  samples              : %s\n', samples_fp);
fprintf('  events               : %s\n', events_fp);
fprintf('  ground_truth_drift   : %s\n', gtd_fp);
fprintf('  ground_truth_anomaly : %s\n', gta_fp);
fprintf('  run_metadata         : %s\n', meta_fp);
end


% =========================================================================
function validate_timeline(tl)
required = {'timeline_id', 'phases'};
for i = 1:numel(required)
    if ~isfield(tl, required{i})
        error('build_timeline:missing_field', ...
              'timeline JSON missing required field "%s"', required{i});
    end
end
if isempty(tl.phases)
    error('build_timeline:no_phases', 'timeline must have at least 1 phase');
end
phase_ids = cellfun(@(p) double(p.phase_id), tl.phases);
if numel(unique(phase_ids)) ~= numel(phase_ids)
    error('build_timeline:duplicate_phase_id', ...
          'phase_id values must be unique within a timeline');
end
end


function out = normalize_array(x)
% Coerce x to a cell array of structs, regardless of whether jsondecode
% returned a struct array (homogeneous fields) or a cell array
% (heterogeneous fields).
if iscell(x)
    out = x(:);
elseif isstruct(x)
    out = arrayfun(@(s) s, x(:), 'UniformOutput', false);
else
    out = {};
end
end


function [t_start_map, t_end_map] = phase_time_windows(phases, default_dur)
% Returns containers.Map from phase_id → timeline-global t_start / t_end (s).
% Caller must pre-normalize `phases` via normalize_array().
t_start_map = containers.Map('KeyType', 'double', 'ValueType', 'double');
t_end_map   = containers.Map('KeyType', 'double', 'ValueType', 'double');
running = 0;
for i = 1:numel(phases)
    p = phases{i};
    pid = double(p.phase_id);
    if isfield(p, 'duration_s') && ~isempty(p.duration_s)
        d = double(p.duration_s);
    else
        d = default_dur;
    end
    t_start_map(pid) = running;
    t_end_map(pid)   = running + d;
    running = running + d;
end
end


function m = build_anomaly_index(tl, phase_t_start)
% Build containers.Map from phase_id → cell array of anomaly spec structs.
% Anomaly times in JSON are PHASE-LOCAL by convention (so authoring is
% intuitive — each anomaly belongs to one phase and `t_start_s`/`t_end_s`
% are relative to phase start at 0). The ground-truth parquet renders
% them in timeline-global seconds for join-friendliness.
m = containers.Map('KeyType', 'double', 'ValueType', 'any');
if ~isfield(tl, 'ground_truth_anomaly') || isempty(tl.ground_truth_anomaly)
    return
end
gta_list = normalize_array(tl.ground_truth_anomaly);
for j = 1:numel(gta_list)
    a = gta_list{j};
    pid = double(a.phase_id);
    if ~isKey(phase_t_start, pid)
        error('build_timeline:bad_anomaly_phase', ...
              'ground_truth_anomaly[%d] references unknown phase_id %d', j, pid);
    end
    % Strip the routing-only fields; keep only what `+anomalies` care about.
    spec = a;
    if isfield(spec, 'phase_id'),   spec = rmfield(spec, 'phase_id'); end
    if isfield(spec, 'anomaly_id'), spec = rmfield(spec, 'anomaly_id'); end
    if isfield(spec, 'note'),       spec = rmfield(spec, 'note'); end

    if isKey(m, pid)
        bucket = m(pid);
    else
        bucket = {};
    end
    bucket{end + 1} = spec; %#ok<AGROW>
    m(pid) = bucket;
end
end


function gtd = build_ground_truth_drift(raw_list, phases, default_dur)
% Convert ground_truth_drift array → table with derived time bounds.
list = normalize_array(raw_list);
n    = numel(list);
drift_id        = strings(n, 1);
phase_id_start  = zeros(n, 1, 'int32');
phase_id_end    = zeros(n, 1, 'int32');
start_time_s    = zeros(n, 1);
end_time_s      = zeros(n, 1);
affected_kpis   = strings(n, 1);
expected_dir    = strings(n, 1);
note            = strings(n, 1);

[t_start_map, t_end_map] = phase_time_windows(phases, default_dur);

for j = 1:n
    g = list{j};
    drift_id(j)       = string(g.drift_id);
    phase_id_start(j) = int32(g.phase_id_start);
    phase_id_end(j)   = int32(g.phase_id_end);

    if ~isKey(t_start_map, double(g.phase_id_start)) || ...
       ~isKey(t_end_map,   double(g.phase_id_end))
        error('build_timeline:bad_gt_phase', ...
              'ground_truth_drift references unknown phase id (%d or %d)', ...
              g.phase_id_start, g.phase_id_end);
    end
    start_time_s(j) = t_start_map(double(g.phase_id_start));
    end_time_s(j)   = t_end_map(double(g.phase_id_end));

    if isfield(g, 'affected_kpis')
        affected_kpis(j) = strjoin(string(g.affected_kpis), ',');
    end
    if isfield(g, 'expected_direction'),  expected_dir(j) = string(g.expected_direction); end
    if isfield(g, 'note'),                note(j) = string(g.note); end
end

gtd = table(drift_id, phase_id_start, phase_id_end, ...
            start_time_s, end_time_s, ...
            affected_kpis, expected_dir, note, ...
            'VariableNames', {'drift_id', 'phase_id_start', 'phase_id_end', ...
                              'start_time_s', 'end_time_s', ...
                              'affected_kpis', 'expected_direction', 'note'});
end


function gta = build_ground_truth_anomaly(raw_list, phase_t_start)
% Convert ground_truth_anomaly array → table.
% Times in JSON are phase-local; rendered table uses timeline-global s.
list = normalize_array(raw_list);
n = numel(list);
anomaly_id          = strings(n, 1);
anomaly_type        = strings(n, 1);
phase_id            = zeros(n, 1, 'int32');
t_start_global_s    = zeros(n, 1);
t_end_global_s      = zeros(n, 1);
affected_ue_ids_str = strings(n, 1);
affected_cell_ids_str = strings(n, 1);
severity            = zeros(n, 1);
note                = strings(n, 1);

for j = 1:n
    a   = list{j};
    pid = double(a.phase_id);
    if ~isKey(phase_t_start, pid)
        error('build_timeline:bad_anomaly_phase', ...
              'ground_truth_anomaly[%d] references unknown phase_id %d', j, pid);
    end
    offset = phase_t_start(pid);

    anomaly_id(j)       = string(getf(a, 'anomaly_id', ''));
    anomaly_type(j)     = string(a.type);
    phase_id(j)         = int32(pid);
    t_start_global_s(j) = double(a.t_start_s) + offset;
    t_end_global_s(j)   = double(a.t_end_s)   + offset;

    if isfield(a, 'affected_ue_ids') && ~isempty(a.affected_ue_ids)
        affected_ue_ids_str(j) = strjoin(string(a.affected_ue_ids(:).'), ',');
    else
        affected_ue_ids_str(j) = "all";
    end
    if isfield(a, 'affected_cell_ids') && ~isempty(a.affected_cell_ids)
        affected_cell_ids_str(j) = strjoin(string(a.affected_cell_ids(:).'), ',');
    else
        affected_cell_ids_str(j) = "all";
    end

    % Pick whichever severity field is set; report 0 if none.
    if isfield(a, 'delta_db'),        severity(j) = double(a.delta_db);
    elseif isfield(a, 'rate_db_per_s'), severity(j) = double(a.rate_db_per_s);
    elseif isfield(a, 'stuck_value_dbm'), severity(j) = double(a.stuck_value_dbm);
    end

    note(j) = string(getf(a, 'note', ''));
end

gta = table(anomaly_id, anomaly_type, phase_id, ...
            t_start_global_s, t_end_global_s, ...
            affected_ue_ids_str, affected_cell_ids_str, severity, note, ...
            'VariableNames', {'anomaly_id', 'anomaly_type', 'phase_id', ...
                              't_start_s', 't_end_s', ...
                              'affected_ue_ids', 'affected_cell_ids', ...
                              'severity', 'note'});
end


function v = getf(s, f, dflt)
if isfield(s, f), v = s.(f); else, v = dflt; end
end


function v = get_default(s, fname, dflt)
if isfield(s, fname), v = s.(fname); else, v = dflt; end
end
