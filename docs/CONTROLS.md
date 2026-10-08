# C05 selector implementations

This branch restores the frozen `PlanController.choose()` implementation and enables the six non-LLM modes in `plan_solver.py`. All modes use the same input graph, four-solution native engine and objective. The five finite-plan selectors use the registry built by `build_plans()`.

## Run

```bash
git switch non-llm-controls
python -m pip install -e .
python scripts/build_native.py
python -m cipheur_c05 data/CP-SCALE-AU-L002/g0340.npz \
  --mode knn --seed 67 --seconds 360 \
  --out run_outputs/g0340_knn_seed67.json
```

`--mode` accepts `llm`, `knn`, `linucb`, `bandit`, `rule`, `static`, and `adaptive`. Only `llm` constructs the HTTP provider and uses `CIPHEUR_API_KEY`.

## Shared plan selection and update

For the five finite-plan controls, the first decision opportunity is at 20 seconds, followed by 40-second checkpoints, up to eight decisions. Each checkpoint closes the prior interval, computes current evidence, builds the eligible plan registry and installs the chosen plan.

The feature vector combines global search state, plan family and target-specific slot/anchor evidence. An interval with incumbent gain `gain`, starting incumbent `value`, and actual wall duration `seconds` has reward:

```text
log1p(gain / max(1, value) * 1,000,000 / max(0.01, seconds))
```

The shared update maintains a ridge inverse, reward vector, the last 160 feature/reward samples, and family statistics. `alpha=0.25` and `ridge=1.0`. The KNN, LinUCB and bandit modes initially select `spread` until a credited interval is available.

| Mode | Selection rule |
|---|---|
| `knn` | Five nearest feature vectors; inverse-distance weights `1/(0.2 + distance)`; weighted reward estimate plus optimism `alpha / sqrt(1 + sum(weights))` |
| `linucb` | Ridge estimate `theta · x` plus optimism `alpha * sqrt(xᵀ A⁻¹ x)` |
| `bandit` | Statistics shared by template/follow-up family; reward mean uses EMA weight 0.5; unvisited-family estimates age with an 80-second half-life, plus a count-based optimism term |
| `rule` | Choose a structurally ranked directed plan ending in spread when stagnation is at least 40 seconds, diversity is below 200,000 ppm and at least 32 seconds remain; otherwise spread |
| `static` | Select spread at every checkpoint |
| `adaptive` | Keep the original `v05_nonllm_adaptive` baseline action throughout the run; no finite-plan checkpoint selection |

KNN, LinUCB and bandit add a declared spread preference `0.1 / (1 + observations)` to the spread score. Numeric ties use the shared structural ordering. The source definitions are in [`plans.py`](../cipheur_v06/plans.py); the scheduling and adaptive path are in [`plan_solver.py`](../cipheur_v06/plan_solver.py).

## Records

The branch adds 240 control run records to the 40 LLM records, for the registered eight views × five seeds × seven modes. See [`results/CONTROLS.md`](../results/CONTROLS.md) and [`docs/RESULTS.md`](RESULTS.md).

```bash
python results/recompute_means.py
python scripts/smoke_controls.py
```

The first command recomputes objective means from the archived integer values. The second uses a small offline graph to exercise each non-LLM mode.
