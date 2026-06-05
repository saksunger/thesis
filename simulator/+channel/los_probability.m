function p_los = los_probability(d_2d_m, h_ut_m, scenario)
%LOS_PROBABILITY  TR 38.901 Table 7.4.2-1 LoS probability.
%
% Implements LoS-probability formulas per
%   3GPP TR 38.901 v17.1.0 §7.4.2 (Table 7.4.2-1).
%
% Args:
%   d_2d_m   : 2-D distance UE↔BS in meters (scalar or vector)
%   h_ut_m   : UE antenna height in meters (scalar, ignored for "UMi")
%   scenario : string, "UMa" | "UMi" | "RMa" (RMa not implemented yet)
%
% Returns:
%   p_los    : LoS probability in [0,1], same size as d_2d_m

arguments
    d_2d_m   (:,:) double {mustBeNonnegative}
    h_ut_m   (1,1) double {mustBePositive}        = 1.5
    scenario (1,1) string                         = "UMa"
end

switch upper(scenario)
    case "UMA"
        % d_2D ≤ 18 m  → P_LoS = 1
        % d_2D >  18 m → P_LoS = (18/d2D + exp(-d2D/63) * (1 - 18/d2D)) * (1 + C')
        % where C' = 0                                            for h_UT ≤ 13 m
        %       C' = ((h_UT - 13) / 10)^1.5 * g(d_2D)             for 13 < h_UT ≤ 23 m
        %       g(d_2D) = 0                                       for d_2D ≤ 18
        %       g(d_2D) = (5/4) * (d_2D/100)^3 * exp(-d_2D/150)   for d_2D > 18
        if h_ut_m <= 13
            Cprime = 0;
        elseif h_ut_m <= 23
            g = zeros(size(d_2d_m));
            mask = d_2d_m > 18;
            g(mask) = (5/4) * (d_2d_m(mask)/100).^3 .* exp(-d_2d_m(mask)/150);
            Cprime = ((h_ut_m - 13) / 10).^1.5 .* g;
        else
            error('los_probability:hutOutOfRange', ...
                  'UMa LoS-prob formula valid only for h_UT ≤ 23 m, got %g.', h_ut_m);
        end
        p_los = ones(size(d_2d_m));
        mask  = d_2d_m > 18;
        d     = d_2d_m(mask);
        base  = (18./d) + exp(-d/63) .* (1 - 18./d);
        if isscalar(Cprime)
            p_los(mask) = base .* (1 + Cprime);
        else
            p_los(mask) = base .* (1 + Cprime(mask));
        end

    case "UMI"
        % d_2D ≤ 18 m  → P_LoS = 1
        % d_2D >  18 m → P_LoS = 18/d_2D + exp(-d_2D/36) * (1 - 18/d_2D)
        p_los = ones(size(d_2d_m));
        mask  = d_2d_m > 18;
        d     = d_2d_m(mask);
        p_los(mask) = (18./d) + exp(-d/36) .* (1 - 18./d);

    case "RMA"
        error('los_probability:notImplemented', 'RMa LoS-prob not implemented yet.');

    otherwise
        error('los_probability:unknownScenario', 'Unknown scenario "%s".', scenario);
end

% safety clamp (numerical)
p_los = min(max(p_los, 0), 1);
end
