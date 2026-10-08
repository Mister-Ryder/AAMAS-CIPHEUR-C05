#!/usr/bin/env python3
"""Run one frozen, independently audited official StableSolver MWIS position.

The measured clock starts before hashing the NPZ, DIMACS, and METIS inputs and
ends after reading and checking the native certificate against the source NPZ.
The official solver's own Time starts after it has loaded the DIMACS graph;
trajectory timestamps retain that time and add a measured startup/load offset.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import resource
import subprocess
import time
from pathlib import Path

import numpy as np


ARMS = {
    "stablesolver_local_search": "local-search",
    "stablesolver_large_neighborhood_search": "large-neighborhood-search",
    "stablesolver_greedy_gwmin": "greedy-gwmin",
}
SOURCE_SHA256 = "c8efdf57a822b9386d1f480999af4045db64c1e6acbe5728564644196e8027c1"
BINARY_SHA256 = "a0515f47072400b8f64f38496e3e97832a65fb69c967f1bd6d5fe298eb4d5f2e"
SOURCE_COMMIT = "efab011b460c2675647fa1995011eb332ab9ec7d"
POLL_SECONDS = 0.01


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_archive_sha256(repo: Path) -> str:
    process = subprocess.Popen(
        ["git", "-C", str(repo), "archive", "HEAD"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None
    digest = hashlib.sha256()
    for block in iter(lambda: process.stdout.read(1 << 20), b""):
        digest.update(block)
    process.stdout.close()
    stderr = process.stderr.read() if process.stderr is not None else b""
    if process.wait() != 0:
        raise RuntimeError("git archive failed: " + stderr.decode("utf-8", "replace"))
    return digest.hexdigest()


def one_record(manifest: dict, graph_id: str) -> dict:
    records = [r for r in manifest["graphs"] if r.get("graph_id") == graph_id]
    if len(records) != 1:
        raise ValueError("missing or duplicate graph manifest record")
    record = records[0]
    for key in ("input_name", "dimacs_name", "metis_name"):
        name = record.get(key)
        if not isinstance(name, str) or Path(name).name != name or name in ("", ".", ".."):
            raise ValueError("unsafe graph manifest filename: " + key)
    return record


def certificate_vertices(path: Path) -> list[int]:
    tokens = path.read_text(encoding="ascii").split()
    if not tokens:
        raise ValueError("empty native certificate")
    try:
        return [int(token) for token in tokens]
    except ValueError as exc:
        raise ValueError("native certificate contains a noninteger vertex ID") from exc


def exact_audit(selected: list[int], native: dict, npz_path: Path, record: dict) -> int:
    with np.load(npz_path, allow_pickle=False) as data:
        weights = data["weight_ticks"]
        edge_u = data["edge_u"]
        edge_v = data["edge_v"]
    if len(weights) != record["n"] or len(edge_u) != record["m"] or len(edge_v) != record["m"]:
        raise ValueError("source NPZ shape differs from frozen manifest")
    if len(selected) != len(set(selected)) or any(v < 0 or v >= len(weights) for v in selected):
        raise ValueError("native certificate has duplicate or out-of-range zero-based vertex IDs")
    chosen = np.zeros(len(weights), dtype=np.bool_)
    chosen[selected] = True
    if bool(np.any(chosen[edge_u] & chosen[edge_v])):
        raise ValueError("native certificate violates an original NPZ conflict edge")
    value = sum(int(weights[v]) for v in selected)  # Python integer, never int64 overflow.
    output = native.get("Output")
    if not isinstance(output, dict) or not isinstance(output.get("Value"), str):
        raise ValueError("native JSON has no exact string Output.Value")
    if int(output["Value"]) != value:
        raise ValueError("native JSON Output.Value differs from exact source NPZ sum")
    solution = output.get("Solution")
    if not isinstance(solution, dict) or solution.get("Feasible") is not True:
        raise ValueError("native JSON does not report a feasible solution")
    if type(solution.get("NumberOfVertices")) is not int or solution["NumberOfVertices"] != len(selected):
        raise ValueError("native JSON solution cardinality differs from certificate")
    if type(solution.get("Weight")) is not int or solution["Weight"] != value:
        raise ValueError("native JSON solution weight differs from exact source NPZ sum")
    return value


def numeric_time(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(label + " is not numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(label + " is negative or nonfinite")
    return result


def trajectory_points(native: dict, final_value: int) -> list[tuple[float, int]]:
    records = native.get("IntermediaryOutputs", [])
    if not isinstance(records, list):
        raise ValueError("native IntermediaryOutputs is not a list")
    points = []
    previous_value = -1
    previous_time = -1.0
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("native intermediary row is not an object")
        t = numeric_time(row.get("Time"), "native intermediary Time")
        if t < previous_time:
            raise ValueError("native intermediary times run backward")
        previous_time = t
        raw_value = row.get("Value")
        if not isinstance(raw_value, str):
            raise ValueError("native intermediary Value is not an exact string")
        value = int(raw_value)
        if value > previous_value:
            points.append((t, value))
            previous_value = value
    output = native["Output"]
    final_time = numeric_time(output.get("Time"), "native final Time")
    if final_time < previous_time or final_value < previous_value:
        raise ValueError("native final Time/Value is behind an intermediary output")
    if final_value > previous_value or not points:
        points.append((final_time, final_value))
    return points


def proc_peak_bytes(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/status", "r", encoding="ascii") as stream:
            for line in stream:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        pass
    return 0


def save_json_new(path: Path, obj: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    for flag in ("protocol", "manifest", "data-dir", "input-dir", "output-root", "source", "binary"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--graph-id", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--arm", choices=tuple(ARMS), required=True)
    parser.add_argument("--cpu", type=int, required=True)
    parser.add_argument("--pilot-wall-seconds", type=float)
    parser.add_argument("--pilot-native-seconds", type=float)
    args = parser.parse_args()

    protocol_path = args.protocol.resolve(strict=True)
    manifest_path = args.manifest.resolve(strict=True)
    source_path = args.source.resolve(strict=True)
    binary_path = args.binary.resolve(strict=True)
    if not source_path.is_dir() or not binary_path.is_file():
        raise ValueError("--source must be the frozen git repository and --binary a file")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if args.graph_id not in protocol["graph_sha256"] or args.seed != 0:
        raise ValueError("official StableSolver position requires registered graph_id and seed=0")
    identity = protocol["method_identity"][args.arm]
    if identity["algorithm_flag"] != ARMS[args.arm]:
        raise ValueError("protocol algorithm flag differs from frozen runner")
    if identity["source_sha256"] != SOURCE_SHA256 or identity["binary_sha256"] != BINARY_SHA256:
        raise ValueError("protocol official source/binary hashes differ from frozen runner")
    record = one_record(manifest, args.graph_id)
    if record["input_sha256"] != protocol["graph_sha256"][args.graph_id]:
        raise ValueError("manifest source NPZ SHA256 differs from protocol")
    view = args.graph_id.split("__", 1)[1]
    if record["input_name"] != view + ".npz" or record["dimacs_name"] != view + ".dimacs":
        raise ValueError("graph manifest does not preserve original vertex/view mapping")
    npz_path = args.data_dir / record["input_name"]
    dimacs_path = args.input_dir / record["dimacs_name"]
    metis_path = args.input_dir / record["metis_name"]
    for path in (npz_path, dimacs_path, metis_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    pilot = args.pilot_wall_seconds is not None or args.pilot_native_seconds is not None
    if pilot:
        if args.pilot_wall_seconds is None or args.pilot_native_seconds is None:
            raise ValueError("pilot requires both --pilot-wall-seconds and --pilot-native-seconds")
        if not any(part.lower().startswith("pilot") for part in args.output_root.parts):
            raise ValueError("pilot override requires a separate pilot output directory")
        if view != "g0340" or not (0 < args.pilot_native_seconds < args.pilot_wall_seconds < 900):
            raise ValueError("pilot override requires g0340 and 0 < native < total wall < 900")
        wall_limit = args.pilot_wall_seconds
        native_limit = args.pilot_native_seconds
    else:
        wall_limit = float(protocol["budget"]["wall_seconds_per_position"])
        if wall_limit != 900.0:
            raise ValueError("formal official position requires exactly 900 s total wall")
        native_limit = 895.0

    source_commit = subprocess.check_output(
        ["git", "-C", str(source_path), "rev-parse", "HEAD"], text=True).strip()
    source_hash = git_archive_sha256(source_path)
    binary_hash = sha256(binary_path)
    if source_commit != SOURCE_COMMIT or source_hash != SOURCE_SHA256 or binary_hash != BINARY_SHA256:
        raise ValueError("official StableSolver commit, source archive, or binary SHA256 mismatch")
    protocol_hash = sha256(protocol_path)
    runner_hash = sha256(Path(__file__).resolve())
    os.sched_setaffinity(0, {args.cpu})
    affinity = sorted(os.sched_getaffinity(0))
    if affinity != [args.cpu]:
        raise RuntimeError("single-CPU affinity was not applied")
    env = os.environ.copy()
    env.update({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                "VECLIB_MAXIMUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"})

    output_root = args.output_root.resolve()
    stem = f"{args.graph_id}__s{args.seed:04d}"
    result_path = output_root / "results" / args.arm / (stem + ".json")
    native_path = output_root / "native" / args.arm / (stem + ".json")
    certificate_path = output_root / "certificates" / args.arm / (stem + ".sol")
    trace_path = output_root / "traces" / args.arm / (stem + ".csv")
    log_path = output_root / "logs" / args.arm / (stem + ".log")
    lock_path = output_root / "locks" / args.arm / (stem + ".lock")
    paths = (result_path, native_path, certificate_path, trace_path, log_path, lock_path)
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError("refusing to overwrite existing position artifact: " + str(path))
    with lock_path.open("x", encoding="utf-8") as stream:
        stream.write(f"arm={args.arm} graph_id={args.graph_id} seed=0 pid={os.getpid()}\n")

    start = time.monotonic()
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    status = "error"
    error = None
    selected = None
    value_ticks = None
    native = None
    trajectory = None
    command = None
    return_code = None
    child_wall = None
    first_observed = None
    graph_load_offset = None
    alignment_method = None
    peak_rss = 0
    try:
        for path, expected in ((npz_path, protocol["graph_sha256"][args.graph_id]),
                               (dimacs_path, record["dimacs_sha256"]),
                               (metis_path, record["metis_sha256"])):
            if sha256(path) != expected:
                raise ValueError("frozen graph SHA256 mismatch: " + str(path))
        if time.monotonic() - start >= wall_limit - 0.5:
            raise TimeoutError("graph input hashing exhausted wall budget")

        command = [str(binary_path), "--input", str(dimacs_path), "--format", "dimacs1992",
                   "--algorithm", ARMS[args.arm], "--seed", "0", "--certificate", str(certificate_path),
                   "--output", str(native_path), "--verbosity-level", "0"]
        if args.arm != "stablesolver_greedy_gwmin":
            command.extend(["--time-limit", f"{native_limit:g}"])
        child_start = time.monotonic()
        with log_path.open("x", encoding="utf-8") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
            timed_out = False
            while process.poll() is None:
                now = time.monotonic()
                if first_observed is None and native_path.exists():
                    first_observed = now
                peak_rss = max(peak_rss, proc_peak_bytes(process.pid))
                if now >= start + wall_limit - 0.5:
                    timed_out = True
                    process.terminate()
                    try:
                        process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    break
                time.sleep(POLL_SECONDS)
            return_code = process.wait()
        child_wall = time.monotonic() - child_start
        if first_observed is None and native_path.exists():
            first_observed = time.monotonic()
        if timed_out:
            raise TimeoutError("native process exceeded reserved total wall deadline")
        if return_code != 0:
            raise RuntimeError(f"official StableSolver exited with code {return_code}")
        native = json.loads(native_path.read_text(encoding="utf-8"))
        selected = certificate_vertices(certificate_path)
        value_ticks = exact_audit(selected, native, npz_path, record)
        points = trajectory_points(native, value_ticks)
        native_final_time = numeric_time(native["Output"].get("Time"), "native final Time")
        # StableSolver can create its JSON before the first listed improvement,
        # so the first file observation is not a reliable search-clock marker.
        # The process wall duration minus final native Time bounds startup and
        # graph loading from above (it also includes final output serialization).
        if child_wall + 0.02 < native_final_time:
            raise ValueError("native final Time exceeds measured native process wall")
        graph_load_offset = max(0.0, child_wall - native_final_time)
        alignment_method = "native_process_wall_minus_final_native_time_upper_bound"
        child_start_elapsed = child_start - start
        trajectory = [(child_start_elapsed + graph_load_offset + t, t, value)
                      for t, value in points]
        if time.monotonic() - start > wall_limit:
            raise TimeoutError("position exceeded total wall budget including NPZ certificate audit")
        status = "ok"
    except Exception as exc:
        error = type(exc).__name__ + ": " + str(exc)

    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    wall_seconds = time.monotonic() - start
    cpu_seconds = ((usage_after.ru_utime + usage_after.ru_stime)
                   - (usage_before.ru_utime + usage_before.ru_stime))
    if peak_rss <= 0 and command is not None:
        peak_rss = int(usage_after.ru_maxrss) * 1024
    if trajectory is not None:
        with trace_path.open("x", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("elapsed_seconds", "native_time_seconds", "value_ticks"))
            writer.writerows((f"{elapsed:.9f}", f"{native_time:.9f}", value)
                             for elapsed, native_time, value in trajectory)
    result = {
        "schema": "classical_wmis_position_v1",
        "arm": args.arm, "graph_id": args.graph_id, "seed": args.seed,
        "protocol_sha256": protocol_hash, "runner_sha256": runner_hash,
        "input_sha256": protocol["graph_sha256"][args.graph_id],
        "dimacs_sha256": record["dimacs_sha256"], "metis_sha256": record["metis_sha256"],
        "source_sha256": source_hash, "binary_sha256": binary_hash,
        "config": {"algorithm": ARMS[args.arm], "native_mode": ARMS[args.arm],
                   "native_threads": 1, "native_seed": 0,
                   "native_time_limit_seconds": None if args.arm == "stablesolver_greedy_gwmin" else native_limit,
                   "input_format": "dimacs1992", "weight_unit": "integer contact microseconds (ticks)",
                   "method_source": "official fontanf/stablesolver at " + SOURCE_COMMIT,
                   "trajectory_alignment_method": alignment_method,
                   "trajectory_offset_is_observation_upper_bound": True},
        "cpu_id": args.cpu, "cpu_affinity": affinity,
        "wall_limit_seconds": wall_limit, "wall_seconds": wall_seconds,
        "cpu_seconds": cpu_seconds, "peak_rss_bytes": peak_rss,
        "status": status, "selected_zero_based": selected, "value_ticks": value_ticks,
        "trajectory_path": str(trace_path.relative_to(output_root)) if trace_path.exists() else None,
        "native_path": str(native_path.relative_to(output_root)) if native_path.exists() else None,
        "certificate_path": str(certificate_path.relative_to(output_root)) if certificate_path.exists() else None,
        "log_path": str(log_path.relative_to(output_root)) if log_path.exists() else None,
        "native_meta": {"Output": native.get("Output"), "Parameters": native.get("Parameters")}
                       if isinstance(native, dict) else None,
        "native_return_code": return_code, "native_process_wall_seconds": child_wall,
        "native_first_output_observed_seconds": first_observed - start if first_observed else None,
        "native_graph_load_offset_seconds": graph_load_offset,
        "native_time_origin": "after DIMACS graph load, inside official solver",
        "trajectory_alignment_method": alignment_method,
        "command": command, "error": error,
    }
    save_json_new(result_path, result)
    print(json.dumps({"result": str(result_path), "status": status,
                      "value_ticks": value_ticks, "wall_seconds": wall_seconds}, separators=(",", ":")))
    if status != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
