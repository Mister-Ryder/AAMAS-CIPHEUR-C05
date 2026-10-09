# C05 900-second non-LLM controls

This branch holds the C05-KNN and C05-LinUCB selectors and the original CHILS p4 custom comparator for the 900-second CP-SCALE-AU-L002 study. The shared registration and audit procedure are in [README.md](README.md), inherited from `main`.

The complete 160-position matrix passed independent audit. This branch publishes the 120 control positions and 24 view means in [control results](controls/results/). Each method has 40 positions; the final equal-view means, in contact-seconds, are:

| Method | Positions | Mean contact-seconds |
|---|---:|---:|
| C05-KNN | 40 | 1,138,451.911341275 |
| C05-LinUCB | 40 | 1,138,417.6763745 |
| CHILS-p4-custom (`search_step=10`) | 40 | 1,134,942.785685025 |

The [public audit receipt](controls/results/audit_public.json) contains private audit and original result hashes. [Pairwise deltas](controls/results/paired_deltas.csv) use the same graph and seed for each Codex and control position and are computed in contact-seconds as `Codex - control`.

The C05 control runner is [run_c05_controls_900s.py](scripts/run_c05_controls_900s.py). It uses the frozen C05 source plus the shared one-line call-cap extension, validates every graph hash before launch, keeps one native thread per position, schedules 80 control positions, and writes each result only once. The recorded deployment reserves 12 physical CPUs for the control queue. CPU IDs in the original registration describe that cloud deployment; a new machine may use different physical CPU IDs while preserving the worker count and per-position thread limit.

The CHILS comparator uses the pinned original implementation from the historical C04 source, copied under [chils_p4/source](chils_p4/source). Its [public registration](chils_p4/registration_public.json), [source manifest](chils_p4/source_manifest_public.json), and original compiled binary are archived without the cloud's private paths. The registration retains SHA256 values for the original private registration, source manifest, build receipt, and binary. Its configuration is population four, one native thread, `search_step=10`, and a 900-second end-to-end deadline. The original cloud run used six physical CPUs for this arm. [run_chils_public.py](chils_p4/run_chils_public.py) accepts a graph directory, a fresh output directory, and six permitted CPU IDs to rerun the original kernel with those settings.

The controls branch does not contain Codex raw model input/output archives. Its public result directory contains only sanitized per-position metrics, source and binary hashes, numeric summaries, and independent audit receipts. Full private solver results remain outside Git.

The separate [classical and published-method package](classical_baselines/README.md) describes six additional fresh 40-position method frames, including their resource scopes and audit hashes. It keeps the 9 GiB local-search frame and the one-pass GWMIN frame distinct from the primary 900-second search frame. SA results are held outside this experiment package.
