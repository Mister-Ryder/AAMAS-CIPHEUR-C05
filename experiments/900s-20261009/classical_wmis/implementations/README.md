# Independent exact-weight classical MWIS baselines

`classical_mwis.cpp` implements two single-threaded methods over the same
DIMACS1992 conflict graph. Each vertex is one contact, each edge is a conflict,
and every vertex weight is the exact `weight_ticks` value from the source NPZ.
The input mapping is fixed: DIMACS vertex `i + 1` is NPZ row `i`.

## Methods

- **GRASP**: Each restart constructs a maximal independent set using a
  rank-based restricted candidate list. Candidate scores are
  `weight / (1 + degree)^alpha`; the fixed restart cycle uses
  `(RCL size, alpha) = (4, 0.7), (8, 1.0), (16, 1.3)`. Local search accepts
  positive insert-and-evict moves with greedy refill, then improving
  remove-one-and-refill moves. The best solution across restarts is retained.
- **Hybrid SA (originally labeled simulated annealing)**: Starts from weighted-degree greedy construction.
  A move inserts one nonselected vertex, removes its selected conflicting
  neighbours, and greedily fills newly feasible vertices. The preliminary
  weight gain determines Metropolis acceptance. Temperature decays
  geometrically from `2 * median vertex weight` to
  `0.01 * median vertex weight` within each of ten equal wall-time cycles;
  each cycle restarts from the best solution found so far.

Both methods use fixed algorithm configurations across all eight views and five
registered seeds. No C05 or CHILS search code is linked or called. Objectives,
candidate blocker weights, and selected weight sums use signed 64-bit integers;
the Python wrapper independently recomputes the objective with unbounded Python
integers directly from the NPZ.

## Native build and measured run

On the Ubuntu 20.04 cloud host:

```sh
g++ -std=c++17 -O3 -DNDEBUG -Wall -Wextra -Wpedantic \
  classical_mwis.cpp -o classical_mwis
```

The `run_classical.py` wrapper pins one CPU and enforces the registered 900 s
wall cap. Its clock includes input hash checks, DIMACS parsing, search, solution
extraction, and a feasibility/objective check against the original NPZ. A small
reserve is left for the final check. The pre-frozen NPZ-to-DIMACS conversion is
outside each measured position; the independent converter and auditor verify
that no vertex or edge changes. `--pilot-seconds` only creates pilot receipts
with status `pilot_ok`; it is omitted in measured runs.

Native output contains the best selected NPZ zero-based IDs and exact tick
objective. The CSV trace records every strict incumbent improvement with
monotonic elapsed seconds, exact tick objective, iteration, and restart number.
The wrapper receipt records source, binary, runner, protocol, input and DIMACS
hashes, CPU affinity, wall time, CPU time, peak RSS, status, selected IDs, and
the trace path.

## Frozen cloud build and pilot

Cloud directory: `/root/autodl-tmp/cipheur_classical_900_20261009/implementations`.

| Artifact | SHA256 |
| --- | --- |
| `classical_mwis.cpp` | `e3326bab571697a65ffa915c7aba3011cb3fe82fd0ef18f268579259d35f3133` |
| `classical_mwis` | `3d0ab08225265c96f0695bf867f189c168d4ec1cb5da5f7c43d9012f4682b8a5` |
| `run_classical.py` | `5fb57c0554483ddcc94060a2845965d92d7454230612bfaab3ee222252868d59` |

The 10 s `g0340`, seed 67 cloud pilots used CPU 0 for GRASP and CPU 1 for SA.
Both returned feasible selected sets, exact NPZ integer objectives, and
strictly improving time traces under independent `audit_pilot.py` checks.
The end-to-end wrapper was also exercised on CPU 2 and CPU 3 with pilot
receipts, including the same local NPZ feasibility check used by measured runs.
