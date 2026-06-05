function sweep_static(out_dir, opts)
%SWEEP_STATIC  Static (TTT × hysteresis × A3-offset × seed) sweep runner.
%
% Produces the configuration-performance training matrix consumed by the
% Phase 8 surrogate model. Each row is one MATLAB sim run; columns are
% the controlled knobs (HO parameters), the seed, and the mobility KPIs
% defined by ADR-15 (HOSR, HOFR_rate, RLF_rate, ping_pong_rate + RSRP/
% RSRQ/SINR distribution summaries).
%
% Output: <out_dir>/sweep_config_perf.parquet
%         <out_dir>/sweep_metadata.json
%
% Grid (defaults):
%   ttt_ms     : [256, 480, 1024]              (3 values)
%   hyst_db    : [  0,   2,    4,    6]        (4 values)
%   a3_off_db  : [  0,   3,    6]              (3 values)
%   seeds      : 1..10                          (10 values)
%   -> 360 runs total. With parfor on 8 workers, ~5-10 min wall clock.
%
% Args:
%   out_dir : output directory (default: data/simulated/sweep_static)
%   opts    : struct (optional) with optional fields:
%               .ttt_ms_grid    [vector] TTT grid in ms
%               .hyst_db_grid   [vector] hysteresis grid in dB
%               .a3_off_db_grid [vector] A3 offset grid in dB
%               .seeds          [vector] seeds (master_seed values)
%               .n_ue           [int]    UEs per run (default 12)
%               .duration_s     [double] per-run duration (default 120)
%               .use_parfor     [bool]   parallel exec (default true)
%
% Per-run scenario: `scenarios.baseline` (UMa, default ISD/area), with
% only the HO knobs swept. All other knobs (channel, noise, mobility
% speed, layout) are held fixed across the grid so the resulting
% (knob -> KPI) mapping is causally clean for the surrogate.
%
% ADR-15: KPIs reported are mobility-family only (TS 28.554 §6.3.1-2).
% No throughput / latency / jitter columns.

arguments
    out_dir (1,1) string = "data/simulated/sweep_static"
    opts    (1,1) struct = struct()
end

% --- 1. Grid + defaults ----------------------------------------------------
ttt_ms_grid    = getf(opts, 'ttt_ms_grid',    [256, 480, 1024]);
hyst_db_grid   = getf(opts, 'hyst_db_grid',   [0, 2, 4, 6]);
a3_off_db_grid = getf(opts, 'a3_off_db_grid', [0, 3, 6]);
seeds          = getf(opts, 'seeds',          1:10);
n_ue_per_run   = getf(opts, 'n_ue',           12);
duration_s     = getf(opts, 'duration_s',     120);
use_parfor     = getf(opts, 'use_parfor',     true);

if ~exist(out_dir, 'dir'), mkdir(out_dir); end

% Flatten the 4-D grid into a row-array of run specs so parfor can slice
% trivially (and so the per-run code path is identical to serial mode).
[T, H, A, S] = ndgrid(ttt_ms_grid, hyst_db_grid, a3_off_db_grid, seeds);
n_runs       = numel(T);
ttt_col      = T(:);
hyst_col     = H(:);
a3_col       = A(:);
seed_col     = S(:);

fprintf('Sweep grid: %d TTT × %d hyst × %d A3 × %d seeds = %d runs\n', ...
        numel(ttt_ms_grid), numel(hyst_db_grid), numel(a3_off_db_grid), ...
        numel(seeds), n_runs);
fprintf('Per-run: n_ue=%d, duration_s=%g s, scenario=baseline (UMa)\n', ...
        n_ue_per_run, duration_s);

% --- 2. Pre-allocate result columns (parfor needs sliced outputs) ---------
ho_attempt_count = zeros(n_runs, 1, 'int32');
ho_success_count = zeros(n_runs, 1, 'int32');
ho_fail_count    = zeros(n_runs, 1, 'int32');
rlf_count        = zeros(n_runs, 1, 'int32');
pp_count         = zeros(n_runs, 1, 'int32');

rsrp_p10 = zeros(n_runs, 1); rsrp_p50 = zeros(n_runs, 1); rsrp_p90 = zeros(n_runs, 1);
sinr_p10 = zeros(n_runs, 1); sinr_p50 = zeros(n_runs, 1); sinr_p90 = zeros(n_runs, 1);
rsrq_p10 = zeros(n_runs, 1); rsrq_p50 = zeros(n_runs, 1); rsrq_p90 = zeros(n_runs, 1);

