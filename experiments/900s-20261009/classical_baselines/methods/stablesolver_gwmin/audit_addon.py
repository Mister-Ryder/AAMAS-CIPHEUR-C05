#!/usr/bin/env python3
"""Independently audit 40 fresh 3 GiB GWMIN process repetitions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_receipt(receipt_path: Path, artifact_root: Path, data_path: Path,
                  graph_id: str, repeat: int, addon: dict, reference: dict,
                  manifest_row: dict, addon_hash: str) -> dict:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    required = {
        "status": "ok", "arm": addon["arm"], "graph_id": graph_id, "seed": 0,
        "protocol_sha256": addon["reference_protocol_sha256"],
        "addon_protocol_sha256": addon_hash,
        "binary_sha256": addon["binary_sha256"],
        "source_sha256": reference["method_identity"][addon["arm"]]["source_sha256"],
        "runner_sha256": addon["runner_sha256"],
        "converter_sha256": addon["timed_input_conversion"]["converter_sha256"],
        "input_sha256": reference["graph_sha256"][graph_id],
        "dimacs_sha256": manifest_row["dimacs_sha256"],
        "runtime_dimacs_sha256": manifest_row["dimacs_sha256"],
        "wall_limit_seconds": 900,
    }
    for key, value in required.items():
        if receipt.get(key) != value:
            raise ValueError("receipt field differs: " + key)
    if receipt.get("native_return_code") != 0:
        raise ValueError("native solver did not exit successfully")
    runtime_rel = receipt.get("runtime_dimacs_path")
    if not isinstance(runtime_rel, str):
        raise ValueError("timed runtime DIMACS path is missing")
    runtime_path = artifact_root / runtime_rel
    if (runtime_path.resolve().parent != (artifact_root / "inputs").resolve() or
            sha256(runtime_path) != manifest_row["dimacs_sha256"] or
            str(runtime_path) not in receipt.get("command", [])):
        raise ValueError("timed NPZ conversion differs or was not used by solver")
    if receipt.get("config", {}).get("native_time_limit_seconds") is not None:
        raise ValueError("native time limit differs")
    if receipt.get("config", {}).get("native_threads") != 1:
        raise ValueError("native thread count differs")
    if receipt.get("config", {}).get("native_seed") != 0:
        raise ValueError("native seed differs")
    command = receipt.get("command")
    if (not isinstance(command, list) or "--time-limit" in command or
            command[command.index("--algorithm") + 1] != "greedy-gwmin" or
            command[command.index("--seed") + 1] != "0"):
        raise ValueError("official greedy command differs")
    affinity = receipt.get("cpu_affinity")
    if affinity != [receipt.get("cpu_id")]:
        raise ValueError("single-CPU affinity missing")
    wall = receipt.get("wall_seconds")
    if type(wall) not in (int, float) or not 0 < wall <= 900.1:
        raise ValueError("wall time exceeds 900-second cap")
    if sha256(data_path) != reference["graph_sha256"][graph_id]:
        raise ValueError("source NPZ SHA256 differs")
    with np.load(data_path, allow_pickle=False) as source:
        weights = source["weight_ticks"]
        edge_u, edge_v = source["edge_u"], source["edge_v"]
    selected = receipt.get("selected_zero_based")
    if not isinstance(selected, list) or any(type(v) is not int for v in selected):
        raise ValueError("selected vertices are missing or noninteger")
    if len(selected) != len(set(selected)) or any(v < 0 or v >= len(weights) for v in selected):
        raise ValueError("selected vertices are duplicate or out of range")
    certificate_rel = receipt.get("certificate_path")
    if not isinstance(certificate_rel, str):
        raise ValueError("certificate path is missing")
    certificate = artifact_root / certificate_rel
    if certificate.resolve().parent != (artifact_root / "certificates" / addon["arm"]).resolve():
        raise ValueError("certificate path leaves expected artifact location")
    certificate_ids = [int(token) for token in certificate.read_text(encoding="ascii").split()]
    if certificate_ids != selected:
        raise ValueError("receipt does not match native certificate")
    chosen = np.zeros(len(weights), dtype=np.bool_)
    chosen[selected] = True
    if bool(np.any(chosen[edge_u] & chosen[edge_v])):
        raise ValueError("selected vertices contain a conflict")
    exact_value = sum(int(weights[v]) for v in selected)
    if exact_value != receipt.get("value_ticks"):
        raise ValueError("objective differs from original NPZ integer sum")
    trace_rel = receipt.get("trajectory_path")
    if not isinstance(trace_rel, str):
        raise ValueError("trajectory path is missing")
    trace = artifact_root / trace_rel
    if trace.resolve().parent != (artifact_root / "traces" / addon["arm"]).resolve():
        raise ValueError("trace path leaves expected artifact location")
    with trace.open(encoding="utf-8", newline="") as stream:
        points = list(csv.DictReader(stream))
    if not points:
        raise ValueError("search trajectory has no points")
    last_time, last_value = -1.0, -1
    for point in points:
        t, value = float(point["elapsed_seconds"]), int(point["value_ticks"])
        if t < last_time or t > wall + 0.1 or value <= last_value:
            raise ValueError("trajectory is not a timed strict-improvement record")
        last_time, last_value = t, value
    if last_value != exact_value:
        raise ValueError("trajectory final incumbent differs from audited result")
    return {
        "graph_id": graph_id, "repeat": repeat, "native_seed_argument": 0,
        "value_ticks": exact_value, "contact_seconds": exact_value / 1_000_000,
        "wall_seconds": wall, "cpu_seconds": receipt.get("cpu_seconds"),
        "cpu_id": receipt.get("cpu_id"), "peak_rss_bytes": receipt.get("peak_rss_bytes"),
        "strict_improvement_events": len(points), "receipt_sha256": sha256(receipt_path),
        "trace_sha256": sha256(trace), "certificate_sha256": sha256(certificate),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon-root", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    addon_root = args.addon_root.resolve(strict=True)
    reference_root = args.reference_root.resolve(strict=True)
    data_dir = args.data_dir.resolve(strict=True)
    addon = json.loads((addon_root / "addon_preregistration.json").read_text(encoding="utf-8"))
    reference = json.loads((addon_root / "reference_preregistration.json").read_text(encoding="utf-8"))
    if sha256(Path(__file__).resolve()) != addon["auditor_sha256"]:
        raise ValueError("add-on auditor source changed after registration freeze")
    if sha256(addon_root / "reference_preregistration.json") != addon["reference_protocol_sha256"]:
        raise ValueError("reference protocol snapshot SHA256 differs")
    if sha256(addon_root / "metis_manifest.json") != addon["reference_graph_manifest_sha256"]:
        raise ValueError("graph manifest snapshot SHA256 differs")
    if sha256(addon_root / "implementations/run_stablesolver.py") != addon["runner_sha256"]:
        raise ValueError("frozen runner snapshot SHA256 differs")
    if (addon["timed_input_conversion"]["clock_includes_conversion"] is not True or
            sha256(addon_root / "convert_npz_to_metis.py") !=
            addon["timed_input_conversion"]["converter_sha256"]):
        raise ValueError("timed converter differs from frozen registration")
    if (addon["existing_repeat"] is not None or addon["existing_receipt_sha256"] or
            addon["additional_repeats"] != [0, 1, 2, 3, 4] or
            addon["positions"] != {"existing": 0, "additional": 40, "total": 40} or
            addon["budget"]["maximum_concurrent_runs"] != 6 or
            addon["budget"]["native_time_limit_seconds"] is not None or
            addon["budget"]["address_space_limit_bytes_per_process"] != 3 * 1024 ** 3):
        raise ValueError("strict six-worker audit requires 40 fresh process receipts")
    progress = json.loads((addon_root / "batch_progress.json").read_text(encoding="utf-8"))
    if (progress.get("status") != "complete" or progress.get("expected_additional") != 40 or
            progress.get("already_complete") != 0 or progress.get("newly_launched") != 40 or
            progress.get("finished") != 40 or progress.get("failed") != [] or
            progress.get("active") != [] or len(progress.get("completed", [])) != 40 or
            progress.get("workers") != 1 or progress.get("cpus") != [23] or
            progress.get("address_space_limit_bytes_per_process") != 3 * 1024 ** 3 or
            progress.get("launcher_sha256") != addon["launcher_sha256"] or
            progress.get("addon_protocol_sha256") != sha256(addon_root / "addon_preregistration.json")):
        raise ValueError("fresh40 launcher ledger or CPU allocation differs")
    expected_jobs = {(view, repeat) for view in addon["views"]
                     for repeat in addon["additional_repeats"]}
    completed_jobs = {(task.get("graph_id"), task.get("repeat"))
                      for task in progress["completed"]}
    if (completed_jobs != expected_jobs or
            any(task.get("cpu") != 23 or task.get("return_code") != 0 or
                task.get("reused_existing_addon_receipt") is True
                for task in progress["completed"])):
        raise ValueError("launcher completed-job ledger differs from 40 fresh jobs")
    manifest = json.loads((addon_root / "metis_manifest.json").read_text(encoding="utf-8"))
    by_graph = {row["graph_id"]: row for row in manifest["graphs"]}
    if len(by_graph) != 8:
        raise ValueError("frozen conversion manifest must contain eight views")
    addon_hash = sha256(addon_root / "addon_preregistration.json")
    rows = []
    failures = []
    for view in addon["views"]:
        data_path = data_dir / (view.split("__", 1)[1] + ".npz")
        for repeat in addon["additional_repeats"]:
            artifact_root = addon_root / f"replicate-{repeat:02d}"
            receipt_path = artifact_root / "results" / addon["arm"] / (view + "__s0000.json")
            try:
                rows.append(check_receipt(receipt_path, artifact_root, data_path,
                                          view, repeat, addon, reference,
                                          by_graph[view], addon_hash))
            except Exception as exc:
                failures.append({"graph_id": view, "repeat": repeat,
                                 "reason": type(exc).__name__ + ": " + str(exc)})
    analysis = addon_root / "analysis"
    analysis.mkdir(exist_ok=True)
    position_path = analysis / "positions.csv"
    if rows:
        with position_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    complete = not failures and len(rows) == addon["positions"]["total"]
    by_view = {}
    by_view_exact = {}
    overall = None
    overall_exact = None
    if complete:
        for view in addon["views"]:
            values = [r["value_ticks"] for r in rows if r["graph_id"] == view]
            if len(values) != 5:
                raise ValueError("view does not have five independently measured process runs")
            by_view[view] = sum(values) / (5 * 1_000_000)
            by_view_exact[view] = str(Decimal(sum(values)) / Decimal(5_000_000))
        overall = sum(r["value_ticks"] for r in rows) / (40 * 1_000_000)
        overall_exact = str(Decimal(sum(r["value_ticks"] for r in rows)) /
                            Decimal(40_000_000))
    report = {
        "schema": "stablesolver_gwmin_3gib_900s_full40_six_worker_max_independent_audit_v1",
        "addon_protocol_sha256": sha256(addon_root / "addon_preregistration.json"),
        "reference_protocol_sha256": addon["reference_protocol_sha256"],
        "method": addon["arm"], "seed_argument_ignored_by_official_method": True,
        "resource_frame": "separate 3 GiB per process / natural one-pass stop / outer 900 s, timed conversion",
        "actual_worker_count": progress["workers"], "actual_cpu_ids": progress["cpus"],
        "budget": addon["budget"],
        "measurement_unit": "separate timed OS process, not independent random seed",
        "expected_positions": 40, "audited_valid_positions": len(rows),
        "failures": failures, "complete": complete,
        "by_view_mean_contact_seconds": by_view,
        "by_view_mean_contact_seconds_exact": by_view_exact,
        "overall_mean_contact_seconds": overall,
        "overall_mean_contact_seconds_exact": overall_exact,
        "position_index_sha256": sha256(position_path) if rows else None,
    }
    (analysis / "audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"valid": len(rows), "expected": 40, "complete": complete,
                      "failure_count": len(failures), "overall": overall}))
    if not complete:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
