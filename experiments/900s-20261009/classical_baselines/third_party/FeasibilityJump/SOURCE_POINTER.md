# FeasibilityJump source and adapter

The included `fj_mwis.cpp` is the thin adapter used for the measured MWIS arm. It calls the author's **unmodified** `feasibilityjump.hh` from [SINTEF/feasibilityjump](https://github.com/sintef/feasibilityjump) at commit `93f1c2ae4bb00fa333dd82c23d7d1abec7dd4fc5`. The upstream header SHA-256 is `87fd81e0c75d3c701fe64768f31a24c6d0a185832c0f7ce7cfe0b116cd192435`; the adapter SHA-256 is `df29ddb795d12aff6716c3fc9794eb82eb82682a87556d6466bf0e77a7371a4d`. Both are MIT licensed; the upstream license text is included here.

To compile the adapter from this layout, obtain the pinned upstream header and place it at `source/feasibilityjump/feasibilityjump.hh`, preserving the adapter's relative include. The measured configuration maps each graph vertex to a binary variable and each conflict edge to `x_u + x_v <= 1`. It minimizes the negative original integer weight scaled exactly by `2^-29`; that fixed scale preserves integer objective ordering while changing the native penalty-relative scale. The adapter supplies no incumbent or external heuristic. It emits feasible-incumbent checkpoints for deadline recovery.

The measured executable, compiler flags and SHA-256 must match the frozen private build receipt before any result is released. The public result manifest records that receipt and the independently audited selected-set hashes.
