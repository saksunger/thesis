function spec = apply_overrides(spec, overrides)
%APPLY_OVERRIDES  Recursive shallow merge of override fields into spec.
%
% Supports two override styles:
%
%   1. Top-level: override directly merges into spec.
%        overrides = struct('n_ue', 24, 'duration_s', 120)
%
%   2. Nested: override.<section> merges into spec.<section> field-by-field,
%      so callers can change e.g. just one HO parameter:
%        overrides = struct('ho_params', struct('ttt_s', 0.512))
%        overrides = struct('meas_params', struct('scenario', "UMi"))
%
% Unknown fields raise an error so typos in timeline JSON are caught early.

arguments
    spec      (1,1) struct
    overrides (1,1) struct
end

over_fields = fieldnames(overrides);
known_top   = fieldnames(spec);

for i = 1:numel(over_fields)
    f = over_fields{i};
    val = overrides.(f);
    if ~ismember(f, known_top)
        error('apply_overrides:unknown_field', ...
              ['unknown override field "%s". Known fields: %s'], ...
              f, strjoin(known_top, ', '));
    end
    if isstruct(spec.(f)) && isstruct(val)
        % nested merge
        sub  = spec.(f);
        subf = fieldnames(val);
        for j = 1:numel(subf)
            sf = subf{j};
            if ~isfield(sub, sf)
                error('apply_overrides:unknown_subfield', ...
                      'unknown override subfield "%s.%s"', f, sf);
            end
            sub.(sf) = val.(sf);
        end
        spec.(f) = sub;
    else
        spec.(f) = val;
    end
end
end
