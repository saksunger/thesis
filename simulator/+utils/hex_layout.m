function cells = hex_layout(n_tiers, isd_m, h_bs_m)
%HEX_LAYOUT  Build an N-tier hexagonal cell layout.
%
% Returns a struct array `cells(k)` with fields:
%   .id     (int)        — unique cell id, 1..N
%   .x_m    (double)     — cell center x coordinate in meters
%   .y_m    (double)     — cell center y coordinate in meters
%   .z_m    (double)     — antenna height
%   .tier   (int)        — 0 = center, 1 = first ring, etc.
%
% Args:
%   n_tiers : 0 → 1 cell, 1 → 7 cells (1 + 6), 2 → 19 cells (1+6+12), 3 → 37
%   isd_m   : inter-site distance (meters)
%   h_bs_m  : BS antenna height (meters)
%
% N_cells(n_tiers) = 1 + 3 * n_tiers * (n_tiers + 1).
%
% Hex coordinate scheme: axial coordinates (q, r) on a flat-top hex grid,
% then Cartesian conversion: x = isd * (q + r/2),  y = isd * (sqrt(3)/2) * r.
% Cell IDs are assigned ring-by-ring, then by angular order within ring.

arguments
    n_tiers (1,1) {mustBeInteger, mustBeNonnegative} = 1
    isd_m   (1,1) double  {mustBePositive}            = 500
    h_bs_m  (1,1) double  {mustBePositive}            = 25
end

% --- enumerate axial coordinates ring by ring ---
coords = [0, 0];           % center
for tier = 1:n_tiers
    % Start at (tier, 0), walk around the ring in 6 directions
    % Direction vectors for axial coordinates (flat-top hex)
    dirs = [ -1,  1;   % NW
             -1,  0;   % W
              0, -1;   % SW
              1, -1;   % SE
              1,  0;   % E
              0,  1];  % NE
    q = tier; r = 0;
    for d = 1:6
        for step = 1:tier
            coords(end+1, :) = [q, r]; %#ok<AGROW>
            q = q + dirs(d, 1);
            r = r + dirs(d, 2);
        end
    end
end

% --- Cartesian conversion ---
n_cells = size(coords, 1);
cells(n_cells, 1) = struct('id', [], 'x_m', [], 'y_m', [], 'z_m', [], 'tier', []);
for k = 1:n_cells
    q = coords(k, 1);
    r = coords(k, 2);
    cells(k).id    = k;
    cells(k).x_m   = isd_m * (q + r/2);
    cells(k).y_m   = isd_m * (sqrt(3)/2) * r;
    cells(k).z_m   = h_bs_m;
    cells(k).tier  = max(abs(q), max(abs(r), abs(q + r)));
end
end
