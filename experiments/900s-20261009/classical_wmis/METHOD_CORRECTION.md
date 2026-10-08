# Method correction: originally labeled simulated annealing

The 900-second arm identified by the frozen code key `simulated_annealing` is a hybrid heuristic. Its implementation constructs a weighted-degree greedy initial independent set, applies an insert-and-evict move followed by greedy repair after an accepted proposal, and restarts from the best incumbent in ten cooling cycles. The acceptance probability uses the objective change before greedy repair, while the submitted state includes the repair. It therefore must not be interpreted as an unaugmented Metropolis simulated-annealing baseline.

The 40/40 final certificates and their exact-weight objective values remain audited observations. Their eight-view equal-weight mean is 1,143,916.7904964 contact-seconds. The original raw arm key, frozen source hash, receipts and trajectories are retained for provenance. In the existing trajectory figures, the legend `SA` refers to this hybrid heuristic. This arm is excluded from the plain classical-SA comparison until a separately registered plain-SA experiment is completed.

Resource review found that these 40 positions were pinned to one physical CPU each, used less than the registered 900-second outer limit, and had a 3 GiB per-process address-space cap. The correction concerns the algorithm definition and acceptance rule, not an invalid feasibility certificate or an observed CPU/wall-time excess.

The StableSolver local-search and large-neighborhood-search algorithms in the frozen upstream commit accept a CLI `--seed`, but those two algorithm branches do not use that argument. Their original 8/8 framing is one run per graph view. It must not be described as five stochastic seeds per view; later repeated processes, if run, are labeled process repeats.
