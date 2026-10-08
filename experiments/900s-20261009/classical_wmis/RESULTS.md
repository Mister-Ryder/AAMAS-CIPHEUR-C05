# Classical WMIS 900-second comparison: technical record

**Method correction:** The frozen arm key `simulated_annealing` denotes a
hybrid with weighted-degree greedy initialization, greedy repair after accepted
moves, and incumbent restarts. Its valid score is archived separately from a
plain simulated-annealing baseline. The existing figure legend `SA` denotes
this hybrid. See [METHOD_CORRECTION.md](METHOD_CORRECTION.md).

## Scope and extraction rule

- Physical source: CP-SCALE-AU-L002; eight registered constraint views of one source, with identical NPZ graph hashes across the two registrations.
- Outcome: independently audited integer `value_ticks` divided exactly by 1,000,000 to obtain contact seconds. The overall mean is the unweighted mean of the eight view means.
- C05-Codex, C05-KNN, C05-LinUCB and CHILS each have five seeds per view (40 measured positions per method). GRASP and simulated annealing use the same five registered seeds per view.
- StableSolver local search and large neighborhood search each have one measured seed-ignored seed=0 run per view (8 measured positions per method). The frozen registration notes that time-limited outputs may vary across repeated runs. A StableSolver view score can be repeated across C05 seed rows solely for paired arithmetic; those rows do not create 40 independent runs.
- All primary search arms have a 900-second end-to-end cap. StableSolver greedy-gwmin is a supplementary one-pass arm, capped at 900 seconds and reported with its actual natural stop time.
- C05 strict audit: 160/160 valid positions. Classical audit state: incomplete_or_invalid; 96/104 valid positions; 4/5 arms complete.

## Overall means

| Method | Audited valid positions | Mean objective (contact seconds) | Mean actual wall time (seconds) | Mean CPU time (seconds) | Status |
| --- | ---: | ---: | ---: | ---: | --- |
| C05-Codex | 40/40 | 1138760.36872255 | 900.040 | 898.041 | audited_complete |
| C05-KNN | 40/40 | 1138451.911341275 | 900.013 | 897.779 | audited_complete |
| C05-LinUCB | 40/40 | 1138417.6763745 | 900.013 | 897.933 | audited_complete |
| CHILS-p4-custom | 40/40 | 1134942.785685025 | 900.011 | 899.585 | audited_complete |
| StableSolver local search | 0/8 | — | — | — | resource_limited |
| StableSolver large neighborhood search | 8/8 | 1125961.321395625 | 895.841 | 877.058 | audited_complete |
| GRASP | 40/40 | 1068698.2984871 | 898.573 | 897.714 | audited_complete |
| Hybrid SA (archived; originally labeled simulated annealing) | 40/40 | 1143916.7904964 | 898.565 | 897.756 | audited_complete |

## Resource registration

| Method group | Native threads per position | CPU placement | Memory limit | Concurrent positions |
| --- | ---: | --- | --- | ---: |
| C05-Codex | 1 | Audited result receipts show one assigned CPU per position | Not specified in C05 registration | 6 planned workers |
| C05-KNN and C05-LinUCB | 1 | Audited result receipts show one assigned CPU per position | Not specified in C05 registration | 12 planned control workers combined |
| CHILS-p4-custom | 1 | Audited result receipts show one assigned CPU per position | Not specified in C05 registration | 6 planned workers |
| Classical arms, including greedy | 1 | One distinct physical CPU per live position | 3 GiB RLIMIT_AS per OS process; 72 GiB aggregate cloud cgroup | At most 24 |

## Comparison conditions

- All primary positions have a registered 900-second end-to-end upper bound within their runners. Clock scopes differ: C05 controls hash the NPZ before their solve clock, while classical positions hash it inside the 900-second clock. Loading, preprocessing, search, extraction, and feasibility can therefore occupy different shares; identical net search time is not established.
- The C05 registration specifies one native thread per position but no per-position RAM cap. The primary classical 3 GiB RLIMIT_AS applies to the Python wrapper and native child as separate OS processes; the 72 GiB cloud cgroup is the hard aggregate cap. Local-search positions failing under the primary cap remain excluded. The separate recovery uses 6 GiB RLIMIT_AS per process under the same cgroup; per-position memory parity with C05 is unproved, and 24 × 3 GiB is not asserted as an aggregate RSS bound.
- Planned concurrent load differs: C05 assigns 6 Codex, 12 controls combined, and 6 CHILS workers; the classical registration permits up to 24 simultaneous positions. These are registered limits or allocations, not a claim that actual machine load was identical.
- The C05-Codex strict audit records 840 external LLM calls. Single-CPU affinity and local wall/CPU accounting describe the cloud solver position; the external model computation is separate.
- C05 methods have five measured seed positions per view (40 per arm). Official StableSolver methods have one measured seed-ignored position per view (8 per arm); repeating a view value for paired arithmetic does not add measured runs.

