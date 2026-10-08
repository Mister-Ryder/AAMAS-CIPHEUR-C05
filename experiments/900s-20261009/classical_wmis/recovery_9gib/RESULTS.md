# Separate 9 GiB StableSolver local-search result

The 8/8 audited contingency is separate from the 3 GiB primary comparison and failed 6 GiB frame.

| View | Objective (contact seconds) | Actual wall seconds |
| --- | ---: | ---: |
| g0340 | 1611612.4559 | 871.7484700195491 |
| g0680 | 1238830.700809 | 866.2220148853958 |
| g1200 | 883669.893016 | 860.2802024818957 |
| g1800 | 680517.666261 | 855.2649111934006 |
| gW1200_gE0340_s0150 | 1256203.282786 | 867.2058871239424 |
| gW0340_gE1200_s0150 | 1255384.373079 | 866.4387081637979 |
| gW0680_gE1200_s0150 | 1061756.024208 | 862.6948408670723 |
| gW1200_gE0680_s0150 | 1060753.736721 | 862.776580248028 |
| Equal-view mean | 1131091.0165975 | — |

The native cap was 850 seconds inside the 900-second outer wall limit. The [independent audit](results/audit_public.json) accepted all eight results. The [recovery-only trajectories](results/figures/manifest_public.json) use recorded incumbent event times.
