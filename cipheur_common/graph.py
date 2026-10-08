"""Lossless positive-int MWIS input. Graphs, not inferred physical constraints, are authoritative."""
from __future__ import annotations
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
import hashlib
import json
import numpy as np


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def strict_int(x):
    if isinstance(x, (bool, float, np.floating)):
        raise ValueError("Weights/indices must be integers; float rounding is forbidden")
    if not isinstance(x, (int, np.integer, str)):
        raise ValueError(f"Not an integer: {type(x)}")
    return int(x)


@dataclass
class Graph:
    weights: tuple[int, ...]
    adj: tuple[frozenset[int], ...]
    meta: dict = field(default_factory=dict)
    source_sha256: str = ""

    def __post_init__(self):
        self.weights = tuple(strict_int(w) for w in self.weights)
        self.adj = tuple(frozenset(strict_int(v) for v in a) for a in self.adj)
        n = len(self.weights)
        if len(self.adj) != n or n >= 2**31 - 1:
            raise ValueError("Invalid vertex count")
        # CHILS adds random perturbations and computes 2*W[v] internally. This
        # conservative, explicitly checked range avoids signed-64 intermediate overflow.
        if any(w <= 0 for w in self.weights) or sum(self.weights) > (2**63 - 1)//4:
            raise ValueError("Positive weights with total <= INT64_MAX/4 required; no clipping")
        for u, nei in enumerate(self.adj):
            if u in nei or any(v < 0 or v >= n for v in nei):
                raise ValueError("Self-loop or out-of-range endpoint")
            if any(u not in self.adj[v] for v in nei):
                raise ValueError("Asymmetric adjacency")
        for k, values in list(self.meta.items()):
            if k in {"start_ticks", "end_ticks", "ground_gap_by_node_ticks"}:
                self.meta[k] = [strict_int(v) for v in values]
            elif k in {"antenna_id", "satellite_id", "contact_id"}:
                self.meta[k] = [v.decode("utf-8") if isinstance(v, bytes) else str(v) for v in values]
            if len(values) != n:
                raise ValueError(f"Metadata length mismatch: {k}")
        if "start_ticks" in self.meta and "end_ticks" in self.meta:
            if any(int(b) < int(a) for a, b in zip(self.meta["start_ticks"], self.meta["end_ticks"])):
                raise ValueError("Negative contact duration")

    @property
    def n(self):
        return len(self.weights)

    @property
    def m(self):
        return sum(map(len, self.adj))//2

    def weight(self, chosen):
        return sum(self.weights[v] for v in chosen)

    def feasible(self, chosen):
        s = set(chosen)
        return all(0 <= v < self.n for v in s) and all(not (self.adj[v] & s) for v in s)

    def fingerprint(self):
        obj = {"weights": self.weights, "edges": [(u, v) for u in range(self.n) for v in sorted(self.adj[u]) if u < v], "meta": self.meta}
        return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def csr(self):
        ptr = [0]
        edges = []
        for nei in self.adj:
            edges.extend(sorted(nei))
            ptr.append(len(edges))
        return (np.asarray(self.weights, dtype=np.int64), np.asarray(ptr, dtype=np.int64), np.asarray(edges, dtype=np.int32))

    def degree_seed(self):
        chosen, blocked = set(), set()
        # Exact Fraction ordering, unlike float division; used identically for all arms.
        for v in sorted(range(self.n), key=lambda u: (-Fraction(self.weights[u], len(self.adj[u])+1), u)):
            if v not in blocked:
                chosen.add(v)
                blocked.add(v)
                blocked.update(self.adj[v])
        return chosen

    def write_metis(self, path):
        with open(path, "w", encoding="ascii", newline="\n") as f:
            f.write(f"{self.n} {self.m} 10\n")
            for u in range(self.n):
                f.write(str(self.weights[u]) + "".join(f" {v+1}" for v in sorted(self.adj[u])) + "\n")


def from_edges(weights, edges, meta=None, source_sha256=""):
    w = tuple(strict_int(x) for x in weights)
    a = [set() for _ in w]
    seen = set()
    for x, y in edges:
        u, v = strict_int(x), strict_int(y)
        if not (0 <= u < len(w) and 0 <= v < len(w)) or u == v:
            raise ValueError("Invalid edge")
        e = (min(u, v), max(u, v))
        if e in seen:
            raise ValueError("Duplicate undirected edge; fix input explicitly")
        seen.add(e)
        a[u].add(v); a[v].add(u)
    return Graph(w, tuple(frozenset(s) for s in a), meta or {}, source_sha256)


def load_graph(path):
    path = Path(path)
    digest = sha256_file(path)
    if path.suffix.lower() == ".npz":
        with np.load(path, allow_pickle=False) as z:
            w = z["weight_ticks"].tolist()
            us, vs = z["edge_u"].tolist(), z["edge_v"].tolist()
            if len(us) != len(vs):
                raise ValueError("Edge array length mismatch")
            meta = {}
            for key in ("satellite_id", "antenna_id", "start_ticks", "end_ticks", "ground_gap_by_node_ticks", "contact_id"):
                if key in z:
                    meta[key] = z[key].tolist()
            if "ground_gap_by_node_ticks" not in meta and "ground_gap_ticks" in z:
                meta["ground_gap_by_node_ticks"] = [strict_int(z["ground_gap_ticks"].item())]*len(w)
        return from_edges(w, zip(us, vs), meta, digest)
    if path.suffix.lower() == ".json":
        obj = json.loads(path.read_text(encoding="utf-8"))
        w = obj.get("weight_ticks", obj.get("weights"))
        if isinstance(w, dict):
            w = [w[str(i)] for i in range(len(w))]
        return from_edges(w, obj["edges"], obj.get("metadata", {}), digest)
    lines = [s.strip() for s in path.read_text(encoding="ascii").splitlines() if s.strip() and not s.lstrip().startswith("%")]
    header = [int(x) for x in lines[0].split()]
    if len(header) != 3 or header[2] != 10:
        raise ValueError("Only vertex-weighted METIS 'n m 10' is supported")
    n, m, _ = header
    if len(lines) != n+1:
        raise ValueError("Wrong METIS row count")
    w, adj = [], []
    for line in lines[1:]:
        row = list(map(int, line.split()))
        w.append(row[0]); a = [x-1 for x in row[1:]]
        if len(a) != len(set(a)):
            raise ValueError("Duplicate METIS adjacency")
        adj.append(frozenset(a))
    g = Graph(tuple(w), tuple(adj), {}, digest)
    if g.m != m:
        raise ValueError("Wrong METIS edge count")
    return g
