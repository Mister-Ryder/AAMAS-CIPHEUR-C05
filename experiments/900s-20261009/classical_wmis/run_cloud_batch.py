#!/usr/bin/env python3
"""Durable, one-core-per-position launcher for the frozen classical WMIS study."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import resource
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


OFFICIAL_ARMS = (
    "stablesolver_local_search",
    "stablesolver_large_neighborhood_search",
    "stablesolver_greedy_gwmin",
)
CUSTOM_ARMS = ("grasp", "simulated_annealing")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, content: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def limit_position_memory() -> None:
    # 24 positions x 3 GiB is no more than the 72 GiB cgroup allotment.
    cap = 3 * 1024 ** 3
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))


def frame(protocol: dict) -> list[tuple[str, str, int]]:
    all_arms = list(protocol["planned_arms"]) + list(protocol["supplementary_arms"])
    if set(all_arms) != set(OFFICIAL_ARMS + CUSTOM_ARMS):
        raise ValueError("unrecognized algorithm frame")
    positions = [
        (arm, graph_id, seed)
        for arm in all_arms
        for graph_id in protocol["graph_sha256"]
        for seed in ([0] if arm in OFFICIAL_ARMS else protocol["seeds"])
    ]
    if len(positions) != protocol["expected_measured_positions_total"] or len(positions) != len(set(positions)):
        raise ValueError("position count or uniqueness differs from registration")
    random.Random(20261009).shuffle(positions)
    return positions


def check_files(root: Path, protocol: dict, manifest: dict) -> None:
    if protocol.get("status") != "frozen":
        raise ValueError("registration has not been frozen")
    if manifest["protocol_sha256"] != sha256(root / "preregistration.json"):
        raise ValueError("graph manifest and frozen registration disagree")
    if protocol["budget"]["wall_seconds_per_position"] != 900:
        raise ValueError("unexpected wall budget")
    by_id = {r["graph_id"]: r for r in manifest["graphs"]}
    if set(by_id) != set(protocol["graph_sha256"]):
        raise ValueError("converted graph set is incomplete")
    for graph_id, source_hash in protocol["graph_sha256"].items():
        row = by_id[graph_id]
        data = root / "inputs" / "data" / "CP-SCALE-AU-L002" / row["input_name"]
        dimacs = root / "inputs" / "graphs" / row["dimacs_name"]
        metis = root / "inputs" / "graphs" / row["metis_name"]
        for path, wanted in ((data, source_hash), (dimacs, row["dimacs_sha256"]),
                             (metis, row["metis_sha256"])):
            if sha256(path) != wanted:
                raise ValueError("missing or mismatching input: " + str(path))
    impl = root / "implementations"
    if sha256(impl / "classical_mwis.cpp") != protocol["method_identity"]["grasp"]["source_sha256"]:
        raise ValueError("custom C++ source changed")
    if sha256(impl / "classical_mwis") != protocol["method_identity"]["grasp"]["binary_sha256"]:
        raise ValueError("custom C++ binary changed")
    if sha256(impl / "run_classical.py") != protocol["method_identity"]["grasp"]["runner_sha256"]:
        raise ValueError("custom runner changed")
    if sha256(impl / "run_stablesolver.py") != protocol["method_identity"]["stablesolver_local_search"]["runner_sha256"]:
        raise ValueError("official runner changed")
    if sha256(root / "source" / "stablesolver" / "build" / "src" / "stable" / "stablesolver_stable") != \
            protocol["method_identity"]["stablesolver_local_search"]["binary_sha256"]:
        raise ValueError("official binary changed")


def argv_for(root: Path, arm: str, graph_id: str, seed: int, cpu: int) -> list[str]:
    impl = root / "implementations"
    if arm in OFFICIAL_ARMS:
        script = impl / "run_stablesolver.py"
        source = root / "source" / "stablesolver"
        binary = source / "build" / "src" / "stable" / "stablesolver_stable"
    else:
        script = impl / "run_classical.py"
        source = impl / "classical_mwis.cpp"
        binary = impl / "classical_mwis"
    return [sys.executable, str(script), "--protocol", str(root / "preregistration.json"),
            "--manifest", str(root / "inputs" / "graphs" / "metis_manifest.json"),
            "--data-dir", str(root / "inputs" / "data" / "CP-SCALE-AU-L002"),
            "--input-dir", str(root / "inputs" / "graphs"),
            "--output-root", str(root), "--source", str(source), "--binary", str(binary),
            "--graph-id", graph_id, "--seed", str(seed), "--arm", arm, "--cpu", str(cpu)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=24)
    args = ap.parse_args()
    root = args.root.resolve(strict=True)
    protocol = json.loads((root / "preregistration.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "inputs" / "graphs" / "metis_manifest.json").read_text(encoding="utf-8"))
    check_files(root, protocol, manifest)
    jobs = frame(protocol)
    allowed = sorted(os.sched_getaffinity(0))
    if args.workers < 1 or args.workers > 24 or len(allowed) < args.workers:
        raise ValueError("workers exceed registered 24-core allocation")
    cores = allowed[:args.workers]
    for arm, graph_id, seed in jobs:
        result = root / "results" / arm / f"{graph_id}__s{seed:04d}.json"
        if result.exists():
            raise FileExistsError("refusing to relaunch an existing position: " + str(result))
    progress = root / "batch_progress.json"
    if progress.exists():
        raise FileExistsError("refusing to overwrite existing batch progress")
    launch_logs = root / "launch_logs"
    launch_logs.mkdir(exist_ok=True)
    record = {"schema": "classical_wmis_batch_v1", "started_at_utc": now(),
              "registration_sha256": sha256(root / "preregistration.json"),
              "launcher_sha256": sha256(Path(__file__)), "workers": args.workers,
              "cpus": cores, "scheduled": len(jobs), "launched": 0, "finished": 0,
              "failed": [], "active": [], "completed": [], "status": "running"}
    active = {}
    next_index = 0
    available = list(cores)
    while next_index < len(jobs) or active:
        while available and next_index < len(jobs):
            cpu = available.pop(0)
            arm, graph_id, seed = jobs[next_index]
            next_index += 1
            label = f"{arm}__{graph_id}__s{seed:04d}"
            log_path = launch_logs / (label + ".log")
            with log_path.open("wb") as log:
                child = subprocess.Popen(argv_for(root, arm, graph_id, seed, cpu), cwd=root,
                                         stdout=log, stderr=subprocess.STDOUT,
                                         start_new_session=True, preexec_fn=limit_position_memory,
                                         env=dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
                                                  MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1"))
            active[child.pid] = {"process": child, "arm": arm, "graph_id": graph_id,
                                 "seed": seed, "cpu": cpu, "started_at_utc": now(),
                                 "log": str(log_path.relative_to(root))}
            record["launched"] += 1
        for pid, task in list(active.items()):
            ret = task["process"].poll()
            if ret is None:
                continue
            public = {k: v for k, v in task.items() if k != "process"}
            public["return_code"] = ret
            public["finished_at_utc"] = now()
            if ret == 0:
                record["completed"].append(public)
            else:
                record["failed"].append(public)
            record["finished"] += 1
            available.append(task["cpu"])
            del active[pid]
        available.sort()
        record["active"] = [{k: v for k, v in task.items() if k != "process"}
                            for task in active.values()]
        record["updated_at_utc"] = now()
        write_json(progress, record)
        if active:
            time.sleep(5)
    record["status"] = "complete" if not record["failed"] else "completed_with_failures"
    record["completed_at_utc"] = now()
    write_json(progress, record)
    print(json.dumps({k: record[k] for k in ("status", "scheduled", "finished", "failed")}))
    if record["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
