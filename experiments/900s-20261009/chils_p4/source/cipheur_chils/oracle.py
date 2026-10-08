"""Auditable bounded MWIS branch-and-bound with sound integer bounds on interruption."""
from __future__ import annotations
from dataclasses import dataclass, asdict
import time
from fractions import Fraction


@dataclass
class Bound:
    lower: int
    upper: int
    selected: list[int]
    nodes: int
    exact: bool
    reason: str

    def to_json(self): return asdict(self)


def solve_region(g, vertices, max_nodes=20_000, seconds=0.1, max_vertices=180):
    vs = sorted(vertices)
    if not vs: return Bound(0, 0, [], 0, True, "empty")
    # A too-large region is NOT truncated. A feasible bound is still useful.
    chosen, blocked = set(), set()
    region_set=set(vs)
    for v in sorted(vs, key=lambda u: (-Fraction(g.weights[u],1+len(g.adj[u] & region_set)), u)):
        if v not in blocked:
            chosen.add(v); blocked.add(v); blocked.update(g.adj[v])
    lb = g.weight(chosen)
    if len(vs)>max_vertices:
        return Bound(lb, g.weight(vs), sorted(chosen), 0, False, "region_limit_not_truncated")
    start = time.perf_counter()
    ix = {v: i for i, v in enumerate(vs)}
    w = [g.weights[v] for v in vs]
    adj = [sum(1 << ix[u] for u in g.adj[v] if u in ix) for v in vs]

    def clique_ub(mask):
        # Partition vertices into disjoint cliques. An IS can take at most one
        # vertex per clique; sum of clique maxima is a valid MWIS upper bound.
        groups, maxima = [], []
        remaining = mask
        while remaining:
            bit = remaining & -remaining; remaining ^= bit
            v = bit.bit_length()-1
            for j, clique in enumerate(groups):
                if clique & ~adj[v] == 0:
                    groups[j] |= bit; maxima[j] = max(maxima[j], w[v]); break
            else:
                groups.append(bit); maxima.append(w[v])
        return sum(maxima)

    full = (1 << len(vs))-1
    incumbent = sum(1 << ix[v] for v in chosen)
    root = clique_ub(full)
    stack = [(full, 0, 0, root)]
    visited = 0
    while stack:
        if visited >= max_nodes or time.perf_counter()-start >= seconds: break
        mask, value, selected, ub = stack.pop(); visited += 1
        if ub <= lb: continue
        if not mask:
            if value > lb: lb, incumbent = value, selected
            continue
        # Branch choice affects effort only, never the certificate's validity.
        ids = [i for i in range(len(vs)) if mask >> i & 1]
        v = max(ids, key=lambda i: ((adj[i] & mask).bit_count(), w[i], -i))
        bit = 1 << v
        for child, val, sol in ((mask & ~bit, value, selected), (mask & ~bit & ~adj[v], value+w[v], selected|bit)):
            upper = val+clique_ub(child)
            if not child:
                if val > lb: lb, incumbent = val, sol
            elif upper > lb:
                stack.append((child, val, sol, upper))
    ub = max([lb]+[item[3] for item in stack])
    exact = ub == lb
    selected = [v for i, v in enumerate(vs) if incumbent >> i & 1]
    assert g.feasible(selected) and g.weight(selected)==lb and lb<=ub
    return Bound(lb, ub, selected, visited, exact, "optimum" if exact else "budget")
