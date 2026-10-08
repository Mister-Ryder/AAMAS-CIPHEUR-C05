#!/usr/bin/env python3
"""Run the frozen C05 KNN/LinUCB selectors at 900 s in a fresh output root."""
import argparse
import hashlib
import json
import multiprocessing as mp
import os
import random
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"
sys.dont_write_bytecode = True

CPU = None
SOURCE = None
BASE_SOURCE = None
DATA_DIR = None
OUT_ROOT = None
INPUT_HASHES = None
SOURCE_RECORD_SHA256 = None
PROTOCOL_SHA256 = None
RUNNER_SHA256 = None


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_new_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def independent_cpus():
    allowed = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else list(range(os.cpu_count() or 1))
    seen, physical = set(), []
    for cpu in allowed:
        sibling_path = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list")
        siblings = sibling_path.read_text().strip() if sibling_path.exists() else str(cpu)
        if siblings not in seen:
            physical.append(cpu)
            seen.add(siblings)
    return physical


def init(cpu_queue, source, base_source, data_dir, out_root, input_hashes, source_record_hash, protocol_hash, runner_hash):
    global CPU, SOURCE, BASE_SOURCE, DATA_DIR, OUT_ROOT, INPUT_HASHES
    global SOURCE_RECORD_SHA256, PROTOCOL_SHA256, RUNNER_SHA256
    CPU = cpu_queue.get()
    os.sched_setaffinity(0, {CPU})
    SOURCE, BASE_SOURCE, DATA_DIR, OUT_ROOT = map(Path, (source, base_source, data_dir, out_root))
    INPUT_HASHES = input_hashes
    SOURCE_RECORD_SHA256, PROTOCOL_SHA256, RUNNER_SHA256 = source_record_hash, protocol_hash, runner_hash
    if BASE_SOURCE.exists():
        sys.path.insert(0, str(BASE_SOURCE))
    sys.path.insert(0, str(SOURCE))
    os.environ.pop("CIPHEUR_API_KEY", None)


