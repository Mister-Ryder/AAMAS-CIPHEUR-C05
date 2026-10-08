#!/usr/bin/env python3
"""Portable replay of the registered original CHILS p4, step-10 arm."""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "source"
REGISTRATION = ROOT / "registration_public.json"
SOURCE_MANIFEST = ROOT / "source_manifest_public.json"
CPU = None
DATA_DIR = None
OUT_ROOT = None
GRAPH_HASHES = None
REGISTRATION_HASH = None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def init(queue, data_dir, out_root, graph_hashes, registration_hash):
    global CPU, DATA_DIR, OUT_ROOT, GRAPH_HASHES, REGISTRATION_HASH
    CPU = queue.get()
    os.sched_setaffinity(0, {CPU})
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                 "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[name] = "1"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SOURCE))
    DATA_DIR, OUT_ROOT = Path(data_dir), Path(out_root)
    GRAPH_HASHES = graph_hashes
    REGISTRATION_HASH = registration_hash


def work(task):
    graph_id, seed = task
    graph_path = DATA_DIR / (graph_id.split("__", 1)[1] + ".npz")
    if sha256(graph_path) != GRAPH_HASHES[graph_id]:
        raise ValueError("Input graph hash changed")
    output = OUT_ROOT / "chils_p4" / "results" / f"{graph_id}__CHILS-p4-original__seed{seed}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    from cipheur_chils import native
    config = native.RunConfig(seconds=900.0, population=4, threads=1, seed=seed,
                              step=10.0, cycles=2**63 - 1, ls_iterations=2**63 - 1,
                              max_snapshots=0)
    original_library = native._library
    boundary = []

    def observed_library(original=False):
        if original is not True:
            raise ValueError("Original CHILS kernel was not selected")
        library, path = original_library(original)
        function = library.cc_run

        class Proxy:
            def cc_run(self, *args):
                if (args[4], args[5], args[6], args[8], args[9], args[10]) != (
                    4, 1, seed, 10.0, 2**63 - 1, 2**63 - 1
                ):
                    raise ValueError("Native CHILS parameters differ from registration")
                if bool(args[12]):
                    raise ValueError("Unexpected guidance callback")
                boundary.append({"native_binary_sha256": sha256(Path(path)),
                                 "population": args[4], "threads": args[5],
                                 "seed": args[6], "step": args[8],
                                 "callback_null": True})
                return function(*args)

        return Proxy(), path

    started = time.time()
    native._library = observed_library
    try:
        result = native.run_instance(graph_path, method="original", config=config)
    finally:
        native._library = original_library
    if len(boundary) != 1 or result.get("status") != "ok":
        raise ValueError("Original CHILS call failed")
    result.update(graph_id=graph_id, seed=seed, arm="CHILS-p4-original-step10",
                  input_sha256=GRAPH_HASHES[graph_id], worker_cpu=CPU,
                  registration_public_sha256=REGISTRATION_HASH,
                  native_call_boundaries=boundary, model_calls=0,
                  started_unix=started, finished_unix=time.time())
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return {"graph_id": graph_id, "seed": seed, "status": result["status"],
            "value_ticks": result["value_ticks"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--out-root", required=True, type=Path)
    parser.add_argument("--cpus", required=True, help="Six permitted CPU IDs, comma-separated")
    args = parser.parse_args()
    registration = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    for relative, expected in manifest["copied_exact_origin_files"].items():
        if sha256(SOURCE / relative) != expected:
            raise ValueError("CHILS source hash mismatch: " + relative)
    if sha256(SOURCE / "build" / "libchils_original.so") != registration["native_binary_sha256"]:
        raise ValueError("CHILS binary hash mismatch")
    data_dir = args.data_dir.resolve(strict=True)
    out_root = args.out_root.resolve()
    if out_root.exists():
        raise FileExistsError("Use a new output root")
    graph_hashes = registration["graph_sha256"]
    for graph_id, expected in graph_hashes.items():
        path = data_dir / (graph_id.split("__", 1)[1] + ".npz")
        if sha256(path) != expected:
            raise ValueError("Graph hash mismatch: " + graph_id)
    cpus = tuple(int(value) for value in args.cpus.split(","))
    if len(cpus) != 6 or len(set(cpus)) != 6 or not set(cpus) <= os.sched_getaffinity(0):
        raise ValueError("Expected six distinct permitted CPU IDs")
    tasks = [(graph_id, seed) for graph_id in graph_hashes for seed in registration["seeds"]]
    random.Random(20261009).shuffle(tasks)
    if len(tasks) != 40:
        raise ValueError("Expected 40 CHILS positions")
    out_root.mkdir(parents=True)
    context = mp.get_context("spawn")
    queue = context.Queue()
    for cpu in cpus:
        queue.put(cpu)
    outcomes = []
    with ProcessPoolExecutor(max_workers=6, mp_context=context, initializer=init,
                             initargs=(queue, str(data_dir), str(out_root), graph_hashes,
                                       sha256(REGISTRATION))) as pool:
        futures = [pool.submit(work, task) for task in tasks]
        for future in as_completed(futures):
            outcome = future.result()
            outcomes.append(outcome)
            print(json.dumps({"completed": len(outcomes), "expected": 40, **outcome}), flush=True)
    with (out_root / "chils_p4" / "completion.json").open("x", encoding="utf-8") as stream:
        json.dump({"expected": 40, "completed": len(outcomes),
                   "all_ok": len(outcomes) == 40 and all(row["status"] == "ok" for row in outcomes),
                   "outcomes": outcomes}, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
