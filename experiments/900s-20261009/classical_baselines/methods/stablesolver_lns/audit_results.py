#!/usr/bin/env python3
"""Independent exact original-NPZ audit of fresh StableSolver LNS certificates."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path

import numpy as np


ARM = "stablesolver_large_neighborhood_search"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def contained_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError("artifact path escapes output root")
    return path


def trace_audit(path: Path, final_value: int, wall_seconds: float) -> tuple[int, float]:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or set(rows[0]) != {"elapsed_seconds", "native_time_seconds", "value_ticks"}:
        raise ValueError("missing or malformed strict-improvement trajectory")
    previous_elapsed = -1.0
    previous_native = -1.0
    previous_value = -1
    for row in rows:
        elapsed = float(row["elapsed_seconds"])
        native = float(row["native_time_seconds"])
        value = int(row["value_ticks"])
        if (not math.isfinite(elapsed) or not math.isfinite(native) or
                elapsed < previous_elapsed or native < previous_native or
                elapsed < native or elapsed > wall_seconds + 0.1):
            raise ValueError("trajectory clock is inconsistent with measured wall time")
        if value <= previous_value:
            raise ValueError("trajectory contains a non-improving point")
        previous_elapsed, previous_native, previous_value = elapsed, native, value
    if previous_value != final_value:
        raise ValueError("last trajectory value differs from exact final certificate")
    return len(rows), previous_elapsed


def audit_one(path: Path, root: Path, output_root: Path, data_dir: Path, registration: dict,
              reference: dict, record_by_id: dict, pilot: bool) -> dict:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    graph_id = receipt["graph_id"]
    repeat = receipt["repeat"]
    if graph_id not in record_by_id or repeat not in registration["repeats"]:
        raise ValueError("unregistered graph/repeat position: " + path.name)
    record = record_by_id[graph_id]
    expected_stem = f"{graph_id}__s0000__r{repeat:02d}.json"
    if path.name != expected_stem:
        raise ValueError("receipt filename does not identify its registered position")
    if (receipt["arm"] != ARM or receipt["seed"] != 0 or receipt["status"] != "ok" or
            receipt["pilot"] is not pilot or
            receipt["protocol_sha256"] != registration["reference_protocol_sha256"] or
            receipt["v2_protocol_sha256"] != sha256(root / "preregistration.json") or
            receipt["runner_sha256"] != registration["runner_sha256"] or
            receipt["converter_sha256"] != registration["converter_sha256"] or
            receipt["source_sha256"] != registration["official_source_archive_sha256"] or
            receipt["binary_sha256"] != registration["official_binary_sha256"] or
            receipt["input_sha256"] != reference["graph_sha256"][graph_id] or
            receipt["dimacs_sha256"] != record["dimacs_sha256"] or
            receipt["runtime_dimacs_sha256"] != record["dimacs_sha256"]):
        raise ValueError("result identity, status, or timed input hash mismatch: " + path.name)
    wall_limit = float(receipt["wall_limit_seconds"])
    wall = float(receipt["wall_seconds"])
    if pilot:
        if not (0 < wall_limit <= 60):
            raise ValueError("unexpected pilot wall cap")
    elif wall_limit != 900:
        raise ValueError("formal position lacks exact 900-second outer cap")
    if not (0 < wall <= wall_limit + 0.1):
        raise ValueError("position exceeded measured outer wall budget")
    if (receipt["address_space_limit_bytes_per_process"] != 3 * 1024 ** 3 or
            receipt["cpu_affinity"] != [receipt["cpu_id"]] or
            (not pilot and receipt["cpu_id"] not in registration["budget"]["cpu_ids"])):
        raise ValueError("CPU affinity or per-process memory limit differs from registration")
    if receipt["native_return_code"] != 0:
        raise ValueError("official solver did not exit successfully")
    config = receipt["config"]
    if (config["native_mode"] != "large-neighborhood-search" or config["native_seed"] != 0 or
            config["native_threads"] != 1 or config["input_format"] != "dimacs1992"):
        raise ValueError("official LNS mode or native seed differs")
    native_limit = float(config["native_time_limit_seconds"])
    if not (0 < native_limit <= registration["budget"]["native_time_limit_seconds"]):
        raise ValueError("native time budget exceeds registration")
    runtime_path = contained_path(output_root, receipt["runtime_dimacs_path"])
    if sha256(runtime_path) != record["dimacs_sha256"]:
        raise ValueError("fresh runtime DIMACS differs from frozen exact conversion")
    command = receipt["command"]
    if (not isinstance(command, list) or
            command[command.index("--input") + 1] != str(runtime_path) or
            command[command.index("--algorithm") + 1] != "large-neighborhood-search" or
            command[command.index("--seed") + 1] != "0" or
            abs(float(command[command.index("--time-limit") + 1]) - native_limit) > 1e-3):
        raise ValueError("actual native command differs from registered algorithm or runtime input")
    native_path = contained_path(output_root, receipt["native_path"])
    certificate_path = contained_path(output_root, receipt["certificate_path"])
    trace_path = contained_path(output_root, receipt["trajectory_path"])
    native = json.loads(native_path.read_text(encoding="utf-8"))
    selected = receipt["selected_zero_based"]
    if not isinstance(selected, list) or any(type(v) is not int for v in selected):
        raise ValueError("certificate vertex IDs must be integers")
    native_selected = [int(token) for token in certificate_path.read_text(encoding="ascii").split()]
    if native_selected != selected or len(set(selected)) != len(selected):
        raise ValueError("native certificate does not equal receipt or contains duplicates")
    npz_path = data_dir / record["input_name"]
    if sha256(npz_path) != record["input_sha256"]:
        raise ValueError("original NPZ bytes differ from registered input")
    with np.load(npz_path, allow_pickle=False) as data:
        weights = data["weight_ticks"]
        edge_u = data["edge_u"]
        edge_v = data["edge_v"]
    if (len(weights) != record["n"] or len(edge_u) != record["m"] or
            any(v < 0 or v >= len(weights) for v in selected)):
        raise ValueError("source NPZ shape or selected vertex index invalid")
    chosen = np.zeros(len(weights), dtype=np.bool_)
    chosen[selected] = True
    if bool(np.any(chosen[edge_u] & chosen[edge_v])):
        raise ValueError("selected set contains an original NPZ conflict")
    exact = sum(int(weights[v]) for v in selected)
    output = native["Output"]
    solution = output["Solution"]
    if (int(output["Value"]) != exact or solution["Feasible"] is not True or
            solution["NumberOfVertices"] != len(selected) or solution["Weight"] != exact or
            receipt["value_ticks"] != exact):
        raise ValueError("native and exact original-NPZ objective/certificate mismatch")
    trace_points, final_trace_time = trace_audit(trace_path, exact, wall)
    return {"graph_id": graph_id, "repeat": repeat, "native_seed_argument": 0,
            "value_ticks": exact, "wall_seconds": wall,
            "cpu_seconds": receipt["cpu_seconds"], "cpu_id": receipt["cpu_id"],
            "native_peak_rss_bytes": receipt["native_peak_rss_bytes"],
            "parent_peak_rss_bytes": receipt["parent_peak_rss_bytes"],
            "sampled_native_phase_tree_peak_rss_bytes": receipt["sampled_native_phase_tree_peak_rss_bytes"],
            "trace_points": trace_points, "last_trace_elapsed_seconds": final_trace_time,
            "receipt_sha256": sha256(path), "native_sha256": sha256(native_path),
            "certificate_sha256": sha256(certificate_path), "trace_sha256": sha256(trace_path),
            "fresh_dimacs_sha256": sha256(runtime_path)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--classical-root", type=Path, required=True)
    parser.add_argument("--pilot-output-root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    classical_root = args.classical_root.resolve(strict=True)
    output_root = args.pilot_output_root.resolve(strict=True) if args.pilot_output_root else root
    pilot = args.pilot_output_root is not None
    registration = json.loads((root / "preregistration.json").read_text(encoding="utf-8"))
    reference = json.loads((root / "reference_preregistration.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "metis_manifest.json").read_text(encoding="utf-8"))
    if registration["status"] != "frozen" or registration["arm"] != ARM:
        raise ValueError("LNS v3 registration is not frozen")
    registration_hash = sha256(root / "preregistration.json")
    if not pilot:
        progress_path = root / "batch_progress.json"
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        expected_cpus = registration["budget"]["cpu_ids"]
        if (progress.get("schema") != "official_stablesolver_lns_v3_batch_v1" or
                progress.get("status") != "complete" or
                progress.get("workers") != 5 or progress.get("cpus") != expected_cpus or
                progress.get("scheduled") != 40 or progress.get("launched") != 40 or
                progress.get("finished") != 40 or progress.get("failed") or
                progress.get("active") or
                progress.get("protocol_sha256") != registration_hash or
                progress.get("launcher_sha256") != registration["launcher_sha256"]):
            raise ValueError("five-worker formal batch progress or frozen launcher receipt differs")
        completed = progress.get("completed", [])
        if (len(completed) != 40 or
                any(row.get("return_code") != 0 or row.get("cpu") not in expected_cpus
                    for row in completed) or
                len({(row.get("graph_id"), row.get("repeat")) for row in completed}) != 40):
            raise ValueError("batch completion ledger does not contain forty unique successful positions")
    for filename, expected in (("reference_preregistration.json", registration["reference_protocol_sha256"]),
                               ("metis_manifest.json", registration["graph_manifest_sha256"]),
                               ("convert_npz_to_metis.py", registration["converter_sha256"]),
                               ("run_lns_position.py", registration["runner_sha256"]),
                               ("run_batch.py", registration["launcher_sha256"]),
                               ("audit_results.py", registration["audit_sha256"])):
        if sha256(root / filename) != expected:
            raise ValueError("frozen code/protocol changed: " + filename)
    record_by_id = {r["graph_id"]: r for r in manifest["graphs"]}
    if len(record_by_id) != 8:
        raise ValueError("manifest must contain eight unique views")
    result_files = sorted((output_root / "results" / ARM).glob("*.json"))
    expected_count = 1 if pilot else registration["expected_positions"]
    if len(result_files) != expected_count:
        raise ValueError(f"expected {expected_count} result receipts, found {len(result_files)}")
    data_dir = classical_root / "inputs" / "data" / "CP-SCALE-AU-L002"
    indexed = [audit_one(path, root, output_root, data_dir, registration, reference, record_by_id, pilot)
               for path in result_files]
    positions = {(r["graph_id"], r["repeat"]) for r in indexed}
    wanted = {(graph_id, repeat) for graph_id in reference["graph_sha256"] for repeat in registration["repeats"]}
    if len(positions) != len(indexed) or (not pilot and positions != wanted):
        raise ValueError("duplicate, omitted or unregistered graph/repeat frame")
    mean = Decimal(sum(r["value_ticks"] for r in indexed)) / Decimal(len(indexed) * 1_000_000)
    summary = {"schema": "stablesolver_lns_v3_independent_audit_v1",
               "status": "pilot_audited" if pilot else "audited_complete",
               "pilot": pilot, "valid_positions": len(indexed),
               "expected_positions": expected_count,
               "mean_contact_seconds_exact": str(mean),
               "protocol_sha256": registration_hash,
               "positions": indexed}
    destination = output_root / "independent_audit.json"
    if destination.exists():
        raise FileExistsError("refusing to overwrite independent audit")
    destination.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("status", "valid_positions", "mean_contact_seconds_exact")}))


if __name__ == "__main__":
    main()