def work(task):
    graph_id, seed, mode = task
    graph = DATA_DIR / (graph_id.split("__", 1)[1] + ".npz")
    path = OUT_ROOT / "results" / mode / f"{graph_id}__{mode}__seed{seed}.json"
    events = path.with_suffix(".events.jsonl")
    if path.exists() or events.exists():
        raise FileExistsError(f"No implicit rerun: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    begin = time.time()
    with events.open("x", encoding="utf-8") as stream:
        def event(obj):
            stream.write(json.dumps(obj, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        event({"event": "started", "graph_id": graph_id, "seed": seed, "mode": mode, "cpu": CPU, "unix": begin})
        try:
            if sha256(graph) != INPUT_HASHES[graph_id]:
                raise ValueError("Input graph bytes differ from preregistration")
            from cipheur_v06.contracts import Config
            from cipheur_v06.plan_solver import solve
            config = Config(seconds=900, seed=seed, max_calls=21)
            result = solve(graph, mode, config, None, on_event=event, graph_id=graph_id)
            if result.get("target_seconds") != 900 or result.get("config", {}).get("max_calls") != 21:
                raise AssertionError("900-second shared C05 configuration was not used")
            if result.get("input_sha256") != INPUT_HASHES[graph_id]:
                raise AssertionError("Result graph hash differs from preregistration")
            result.update(
                arm=mode,
                phase="seed_replication_900s_20261009",
                worker_cpu=CPU,
                worker_affinity=sorted(os.sched_getaffinity(0)),
                started_unix=begin,
                finished_unix=time.time(),
                source_patch_record_sha256=SOURCE_RECORD_SHA256,
                protocol_sha256=PROTOCOL_SHA256,
                runner_sha256=RUNNER_SHA256,
            )
        except Exception as exc:
            result = {
                "graph_id": graph_id,
                "seed": seed,
                "mode": mode,
                "arm": mode,
                "phase": "seed_replication_900s_20261009",
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
                "worker_cpu": CPU,
                "started_unix": begin,
                "finished_unix": time.time(),
                "source_patch_record_sha256": SOURCE_RECORD_SHA256,
                "protocol_sha256": PROTOCOL_SHA256,
                "runner_sha256": RUNNER_SHA256,
            }
        write_new_json(path, result)
        event({"event": "finished", "status": result.get("status"), "unix": time.time()})
    return {"graph_id": graph_id, "seed": seed, "mode": mode, "status": result.get("status")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path, help="isolated source copy after prepare_c05_source.py")
    parser.add_argument("--base-source", type=Path, default=Path("/nonexistent"), help="frozen v0.6 base source when not installed")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--out-root", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--cpu-offset", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    protocol_path = args.protocol.resolve(strict=True)
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol.get("schema") != "cipheur_c05_900s_preregistration_v1":
        raise ValueError("Wrong preregistration schema")
    if protocol.get("budget", {}).get("wall_seconds_per_position") != 900:
        raise ValueError("Expected fixed 900-second budget")
    if protocol.get("c05_selection_protocol", {}).get("max_model_calls_per_position") != 21:
        raise ValueError("Expected shared 21-selection policy")
    source = args.source.resolve(strict=True)
    base_source = args.base_source.resolve()
    data_dir = args.data_dir.resolve(strict=True)
    root = args.out_root.resolve()
    patch_record_path = root / "registrations" / "c05_source_patch.json"
    if not patch_record_path.exists():
        raise FileNotFoundError(f"Source patch record required: {patch_record_path}")
    patch_record = json.loads(patch_record_path.read_text(encoding="utf-8"))
    if Path(patch_record["source_copy"]).resolve() != source:
        raise ValueError("Runner source differs from registered source copy")
    contracts_hash = sha256(source / "cipheur_v06" / "contracts.py")
    if contracts_hash != patch_record.get("patched_contracts_sha256"):
        raise ValueError("Patched contracts.py hash mismatch")
    graph_hashes = protocol["graph_sha256"]
    if len(graph_hashes) != 8 or protocol.get("seeds") != [67, 71, 73, 79, 83]:
        raise ValueError("Eight-view five-seed scope mismatch")
    for graph_id, expected in graph_hashes.items():
        graph = data_dir / (graph_id.split("__", 1)[1] + ".npz")
        if sha256(graph) != expected:
            raise ValueError(f"Input graph bytes differ from preregistration: {graph_id}")
    if args.workers < 1 or args.cpu_offset < 0:
        raise ValueError("Invalid worker/CPU offset")
    cpus = independent_cpus()
    selected_cpus = cpus[args.cpu_offset : args.cpu_offset + args.workers]
    if len(selected_cpus) != args.workers:
        raise ValueError("Too few independent physical CPUs")
    pairs = [(g, s) for g in graph_hashes for s in protocol["seeds"]]
    random.Random(20261009).shuffle(pairs)
    tasks = [(g, s, mode) for g, s in pairs for mode in ("knn", "linucb")]
    if len(tasks) != 80:
        raise AssertionError("Expected 80 C05 control positions")
    registration = {
        "schema": "cipheur_c05_controls_900s_registration_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "phase": protocol["phase"],
        "modes": ["knn", "linucb"],
        "graph_ids": list(graph_hashes),
        "graph_sha256": graph_hashes,
        "seeds": protocol["seeds"],
        "expected_positions": len(tasks),
        "seconds": 900,
        "max_calls": 21,
        "first_decision_seconds": 20,
        "decision_interval_seconds": 40,
        "population": 4,
        "native_threads_per_position": 1,
        "workers": args.workers,
        "physical_cpu_ids": selected_cpus,
        "task_order_seed": 20261009,
        "source_path": str(source),
        "source_patch_record_sha256": sha256(patch_record_path),
        "patched_contracts_sha256": contracts_hash,
        "protocol_sha256": sha256(protocol_path),
        "runner_sha256": sha256(Path(__file__)),
        "data_dir": str(data_dir),
        "base_source": str(base_source) if base_source.exists() else None,
    }
    if args.dry_run:
        print(json.dumps(registration, ensure_ascii=False, indent=2))
        return
    if (root / "registrations" / "c05_controls_900s.json").exists():
        raise FileExistsError("C05 controls registration already exists")
    if (root / "results" / "knn").exists() or (root / "results" / "linucb").exists():
        raise FileExistsError("C05 controls results already exist")
    write_new_json(root / "registrations" / "c05_controls_900s.json", registration)
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    for cpu in selected_cpus:
        queue.put(cpu)
    outcomes = []
    with ProcessPoolExecutor(
        max_workers=args.workers,
        mp_context=ctx,
        initializer=init,
        initargs=(queue, str(source), str(base_source), str(data_dir), str(root), graph_hashes,
                  registration["source_patch_record_sha256"], registration["protocol_sha256"],
                  registration["runner_sha256"]),
    ) as pool:
        futures = [pool.submit(work, task) for task in tasks]
        for future in as_completed(futures):
            try:
                outcome = future.result()
            except Exception as exc:
                outcome = {"status": "worker_failed", "error": f"{type(exc).__name__}: {exc}"}
            outcomes.append(outcome)
            print(json.dumps({"completed": len(outcomes), "expected": len(tasks), **outcome}), flush=True)
    completion = {
        "expected": len(tasks),
        "completed": len(outcomes),
        "all_ok": len(outcomes) == len(tasks) and all(row["status"] == "ok" for row in outcomes),
        "outcomes": outcomes,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_new_json(root / "analysis" / "c05_controls_900s_completion.json", completion)
    if not completion["all_ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
