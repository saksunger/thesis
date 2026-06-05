function tests = test_hex_layout
%TEST_HEX_LAYOUT  MATLAB unit tests for `utils.hex_layout`.
tests = functiontests(localfunctions);
end

function setupOnce(testCase) %#ok<INUSD>
addpath(genpath(fullfile(fileparts(mfilename('fullpath')), '..')));
end

function test_cell_count(tc)
% N_cells(n_tiers) = 1 + 3*n*(n+1)
verifyEqual(tc, numel(utils.hex_layout(0, 500, 25)),  1);
verifyEqual(tc, numel(utils.hex_layout(1, 500, 25)),  7);
verifyEqual(tc, numel(utils.hex_layout(2, 500, 25)), 19);
verifyEqual(tc, numel(utils.hex_layout(3, 500, 25)), 37);
end

function test_center_at_origin(tc)
cells = utils.hex_layout(1, 500, 25);
verifyEqual(tc, cells(1).x_m, 0, 'AbsTol', 1e-9);
verifyEqual(tc, cells(1).y_m, 0, 'AbsTol', 1e-9);
verifyEqual(tc, cells(1).tier, 0);
end

function test_first_ring_distance(tc)
% All tier-1 cells must sit at distance ≈ ISD from the center.
isd = 500;
cells = utils.hex_layout(1, isd, 25);
for k = 2:7
    d = hypot(cells(k).x_m, cells(k).y_m);
    verifyEqual(tc, d, isd, 'AbsTol', 1e-6, ...
                sprintf('Tier-1 cell %d not at ISD from center (d=%.2f)', cells(k).id, d));
    verifyEqual(tc, cells(k).tier, 1);
end
end

function test_height_set(tc)
cells = utils.hex_layout(1, 500, 30);
verifyTrue(tc, all([cells.z_m] == 30));
end
