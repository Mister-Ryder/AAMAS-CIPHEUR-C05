#!/usr/bin/env python3
"""Independent exact-graph and result audit for the classical MWIS 900 s frame.

This script never imports a converter, native solver, or optimizer graph class.
It reads source NPZ arrays and both frozen text graph formats directly.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def graph_arrays(path: Path, expected_sha256: str):
    if sha256(path) != expected_sha256:
        raise ValueError(f"Source NPZ SHA256 mismatch: {path}")
    with np.load(path, allow_pickle=False) as data:
        w = data["weight_ticks"].astype(np.int64, copy=True)
        u = data["edge_u"].astype(np.int64, copy=True)
        v = data["edge_v"].astype(np.int64, copy=True)
    n, m = len(w), len(u)
    if n == 0 or len(v) != m or np.any(w <= 0):
        raise ValueError(f"Invalid source NPZ graph: {path}")
    if np.any(u < 0) or np.any(v >= n) or np.any(u >= v):
        raise ValueError(f"Source NPZ edge range/orientation mismatch: {path}")
    pairs = np.column_stack((u, v))
    order = np.lexsort((pairs[:, 1], pairs[:, 0]))
    sorted_pairs = pairs[order]
    if m > 1 and np.any(np.all(sorted_pairs[1:] == sorted_pairs[:-1], axis=1)):
        raise ValueError(f"Source NPZ contains duplicate edges: {path}")
    return w, u, v, sorted_pairs


def expected_neighbors(n: int, u: np.ndarray, v: np.ndarray):
    source = np.concatenate((u, v))
    neighbor = np.concatenate((v, u))
    key = source * np.int64(n) + neighbor
    order = np.argsort(key)
    counts = np.bincount(source, minlength=n)
    offsets = np.concatenate(([0], np.cumsum(counts, dtype=np.int64)))
    return neighbor[order], offsets


def verify_metis(path: Path, weights: np.ndarray, u: np.ndarray, v: np.ndarray):
    n, m = len(weights), len(u)
    neighbors, offsets = expected_neighbors(n, u, v)
    with path.open("r", encoding="ascii") as stream:
        if stream.readline().split() != [str(n), str(m), "10"]:
            raise ValueError(f"METIS header mismatch: {path}")
        for i in range(n):
            raw = stream.readline()
            if not raw:
                raise ValueError(f"METIS ended before vertex {i}: {path}")
            fields = raw.split()
            if not fields or int(fields[0]) != int(weights[i]):
                raise ValueError(f"METIS vertex weight mismatch at {i}: {path}")
            observed = np.fromiter((int(v) - 1 for v in fields[1:]), dtype=np.int64)
            expected = neighbors[int(offsets[i]):int(offsets[i + 1])]
            if not np.array_equal(observed, expected):
                raise ValueError(f"METIS adjacency mismatch at {i}: {path}")
        if stream.readline() != "":
            raise ValueError(f"METIS contains extra records: {path}")


def verify_dimacs(path: Path, weights: np.ndarray, pairs: np.ndarray):
    n, m = len(weights), len(pairs)
    with path.open("r", encoding="ascii") as stream:
        if stream.readline().split() != ["p", "edge", str(n), str(m)]:
            raise ValueError(f"DIMACS header mismatch: {path}")
        for i in range(n):
            fields = stream.readline().split()
            if fields != ["n", str(i + 1), str(int(weights[i]))]:
                raise ValueError(f"DIMACS vertex record mismatch at {i}: {path}")
        for i, (u, v) in enumerate(pairs):
            fields = stream.readline().split()
            if fields != ["e", str(int(u) + 1), str(int(v) + 1)]:
                raise ValueError(f"DIMACS edge record mismatch at {i}: {path}")
        if stream.readline() != "":
            raise ValueError(f"DIMACS contains extra records: {path}")


def verify_inputs(protocol: dict, protocol_path: Path, data_dir: Path, graph_dir: Path):
    manifest_path = graph_dir / "metis_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "cipheur_classical_metis_manifest_v1":
        raise ValueError("Wrong graph conversion manifest schema")
    protocol_match = manifest.get("protocol_sha256") == sha256(protocol_path)
    if not protocol_match and protocol.get("status") != "draft_requires_source_and_overflow_gates_before_launch":
        raise ValueError("Graph conversion manifest protocol SHA256 mismatch")
    if manifest.get("converter_sha256") != protocol["representation"].get("converter_sha256"):
        raise ValueError("Graph converter source hash differs from registration")
    records = manifest.get("graphs")
    if not isinstance(records, list) or len(records) != len(protocol["graph_sha256"]):
        raise ValueError("Graph conversion manifest does not cover all registered views")
    by_id = {row.get("graph_id"): row for row in records}
    if len(by_id) != len(records) or set(by_id) != set(protocol["graph_sha256"]):
        raise ValueError("Duplicate/missing graph conversion manifest records")
    arrays = {}
    graph_rows = []
    for graph_id, expected_hash in protocol["graph_sha256"].items():
        name = graph_id.split("__", 1)[1]
        record = by_id[graph_id]
        npz = data_dir / f"{name}.npz"
        weights, u, v, pairs = graph_arrays(npz, expected_hash)
        if record.get("input_sha256") != expected_hash or record.get("n") != len(weights) or record.get("m") != len(u):
            raise ValueError(f"Graph manifest source identity/shape mismatch: {graph_id}")
        if record.get("weight_sum_ticks") != sum(int(x) for x in weights):
            raise ValueError(f"Graph manifest weight sum mismatch: {graph_id}")
        for fmt in ("metis", "dimacs"):
            filename = record.get(f"{fmt}_name")
            expected_file_hash = record.get(f"{fmt}_sha256")
            file = graph_dir / filename if isinstance(filename, str) else None
            if file is None or file.name != filename or not file.is_file():
                raise ValueError(f"Missing/unsafe {fmt} file: {graph_id}")
            if sha256(file) != expected_file_hash or file.stat().st_size != record.get(f"{fmt}_bytes"):
                raise ValueError(f"{fmt} bytes differ from conversion manifest: {graph_id}")
            if fmt == "metis":
                verify_metis(file, weights, u, v)
            else:
                verify_dimacs(file, weights, pairs)
        arrays[graph_id] = (weights, u, v)
        graph_rows.append({"graph_id": graph_id, "n": len(weights), "m": len(u),
                           "npz_sha256": expected_hash, "metis_sha256": record["metis_sha256"],
                           "dimacs_sha256": record["dimacs_sha256"], "exactly_equivalent": True})
    return arrays, by_id, graph_rows, protocol_match


def expected_positions(protocol: dict):
    arms = list(protocol["planned_arms"]) + list(protocol.get("supplementary_arms", []))
    deterministic = {a for a in arms if a.startswith("stablesolver_")}
    return {(arm, graph_id, seed)
            for arm in arms for graph_id in protocol["graph_sha256"]
            for seed in ([0] if arm in deterministic else protocol["seeds"])}


def check_result(row: dict, arm: str, graph_id: str, seed: int, arrays, graph_record: dict, protocol: dict):
    errors = []
    weights, edge_u, edge_v = arrays
    if row.get("arm") != arm or row.get("graph_id") != graph_id or row.get("seed") != seed:
        errors.append("identity_mismatch")
    if row.get("status") != "ok":
        errors.append("status_not_ok")
    if row.get("input_sha256") != protocol["graph_sha256"][graph_id]:
        errors.append("input_hash_mismatch")
    if row.get("dimacs_sha256") != graph_record["dimacs_sha256"]:
        errors.append("dimacs_hash_mismatch")
    if row.get("metis_sha256") not in (None, graph_record["metis_sha256"]):
        errors.append("metis_hash_mismatch")
    registered = protocol["method_identity"][arm]
    for name in ("source_sha256", "binary_sha256"):
        digest = row.get(name)
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            errors.append(name + "_missing_or_malformed")
        elif registered.get(name) is not None and digest != registered[name]:
            errors.append(name + "_registration_mismatch")
    if registered.get("runner_sha256") is not None and row.get("runner_sha256") != registered["runner_sha256"]:
        errors.append("runner_hash_registration_mismatch")
    if row.get("protocol_sha256") != protocol["_file_sha256"]:
        errors.append("protocol_hash_registration_mismatch")
    config = row.get("config")
    if not isinstance(config, dict):
        errors.append("config_missing")
    else:
        recorded_algorithm = config.get("algorithm", config.get("mode"))
        if recorded_algorithm != registered.get("algorithm_flag"):
            errors.append("algorithm_flag_mismatch")
        native_threads = config.get("native_threads", config.get("threads", 1))
        if native_threads != 1:
            errors.append("native_thread_count_mismatch")
    max_wall = (protocol["budget"]["wall_seconds_per_position"] +
                protocol["budget"]["independent_audit_wall_tolerance_seconds"])
    if not isinstance(row.get("wall_seconds"), (int, float)) or not 0 < row["wall_seconds"] <= max_wall:
        errors.append("wall_budget_invalid")
    if not isinstance(row.get("cpu_seconds"), (int, float)) or row["cpu_seconds"] < 0:
        errors.append("cpu_seconds_invalid")
    affinity = row.get("cpu_affinity")
    if not isinstance(affinity, list) or len(affinity) != 1 or affinity[0] != row.get("cpu_id"):
        errors.append("single_cpu_affinity_invalid")
    if type(row.get("peak_rss_bytes")) is not int or row["peak_rss_bytes"] < 0:
        errors.append("peak_rss_invalid")
    elif row["peak_rss_bytes"] > protocol["budget"]["address_space_limit_bytes_per_position"]:
        errors.append("memory_cap_exceeded")
    trajectory = row.get("trajectory_path")
    if trajectory is not None and (not isinstance(trajectory, str) or
                                   Path(trajectory).is_absolute() or ".." in Path(trajectory).parts):
        errors.append("unsafe_trajectory_path")
    selected = row.get("selected_zero_based")
    recomputed = None
    if not isinstance(selected, list) or any(type(v) is not int for v in selected):
        errors.append("selected_missing_or_noninteger")
    elif len(selected) != len(set(selected)):
        errors.append("duplicate_selected_vertex")
    elif any(v < 0 or v >= len(weights) for v in selected):
        errors.append("selected_vertex_out_of_range")
    else:
        chosen = np.zeros(len(weights), dtype=np.bool_)
        chosen[selected] = True
        if bool(np.any(chosen[edge_u] & chosen[edge_v])):
            errors.append("selected_vertices_conflict")
        recomputed = sum(int(weights[v]) for v in selected)
        if type(row.get("value_ticks")) is not int or recomputed != row["value_ticks"]:
            errors.append("integer_objective_mismatch")
    return errors, recomputed


def audit_results(protocol: dict, arrays: dict, records: dict, root: Path, allow_incomplete: bool):
    expected = expected_positions(protocol)
    seen = {}
    invalid = []
    extras = []
    positions = []
    for arm in list(protocol["planned_arms"]) + list(protocol.get("supplementary_arms", [])):
        for path in sorted((root / "results" / arm).glob("*.json")):
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                invalid.append({"path": str(path), "errors": [f"parse_error: {exc}"]})
                continue
            identity = (arm, result.get("graph_id"), result.get("seed"))
            if identity not in expected or identity in seen:
                extras.append({"path": str(path), "identity": list(identity)})
                continue
            seen[identity] = path
            errors, recomputed = check_result(result, arm, identity[1], identity[2],
                                              arrays[identity[1]], records[identity[1]], protocol)
            trajectory = result.get("trajectory_path")
            if isinstance(trajectory, str) and not Path(trajectory).is_absolute() and ".." not in Path(trajectory).parts:
                if not (root / trajectory).is_file():
                    errors.append("trajectory_file_missing")
            line = {"arm": arm, "graph_id": identity[1], "seed": identity[2],
                    "audited_ok": not errors, "value_ticks": recomputed,
                    "contact_seconds": format(Decimal(recomputed) / 1000000, "f") if recomputed is not None else None,
                    "wall_seconds": result.get("wall_seconds"), "cpu_seconds": result.get("cpu_seconds"),
                    "path": str(path), "errors": ";".join(errors)}
            positions.append(line)
            if errors:
                invalid.append({"path": str(path), "errors": errors})
    missing = [list(p) for p in sorted(expected - set(seen))]
    if not allow_incomplete and (missing or extras or invalid):
        status = "invalid"
    elif not missing and not extras and not invalid:
        status = "complete_valid"
    else:
        status = "incomplete_or_invalid"
    summaries = []
    by_view = []
    for arm in list(protocol["planned_arms"]) + list(protocol.get("supplementary_arms", [])):
        valid = [p for p in positions if p["arm"] == arm and p["audited_ok"]]
        n_expected = protocol["positions_per_arm"][arm]
        total = sum(p["value_ticks"] for p in valid)
        mean = format(Decimal(total) / Decimal(len(valid) * 1000000), "f") if valid else None
        summaries.append({"arm": arm, "measured_positions": len(valid), "expected_positions": n_expected,
                          "complete": len(valid) == n_expected,
                          "mean_contact_seconds": mean if len(valid) == n_expected else None,
                          "mean_measured_partial_contact_seconds": mean if len(valid) != n_expected else None,
                          "seed_independent_reference": arm.startswith("stablesolver_"),
                          "supplementary": arm in protocol.get("supplementary_arms", [])})
        for graph_id in protocol["graph_sha256"]:
            group = [p for p in valid if p["graph_id"] == graph_id]
            expected_group = 1 if arm.startswith("stablesolver_") else len(protocol["seeds"])
            by_view.append({"arm": arm, "graph_id": graph_id,
                            "valid_positions": len(group), "expected_positions": expected_group,
                            "mean_contact_seconds": format(Decimal(sum(p["value_ticks"] for p in group)) /
                                                           Decimal(len(group) * 1000000), "f")
                            if len(group) == expected_group else None})
    return {"status": status, "expected_positions": len(expected),
            "saved_positions": len(seen), "audited_valid_positions": sum(p["audited_ok"] for p in positions),
            "missing": missing, "extra_or_duplicate": extras, "invalid": invalid,
            "method_summaries": summaries, "by_view": by_view}, positions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--graph-dir", required=True, type=Path)
    parser.add_argument("--results-root", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    protocol_path = args.protocol.resolve(strict=True)
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    protocol["_file_sha256"] = sha256(protocol_path)
    arrays, records, graph_rows, protocol_match = verify_inputs(
        protocol, protocol_path, args.data_dir.resolve(strict=True), args.graph_dir.resolve(strict=True))
    result = {"schema": "cipheur_classical_900s_independent_audit_v1",
              "generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "protocol_sha256": sha256(protocol_path), "conversion_manifest_protocol_hash_matches": protocol_match,
              "all_graphs_exactly_equivalent": all(x["exactly_equivalent"] for x in graph_rows),
              "graphs": graph_rows}
    positions = []
    if args.results_root:
        result_audit, positions = audit_results(protocol, arrays, records,
                                                args.results_root.resolve(), args.allow_incomplete)
        result.update(result_audit)
    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / "classical_900s_independent_audit.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        if positions:
            with (args.out_dir / "classical_900s_positions.csv").open("w", encoding="utf-8-sig", newline="") as out:
                writer = csv.DictWriter(out, fieldnames=list(positions[0]))
                writer.writeheader()
                writer.writerows(positions)
        if result.get("by_view"):
            with (args.out_dir / "classical_900s_by_view.csv").open("w", encoding="utf-8-sig", newline="") as out:
                writer = csv.DictWriter(out, fieldnames=list(result["by_view"][0]))
                writer.writeheader()
                writer.writerows(result["by_view"])
    print(json.dumps({k: result.get(k) for k in ("all_graphs_exactly_equivalent", "conversion_manifest_protocol_hash_matches",
                                                "status", "expected_positions", "saved_positions", "audited_valid_positions")},
                     ensure_ascii=False, sort_keys=True))
    if result.get("status") == "invalid" or not result["all_graphs_exactly_equivalent"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
