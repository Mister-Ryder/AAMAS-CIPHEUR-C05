# Control results

These files are additions on the `non-llm-controls` branch. Combine the 240 records in `c05_control_runs.csv` with the 40 main-branch records in `c05_llm_runs.csv` to reproduce the complete 280-position C05 replication.

`c05_all_modes_summary.csv` reports the final mean for all seven modes; `c05_llm_vs_controls_paired.csv` stores 40 same-view/same-seed differences for each of six controls. The difference is `LLM value_ticks - control value_ticks`. The historical CHILS records use population four.

One physical source underlies all eight views. See `docs/RESULTS.md`, `results/protocol.json`, and `results/provenance.json` for timing, model configuration, data fields, aggregation, and audit scope.
