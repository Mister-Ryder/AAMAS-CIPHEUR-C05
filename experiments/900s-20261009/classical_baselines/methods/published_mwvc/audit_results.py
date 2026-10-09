#!/usr/bin/env python3
"""Independently audit saved certificates against the eight source NPZ graphs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_position(root: Path, result_root: Path, family: str, receipt_path: Path,
                   protocol: dict, protocol_hash: str, manifest: dict,
                   build_hash: str, build: dict) -> dict:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    arm, graph_id, seed = receipt["arm"], receipt["graph_id"], receipt["seed"]
    if arm not in protocol["arms"] or graph_id not in protocol["source_graph_sha256"] or seed not in protocol["seeds"]:
        raise ValueError("receipt outside registered position frame")
    stem = f"{graph_id}__s{seed:04d}"
    expected_path = result_root / family / "results" / arm / (stem + ".json")
    if receipt_path != expected_path or receipt.get("schema") != "published_mwvc_position_v1":
        raise ValueError("receipt name or schema mismatch")
    wanted_status = "pilot_ok" if family == "pilot" else "ok"
    if receipt.get("status") != wanted_status or receipt.get("error") is not None:
        raise ValueError("position did not finish validly")
    if receipt.get("protocol_sha256") != protocol_hash or receipt.get("build_manifest_sha256") != build_hash:
        raise ValueError("registration or build hash mismatch")
    if receipt.get("binary_sha256") != build["arms"][arm]["binary_sha256"]:
        raise ValueError("receipt binary hash mismatch")
    if receipt.get("runner_sha256") != sha256(root / "run_position.py"):
        raise ValueError("runner hash mismatch")
    if receipt.get("upstream_commit") != protocol["source_git"][arm]["commit"]:
        raise ValueError("upstream commit mismatch")
    if receipt.get("native_threads") != 1 or receipt.get("cpu_affinity") != [receipt.get("cpu_id")]:
        raise ValueError("CPU affinity or thread count mismatch")
    if receipt.get("address_space_limit_bytes") != 3221225472:
        raise ValueError("position memory limit mismatch")
    if not isinstance(receipt.get("parent_peak_rss_bytes"), int) or receipt["parent_peak_rss_bytes"] <= 0:
        raise ValueError("parent peak RSS missing")
    if not isinstance(receipt.get("sampled_tree_peak_rss_bytes"), int) or receipt["sampled_tree_peak_rss_bytes"] <= 0:
        raise ValueError("sampled process-tree RSS peak missing")
    wall_limit = receipt.get("wall_limit_seconds")
    if family == "measured" and wall_limit != 900:
        raise ValueError("measured wall limit differs from 900 seconds")
    if type(receipt.get("wall_seconds")) not in (float, int) or receipt["wall_seconds"] > wall_limit + 0.1:
        raise ValueError("wall cap exceeded")
    preempted = receipt.get("outer_preempted") is True
    if preempted:
        if (arm != "fj" or receipt.get("native_return_code") != 124 or
                receipt.get("wall_seconds", 0) < receipt.get("wall_limit_seconds", 0) - 5):
            raise ValueError("invalid FJ near-deadline preemption record")
    elif receipt.get("native_return_code") != 0:
        raise ValueError("native solver did not exit normally")
    row = next((r for r in manifest["graphs"] if r["graph_id"] == graph_id), None)
    if row is None:
        raise ValueError("source graph absent from manifest")
    graph = root / "inputs" / "graphs" / row["metis_name"]
    source = root / "inputs" / "data" / "CP-SCALE-AU-L002" / row["input_name"]
    if sha256(graph) != row["metis_sha256"] or sha256(source) != row["input_sha256"]:
        raise ValueError("source or METIS hash mismatch")
    if receipt.get("metis_sha256") != row["metis_sha256"] or receipt.get("input_sha256") != row["input_sha256"]:
        raise ValueError("receipt input hash mismatch")
    runtime_rel = receipt.get("runtime_graph_relative_path")
    if not runtime_rel or receipt.get("converter_sha256") != sha256(root / "convert_npz_to_metis.py"):
        raise ValueError("per-position conversion provenance missing")
    runtime_graph = result_root / runtime_rel
    if sha256(runtime_graph) != row["metis_sha256"] or receipt.get("runtime_graph_sha256") != row["metis_sha256"]:
        raise ValueError("per-position METIS graph differs from frozen exact conversion")
    native_path = result_root / receipt["native_relative_path"]
    native = json.loads(native_path.read_text(encoding="utf-8"))
    if arm == "fj" and native.get("checkpoint_final") is not (not preempted):
        raise ValueError("FJ checkpoint finality differs from native termination")
    selected = native.get("selected")
    if native.get("schema") != "published_mwvc_native_v1" or native.get("solver") != protocol["arms"][arm] or native.get("seed") != seed:
        raise ValueError("native method, schema or seed mismatch")
    if not isinstance(selected, list) or any(type(v) is not int for v in selected):
        raise ValueError("native selected IDs are not integers")
    with np.load(source, allow_pickle=False) as data:
        weights = data["weight_ticks"]
        edge_u = data["edge_u"]
        edge_v = data["edge_v"]
    if len(weights) != row["n"] or len(edge_u) != row["m"]:
        raise ValueError("NPZ graph size mismatch")
    if len(selected) != len(set(selected)) or any(v < 0 or v >= len(weights) for v in selected):
        raise ValueError("native selected IDs duplicate or out of range")
    bits = np.zeros(len(weights), dtype=bool)
    bits[selected] = True
    if np.any(bits[edge_u] & bits[edge_v]):
        raise ValueError("independent-set certificate violates an NPZ edge")
    value = sum(int(weights[v]) for v in selected)
    total = sum(int(w) for w in weights)
    if total != protocol["graph_invariants"]["weight_sum_ticks_per_view"]:
        raise ValueError("NPZ total weight differs from registration")
    if native.get("value_ticks") != value or native.get("cover_weight_ticks") != total - value or native.get("total_weight_ticks") != total:
        raise ValueError("native exact objective or complementary cover weight mismatch")
    if receipt.get("selected_zero_based") != selected or receipt.get("value_ticks") != value:
        raise ValueError("receipt differs from native certificate")
    if receipt.get("native_meta") != {k: v for k, v in native.items() if k != "selected"}:
        raise ValueError("receipt native metadata differs")
    return {
        "arm": arm, "graph_id": graph_id, "seed": seed, "value_ticks": value,
        "contact_seconds": str(Decimal(value) / Decimal(1000000)),
        "wall_seconds": receipt["wall_seconds"], "cpu_seconds": receipt["cpu_seconds"],
        "peak_rss_bytes": receipt["peak_rss_bytes"],
        "parent_peak_rss_bytes": receipt["parent_peak_rss_bytes"],
        "sampled_tree_peak_rss_bytes": receipt["sampled_tree_peak_rss_bytes"],
        "receipt_sha256": sha256(receipt_path), "native_sha256": sha256(native_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--result-root", type=Path)
    parser.add_argument("--build-manifest", type=Path)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--out-dir", type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    result_root = (args.result_root or root).resolve(strict=True)
    family = "pilot" if args.pilot else "measured"
    out_dir = (args.out_dir or (root / "local_pilot" / "analysis" if args.pilot else root / "analysis")).resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError("refusing to overwrite nonempty audit output")
    out_dir.mkdir(parents=True, exist_ok=True)
    registration = root / "preregistration.json"
    protocol = json.loads(registration.read_text(encoding="utf-8"))
    for filename, wanted in protocol["harness_sha256"].items():
        if sha256(root / filename) != wanted:
            raise ValueError("frozen harness file changed: " + filename)
    graph_manifest_path = root / "inputs" / "graphs" / "metis_manifest.json"
    graph_manifest = json.loads(graph_manifest_path.read_text(encoding="utf-8"))
    if sha256(graph_manifest_path) != protocol["graph_manifest_sha256"]:
        raise ValueError("graph manifest changed")
    build_path = (args.build_manifest or root / "build_manifest.json").resolve(strict=True)
    build = json.loads(build_path.read_text(encoding="utf-8"))
    if build["protocol_sha256"] != sha256(registration):
        raise ValueError("build manifest differs from frozen protocol")
    receipts = sorted((result_root / family / "results").glob("*/*.json"))
    frame = {(arm, graph_id, seed) for arm in protocol["arms"]
             for graph_id in protocol["source_graph_sha256"] for seed in protocol["seeds"]}
    seen = set()
    rows = []
    failures = []
    for receipt_path in receipts:
        try:
            row = check_position(root, result_root, family, receipt_path, protocol,
                                 sha256(registration), graph_manifest, sha256(build_path), build)
            key = (row["arm"], row["graph_id"], row["seed"])
            if key in seen:
                raise ValueError("duplicate position")
            seen.add(key)
            rows.append(row)
        except Exception as exc:
            failures.append({"path": str(receipt_path), "error": type(exc).__name__ + ": " + str(exc)})
    if not args.pilot:
        for arm, graph_id, seed in sorted(frame - seen):
            failures.append({"arm": arm, "graph_id": graph_id, "seed": seed, "error": "missing valid position"})
        if len(receipts) != protocol["expected_measured_positions_total"]:
            failures.append({"error": "receipt count differs from frozen position frame"})
    else:
        if not rows:
            failures.append({"error": "no valid pilot receipts"})
    summary = {"schema": "published_mwis_independent_audit_v2", "family": family,
               "protocol_sha256": sha256(registration), "build_manifest_sha256": sha256(build_path),
               "observed_receipts": len(receipts), "valid_positions": len(rows), "failures": failures,
               "rows": rows}
    by_arm_view = defaultdict(list)
    for row in rows:
        by_arm_view[(row["arm"], row["graph_id"])].append(row["value_ticks"])
    overall = []
    if not args.pilot and not failures:
        for arm in protocol["arms"]:
            means = []
            for graph_id in protocol["source_graph_sha256"]:
                values = by_arm_view[(arm, graph_id)]
                if len(values) != len(protocol["seeds"]):
                    raise ValueError("per-view seed count differs")
                means.append(Decimal(sum(values)) / Decimal(len(values)))
            mean_ticks = sum(means) / Decimal(len(means))
            overall.append({"arm": arm, "valid_positions": 40,
                            "mean_contact_seconds": str(mean_ticks / Decimal(1000000))})
    summary["overall"] = overall
    (out_dir / "audit.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with (out_dir / "positions.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["arm", "graph_id", "seed", "value_ticks", "contact_seconds",
                                                       "wall_seconds", "cpu_seconds", "peak_rss_bytes", "parent_peak_rss_bytes", "sampled_tree_peak_rss_bytes", "receipt_sha256", "native_sha256"])
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"valid_positions": len(rows), "failed_checks": len(failures), "overall": overall,
                      "audit": str(out_dir / "audit.json")}))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
