function calibration_sweep(out_dir_root, isd_grid, area_grid, n_ue)
%CALIBRATION_SWEEP  Generate one calibration_run output per (ISD × area).
%
% Each (ISD, area) combination is written to a separate subdirectory under
% ``out_dir_root``, so the Python KS-test pipeline can load them
% independently and compare KS-statistic across configurations.
%
% Args:
%   out_dir_root : char/string parent directory
%   isd_grid     : vector of ISD values to try (m). Default [500 1000 1500]
%   area_grid    : vector of trajectory half-widths (m). Default [1500 2500 3500]
%   n_ue         : UEs per config. Default 6.
%
% Output layout:
%   out_dir_root/isd<I>_area<A>/kpis_ue*.parquet, events_ue*.parquet,
%                                run_metadata.json

arguments
    out_dir_root (1,1) string
    isd_grid     (1,:) double = [500 1000 1500]
    area_grid    (1,:) double = [1500 2500 3500]
    n_ue         (1,1) double {mustBePositive, mustBeInteger} = 6
end

if ~exist(out_dir_root, 'dir'), mkdir(out_dir_root); end

n_total = numel(isd_grid) * numel(area_grid);
k = 0;
t0 = tic;
for isd = isd_grid
    for area = area_grid
        k = k + 1;
        sub = sprintf('isd%d_area%d', round(isd), round(area));
        out_dir = fullfile(out_dir_root, sub);
        fprintf('[%d/%d] %s  (isd=%d  area=%d  n_ue=%d) ...\n', ...
                k, n_total, sub, round(isd), round(area), n_ue);
        opts = struct( ...
            'n_ue',        n_ue, ...
            't_total_s',   60, ...
            'area_m',      area, ...
            'speed_mps',   10, ...
            'isd_m',       isd, ...
            'master_seed', 100 + k);
        calibration_run(out_dir, opts);
    end
end
fprintf('\nSweep done: %d configs in %.1f s.\n', n_total, toc(t0));
end
