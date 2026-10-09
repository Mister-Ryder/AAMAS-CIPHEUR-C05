# CIPHEUR 900 s experiment release, 2026-10-09 v1

This directory accompanies the frozen AAMAS 2027 manuscript in [`paper_snapshot/`](paper_snapshot/). It is an interim release of **completed and audited** results. The paper source and compiled PDF are self-contained in that directory. The branch also inherits the 900 s KNN, LinUCB, CHILS, and other completed comparison data and source material from `non-llm-controls` under [`experiments/900s-20261009/`](../../experiments/900s-20261009/).

## Scope

The evidence directory contains registrations, graph and source hashes, position-level objective/feasibility summaries, audit outputs, and available execution metadata for the completed L002 four-view screen, the 40-position SIU-Off and Fixed-Spread controls, the 40-position newly authored `CIPHEUR-off-GEN` selector, and the three completed 12-position L003 arms. The four used L003 graph files are in [`evidence/l003_graphs/`](evidence/l003_graphs/); the L002 graph files are inherited under [`data/CP-SCALE-AU-L002/`](../../data/CP-SCALE-AU-L002/). The L003 source non-use claim is author attestation; the repository records do not independently establish a global non-use history. `CIPHEUR-off-EXP` and `CIPHEUR-off-VAL` are archived KNN and LinUCB programmatic controls. `CIPHEUR-off-RAND` is an offline random intervention control, not SIU-Off. These display names do not change the raw arm identifiers in the evidence.

The eight numeric CSVs in [`plots/csv/`](plots/csv/) and [`plots/plot_public_csv.py`](plots/plot_public_csv.py) regenerate the paper's anytime, attainment, SIU-event, and asynchronous timing figures. See [`plots/README.md`](plots/README.md) for inputs and command. CHILS has terminal outcomes but no matching intermediate trajectory and is excluded from the anytime curves.

## Completeness boundary

The fresh 8-view, 5-seed extensions of `CIPHEUR-online`, `CIPHEUR-off-RAND`, Single-Op, and No-Struct were incomplete at this snapshot and are excluded as full-frame results. L003 `CIPHEUR-online`, the L003 authored-selector supplement, L004, and synchronized-versus-asynchronous runs are also excluded. The paper distinguishes the historical completed 40-position comparison, the fresh 12-position framework screen, the completed 40-position controls, and the L003 12-position partial comparison. Do not combine these frames as if they were one paired experiment.

Objective values are integer `value_ticks`; contact-seconds are `value_ticks / 1,000,000`. The registrations and position-level files define each graph, seed, arm, host partition, and 900 s budget. Source hashes are provided for provenance. This release omits private model prompt/response bodies, infrastructure endpoints, credentials, and uncompleted records. The public plots can be regenerated from their numeric CSVs; recreating provider responses requires material outside this release.
