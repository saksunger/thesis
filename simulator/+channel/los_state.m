function los = los_state(ue_track, cells, h_ut_m, scenario, d_corr_m, seed)
%LOS_STATE  Piecewise-constant LoS state along a UE trajectory.
%
% Generates a (T × K) logical matrix of LoS / NLoS state per (UE-sample,
% cell). To respect TR 38.901 §7.6.3.3 LoS-state spatial correlation, the
% trajectory is divided into arc-length segments of length d_corr_m and a
% single Bernoulli draw is made per (segment, cell), with the LoS
% probability evaluated at the segment midpoint using
% `channel.los_probability` (TR 38.901 Table 7.4.2-1).
%
% Args:
%   ue_track : struct from `mobility.waypoint_track` (.t_s, .x_m, .y_m)
%   cells    : struct array from `utils.hex_layout`
%   h_ut_m   : UE antenna height (m)
%   scenario : string, "UMa" | "UMi"
%   d_corr_m : LoS decorrelation distance (m). Default per TR 38.901:
%                UMa: 50 m, UMi: 15 m
%   seed     : RNG seed (scalar)
%
% Returns:
%   los      : T × K logical
%
% Note: this is a simplified per-cell-independent piecewise-constant model.
% A fully correlated 2D LoS field (per TR 38.901 §7.6.3.3) is a future
% refinement (see plan Phase 1.2).

arguments
    ue_track (1,1) struct
    cells    (:,1) struct
    h_ut_m   (1,1) double {mustBePositive}
    scenario (1,1) string                       = "UMa"
    d_corr_m (1,1) double {mustBePositive}      = 50
    seed     (1,1) double                       = 0
end

T = numel(ue_track.t_s);
K = numel(cells);
los = false(T, K);

% --- arc length along the trajectory ---
dx = diff(ue_track.x_m);
dy = diff(ue_track.y_m);
seg_len = [0; cumsum(hypot(dx, dy))];
s_max   = seg_len(end);

% --- degenerate static UE: single LoS draw per cell ---
if s_max < eps
    rng(seed, 'twister');
    for k = 1:K
        d_2d = hypot(ue_track.x_m(1) - cells(k).x_m, ...
                     ue_track.y_m(1) - cells(k).y_m);
        p = channel.los_probability(d_2d, h_ut_m, scenario);
        los(:, k) = rand() < p;
    end
    return
end

% --- segment breakpoints (uniform in arc length) ---
s_breaks = (0:d_corr_m:s_max).';
if s_breaks(end) < s_max
    s_breaks(end+1) = s_max;
end

% --- per-segment, per-cell Bernoulli draw ---
rng(seed, 'twister');
for s = 1:numel(s_breaks)-1
    s_lo = s_breaks(s);
    s_hi = s_breaks(s+1);

    % midpoint of segment, in (x, y)
    s_mid = 0.5 * (s_lo + s_hi);
    x_mid = interp1(seg_len, ue_track.x_m, s_mid, 'linear');
    y_mid = interp1(seg_len, ue_track.y_m, s_mid, 'linear');

    % sample indices belonging to this segment
    if s < numel(s_breaks) - 1
        idx = seg_len >= s_lo & seg_len < s_hi;
    else
        idx = seg_len >= s_lo & seg_len <= s_hi; % include final sample
    end

    for k = 1:K
        d_2d = hypot(x_mid - cells(k).x_m, y_mid - cells(k).y_m);
        p = channel.los_probability(d_2d, h_ut_m, scenario);
        los(idx, k) = rand() < p;
    end
end
end
