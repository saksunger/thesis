function track = waypoint_track(waypoints, dt_s)
%WAYPOINT_TRACK  Generate a per-tick (x, y, t) trajectory by linear interpolation.
%
% Args:
%   waypoints : N×3 matrix, each row [x_m, y_m, t_s]; must be sorted by t_s
%   dt_s      : sampling interval in seconds (scalar)
%
% Returns:
%   track     : struct with fields
%       .t_s    (T×1) — time stamps from waypoints(1,3) to waypoints(end,3)
%       .x_m    (T×1) — interpolated x
%       .y_m    (T×1) — interpolated y
%       .vx_mps (T×1) — instantaneous x velocity (finite diff)
%       .vy_mps (T×1) — instantaneous y velocity
%       .v_abs  (T×1) — speed magnitude
%       .heading_rad (T×1) — atan2(vy, vx)
%
% Example:
%   wp = [   0,    0, 0;
%          500,    0, 100;     % walk east 500 m in 100 s → 5 m/s
%          500,  500, 200];    % then north 500 m in 100 s
%   tr = mobility.waypoint_track(wp, 0.1);

arguments
    waypoints (:, 3) double {mustBeNonempty}
    dt_s      (1, 1) double {mustBePositive}
end

if ~issorted(waypoints(:, 3))
    error('waypoint_track:unsortedTimes', 'Waypoint times must be non-decreasing.');
end

t0 = waypoints(1, 3);
tf = waypoints(end, 3);
t_s = (t0:dt_s:tf).';

x_m = interp1(waypoints(:, 3), waypoints(:, 1), t_s, 'linear');
y_m = interp1(waypoints(:, 3), waypoints(:, 2), t_s, 'linear');

% finite-difference velocity (forward, last sample repeats)
if numel(t_s) >= 2
    vx = [diff(x_m); 0] / dt_s;  vx(end) = vx(end-1);
    vy = [diff(y_m); 0] / dt_s;  vy(end) = vy(end-1);
else
    vx = 0;
    vy = 0;
end
v_abs   = hypot(vx, vy);
heading = atan2(vy, vx);

track = struct( ...
    't_s', t_s, ...
    'x_m', x_m, ...
    'y_m', y_m, ...
    'vx_mps', vx, ...
    'vy_mps', vy, ...
    'v_abs', v_abs, ...
    'heading_rad', heading);
end
