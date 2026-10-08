# C05 results, protocol, and data fields

## Registered 900-second extension

The [900-second experiment package](../experiments/900s-20261009/README.md) covers the same eight CP-SCALE-AU-L002 constraint views and seeds 67, 71, 73, 79, 83. The registered four-method matrix has 160 positions, with a 900-second end-to-end budget, population four and one native thread per position. Its independent audit accepted 160/160 positions after checking graph hashes, result identity, selected-set feasibility and exact integer objective reconstruction.

The [C05-Codex position metrics](../experiments/900s-20261009/results/positions.csv) contain 40 positions. This branch adds [120 control position metrics](../experiments/900s-20261009/controls/results/positions.csv), [24 control view means](../experiments/900s-20261009/controls/results/by_view.csv) and [120 same-view/seed differences](../experiments/900s-20261009/controls/results/paired_deltas.csv). The final equal-view means in contact-seconds are C05-Codex **1,138,760.36872255**, C05-KNN **1,138,451.911341275**, C05-LinUCB **1,138,417.6763745**, and CHILS-p4-custom (`search_step=10`) **1,134,942.785685025**. The Codex arm made 840 calls to `gpt-6-luna` and yielded 838 valid plan proposals. The [Codex receipt](../experiments/900s-20261009/results/audit_public.json) and [control receipt](../experiments/900s-20261009/controls/results/audit_public.json) retain SHA256 links to the private audit and original results. Raw model exchanges are stored outside Git.

## Archived 360-second experiment

This release includes the archived C05 LLM replication on **CP-SCALE-AU-L002**: eight views times five seeds, **40/40 completed positions**, with a final equal-weight mean of **1,137,361.735 contact-seconds**. The objective is maximized total contact duration.

## Evaluation protocol

- One physical data source, eight constraint views; seeds **67, 71, 73, 79, 83** for every view and method.
- **360 seconds**, population **4**, and **one native solver thread per position**. The registered replication used 16 concurrent positions. A four-member population is a search configuration; concurrency is separate.
- DeepSeek Flash, thinking enabled, reasoning effort low, maximum 8192 response tokens, HTTP timeout 45 seconds, response TTL 60 seconds, decision interval 40 seconds, planned dwell 24 seconds. The preregistered LLM cap is 320 calls across 40 positions; this is a cap, **not a count of successful plans**.
- The soft wall-clock deadline includes graph loading, observations, native work, explicit repair, HTTP overlap/waiting and integer verification. JSON persistence is outside it. Recorded LLM wall time spans **360.013–360.064 seconds**.
- Objective unit: **1,000,000 integer ticks = 1 contact-second**. This objective is accumulated scheduled contact duration, not solver runtime.
- Average the final objective across seeds within each view, then take an equal-weight mean across all eight views. The matrix is balanced, so this equals the pooled 40-position mean. Exact rational means are preserved in JSON to make rounding explicit.

See [protocol](../results/protocol.json), [input hashes](../results/data_manifest.csv), [provenance and audit scope](../results/provenance.json), [40 run records](../results/c05_llm_runs.csv), and [machine-readable summary](../results/c05_llm_summary.json).

## C05-LLM per-view final means

| View | Positions | Final mean contact-seconds ↑ |
|---|---:|---:|
| g0340 | 5 | 1,624,840.713 |
| g0680 | 5 | 1,249,237.658 |
| g1200 | 5 | 883,885.117 |
| g1800 | 5 | 682,499.559 |
| gW0340_gE1200_s0150 | 5 | 1,261,758.626 |
| gW0680_gE1200_s0150 | 5 | 1,067,791.009 |
| gW1200_gE0340_s0150 | 5 | 1,262,183.728 |
| gW1200_gE0680_s0150 | 5 | 1,066,697.470 |
| **All eight views, equally weighted** | **40** | **1,137,361.735** |

The seed-41 C05 pilot covered only four views and is excluded from this complete replication score.

## Historical population-four reference results

The following entries use the same eight input graph hashes, population four and a nominal 360-second budget. The v0.3/v0.5 batches used seeds 11, 13, 17; C13 used seeds 7, 11, 19, 23, 29. Each batch retains its original initialization, timer and resource configuration in the source records. This table includes population-four runs.

