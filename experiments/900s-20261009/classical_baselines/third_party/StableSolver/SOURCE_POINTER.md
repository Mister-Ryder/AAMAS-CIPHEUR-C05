# Official StableSolver source

The `local-search`, `large-neighborhood-search` and `greedy-gwmin` methods use the unmodified official [fontanf/stablesolver](https://github.com/fontanf/stablesolver) implementation at commit `efab011b460c2675647fa1995011eb332ab9ec7d`. The frozen `git archive HEAD` SHA-256 is `c8efdf57a822b9386d1f480999af4045db64c1e6acbe5728564644196e8027c1`; the measured binary SHA-256 is `a0515f47072400b8f64f38496e3e97832a65fb69c967f1bd6d5fe298eb4d5f2e`.

The native `--seed 0` argument is ignored by these official methods. The five slots per view are separate measured operating-system processes. The public method metadata states the different time, memory and input-conversion scopes for each arm. The official upstream source is obtained from its pinned repository rather than copied into this result package.
