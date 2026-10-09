#!/usr/bin/env python3
"""One independently auditable plain-GRASP position, capped at 900 wall seconds."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import time
from pathlib import Path

import numpy as np
from convert_npz_to_metis import graph_arrays, write_dimacs


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, content: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(content, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def check_solution(result: dict, npz_path: Path) -> tuple[list[int], int]:
    selected = result.get("selected")
    if not isinstance(selected, list) or any(type(v) is not int for v in selected):
        raise ValueError("selected must be a list of integer vertex IDs")
    with np.load(npz_path, allow_pickle=False) as z:
        weights = z["weight_ticks"]
        edge_u = z["edge_u"]
        edge_v = z["edge_v"]
    if len(set(selected)) != len(selected) or any(v < 0 or v >= len(weights) for v in selected):
        raise ValueError("selected contains duplicate or out-of-range IDs")
    bits = np.zeros(len(weights), dtype=np.bool_)
    bits[selected] = True
    if bool(np.any(bits[edge_u] & bits[edge_v])):
        raise ValueError("selected contains conflicting vertices")
    exact = sum(int(weights[v]) for v in selected)
    if type(result.get("value_ticks")) is not int or result["value_ticks"] != exact:
        raise ValueError("reported objective differs from exact NPZ weight sum")
    return selected, exact


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--inputs-root", type=Path, required=True)
    ap.add_argument("--output-root", type=Path,
                    help="separate output root for a short pilot; defaults to --root")
    ap.add_argument("--graph-id", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--cpu", type=int, required=True)
    ap.add_argument("--pilot-seconds", type=float)
    args = ap.parse_args()

    root = args.root.resolve(strict=True)
    output_root = args.output_root.resolve() if args.output_root else root
    protocol_path = root / "preregistration.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol["status"] != "frozen":
        raise ValueError("protocol is not frozen")
    graph = protocol["graphs"].get(args.graph_id)
    if graph is None or args.seed not in protocol["seeds"]:
        raise ValueError("position is outside registration")
    source = root / "plain_grasp.cpp"
    binary = root / "plain_grasp"
    if sha256(source) != protocol["algorithm"]["source_sha256"]:
        raise ValueError("GRASP source differs from registration")
    if sha256(binary) != protocol["algorithm"]["binary_sha256"]:
        raise ValueError("GRASP binary differs from registration")
    if sha256(Path(__file__)) != protocol["algorithm"]["runner_sha256"]:
        raise ValueError("GRASP runner differs from registration")
    converter = root / "convert_npz_to_metis.py"
    if sha256(converter) != protocol["reference_converter_sha256"]:
        raise ValueError("exact NPZ-to-DIMACS converter differs from registration")
    inputs = args.inputs_root.resolve(strict=True)
    npz = inputs / "data" / "CP-SCALE-AU-L002" / graph["npz_name"]
    dimacs = inputs / "graphs" / graph["dimacs_name"]
    os.sched_setaffinity(0, {args.cpu})
    if sorted(os.sched_getaffinity(0)) != [args.cpu]:
        raise RuntimeError("one-core affinity not applied")
    os.environ.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")

    stem = f"{args.graph_id}__s{args.seed:04d}"
    result_path = output_root / "results" / (stem + ".json")
    native_path = output_root / "native" / (stem + ".json")
    trace_path = output_root / "traces" / (stem + ".csv")
    log_path = output_root / "logs" / (stem + ".log")
    runtime_dimacs = output_root / "inputs" / (stem + ".dimacs")
    for path in (result_path, native_path, trace_path, log_path, runtime_dimacs):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing position: {path}")

    wall_cap = float(args.pilot_seconds if args.pilot_seconds is not None else
                     protocol["budget"]["wall_seconds_per_position"])
    if args.pilot_seconds is not None and not (0 < wall_cap <= 30):
        raise ValueError("pilot duration must lie in (0,30] seconds")
    start = time.monotonic()
    cpu_before = time.process_time()
    children_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    status, error, selected, value_ticks, native_meta, command = "error", None, None, None, None, None
    return_code = None
    try:
        if sha256(npz) != graph["npz_sha256"] or sha256(dimacs) != graph["dimacs_sha256"]:
            raise ValueError("input hash mismatch")
        # Build the native format from the original NPZ inside this position's
        # 900-second clock. The output must match the independently frozen graph.
        weights, edge_u, edge_v = graph_arrays(npz)
        write_dimacs(runtime_dimacs, weights, edge_u, edge_v)
        del weights, edge_u, edge_v
        if sha256(runtime_dimacs) != graph["dimacs_sha256"]:
            raise ValueError("timed NPZ-to-DIMACS conversion differs from frozen graph")
        reserve = protocol["budget"]["output_reserve_seconds"]
        native_seconds = wall_cap - (time.monotonic() - start) - reserve
        if native_seconds <= 0:
            raise TimeoutError("input hashing exhausted wall budget")
        command = [str(binary), "--graph", str(runtime_dimacs), "--seed", str(args.seed),
                   "--seconds", f"{native_seconds:.6f}", "--offset-seconds",
                   f"{time.monotonic() - start:.6f}", "--result", str(native_path),
                   "--trace", str(trace_path)]
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                       timeout=max(0.5, wall_cap - (time.monotonic() - start) - 0.5),
                                       check=False)
        return_code = completed.returncode
        if return_code != 0:
            raise RuntimeError(f"native solver exited {return_code}")
        native_meta = json.loads(native_path.read_text(encoding="utf-8"))
        if native_meta.get("mode") != "grasp_plain" or native_meta.get("seed") != args.seed:
            raise ValueError("native mode or seed mismatch")
        selected, value_ticks = check_solution(native_meta, npz)
        if time.monotonic() - start > wall_cap:
            raise TimeoutError("position exceeded 900-second outer cap")
        status = "pilot_ok" if args.pilot_seconds is not None else "ok"
    except Exception as exc:
        error = type(exc).__name__ + ": " + str(exc)
    children_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    receipt = {
        "schema": "plain_grasp_wmis_position_v1", "arm": "grasp_plain",
        "graph_id": args.graph_id, "seed": args.seed, "status": status,
        "input_sha256": graph["npz_sha256"], "dimacs_sha256": graph["dimacs_sha256"],
        "runtime_dimacs_sha256": sha256(runtime_dimacs) if runtime_dimacs.exists() else None,
        "runtime_dimacs_path": str(runtime_dimacs.relative_to(output_root)) if runtime_dimacs.exists() else None,
        "converter_sha256": sha256(converter),
        "source_sha256": sha256(source), "binary_sha256": sha256(binary),
        "runner_sha256": sha256(Path(__file__)), "protocol_sha256": sha256(protocol_path),
        "configuration": protocol["algorithm"]["configuration"],
        "cpu_id": args.cpu, "cpu_affinity": sorted(os.sched_getaffinity(0)),
        "wall_limit_seconds": wall_cap, "wall_seconds": time.monotonic() - start,
        "cpu_seconds": ((children_after.ru_utime + children_after.ru_stime) -
                        (children_before.ru_utime + children_before.ru_stime) +
                        (time.process_time() - cpu_before)),
        "peak_rss_bytes": int(children_after.ru_maxrss) * 1024,
        "selected_zero_based": selected, "value_ticks": value_ticks,
        "trajectory_path": str(trace_path.relative_to(output_root)) if trace_path.exists() else None,
        "native_path": str(native_path.relative_to(output_root)) if native_path.exists() else None,
        "native_meta": {k: v for k, v in (native_meta or {}).items() if k != "selected"},
        "native_return_code": return_code, "command": command, "error": error,
    }
    write_json(result_path, receipt)
    print(json.dumps({"result": str(result_path), "status": status,
                      "value_ticks": value_ticks, "wall_seconds": receipt["wall_seconds"]}))
    if status not in ("ok", "pilot_ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
