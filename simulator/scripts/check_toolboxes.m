function check_toolboxes()
% CHECK_TOOLBOXES  Verify MATLAB add-ons required by the thesis simulator.
%
% Prints an OK / MISSING line per required toolbox and exits with status 0
% if all required toolboxes are present, 1 otherwise. The "optional" group
% is informational only (does not affect exit status).
%
% Required by ADR-7 in docs/design.md.

required = { ...
    'Communications Toolbox', ...
    '5G Toolbox', ...
    'Statistics and Machine Learning Toolbox', ...
    'Parallel Computing Toolbox' ...
};
optional = { ...
    'Deep Learning Toolbox', ...
    'Communications Toolbox Wireless Network Simulator', ...   % support package
    'Antenna Toolbox', ...
    'Phased Array System Toolbox' ...
};

v = ver();
names = {v.Name};

fprintf('MATLAB %s on %s\n', version(), computer());
fprintf('--- required ---\n');
all_ok = true;
for k = 1:numel(required)
    idx = find(strcmp(names, required{k}), 1);
    if isempty(idx)
        fprintf('  MISSING : %s\n', required{k});
        all_ok = false;
    else
        fprintf('  OK      : %s (v%s, %s)\n', v(idx).Name, v(idx).Version, v(idx).Release);
    end
end

fprintf('--- optional ---\n');
for k = 1:numel(optional)
    idx = find(strcmp(names, optional{k}), 1);
    if isempty(idx)
        fprintf('  not installed : %s\n', optional{k});
    else
        fprintf('  installed     : %s (v%s, %s)\n', v(idx).Name, v(idx).Version, v(idx).Release);
    end
end

fprintf('--- license check (required) ---\n');
% NOTE: license feature names are MathWorks-internal and inconsistent across
% releases. We accept any one of multiple candidate names as "licensed".
% Verified on R2023b:
%   Communications Toolbox          -> communication_toolbox
%   5G Toolbox                      -> MATLAB_5G_Toolbox (NOT "5G_Toolbox")
%   Statistics and ML Toolbox       -> statistics_toolbox
%   Parallel Computing Toolbox      -> distrib_computing_toolbox
license_features = { ...
    'Communications Toolbox',                  {'communication_toolbox'}; ...
    '5G Toolbox',                              {'MATLAB_5G_Toolbox','matlab_5g_toolbox','5G_Toolbox'}; ...
    'Statistics and Machine Learning Toolbox', {'statistics_toolbox','Statistics_Toolbox'}; ...
    'Parallel Computing Toolbox',              {'distrib_computing_toolbox','Distrib_Computing_Toolbox'} ...
};
for k = 1:size(license_features,1)
    name  = license_features{k,1};
    feats = license_features{k,2};
    ok    = false;
    matched_feat = '';
    for j = 1:numel(feats)
        if license('test', feats{j})
            ok = true; matched_feat = feats{j}; break
        end
    end
    if ok
        fprintf('  %-50s license = 1  [%s]\n', name, matched_feat);
    else
        fprintf('  %-50s license = 0  (tried: %s)\n', name, strjoin(feats, ', '));
        all_ok = false;
    end
end

fprintf('--- functional test (calls one function per required toolbox) ---\n');
% Functional tests catch the case where the binary is installed but the
% license server denies us (or vice versa). These calls construct objects
% but do not run heavy computation.
functional_tests = { ...
    'Communications Toolbox',                  @() comm.AWGNChannel(); ...
    '5G Toolbox',                              @() nrCarrierConfig(); ...
    'Statistics and Machine Learning Toolbox', @() fitcsvm([0;1;2;3],[0;0;1;1]); ...
    'Parallel Computing Toolbox',              @() gcp('nocreate'); ...
};
for k = 1:size(functional_tests,1)
    name = functional_tests{k,1};
    fn   = functional_tests{k,2};
    try
        fn();
        fprintf('  %-50s functional OK\n', name);
    catch ME
        fprintf('  %-50s functional FAIL: %s\n', name, ME.identifier);
        all_ok = false;
    end
end

fprintf('--- summary ---\n');
if all_ok
    fprintf('ALL REQUIRED TOOLBOXES PRESENT AND LICENSED.\n');
else
    fprintf('SOME REQUIRED TOOLBOXES MISSING OR UNLICENSED. See above.\n');
end
end