elapsed_per_run = zeros(n_runs, 1);

% --- 3. Run the sweep ------------------------------------------------------
% Start parpool if requested + not already up. Use default profile, default
% workers. If the Parallel Toolbox is not available, gracefully fall back
% to serial execution rather than failing the whole sweep.
if use_parfor
    try
        pool = gcp('nocreate');
        if isempty(pool), parpool(); end
    catch ME
        warning('sweep_static:no_parpool', ...
                'Could not start parpool (%s); falling back to serial.', ME.message);
        use_parfor = false;
    end
end

t0 = tic;
if use_parfor
    parfor i = 1:n_runs
        [ha, hs, hf, rl, pp, rsrp_q, sinr_q, rsrq_q, dt] = ...
            run_single(ttt_col(i), hyst_col(i), a3_col(i), seed_col(i), ...
                       n_ue_per_run, duration_s);
        ho_attempt_count(i) = ha;
        ho_success_count(i) = hs;
        ho_fail_count(i)    = hf;
        rlf_count(i)        = rl;
        pp_count(i)         = pp;
        rsrp_p10(i) = rsrp_q(1); rsrp_p50(i) = rsrp_q(2); rsrp_p90(i) = rsrp_q(3);
        sinr_p10(i) = sinr_q(1); sinr_p50(i) = sinr_q(2); sinr_p90(i) = sinr_q(3);
        rsrq_p10(i) = rsrq_q(1); rsrq_p50(i) = rsrq_q(2); rsrq_p90(i) = rsrq_q(3);
        elapsed_per_run(i)  = dt;
    end
else
    for i = 1:n_runs
        [ha, hs, hf, rl, pp, rsrp_q, sinr_q, rsrq_q, dt] = ...
            run_single(ttt_col(i), hyst_col(i), a3_col(i), seed_col(i), ...
                       n_ue_per_run, duration_s);
        ho_attempt_count(i) = ha;
        ho_success_count(i) = hs;
        ho_fail_count(i)    = hf;
        rlf_count(i)        = rl;
        pp_count(i)         = pp;
        rsrp_p10(i) = rsrp_q(1); rsrp_p50(i) = rsrp_q(2); rsrp_p90(i) = rsrp_q(3);
        sinr_p10(i) = sinr_q(1); sinr_p50(i) = sinr_q(2); sinr_p90(i) = sinr_q(3);
        rsrq_p10(i) = rsrq_q(1); rsrq_p50(i) = rsrq_q(2); rsrq_p90(i) = rsrq_q(3);
        elapsed_per_run(i)  = dt;
        if mod(i, 20) == 0
            fprintf('  %d/%d runs done (%.1f s elapsed, avg %.2f s/run)\n', ...
                    i, n_runs, toc(t0), toc(t0)/i);
        end
    end
end
total_elapsed_s = toc(t0);

% --- 4. Derive rates -------------------------------------------------------
attempts_d  = double(ho_attempt_count);
hosr        = double(ho_success_count) ./ max(attempts_d, 1);
hosr(attempts_d == 0)        = NaN;
hofr_rate   = double(ho_fail_count) ./ max(attempts_d, 1);
hofr_rate(attempts_d == 0)   = NaN;
ping_pong_rate = double(pp_count) ./ max(attempts_d, 1);
ping_pong_rate(attempts_d == 0) = NaN;
% Per-UE-per-second RLF rate (interpretable across configs with different durations)
rlf_rate    = double(rlf_count) / (n_ue_per_run * duration_s);

% --- 5. Assemble result table ---------------------------------------------
run_id = int32(1:n_runs).';

T_out = table( ...
    run_id, ...
    single(ttt_col), single(hyst_col), single(a3_col), ...
    int32(seed_col), ...
    repmat(int32(n_ue_per_run), n_runs, 1), ...
    repmat(single(duration_s),  n_runs, 1), ...
    ho_attempt_count, ho_success_count, ho_fail_count, rlf_count, pp_count, ...
    hosr, hofr_rate, rlf_rate, ping_pong_rate, ...
    rsrp_p10, rsrp_p50, rsrp_p90, ...
    sinr_p10, sinr_p50, sinr_p90, ...
    rsrq_p10, rsrq_p50, rsrq_p90, ...
    elapsed_per_run, ...
    'VariableNames', { ...
        'run_id', ...
        'ttt_ms', 'hyst_db', 'a3_off_db', ...
        'seed', 'n_ue', 'duration_s', ...
        'ho_attempt_count', 'ho_success_count', 'ho_fail_count', ...
        'rlf_count', 'pp_count', ...
        'hosr', 'hofr_rate', 'rlf_rate', 'ping_pong_rate', ...
        'rsrp_p10_dbm', 'rsrp_p50_dbm', 'rsrp_p90_dbm', ...
        'sinr_p10_db',  'sinr_p50_db',  'sinr_p90_db', ...
        'rsrq_p10_db',  'rsrq_p50_db',  'rsrq_p90_db', ...
        'elapsed_s'});