## Means by view

Each C05, GRASP, and hybrid-SA cell averages five measured seeds. Each StableSolver cell is one measured run per view with an unused CLI seed.

| View | C05-Codex | C05-KNN | C05-LinUCB | CHILS-p4-custom | StableSolver local search | StableSolver large neighborhood search | GRASP | Hybrid SA (archived) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| g0340 | 1627021.2220094 | 1626391.4540162 | 1626422.7258004 | 1624295.3013054 | — | 1606757.713608 | 1555832.8553174 | 1624229.5987384 |
| g0680 | 1249807.1559956 | 1249720.546139 | 1249674.5638136 | 1246822.8549266 | — | 1233939.023935 | 1146848.5632728 | 1252900.802435 |
| g1200 | 885939.6582008 | 886167.2004262 | 884792.1989252 | 878486.982797 | — | 878847.932789 | 842676.9882518 | 898685.2138978 |
| g1800 | 683849.323555 | 683563.6286884 | 683638.3953646 | 682048.4291238 | — | 681135.339774 | 628040.084205 | 691926.0381688 |
| gW1200_gE0340_s0150 | 1263765.0176584 | 1262764.4211656 | 1263571.8436248 | 1261094.794089 | — | 1248351.846663 | 1195656.7186366 | 1267064.719454 |
| gW0340_gE1200_s0150 | 1263296.0565856 | 1262441.7715262 | 1262715.3031636 | 1259316.0186778 | — | 1249332.201996 | 1195730.6748368 | 1266553.7333464 |
| gW0680_gE1200_s0150 | 1068547.62971 | 1068593.5892238 | 1068732.6826114 | 1064712.0451818 | — | 1054238.193906 | 992165.7851124 | 1075058.087186 |
| gW1200_gE0680_s0150 | 1067856.8860656 | 1067972.6795448 | 1067793.6976924 | 1062765.8593788 | — | 1055088.318494 | 992634.718264 | 1074916.1307448 |

## Supplementary one-pass greedy

The wall-time column is the observed stop time, averaged over the eight distinct measured views in the overall row. This arm is separate from the full-budget search comparison.

Audit status: audited_complete (8/8 audited valid positions).

| View | Mean objective (contact seconds) | Actual stop time (seconds) |
| --- | ---: | ---: |
| g0340 | 1428001.569166 | 1.147 |
| g0680 | 1065567.634886 | 1.470 |
| g1200 | 809272.863416 | 2.205 |
| g1800 | 489395.475268 | 2.588 |
| gW1200_gE0340_s0150 | 1125626.623769 | 1.796 |
| gW0340_gE1200_s0150 | 1130378.71427 | 1.576 |
| gW0680_gE1200_s0150 | 940994.22741 | 1.765 |
| gW1200_gE0680_s0150 | 943714.371695 | 2.040 |
| Overall (8 measured views) | 991618.934985 | 1.823 |

## Candidate implementation compatibility

- Official KaMIS commit `2e4b3861b8063f05e8520434b97ce59e21ca49e7` uses an unsigned 32-bit `NodeWeight` in its weighted path and `int` for some total scores. Each registered graph has a total exact weight of 16,260,928,472,613 ticks, so the exact-weight objective exceeds those accumulator ranges. The study does not scale the weights and does not include KaMIS in the primary comparison table.

## Public evidence

- The [primary audit summary](results/audit_public.json) and [104-position receipt index](results/receipt_index.csv) retain SHA256 links to the private full receipts and audit.
- [Primary failures](results/primary_failures.csv) keep failed positions visible without assigning them an objective.
- [Comparison tables](results/comparison_overall.csv) and [view table](results/comparison_by_view.csv) use only complete audited arms.
- [Recorded trajectory figures](results/figures/manifest_public.json) include only complete audited arms and hash their private inputs.
- Full primary independent audit SHA256: `7bf399b7696f3243da36ed9f530c9a2c2c148121e707830995ab3451ff29c1b0`.
- The [failed 6 GiB local-search frame](recovery_6gib_failed/RESULTS.md) records 0/8 valid final scores under a native 895-second cap and outer 900-second cap.
- The [9 GiB local-search contingency](recovery_9gib/RESULTS.md) uses a native 850-second cap inside the outer 900-second cap and is separate from the primary 3 GiB comparison.
