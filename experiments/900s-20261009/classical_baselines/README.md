# C05 non-LLM controls: corrected 900-second methods

This package complements the frozen C05-KNN, C05-LinUCB and CHILS-p4 controls already on the `non-llm-controls` branch. Its physical source is `CP-SCALE-AU-L002`. Eight registered constraint views represent that one source; they are not eight independent datasets. Every reported method has exactly five measured positions in each view. Randomized methods use seeds `67, 71, 73, 79, 83`; the official StableSolver methods ignore the native seed argument, so slots `r00`–`r04` are separate timed process repeats with native seed `0`.

The objective is the exact sum of `weight_ticks` for a feasible independent set on the original NPZ conflict graph. One contact-second equals 1,000,000 ticks. Each view mean is the arithmetic mean of its five positions, and the overall mean gives each of the eight views equal weight. A score is released only when all 40 receipts, native selected-set certificates and original NPZ graph hashes pass an independent audit and the exporter's second certificate audit.

## Results

| Method | Frame | Scope | Equal-view mean, contact-seconds | Mean measured wall (s) |
| --- | ---: | --- | ---: | ---: |
| grasp_plain_v2 | 40/40 | primary_timed_conversion_900s | 1057129.697335075 | 898.565 |
| fj40 | 40/40 | primary_timed_conversion_900s | 1131650.331062575 | 891.862 |
| fastwvc40 | 40/40 | primary_timed_conversion_900s | 1124487.99147315 | 891.431 |
| lns5_v3 | 40/40 | primary_timed_conversion_900s | 1125481.521509175 | 891.793 |
| ls4_9gib | 40/40 | separate_9gib_native850_900s | 1131305.548309475 | 865.330 |
| gwmin40 | 40/40 | supplementary_one_pass_fresh40 | 991618.934985 | 2.575 |

Scope labels matter. GRASP, FJ, FastWVC, LNS and GWMIN use 900-second outer clocks that include fresh input conversion and certificate audit. StableSolver local search uses a separate 9 GiB per-process address-space cap and an 850-second native limit within 900 seconds. GWMIN stops naturally after one pass and is supplementary; all 40 positions are fresh timed process runs with zero old receipt reuse. Its formal launcher uses one physical worker on CPU 23 within a registered maximum of six. The results table shows its actual mean wall time so its score is not mistaken for a full-budget search result.

## Frozen method and resource settings

| Method | Frozen algorithm setting | Actual registered resource scope | Trace availability |
| --- | --- | --- | --- |
| grasp_plain_v2 | One fixed GRASP configuration: construction score weight_ticks/(1+original degree), feasible RCL size 8 and uniform choice; repeat randomized construction plus strictly positive exact-gain insert/evict local search; no configuration cycling, remove-one refill or greedy repair. | 900 s outer wall includes fresh NPZ-to-DIMACS conversion and NPZ audit; 1 native thread and 1 physical CPU per run; 6 workers; 3 GiB address-space cap per process; shared 72 GiB cgroup; 1.5 s output reserve. | Actual strict incumbent improvements. |
| fj40 | Unmodified MIT FeasibilityJump author header through thin 0/1 MIP adapter; minimize negative exact 2^-29 scaled integer weights; author initialization and callback period 500000 effort units; feasible checkpoints at first improvement and at most every 2 s while improving; no external repair. | 900 s outer wall including fresh NPZ-to-METIS conversion and NPZ audit; native cutoff 890 s; 1 native thread, 1 physical CPU per run; registered maximum 6 workers, formal launcher 5; parent and native child each separately capped at 3 GiB; shared 72 GiB cgroup. | Feasible-incumbent checkpoints; native intermediate trace only when exposed. |
| fastwvc40 | FastWVC native mode 0, author search operations and initialization; GPL-3.0 patch widens objective and penalty accumulators to signed 64-bit, fixes incumbent sentinel and emits exact cover-complement certificate; no external heuristics. | 900 s outer wall including fresh NPZ-to-METIS conversion and NPZ audit; native cutoff 890 s; 1 native thread, 1 physical CPU per run; registered maximum 6 workers, formal launcher 5; parent and native child each separately capped at 3 GiB; shared 72 GiB cgroup. | Final certificate; intermediate incumbent trace only if exposed by the native run. |
| lns5_v3 | Unmodified official StableSolver large-neighborhood-search mode; five independent timed OS process repeats per view; native --seed 0 is ignored by this method. | 900 s outer wall including fresh NPZ-to-DIMACS conversion and NPZ audit; native limit 890 s; 1 native thread and 1 physical CPU per run; 5 workers on CPUs 6–10; wrapper and child each separately capped at 3 GiB; shared 72 GiB cgroup. | Native incumbent trace with measured wall-time alignment bounds. |
| ls4_9gib | Unmodified official StableSolver local-search mode; five independent timed OS process repeats per view; native --seed 0 is ignored by this method. | 900 s outer wall including fresh NPZ-to-DIMACS conversion and NPZ audit; native limit 850 s; 1 native thread and 1 physical CPU per run; 9 GiB address-space cap per process, registered maximum 6 workers and formal launcher 4; shared 72 GiB cgroup. | Native incumbent trace with measured wall-time alignment bounds. |
| gwmin40 | Unmodified official StableSolver greedy-gwmin, one pass to natural stop; five fresh timed OS process repeats per view, native --seed 0 ignored; zero prior receipt reuse. | One-pass natural stop under a 900 s outer cap that includes fresh NPZ-to-DIMACS conversion and NPZ audit; actual runtime reported; 1 native thread and 1 physical CPU per run; 3 GiB address-space cap per process; registered maximum 6 workers, formal launcher 1 on CPU 23; shared 72 GiB cgroup. | Usually final point only; no interpolation. |