| Method | Historical batch | Positions | Final mean ↑ | C05-LLM minus baseline |
|---|---|---:|---:|---:|
| CHILS-p4-default-step | CIPHEUR v0.3.0 | 24 | 1,134,919.030 | +2,442.705 |
| CHILS-p4-default-step | CIPHEUR v0.5.0 | 24 | 1,134,974.390 | +2,387.346 |
| CHILS-p4-original-step10 | v0.6.0 C13 external CHILS p4 | 40 | 1,134,401.061 | +2,960.674 |

[Baseline run records](../results/historical_p4_runs.csv) and [summaries](../results/historical_p4_summary.json) retain separate batches; repeated method names are not pooled. These selected p4 references are not an exhaustive historical leaderboard.

## Complete seven-mode summary

The [`non-llm-controls`](https://github.com/Mister-Ryder/AAMAS-CIPHEUR-C05/tree/non-llm-controls) branch contains the KNN, LinUCB, bandit, rule and static controllers, with adaptive retained as an additional control. Their result files add 240 positions to the 40 LLM positions, completing the registered **280/280** matrix. All seven modes use the same view/seed pairs.

| Mode | Positions | Final mean contact-seconds ↑ |
|---|---:|---:|
| C05-knn | 40 | 1,137,632.199 |
| C05-linucb | 40 | 1,137,512.893 |
| C05-llm | 40 | 1,137,361.735 |
| C05-bandit | 40 | 1,137,219.802 |
| C05-rule | 40 | 1,137,174.045 |
| C05-static | 40 | 1,137,128.217 |
| C05-adaptive | 40 | 1,133,859.370 |

The main branch includes the [seven-mode summary CSV](../results/c05_all_modes_summary.csv). The `non-llm-controls` branch adds `results/c05_control_runs.csv`, `results/c05_all_modes_summary.json`, `results/c05_llm_vs_controls_paired.csv`, and `results/c05_llm_vs_controls_summary.csv`. In the paired files, the key is `(graph_id, seed)` and the signed difference is `LLM value_ticks - control value_ticks`.

## Record fields and audit scope

| Field | Definition |
|---|---|
| `graph_id`, `mode`, `seed` | Unique run key within the C05 replication. |
| `value_ticks` | Final integer objective. |
| `contact_seconds` | `value_ticks / 1000000`. |
| `target_seconds`, `population` | Registered runtime target and population size. |
| `wall_seconds`, `cpu_seconds` | Recorded elapsed and process CPU time. |
| `status` | Archived completion status; all 280 records are `ok`. |
| `feasible` | Archived graph-feasibility flag; all 280 records are `true`. |
| `input_sha256`, `native_sha256` | Graph-input and archived native-build hashes. |
| `source_receipt_sha256` | Hash of the original frozen-source receipt. |
| `source_sha256` | Hash of the original per-run JSON output. |
| `original_result_filename` | Original output filename with private directories removed. |

The prior read-only extraction checked **280/280** selected sets for unique, in-range vertices, exact integer objective sums, no selected graph-edge conflicts, and matching actual input SHA256 hashes for all eight graphs. The audit recorded zero errors. Physical schedule reconstruction is outside that extraction's audit scope. Selected vertex arrays are not fields in these public summary tables.

## Aggregation and file traceability

For mode `m`, view `g` and seed `s`, let `v[m,g,s]` be `value_ticks`. The per-view score is `sum_s v[m,g,s] / (5 * 1000000)`. The final score is `sum_g per_view_score[m,g] / 8`. Thus each of the 40 positions has weight `1/40`. JSON files retain exact rational score strings, while displayed tables round to three decimal places.

The eight views are constraint configurations of the same CP-SCALE-AU-L002 physical source. The repository's `data/CP-SCALE-AU-L002/` directory contains the eight input NPZ files. `data_manifest.csv` gives each view's graph hash and west/east gap values. `provenance.json` records source-file hashes and the extraction audit. `SHA256SUMS.txt` records hashes of the public result files.

**中文字段说明：**最终平均表现统一采用 contact-seconds，计算方式为每个视图先对五个种子取均值，再对八个视图等权平均。主分支保存 LLM 的 40 条明细与七种控制方式的精简总表；non-llm-controls 分支补充其他六种方式的 240 条明细及同图、同种子的配对差值。历史参考只保留 population=4 的 CHILS 批次。
