#!/usr/bin/env python3
"""Run one exact-weight GRASP or SA position with a 900 s wall-clock cap.

The DIMACS input is frozen before the measured launch. Hashing and loading the
graph, native search, extraction, and the local NPZ feasibility check are all
charged to the position's measured time. The output is independently auditable.
"""

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


ARM_TO_MODE = {"grasp": "grasp", "simulated_annealing": "sa"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def dump_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    temp.replace(path)


def validate_result(native: dict, npz_path: Path) -> tuple[list[int], int]:
    selected = native.get("selected")
    if not isinstance(selected, list) or any(type(v) is not int for v in selected):
        raise ValueError("native output has no integer selected list")
    with np.load(npz_path, allow_pickle=False) as z:
        weights = z["weight_ticks"]
        edge_u = z["edge_u"]
        edge_v = z["edge_v"]
    if len(selected) != len(set(selected)) or any(v < 0 or v >= len(weights) for v in selected):
        raise ValueError("selected vertex ID is duplicate or out of range")
    bits = np.zeros(len(weights), dtype=np.bool_)
    bits[selected] = True
    if bool(np.any(bits[edge_u] & bits[edge_v])):
        raise ValueError("native selection violates an NPZ conflict edge")
    objective = sum(int(weights[v]) for v in selected)
    if type(native.get("value_ticks")) is not int or native["value_ticks"] != objective:
        raise ValueError("native objective differs from exact NPZ sum")
    return selected, objective


def fixed_config(arm: str) -> dict:
    common = {
        "mode": ARM_TO_MODE[arm],
        "native_mode": ARM_TO_MODE[arm],
        "native_threads": 1,
        "weight_unit": "integer contact microseconds (ticks)",
        "input_format": "DIMACS1992 p edge / n / e, original vertex order",
        "method_source": "self-contained classical_mwis.cpp; no C05 or CHILS solver calls",
    }
    if arm == "grasp":
        return {**common, **{
            "construction": "rank-based restricted candidate list on weight/(1+degree)^alpha",
            "rcl_sizes": [4, 8, 16],
            "degree_exponents": [0.7, 1.0, 1.3],
            "local_moves": ["positive insert-and-evict with greedy refill",
                            "improving remove-one-and-greedy-refill"],
            "restart_schedule": "cycle fixed construction variants until deadline",
        }}
    return {**common, **{
        "construction": "deterministic weighted-degree greedy",
        "neighbourhood": "insert nonselected vertex, evict selected conflicts, greedily refill",
        "temperature_hot": "2 * median vertex weight",
        "temperature_cold": "0.01 * median vertex weight",
        "cooling": "geometric, ten equal wall-time cycles; reset to incumbent each cycle",
    }}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--graph-id", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--arm", choices=tuple(ARM_TO_MODE), required=True)
    parser.add_argument("--cpu", type=int, required=True)
    parser.add_argument("--pilot-seconds", type=float)
    args = parser.parse_args()

    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.graph_id not in protocol["graph_sha256"] or args.seed not in protocol["seeds"]:
        raise ValueError("position is outside the frozen protocol")
    records = [row for row in manifest["graphs"] if row["graph_id"] == args.graph_id]
    if len(records) != 1:
        raise ValueError("missing or duplicate graph manifest record")
    record = records[0]
    view = args.graph_id.split("__", 1)[1]
    npz_path = args.data_dir / (view + ".npz")
    dimacs_path = args.input_dir / record["dimacs_name"]
    if not args.binary.is_file() or not args.source.is_file():
        raise FileNotFoundError("source or native binary missing")
    source_hash = sha256(args.source)
    binary_hash = sha256(args.binary)
    runner_hash = sha256(Path(__file__))
    protocol_hash = sha256(args.protocol)

    # Each live position receives one designated core. The native child inherits it.
    os.sched_setaffinity(0, {args.cpu})
    actual_affinity = sorted(os.sched_getaffinity(0))
    if actual_affinity != [args.cpu]:
        raise RuntimeError("single-CPU affinity was not applied")
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"

    stem = f"{args.graph_id}__s{args.seed:04d}"
    result_path = args.output_root / "results" / args.arm / (stem + ".json")
    native_path = args.output_root / "native" / args.arm / (stem + ".json")
    trace_path = args.output_root / "traces" / args.arm / (stem + ".csv")
    log_path = args.output_root / "logs" / args.arm / (stem + ".log")
    for path in (result_path, native_path, trace_path, log_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError("refusing to overwrite an existing position: " + str(path))

    registered_limit = float(protocol["budget"]["wall_seconds_per_position"])
    if registered_limit != 900.0:
        raise ValueError("this runner requires the registered 900 s limit")
    if args.pilot_seconds is not None and not (0 < args.pilot_seconds <= 30):
        raise ValueError("pilot seconds must be in (0, 30]")
    wall_limit = args.pilot_seconds if args.pilot_seconds is not None else registered_limit
    start = time.monotonic()
    self_cpu_before = time.process_time()
    child_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    error = None
    selected = None
    value_ticks = None
    native_meta = None
    status = "error"
    return_code = None
    command = None
    try:
        if sha256(npz_path) != protocol["graph_sha256"][args.graph_id]:
            raise ValueError("source NPZ hash does not match protocol")
        if sha256(dimacs_path) != record["dimacs_sha256"]:
            raise ValueError("DIMACS hash does not match manifest")
        # Reserve time for extracting the native solution and checking it against NPZ.
        native_seconds = wall_limit - (time.monotonic() - start) - 1.5
        if native_seconds <= 0:
            raise TimeoutError("input validation exhausted wall budget")
        command = [str(args.binary), "--graph", str(dimacs_path), "--mode", ARM_TO_MODE[args.arm],
                   "--seed", str(args.seed), "--seconds", f"{native_seconds:.6f}",
                   "--offset-seconds", f"{time.monotonic() - start:.6f}",
                   "--result", str(native_path), "--trace", str(trace_path)]
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                       timeout=max(0.5, wall_limit - (time.monotonic() - start) - 0.5),
                                       check=False)
        return_code = completed.returncode
        if return_code != 0:
            raise RuntimeError(f"native solver exited with code {return_code}")
        native_meta = json.loads(native_path.read_text(encoding="utf-8"))
        if native_meta.get("mode") != ARM_TO_MODE[args.arm] or native_meta.get("seed") != args.seed:
            raise ValueError("native mode or seed does not match launch")
        selected, value_ticks = validate_result(native_meta, npz_path)
        if time.monotonic() - start > wall_limit:
            raise TimeoutError("position exceeded wall limit including local audit")
        status = "pilot_ok" if args.pilot_seconds is not None else "ok"
    except Exception as exc:
        error = type(exc).__name__ + ": " + str(exc)
    child_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    wall_seconds = time.monotonic() - start
    result = {
        "schema": "classical_wmis_position_v1",
        "arm": args.arm,
        "graph_id": args.graph_id,
        "seed": args.seed,
        "input_sha256": protocol["graph_sha256"][args.graph_id],
        "metis_sha256": record["metis_sha256"],
        "dimacs_sha256": record["dimacs_sha256"],
        "source_sha256": source_hash,
        "binary_sha256": binary_hash,
        "runner_sha256": runner_hash,
        "protocol_sha256": protocol_hash,
        "config": {**fixed_config(args.arm), "pilot_seconds": args.pilot_seconds},
        "cpu_id": args.cpu,
        "cpu_affinity": actual_affinity,
        "wall_limit_seconds": wall_limit,
        "wall_seconds": wall_seconds,
        "cpu_seconds": ((child_after.ru_utime + child_after.ru_stime) -
                        (child_before.ru_utime + child_before.ru_stime) +
                        (time.process_time() - self_cpu_before)),
        "peak_rss_bytes": int(child_after.ru_maxrss) * 1024,
        "status": status,
        "selected_zero_based": selected,
        "value_ticks": value_ticks,
        "trajectory_path": str(trace_path.relative_to(args.output_root)) if trace_path.exists() else None,
        "native_path": str(native_path.relative_to(args.output_root)) if native_path.exists() else None,
        "native_meta": {k: v for k, v in (native_meta or {}).items() if k != "selected"},
        "native_return_code": return_code,
        "command": command,
        "error": error,
    }
    dump_json_atomic(result_path, result)
    print(json.dumps({"result": str(result_path), "status": status, "value_ticks": value_ticks,
                      "wall_seconds": wall_seconds}, separators=(",", ":")))
    if status not in ("ok", "pilot_ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