parquet_fp = fullfile(out_dir, 'sweep_config_perf.parquet');
parquetwrite(parquet_fp, T_out);

% --- 6. Metadata sidecar ---------------------------------------------------
meta = struct();
meta.runner            = 'sweep_static';
meta.matlab_version    = version();
meta.adr_scope         = 'ADR-14 (NR SA Xn) + ADR-15 (mobility KPIs only)';
meta.scenario          = 'baseline';
meta.ttt_ms_grid       = ttt_ms_grid(:).';
meta.hyst_db_grid      = hyst_db_grid(:).';
meta.a3_off_db_grid    = a3_off_db_grid(:).';
meta.seeds             = seeds(:).';
meta.n_ue_per_run      = n_ue_per_run;
meta.duration_s        = duration_s;
meta.n_runs            = n_runs;
meta.use_parfor        = use_parfor;
meta.total_elapsed_s   = total_elapsed_s;
meta.avg_run_elapsed_s = mean(elapsed_per_run);
[git_ok, sha]          = system('git rev-parse --short HEAD 2>/dev/null');
if git_ok == 0
    meta.git_sha = strtrim(sha);
else
    meta.git_sha = 'unknown';
end

meta_fp = fullfile(out_dir, 'sweep_metadata.json');
fid = fopen(meta_fp, 'w');
fprintf(fid, '%s\n', jsonencode(meta, 'PrettyPrint', true));
fclose(fid);

fprintf('\nSweep done: %d runs in %.1f s (%.2f s/run avg).\n', ...
        n_runs, total_elapsed_s, mean(elapsed_per_run));
fprintf('  parquet  : %s\n', parquet_fp);
fprintf('  metadata : %s\n', meta_fp);
end


% =========================================================================
% Local helpers
% =========================================================================
function [ho_a, ho_s, ho_f, rlf, pp, rsrp_q, sinr_q, rsrq_q, dt] = ...
            run_single(ttt_ms, hyst_db, a3_off_db, seed, n_ue, duration_s)
%RUN_SINGLE  One sweep cell: build baseline phase spec, override knobs, run.
t_run = tic;
overrides = struct( ...
    'n_ue',        double(n_ue), ...
    'duration_s',  double(duration_s), ...
    'master_seed', double(seed), ...
    'ho_params',   struct( ...
        'ttt_s',         double(ttt_ms) / 1000, ...
        'hyst_db',       double(hyst_db), ...
        'a3_offset_db',  double(a3_off_db)));

spec          = scenarios.baseline(overrides);
spec.phase_id = 0;   % standalone sweep cell, no timeline phase context

[samples, events] = utils.run_phase(spec);

if height(events) > 0
    ho_a = int32(sum(events.event_type == "HO_ATTEMPT"));
    ho_s = int32(sum(events.event_type == "HO_SUCCESS"));
    ho_f = int32(sum(events.event_type == "HO_FAIL"));
    rlf  = int32(sum(events.event_type == "RLF"));
    pp   = int32(sum(events.event_type == "PING_PONG"));
else
    ho_a = int32(0); ho_s = int32(0); ho_f = int32(0); rlf = int32(0); pp = int32(0);
end

% Distribution summaries (drop NaNs defensively; sim returns finite values
% for serving cells, but neighbour rows occasionally have non-finite RSRQ
% during corner-case ratios — we don't use neighbour cols here).
rsrp_q = robust_quantile(samples.rsrp_serving_dbm, [0.1, 0.5, 0.9]);
sinr_q = robust_quantile(samples.sinr_serving_db,  [0.1, 0.5, 0.9]);
rsrq_q = robust_quantile(samples.rsrq_serving_db,  [0.1, 0.5, 0.9]);

dt = toc(t_run);
end


function q = robust_quantile(x, probs)
x = x(isfinite(x));
if isempty(x)
    q = nan(1, numel(probs));
else
    q = quantile(x, probs);
end
end


function v = getf(s, fname, dflt)
if isfield(s, fname) && ~isempty(s.(fname))
    v = s.(fname);
else
    v = dflt;
end
end
