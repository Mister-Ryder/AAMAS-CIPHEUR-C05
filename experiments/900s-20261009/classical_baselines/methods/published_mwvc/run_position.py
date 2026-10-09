#!/usr/bin/env python3
"""Run one registered FastWVC or Feasibility Jump position on one Linux CPU."""

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
from convert_npz_to_metis import graph_arrays, write_metis


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, content: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(content, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    temporary.replace(path)


def limit_memory() -> None:
    cap = 3 * 1024**3
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))


def current_rss_bytes(pid: int) -> int:
    try:
        fields = Path(f"/proc/{pid}/statm").read_text(encoding="ascii").split()
        return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
    except (FileNotFoundError, ProcessLookupError):
        return 0


def validate_native(native: dict, source: Path, method: str, seed: int, expected_total: int) -> tuple[list[int], int]:
    if native.get("schema") != "published_mwvc_native_v1" or native.get("solver") != method:
        raise ValueError("native method or schema differs")
    if type(native.get("seed")) is not int or native["seed"] != seed:
        raise ValueError("native seed differs")
    selected = native.get("selected")
    if not isinstance(selected, list) or any(type(x) is not int for x in selected):
        raise ValueError("native selected list is invalid")
    with np.load(source, allow_pickle=False) as archive:
        weights = archive["weight_ticks"]
        edge_u = archive["edge_u"]
        edge_v = archive["edge_v"]
    n = len(weights)
    if len(selected) != len(set(selected)) or any(v < 0 or v >= n for v in selected):
        raise ValueError("selected vertex ID is duplicated or out of range")
    bits = np.zeros(n, dtype=np.bool_)
    bits[selected] = True
    if bool(np.any(bits[edge_u] & bits[edge_v])):
        raise ValueError("selected set is not independent in source NPZ")
    total = sum(int(w) for w in weights)
    value = sum(int(weights[v]) for v in selected)
    for key, wanted in (("total_weight_ticks", total), ("value_ticks", value),
                        ("cover_weight_ticks", total - value)):
        if type(native.get(key)) is not int or native[key] != wanted:
            raise ValueError(f"native {key} differs from independent NPZ recomputation")
    if total != expected_total:
        raise ValueError("source weight sum differs from registration")
    return selected, value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--build-manifest", type=Path)
    parser.add_argument("--arm", choices=("fastwvc", "fj"), required=True)
    parser.add_argument("--graph-id", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--cpu", type=int, required=True)
    parser.add_argument("--pilot-seconds", type=int)
    args = parser.parse_args()

    root = args.root.resolve(strict=True)
    output_root = (args.output_root or root).resolve()
    registration = root / "preregistration.json"
    protocol = json.loads(registration.read_text(encoding="utf-8"))
    for filename, wanted in protocol["harness_sha256"].items():
        if sha256(root / filename) != wanted:
            raise ValueError("frozen harness file changed: " + filename)
    manifest_path = root / "inputs" / "graphs" / "metis_manifest.json"
    graph_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    build_path = (args.build_manifest or (root / "build_manifest.json")).resolve(strict=True)
    build = json.loads(build_path.read_text(encoding="utf-8"))
    if protocol["status"] != "frozen" or build["protocol_sha256"] != sha256(registration):
        raise ValueError("build and frozen registration disagree")
    if protocol["graph_manifest_sha256"] != sha256(manifest_path):
        raise ValueError("converted graph manifest changed")
    if args.arm not in protocol["arms"] or args.graph_id not in protocol["source_graph_sha256"] or args.seed not in protocol["seeds"]:
        raise ValueError("position outside registered frame")
    if args.pilot_seconds is not None and not 5 <= args.pilot_seconds <= 900:
        raise ValueError("pilot wall seconds must be between 5 and 900")
    matching = [r for r in graph_manifest["graphs"] if r["graph_id"] == args.graph_id]
    if len(matching) != 1:
        raise ValueError("graph manifest lacks a unique row")
    row = matching[0]
    method = protocol["arms"][args.arm]
    binary = root / build["arms"][args.arm]["binary_relative_path"]
    if sha256(binary) != build["arms"][args.arm]["binary_sha256"]:
        raise ValueError("native binary changed after build")
    for filename, wanted in protocol["source_git"][args.arm]["source_sha256"].items():
        if sha256(root / filename) != wanted:
            raise ValueError("frozen method source changed")
    os.sched_setaffinity(0, {args.cpu})
    actual_affinity = sorted(os.sched_getaffinity(0))
    if actual_affinity != [args.cpu]:
        raise RuntimeError("single-CPU affinity was not applied")
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"

    source = root / "inputs" / "data" / "CP-SCALE-AU-L002" / row["input_name"]
    graph = root / "inputs" / "graphs" / row["metis_name"]
    stem = f"{args.graph_id}__s{args.seed:04d}"
    family = "pilot" if args.pilot_seconds is not None else "measured"
    result_path = output_root / family / "results" / args.arm / (stem + ".json")
    native_path = output_root / family / "native" / args.arm / (stem + ".json")
    log_path = output_root / family / "logs" / args.arm / (stem + ".log")
    runtime_graph = output_root / family / "inputs" / args.arm / (stem + ".graph")
    for path in (result_path, native_path, log_path, runtime_graph):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError("refusing to overwrite an existing position: " + str(path))

    wall_limit = args.pilot_seconds or protocol["budget"]["outer_wall_seconds_per_position"]
    start = time.monotonic()
    cpu_before = time.process_time()
    child_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    status = "error"
    error = None
    return_code = None
    outer_preempted = False
    sampled_tree_peak_rss_bytes = 0
    native = None
    selected = None
    value = None
    command = None
    try:
        if sha256(source) != protocol["source_graph_sha256"][args.graph_id]:
            raise ValueError("source NPZ hash differs")
        if sha256(graph) != row["metis_sha256"]:
            raise ValueError("METIS input hash differs")
        weights, edge_u, edge_v = graph_arrays(source)
        write_metis(runtime_graph, weights, edge_u, edge_v)
        del weights, edge_u, edge_v
        if sha256(runtime_graph) != row["metis_sha256"]:
            raise ValueError("per-position NPZ-to-METIS conversion differs from frozen reference")
        remaining = wall_limit - (time.monotonic() - start)
        native_limit = min(protocol["budget"]["native_cutoff_seconds"],
                           int(remaining - protocol["budget"]["outer_reserve_seconds"]))
        if native_limit < 1:
            raise TimeoutError("input checks left no solver budget")
        if args.arm == "fastwvc":
            command = [str(binary), str(runtime_graph), str(args.seed), str(native_limit), "0", str(native_path)]
        else:
            command = [str(binary), str(runtime_graph), str(args.seed), str(native_limit), str(native_path)]
        with log_path.open("w", encoding="utf-8") as log:
            child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                     preexec_fn=limit_memory)
            deadline = start + wall_limit - 3.0
            while True:
                sampled_tree_peak_rss_bytes = max(
                    sampled_tree_peak_rss_bytes,
                    current_rss_bytes(os.getpid()) + current_rss_bytes(child.pid),
                )
                code = child.poll()
                if code is not None:
                    return_code = code
                    break
                if time.monotonic() >= deadline:
                    child.kill()
                    child.wait()
                    outer_preempted = True
                    return_code = 124
                    break
                time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
            sampled_tree_peak_rss_bytes = max(
                sampled_tree_peak_rss_bytes, current_rss_bytes(os.getpid()) + current_rss_bytes(child.pid)
            )
        if return_code != 0 and not outer_preempted:
            raise RuntimeError(f"native solver exited with code {return_code}")
        native = json.loads(native_path.read_text(encoding="utf-8"))
        if outer_preempted and (args.arm != "fj" or native.get("checkpoint_final") is not False or
                                time.monotonic() - start < wall_limit - 5.0):
            raise RuntimeError("FJ preemption lacked a near-deadline atomic feasible checkpoint")
        selected, value = validate_native(
            native, source, method, args.seed,
            protocol["graph_invariants"]["weight_sum_ticks_per_view"],
        )
        if time.monotonic() - start > wall_limit:
            raise TimeoutError("position exceeded wall cap including certificate audit")
        status = "pilot_ok" if args.pilot_seconds is not None else "ok"
    except Exception as exc:
        error = type(exc).__name__ + ": " + str(exc)
    child_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    wall = time.monotonic() - start
    receipt = {
        "schema": "published_mwvc_position_v1",
        "status": status,
        "arm": args.arm,
        "graph_id": args.graph_id,
        "seed": args.seed,
        "input_sha256": protocol["source_graph_sha256"][args.graph_id],
        "metis_sha256": row["metis_sha256"],
        "runtime_graph_sha256": sha256(runtime_graph) if runtime_graph.exists() else None,
        "runtime_graph_relative_path": str(runtime_graph.relative_to(output_root)).replace("\\", "/") if runtime_graph.exists() else None,
        "converter_sha256": sha256(root / "convert_npz_to_metis.py"),
        "protocol_sha256": sha256(registration),
        "graph_manifest_sha256": sha256(manifest_path),
        "build_manifest_sha256": sha256(build_path),
        "binary_sha256": build["arms"][args.arm]["binary_sha256"],
        "runner_sha256": sha256(Path(__file__)),
        "upstream_commit": protocol["source_git"][args.arm]["commit"],
        "cpu_id": args.cpu,
        "cpu_affinity": actual_affinity,
        "native_threads": 1,
        "address_space_limit_bytes": protocol["budget"]["address_space_limit_bytes_per_process"],
        "wall_limit_seconds": wall_limit,
        "wall_seconds": wall,
        "cpu_seconds": ((child_after.ru_utime + child_after.ru_stime) -
                        (child_before.ru_utime + child_before.ru_stime) +
                        (time.process_time() - cpu_before)),
        "peak_rss_bytes": int(child_after.ru_maxrss) * 1024,
        "parent_peak_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024,
        "sampled_tree_peak_rss_bytes": sampled_tree_peak_rss_bytes,
        "tree_rss_sampling_interval_seconds": 0.5,
        "selected_zero_based": selected,
        "value_ticks": value,
        "native_meta": {k: v for k, v in (native or {}).items() if k != "selected"},
        "native_relative_path": str(native_path.relative_to(output_root)).replace("\\", "/") if native_path.exists() else None,
        "log_relative_path": str(log_path.relative_to(output_root)).replace("\\", "/") if log_path.exists() else None,
        "native_return_code": return_code,
        "outer_preempted": outer_preempted,
        "command": command,
        "error": error,
    }
    write_json(result_path, receipt)
    print(json.dumps({"receipt": str(result_path), "status": status, "value_ticks": value,
                      "wall_seconds": wall}, separators=(",", ":")))
    if status not in ("ok", "pilot_ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
