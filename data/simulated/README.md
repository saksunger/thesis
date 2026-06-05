# Simulated Data

Output of the MATLAB simulator (`make sim TIMELINE=<name>`). Per-timeline subfolder.

Expected files per timeline:

| File                            | Description                                              |
|---------------------------------|----------------------------------------------------------|
| `events.parquet`                | Per-event log (HO attempts, RLF, ping-pong)              |
| `kpis_1s.parquet`               | Per-cell 1-second aggregated KPIs                        |
| `ground_truth_drift.parquet`    | Injected drift events with start/end/affected cells      |
| `ground_truth_anomaly.parquet`  | Injected anomalies with start/end/affected UEs           |
| `metadata.json`                 | Git SHA, MATLAB version, master seed, timeline config    |

These files are **gitignored** (large, regenerable). Reproduce via:

```bash
make sim TIMELINE=timeline_short
```