Every measured process uses one native solver thread and one physical CPU. The 72 GiB cloud memory limit is shared across concurrent work; each method's per-process cap and worker count are listed above. Parent and native child caps for FJ, FastWVC and LNS are separate address-space limits, not a combined process-tree RSS cap. Formal launcher concurrency can be lower than the registered maximum. FJ uses a standalone single-thread 0/1 MIP adapter around the unmodified author header, with a fixed `2^-29` objective scale and no Xpress or presolve; its result describes this adaptation.

## Evidence and traces

`positions.csv` gives each view and seed or process-repeat slot, exact objective, measured wall/CPU time, observed RSS when available, receipt/certificate/native SHA-256, and whether an incumbent trace is available. `audit_public.json` gives each method's frozen protocol hash, independent audit hash, binary hash, 40/40 coverage and exact eight-view mean. `SHA256SUMS.json` hashes every exported file except itself. The original selected sets and full receipts remain in the private experiment archive so the public package contains no raw Codex model messages or environment paths.

Trace files are reported only when produced by the method and validated by the independent audit. GRASP exposes actual strict improvements. Official StableSolver trajectories derive from its native output and have measured wall-time alignment bounds. FJ exposes feasible checkpoints; FastWVC may have only a final certificate. Missing intermediate events are not interpolated.

## Third-party code

FastWVC comes from the [GNN-MWVC collection](https://github.com/KennethLangedal/GNN-MWVC) pinned at `9c16708ae9c6489fc1ff1ac9d51bcec0743b2577`, with the original algorithm attributed to Cai et al. The `third_party/FastWVC/` directory contains only the signed-64-bit/certificate patch, attribution notice and GPL-3.0 license; the adapted full third-party source is not bundled. FeasibilityJump is pinned to [SINTEF/feasibilityjump](https://github.com/sintef/feasibilityjump) commit `93f1c2ae4bb00fa333dd82c23d7d1abec7dd4fc5`. Its unmodified MIT header is obtained from that commit; this package includes the MIT license, thin adapter and source pointer.

The frozen self-contained GRASP source, measured-position wrapper and independent auditor are in `methods/`. Official StableSolver source is referenced by commit and SHA-256 in `third_party/StableSolver/SOURCE_POINTER.md`.

Earlier GRASP and StableSolver runs with different timing or concurrency scopes were withdrawn from this comparison. None of their measured objectives is reused here, including the earlier heterogeneous GWMIN repeats.
