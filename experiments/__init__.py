"""Multi-run orchestrators that don't belong in any single ``analysis/``
module — typically because they shell out to MATLAB and to multiple
Python pipelines in sequence and aggregate their outputs.

Currently houses Phase 11 Iter A cross-seed replication
(``run_seed_replication.py``).
"""
