# Third-party attribution

The native engine uses the unmodified CHILS `graph.c` and `local_search.c`
search kernel and its supporting headers, distributed under the MIT license.
The original copyright and license are preserved in
[`third_party/CHILS/LICENSE`](third_party/CHILS/LICENSE).

- Upstream: [KarlsruheMIS/CHILS](https://github.com/KarlsruheMIS/CHILS)
- Pinned upstream commit: `515952724cd3dcc6c4365a340ecf0f1da782119a`
- Copyright (c) 2025 Kenneth Langedal, Ernestine Großmann, and Christian Schulz
- Publication: Ernestine Großmann, Kenneth Langedal and Christian Schulz,
  *Concurrent Iterated Local Search for the Maximum Weight Independent Set
  Problem*, SEA 2025, LIPIcs 338, 22:1–22:18,
  [DOI 10.4230/LIPIcs.SEA.2025.22](https://doi.org/10.4230/LIPIcs.SEA.2025.22).

The vendored minimal source subset includes `chils_internal.c`, but the C05
build links only `graph.c` and `local_search.c` with the CIPHEUR native backend.
It does not link the upstream `chils_run` population controller. The build
checks the two compiled upstream source files against their pinned Git blob
hashes.

The CIPHEUR Python modules, native adapters, and release utilities retain the
root MIT license from the frozen research implementation. The `cipheur_v04`,
`cipheur_v05`, `cipheur_cex`, and `cipheur_common` directory names are preserved
to keep the C05 runtime imports and implementation lineage explicit; they
contain the dependencies needed by C05 rather than separate experimental
releases.
