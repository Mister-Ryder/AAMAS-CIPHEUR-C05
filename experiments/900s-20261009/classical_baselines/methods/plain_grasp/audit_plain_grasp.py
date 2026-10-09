#!/usr/bin/env python3
"""Independent original-NPZ and trace audit for the 40 measured plain-GRASP positions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def audit_trace(path: Path, final_value: int, wall_cap: float) -> tuple[int, float]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or set(rows[0]) != {"elapsed_seconds", "value_ticks", "iteration"}:
        raise ValueError("trace missing or has unexpected columns")
    previous_time = -1.0
    previous_value = -1
    previous_iteration = -1
    for i, row in enumerate(rows):
        t = float(row["elapsed_seconds"])
        value = int(row["value_ticks"])
        iteration = int(row["iteration"])
        if t < previous_time or not (0 <= t <= wall_cap + 0.1):
            raise ValueError("trace time is not monotonic or exceeds cap")
        if i == 0 and (value != 0 or iteration != 0):
            raise ValueError("trace must begin at the empty state")
        if i and (value <= previous_value or iteration <= previous_iteration):
            raise ValueError("noninitial trace rows must be strict improvements")
        previous_time, previous_value, previous_iteration = t, value, iteration
    if previous_value != final_value:
        raise ValueError("last trace objective differs from final incumbent")
    return len(rows) - 1, previous_time


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--inputs-root", type=Path, required=True)
    args = ap.parse_args()
    root = args.root.resolve(strict=True)
    inputs = args.inputs_root.resolve(strict=True)
    protocol_path = root / "preregistration.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol["status"] != "frozen":
        raise ValueError("protocol must be frozen")
    wanted_positions = {(g, s) for g in protocol["graphs"] for s in protocol["seeds"]}
    actual_files = list((root / "results").glob("*.json"))
    if len(actual_files) != protocol["expected_positions"]:
        raise ValueError(f"expected 40 receipts, found {len(actual_files)}")
    source_hash = sha256(root / "plain_grasp.cpp")
    binary_hash = sha256(root / "plain_grasp")
    runner_hash = sha256(root / "run_plain_grasp.py")
    converter_hash = sha256(root / "convert_npz_to_metis.py")
    registration_hash = sha256(protocol_path)
    identity = protocol["algorithm"]
    if (source_hash, binary_hash, runner_hash) != (
        identity["source_sha256"], identity["binary_sha256"], identity["runner_sha256"]
    ):
        raise ValueError("source, binary, or runner changed after registration")
    if converter_hash != protocol["reference_converter_sha256"]:
        raise ValueError("timed converter changed after registration")
    seen = set()
    by_view = {g: [] for g in protocol["graphs"]}
    indexed = []
    for path in sorted(actual_files):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        graph_id, seed = receipt["graph_id"], receipt["seed"]
        position = (graph_id, seed)
        if position not in wanted_positions or position in seen:
            raise ValueError("unregistered or duplicate position")
        seen.add(position)
        graph = protocol["graphs"][graph_id]
        if (receipt["arm"] != "grasp_plain" or receipt["status"] != "ok" or
            receipt["source_sha256"] != source_hash or receipt["binary_sha256"] != binary_hash or
            receipt["runner_sha256"] != runner_hash or receipt["protocol_sha256"] != registration_hash or
            receipt["input_sha256"] != graph["npz_sha256"] or
            receipt["dimacs_sha256"] != graph["dimacs_sha256"] or
            receipt.get("runtime_dimacs_sha256") != graph["dimacs_sha256"] or
            receipt.get("converter_sha256") != converter_hash):
            raise ValueError(f"identity or status mismatch: {path.name}")
        runtime_path = root / receipt["runtime_dimacs_path"]
        if (not runtime_path.is_file() or sha256(runtime_path) != graph["dimacs_sha256"] or
                str(runtime_path) not in receipt["command"]):
            raise ValueError(f"timed converted DIMACS differs or was not used: {path.name}")
        if receipt["cpu_affinity"] != [receipt["cpu_id"]] or receipt["wall_limit_seconds"] != 900 or not (
            0 < receipt["wall_seconds"] <= 900.1
        ):
            raise ValueError(f"resource or wall-budget mismatch: {path.name}")
        native_path = root / receipt["native_path"]
        trace_path = root / receipt["trajectory_path"]
        native = json.loads(native_path.read_text(encoding="utf-8"))
        selected = receipt["selected_zero_based"]
        if (native.get("mode") != "grasp_plain" or native.get("seed") != seed or
            native.get("selected") != selected or native.get("value_ticks") != receipt["value_ticks"]):
            raise ValueError(f"native output mismatch: {path.name}")
        if not isinstance(selected, list) or any(type(x) is not int for x in selected) or len(set(selected)) != len(selected):
            raise ValueError(f"invalid vertex list: {path.name}")
        npz = inputs / "data" / "CP-SCALE-AU-L002" / graph["npz_name"]
        dimacs = inputs / "graphs" / graph["dimacs_name"]
        if sha256(npz) != graph["npz_sha256"] or sha256(dimacs) != graph["dimacs_sha256"]:
            raise ValueError(f"input hash mismatch: {graph_id}")
        with np.load(npz, allow_pickle=False) as z:
            weights = z["weight_ticks"]
            edge_u = z["edge_u"]
            edge_v = z["edge_v"]
        if any(v < 0 or v >= len(weights) for v in selected):
            raise ValueError(f"selected vertex out of range: {path.name}")
        bits = np.zeros(len(weights), dtype=np.bool_)
        bits[selected] = True
        if bool(np.any(bits[edge_u] & bits[edge_v])):
            raise ValueError(f"conflicting selected vertices: {path.name}")
        exact = sum(int(weights[v]) for v in selected)
        if exact != receipt["value_ticks"]:
            raise ValueError(f"objective mismatch: {path.name}")
        improvements, last_time = audit_trace(trace_path, exact, 900)
        if improvements != native["improvements"]:
            raise ValueError(f"trace improvement count mismatch: {path.name}")
        by_view[graph_id].append(exact)
        indexed.append({"graph_id": graph_id, "seed": seed, "value_ticks": exact,
                        "wall_seconds": receipt["wall_seconds"], "improvements": improvements,
                        "last_improvement_seconds": last_time, "receipt_sha256": sha256(path),
                        "trace_sha256": sha256(trace_path), "native_sha256": sha256(native_path),
                        "runtime_dimacs_sha256": sha256(runtime_path)})
    if seen != wanted_positions or any(len(values) != 5 for values in by_view.values()):
        raise ValueError("incomplete graph/seed frame")
    total = sum(sum(values) for values in by_view.values())
    summary = {"schema": "plain_grasp_900s_independent_audit_v1", "status": "audited_complete",
               "valid_positions": len(indexed), "expected_positions": len(wanted_positions),
               "mean_value_ticks_exact": str(Decimal(total) / Decimal(len(indexed))),
               "mean_contact_seconds_exact": str(Decimal(total) / Decimal(len(indexed) * 1_000_000)),
               "by_view_mean_contact_seconds": {
                   graph_id: str(Decimal(sum(values)) / Decimal(len(values) * 1_000_000))
                   for graph_id, values in by_view.items()},
               "protocol_sha256": registration_hash, "positions": indexed}
    path = root / "independent_audit.json"
    if path.exists():
        raise FileExistsError("refusing to overwrite audit")
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("status", "valid_positions", "mean_contact_seconds_exact")}))


if __name__ == "__main__":
    main()
