# Exact-weight classical WMIS baselines (900 seconds)

This directory preserves the frozen registration and runnable source for the
CP-SCALE-AU-L002 classical maximum-weight independent-set comparison. It uses
the same [eight source NPZ views](../../../data/CP-SCALE-AU-L002/) as the C05
900-second study. All views come from one physical contact source. Vertex IDs
are the original zero-based NPZ rows, and each objective uses unmodified
`weight_ticks` (1,000,000 ticks per contact-second).

The frozen `simulated_annealing` arm is a hybrid heuristic with greedy repair
and incumbent restarts, not a plain simulated-annealing baseline. Its valid
results remain archived. See [METHOD_CORRECTION.md](METHOD_CORRECTION.md).

The [frozen protocol](preregistration.json) defines 104 measured positions:

| Arm | Runs | Registered seeds |
| --- | ---: | --- |
| StableSolver local search | 8 | One seed-ignored run per view, recorded as seed 0 |
| StableSolver large-neighborhood search | 8 | One seed-ignored run per view, recorded as seed 0 |
| GRASP | 40 | 67, 71, 73, 79, 83 on each view |
| Hybrid SA (originally labeled simulated annealing) | 40 | 67, 71, 73, 79, 83 on each view |
| StableSolver greedy-gwmin (supplementary) | 8 | One natural early-stop run per view, recorded as seed 0 |

Every position has a 900-second outer wall cap and one native thread. The two
time-limited StableSolver searches use a native 895-second cap; greedy-gwmin
reports its actual early-stop time. The supplementary greedy arm stays separate
from full-budget search rankings. StableSolver's native `--seed` flag is unused
by these two algorithm branches. The 8/8 counts mean one measured run per view;
reusing a single view score across five C05 seeds is arithmetic pairing, not
five measurements.

## What is preserved

- `preregistration.json`: frozen inputs, hashes, methods, seeds, budgets, and
  audit criteria. SHA256: `6e76077e6c7e872f8675258ac05febeaf41be3734fe7b5aa0827f8bbcf45914c`.
- `inputs/graphs/metis_manifest.json`: exact hashes for the eight converted
  METIS and DIMACS files. The large text conversions are regenerated locally.
- `convert_npz_to_metis.py`: fixed integer-weight converter, SHA256
  `47c05d9cacacc910210ccccc90353e7923e0fe490d43e14aeb7f8b65b3f835ec`.
- `audit_classical_900s.py` and `analysis/classical_900s_independent_audit.json`:
  independent source/converted-graph audit code and the pre-run receipt. That
  receipt confirms graph equivalence only; it contains no measured results.
- `implementations/`: GRASP and simulated-annealing C++ source, the two frozen
  position wrappers, pilot auditor, small smoke input, and the
  [method description](implementations/README.md).
- `run_cloud_batch.py`: original one-core-per-position launcher. It checks the
  frozen hashes and refuses to overwrite an existing position.

The original run outputs, compiled binaries, StableSolver checkout, and large
graph conversions are not included. The [independently audited result summary](RESULTS.md)
records 96 valid positions out of 104: three complete timed-search arms, one
complete supplementary one-pass arm, and eight resource-limited local-search
positions. The compact [receipt index](results/receipt_index.csv),
[failure index](results/primary_failures.csv), [public audit](results/audit_public.json),
and [recorded search curves](results/figures/manifest_public.json) preserve the
measured evidence and its source hashes.

The [6 GiB local-search follow-up](recovery_6gib_failed/RESULTS.md) retains its
eight failed final receipts without scores. A separately registered
[9 GiB / native 850-second follow-up](recovery_9gib/RESULTS.md) has eight
independently audited results and its own [search curves](recovery_9gib/results/figures/manifest_public.json).

## StableSolver provenance

The three official arms use [fontanf/stablesolver](https://github.com/fontanf/stablesolver)
at commit [`efab011b460c2675647fa1995011eb332ab9ec7d`](https://github.com/fontanf/stablesolver/commit/efab011b460c2675647fa1995011eb332ab9ec7d).
Its [MIT license at that commit](https://github.com/fontanf/stablesolver/blob/efab011b460c2675647fa1995011eb332ab9ec7d/LICENSE)
is Copyright (c) 2020 Florian Fontan. The frozen `git archive HEAD` SHA256 is
`c8efdf57a822b9386d1f480999af4045db64c1e6acbe5728564644196e8027c1`;
the measured cloud binary SHA256 is
`a0515f47072400b8f64f38496e3e97832a65fb69c967f1bd6d5fe298eb4d5f2e`.
No StableSolver source or binary is vendored here.

## Reproduce the registered layout

Use a fresh Linux working directory outside the repository and copy this small
package into it. Place the repository's eight NPZ files under
`inputs/data/CP-SCALE-AU-L002/`. With Python and NumPy installed, run the
converter with `--protocol preregistration.json`, `--data-dir
inputs/data/CP-SCALE-AU-L002`, and an **absent** `--out-dir` such as
`generated_graphs`. Compare its generated manifest hashes with the preserved
manifest, then place the generated `.graph` and `.dimacs` files in
`inputs/graphs/` beside that preserved manifest. The converter intentionally
refuses to overwrite an existing directory.

Run `audit_classical_900s.py` with `--protocol preregistration.json`,
`--data-dir inputs/data/CP-SCALE-AU-L002`, and `--graph-dir inputs/graphs` to
recheck every weight and conflict edge against the NPZ files. Build the custom
C++ source using the command in the method README, and obtain the pinned
StableSolver checkout and binary. The frozen launcher expects the custom
binary at `implementations/classical_mwis`, the StableSolver Git checkout at
`source/stablesolver`, and its binary at
`source/stablesolver/build/src/stable/stablesolver_stable`. It verifies the
registered binary hashes before launch; a new compilation is byte-identical
only if it reproduces the recorded build. Use `run_cloud_batch.py --root <working-dir>
--workers <n>` with up to 24 distinct available CPUs. Its wrappers write each
position's certificate, trajectory, and receipt once. Finally, rerun the
independent auditor with `--results-root <working-dir>` and an external
`--out-dir` before using any result in a comparison.

The machine paths inside the immutable registration describe the original
cloud and staging locations; command-line paths select the reproduction
working directory.
