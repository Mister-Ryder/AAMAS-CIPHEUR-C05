#!/usr/bin/env python3
"""Lossless CP-SCALE NPZ -> weighted METIS 10 and DIMACS1992 conversion.

The conversion is benchmark input preparation and is run once before the
measured positions. Every solver subsequently loads these frozen bytes within
its own 900-second wall clock. Vertex row i remains NPZ zero-based vertex i.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def graph_arrays(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with np.load(path, allow_pickle=False) as data:
        weights = data["weight_ticks"].astype(np.int64, copy=True)
        u = data["edge_u"].astype(np.int64, copy=True)
        v = data["edge_v"].astype(np.int64, copy=True)
    n = len(weights)
    if n == 0 or len(u) != len(v) or np.any(weights <= 0):
        raise ValueError(f"Invalid graph size or weights in {path}")
    if np.any(u < 0) or np.any(v >= n) or np.any(u >= v):
        raise ValueError(f"Invalid edge orientation or endpoint in {path}")
    order = np.lexsort((v, u))
    if len(order) > 1 and np.any((u[order][1:] == u[order][:-1]) &
                                 (v[order][1:] == v[order][:-1])):
        raise ValueError(f"Duplicate undirected edge in {path}")
    return weights, u, v


def write_metis(path: Path, weights: np.ndarray, u: np.ndarray, v: np.ndarray) -> None:
    n, m = len(weights), len(u)
    sources = np.concatenate((u, v))
    neighbors = np.concatenate((v, u))
    order = np.lexsort((neighbors, sources))
    sorted_sources = sources[order]
    sorted_neighbors = neighbors[order]
    ptr = np.empty(n + 1, dtype=np.int64)
    ptr[0] = 0
    ptr[1:] = np.cumsum(np.bincount(sorted_sources, minlength=n), dtype=np.int64)
    if int(ptr[-1]) != 2 * m:
        raise AssertionError("METIS adjacency length mismatch")
    with path.open("x", encoding="ascii", newline="\n", buffering=1 << 20) as out:
        out.write(f"{n} {m} 10\n")
        for i in range(n):
            lo, hi = int(ptr[i]), int(ptr[i + 1])
            adjacency = sorted_neighbors[lo:hi]
            if len(adjacency) > 1 and np.any(adjacency[1:] <= adjacency[:-1]):
                raise AssertionError(f"METIS row {i} is not strictly sorted")
            out.write(str(int(weights[i])))
            if hi > lo:
                out.write(" " + " ".join(str(int(j) + 1) for j in adjacency))
            out.write("\n")
        out.flush()
        os.fsync(out.fileno())


def write_dimacs(path: Path, weights: np.ndarray, u: np.ndarray, v: np.ndarray) -> None:
    n, m = len(weights), len(u)
    order = np.lexsort((v, u))
    with path.open("x", encoding="ascii", newline="\n", buffering=1 << 20) as out:
        out.write(f"p edge {n} {m}\n")
        for i, weight in enumerate(weights, start=1):
            out.write(f"n {i} {int(weight)}\n")
        for j in order:
            out.write(f"e {int(u[j]) + 1} {int(v[j]) + 1}\n")
        out.flush()
        os.fsync(out.fileno())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    protocol_path = args.protocol.resolve(strict=True)
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol.get("physical_source") != "CP-SCALE-AU-L002" or len(protocol["graph_sha256"]) != 8:
        raise ValueError("Expected the eight registered CP-SCALE-AU-L002 views")
    if protocol.get("representation", {}).get("shared_native_graph") != "frozen exact weighted METIS 10 and DIMACS1992 files":
        raise ValueError("Wrong conversion registration")

    data_dir = args.data_dir.resolve(strict=True)
    out_dir = args.out_dir.resolve()
    if out_dir.exists():
        raise FileExistsError(f"Refuse to overwrite frozen METIS directory: {out_dir}")
    out_dir.mkdir(parents=True)
    records = []
    for graph_id, expected_hash in protocol["graph_sha256"].items():
        name = graph_id.split("__", 1)[1]
        input_path = data_dir / f"{name}.npz"
        if sha256(input_path) != expected_hash:
            raise ValueError(f"Input SHA256 mismatch: {graph_id}")
        weights, u, v = graph_arrays(input_path)
        output_path = out_dir / f"{name}.graph"
        dimacs_path = out_dir / f"{name}.dimacs"
        write_metis(output_path, weights, u, v)
        write_dimacs(dimacs_path, weights, u, v)
        record = {
            "graph_id": graph_id,
            "input_name": input_path.name,
            "input_sha256": expected_hash,
            "metis_name": output_path.name,
            "metis_sha256": sha256(output_path),
            "metis_bytes": output_path.stat().st_size,
            "dimacs_name": dimacs_path.name,
            "dimacs_sha256": sha256(dimacs_path),
            "dimacs_bytes": dimacs_path.stat().st_size,
            "n": len(weights),
            "m": len(u),
            "weight_sum_ticks": sum(int(w) for w in weights),
            "max_vertex_weight_ticks": int(weights.max()),
            "vertex_mapping": "METIS row and DIMACS node ID i+1 map to NPZ zero-based row i"
        }
        records.append(record)
        print(json.dumps(record, sort_keys=True), flush=True)
    manifest = {
        "schema": "cipheur_classical_metis_manifest_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": sha256(protocol_path),
        "converter_sha256": sha256(Path(__file__)),
        "graph_count": len(records),
        "graphs": records,
    }
    with (out_dir / "metis_manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
