#!/usr/bin/env python3
"""Launch the gated 9 GiB StableSolver contingency with native limit 850 s."""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PROTOCOL_SHA256 = "f04dfa2ce06b57d931f1bb77ef47eb66b93f4e225167b90e214562245701d9c9"
MANIFEST_SHA256 = "ed5faf8dd093c7b2cc40e38bc2a12596b12cca2724d6a1afe57439a402d405a3"
ARM = "stablesolver_local_search"
CAP_BYTES = 9 * 1024 ** 3
MAX_WORKERS = 8
PRIMARY_POSITIONS = 104
CGROUP_MEMORY_BYTES = 72 * 1024 ** 3
CGROUP_CPU_QUOTA_CORES = 24


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, obj: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def physical_core(cpu: int) -> tuple[int, int]:
    topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
    package = int((topology / "physical_package_id").read_text().strip())
    core = int((topology / "core_id").read_text().strip())
    return package, core


def distinct_physical_cpus(workers: int) -> tuple[list[int], list[tuple[int, int]]]:
    chosen = []
    identities = []
    seen = set()
    for cpu in sorted(os.sched_getaffinity(0)):
        identity = physical_core(cpu)
        if identity in seen:
            continue
        seen.add(identity)
        chosen.append(cpu)
        identities.append(identity)
        if len(chosen) == workers:
            return chosen, identities
    raise RuntimeError("fewer distinct physical cores are available than requested workers")


def cgroup_limits(workers: int) -> dict:
    memory_text = Path("/sys/fs/cgroup/memory.max").read_text().strip()
    if memory_text == "max" or int(memory_text) != CGROUP_MEMORY_BYTES:
        raise RuntimeError("cloud cgroup memory limit differs from the registered 72 GiB")
    if int(memory_text) < workers * CAP_BYTES:
        raise RuntimeError("cgroup memory limit does not cover all 9 GiB position caps")
    quota_text, period_text = Path("/sys/fs/cgroup/cpu.max").read_text().split()
    if quota_text == "max" or int(quota_text) != CGROUP_CPU_QUOTA_CORES * int(period_text):
        raise RuntimeError("cloud cgroup CPU quota differs from the registered 24 cores")
    return {"memory_max_bytes": int(memory_text), "cpu_max": f"{quota_text} {period_text}"}


def limit_and_pin(cpu: int) -> None:
    os.sched_setaffinity(0, {cpu})
    resource.setrlimit(resource.RLIMIT_AS, (CAP_BYTES, CAP_BYTES))


def check_registration(root: Path) -> tuple[dict, dict]:
    protocol_path = root / "preregistration.json"
    manifest_path = root / "inputs/graphs/metis_manifest.json"
    if sha256(protocol_path) != PROTOCOL_SHA256 or sha256(manifest_path) != MANIFEST_SHA256:
        raise ValueError("recovery registration or manifest differs from frozen SHA256")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (protocol["status"] != "frozen" or protocol["planned_arms"] != [ARM]
            or protocol["supplementary_arms"] != []
            or protocol["expected_measured_positions_total"] != 8
            or protocol["positions_per_arm"] != {ARM: 8}
            or protocol["budget"]["wall_seconds_per_position"] != 900
            or protocol["budget"]["address_space_limit_bytes_per_position"] != CAP_BYTES
            or protocol["budget"]["simultaneous_positions_upper_bound"] != MAX_WORKERS):
        raise ValueError("recovery study scope or budget differs from frozen registration")
    if manifest["protocol_sha256"] != PROTOCOL_SHA256:
        raise ValueError("conversion manifest is not bound to frozen recovery registration")
    if len(manifest["graphs"]) != 8 or set(protocol["graph_sha256"]) != {x["graph_id"] for x in manifest["graphs"]}:
        raise ValueError("recovery graph frame does not cover eight original views")
    method = protocol["method_identity"][ARM]
    runner = root / "implementations/run_stablesolver.py"
    original_runner = root.parent / "implementations/run_stablesolver.py"
    original_bytes = original_runner.read_bytes()
    if (sha256(original_runner) != method["runner_parent_sha256"]
            or original_bytes.count(b"native_limit = 895.0") != 1
            or runner.read_bytes() != original_bytes.replace(
                b"native_limit = 895.0", b"native_limit = 850.0")
            or protocol["method_time_controls"][ARM] !=
            "native --time-limit 850 seconds; outer end-to-end 900-second cap"):
        raise ValueError("850-second runner differs from the frozen one-line variant")
    binary = root / "source/stablesolver/build/src/stable/stablesolver_stable"
    if sha256(runner) != method["runner_sha256"] or sha256(binary) != method["binary_sha256"]:
        raise ValueError("official runner or binary changed")
    source = root / "source/stablesolver"
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if commit != method["git_commit"]:
        raise ValueError("official StableSolver source commit changed")
    archive = subprocess.Popen(["git", "-C", str(source), "archive", "HEAD"],
                               stdout=subprocess.PIPE)
    source_digest = hashlib.sha256()
    assert archive.stdout is not None
    for chunk in iter(lambda: archive.stdout.read(1 << 20), b""):
        source_digest.update(chunk)
    archive.stdout.close()
    if archive.wait() != 0 or source_digest.hexdigest() != method["source_sha256"]:
        raise ValueError("official StableSolver source archive SHA256 changed")
    for row in manifest["graphs"]:
        if protocol["graph_sha256"][row["graph_id"]] != row["input_sha256"]:
            raise ValueError("original source NPZ hash differs from graph manifest")
        for path, expected in (
            (root / "inputs/data/CP-SCALE-AU-L002" / row["input_name"], row["input_sha256"]),
            (root / "inputs/graphs" / row["dimacs_name"], row["dimacs_sha256"]),
            (root / "inputs/graphs" / row["metis_name"], row["metis_sha256"]),
        ):
            if sha256(path) != expected:
                raise ValueError(f"shared original input changed: {path}")
    recovery = protocol["recovery"]
    if sha256(root.parent / "preregistration.json") != recovery["parent_preregistration_sha256"]:
        raise ValueError("original primary registration changed")
    if len(recovery["parent_failure_receipts"]) != 2:
        raise ValueError("both original local-search failure receipts are required")
    for receipt in recovery["parent_failure_receipts"]:
        if sha256(root / receipt["result_relative_path"]) != receipt["result_sha256"]:
            raise ValueError("preserved original failure result changed")
        if sha256(root / receipt["native_log_relative_path"]) != receipt["native_log_sha256"]:
            raise ValueError("preserved original failure log changed")
    lineage = protocol["contingency"]
    six = root.parent / lineage["parent_six_gib_directory"]
    for relative, expected in (
        ("preregistration.json", lineage["parent_six_gib_preregistration_sha256"]),
        ("inputs/graphs/metis_manifest.json", lineage["parent_six_gib_graph_manifest_sha256"]),
        ("launch_recovery.py", lineage["parent_six_gib_launcher_sha256"]),
        ("audit_recovery.py", lineage["parent_six_gib_execution_auditor_sha256"]),
    ):
        if sha256(six / relative) != expected:
            raise ValueError(f"6 GiB predecessor file changed: {relative}")
    return protocol, manifest


def check_primary_complete(root: Path, protocol: dict) -> str:
    parent = root.parent
    recovery = protocol["recovery"]
    if sha256(parent / "preregistration.json") != recovery["parent_preregistration_sha256"]:
        raise ValueError("original primary registration changed")
    for receipt in recovery["parent_failure_receipts"]:
        original_result = parent / "results" / ARM / (receipt["graph_id"] + "__s0000.json")
        if sha256(original_result) != receipt["result_sha256"]:
            raise ValueError("original failure receipt changed")
    progress_path = parent / "batch_progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    if (progress.get("status") not in ("complete", "completed_with_failures")
            or progress.get("workers") != 24
            or progress.get("scheduled") != PRIMARY_POSITIONS
            or progress.get("launched") != PRIMARY_POSITIONS
            or progress.get("finished") != PRIMARY_POSITIONS
            or progress.get("active")):
        raise RuntimeError("primary 24-core, 104-position batch has not finished; recovery launch forbidden")
    return sha256(progress_path)


def check_six_gib_incomplete(root: Path, protocol: dict) -> dict:
    """Require a terminal 6 GiB run and an explicitly incomplete full audit."""
    six_root = root.parent / protocol["contingency"]["parent_six_gib_directory"]
    lineage = protocol["contingency"]
    if (sha256(six_root / "preregistration.json") !=
            lineage["parent_six_gib_preregistration_sha256"]):
        raise ValueError("6 GiB frozen preregistration changed")
    if (sha256(six_root / "inputs/graphs/metis_manifest.json") !=
            lineage["parent_six_gib_graph_manifest_sha256"]):
        raise ValueError("6 GiB frozen graph manifest changed")
    if sha256(six_root / "launch_recovery.py") != lineage["parent_six_gib_launcher_sha256"]:
        raise ValueError("6 GiB launcher changed")
    if sha256(six_root / "audit_recovery.py") != lineage["parent_six_gib_execution_auditor_sha256"]:
        raise ValueError("6 GiB execution auditor changed")
    progress_path = six_root / "batch_progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    if (progress.get("status") not in ("complete", "completed_with_failures")
            or progress.get("scheduled") != 8
            or progress.get("launched") != 8
            or progress.get("finished") != 8
            or progress.get("active")
            or not 1 <= progress.get("workers", 0) <= 8
            or progress.get("position_rlimit_as_bytes") != 6 * 1024**3
            or progress.get("registration_sha256") != lineage["parent_six_gib_preregistration_sha256"]
            or progress.get("graph_manifest_sha256") != lineage["parent_six_gib_graph_manifest_sha256"]):
        raise RuntimeError("6 GiB eight-view batch is not terminal and frozen; contingency launch forbidden")
    execution_audit_path = six_root / "analysis/recovery_execution_audit.json"
    independent_audit_path = six_root / "analysis/classical_900s_independent_audit.json"
    execution_audit = json.loads(execution_audit_path.read_text(encoding="utf-8"))
    independent_audit = json.loads(independent_audit_path.read_text(encoding="utf-8"))
    count = independent_audit.get("audited_valid_positions")
    if (execution_audit.get("mode") != "complete_results"
            or execution_audit.get("status") != "invalid"
            or execution_audit.get("protocol_sha256") != lineage["parent_six_gib_preregistration_sha256"]
            or execution_audit.get("manifest_sha256") != lineage["parent_six_gib_graph_manifest_sha256"]
            or execution_audit.get("checks", {}).get("eight_independently_audited_positions") is not False
            or execution_audit.get("checks", {}).get("all_graphs_exactly_equivalent") is not True
            or independent_audit.get("protocol_sha256") != lineage["parent_six_gib_preregistration_sha256"]
            or independent_audit.get("all_graphs_exactly_equivalent") is not True
            or independent_audit.get("expected_positions") != 8
            or independent_audit.get("status") not in ("incomplete_or_invalid", "invalid")
            or not isinstance(count, int)
            or not 0 <= count < 8):
        raise RuntimeError("6 GiB full audit does not prove fewer than eight valid positions; contingency launch forbidden")
    return {
        "six_gib_batch_progress_sha256_at_launch": sha256(progress_path),
        "six_gib_execution_audit_sha256_at_launch": sha256(execution_audit_path),
        "six_gib_independent_audit_sha256_at_launch": sha256(independent_audit_path),
        "six_gib_audited_valid_positions_at_launch": count,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--preflight", action="store_true", help="verify frozen inputs and resource envelope without launching jobs")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if not 1 <= args.workers <= MAX_WORKERS:
        raise ValueError("at most eight concurrent positions are registered")
    protocol, _ = check_registration(root)
    cpus, physical_cores = distinct_physical_cpus(args.workers)
    cgroup = cgroup_limits(args.workers)
    if args.preflight:
        print(json.dumps({"status": "input_and_resource_preflight_valid", "measured_jobs_started": 0,
                          "registration_sha256": PROTOCOL_SHA256,
                          "graph_manifest_sha256": MANIFEST_SHA256,
                          "workers": args.workers, "cpus": cpus,
                          "physical_cores": physical_cores, "cgroup": cgroup}))
        return
    primary_progress_sha256 = check_primary_complete(root, protocol)
    six_gib_gate_receipt = check_six_gib_incomplete(root, protocol)
    jobs = [(graph_id, 0) for graph_id in protocol["graph_sha256"]]
    if len(jobs) != 8 or len(set(jobs)) != 8:
        raise ValueError("eight unique StableSolver local-search positions required")
    for graph_id, seed in jobs:
        stem = f"{graph_id}__s{seed:04d}"
        for category, suffix in (("results", ".json"), ("locks", ".lock"),
                                 ("native", ".json"), ("certificates", ".sol"),
                                 ("traces", ".csv"), ("logs", ".log")):
            if (root / category / ARM / (stem + suffix)).exists():
                raise FileExistsError(f"recovery position already has an artifact: {category}/{stem}")
    progress_path = root / "batch_progress.json"
    with progress_path.open("x", encoding="utf-8") as stream:
        stream.write("{}\n")
    launch_logs = root / "launch_logs"
    launch_logs.mkdir(exist_ok=False)
    record = {
        "schema": "cipheur_stablesolver_local_search_9gib_native850_contingency_batch_v1",
        "status": "running", "started_at_utc": now(),
        "registration_sha256": PROTOCOL_SHA256, "graph_manifest_sha256": MANIFEST_SHA256,
        "launcher_sha256": sha256(Path(__file__).resolve()),
        "primary_batch_progress_sha256_at_launch": primary_progress_sha256,
        **six_gib_gate_receipt,
        "original_failure_result_sha256":
            {receipt["graph_id"]: receipt["result_sha256"]
             for receipt in protocol["recovery"]["parent_failure_receipts"]},
        "position_rlimit_as_bytes": CAP_BYTES, "workers": args.workers,
        "max_registered_workers": MAX_WORKERS, "cpus": cpus,
        "physical_cores": [{"cpu": cpu, "package": package, "core": core}
                           for cpu, (package, core) in zip(cpus, physical_cores)],
        "cgroup": cgroup, "scheduled": len(jobs), "launched": 0, "finished": 0,
        "failed": [], "completed": [], "active": [],
    }
    active = {}
    available = list(cpus)
    next_index = 0
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
               VECLIB_MAXIMUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
    while next_index < len(jobs) or active:
        while available and next_index < len(jobs):
            cpu = available.pop(0)
            graph_id, seed = jobs[next_index]
            next_index += 1
            label = f"{ARM}__{graph_id}__s{seed:04d}"
            log_path = launch_logs / (label + ".log")
            argv = [sys.executable, str(root / "implementations/run_stablesolver.py"),
                    "--protocol", str(root / "preregistration.json"),
                    "--manifest", str(root / "inputs/graphs/metis_manifest.json"),
                    "--data-dir", str(root / "inputs/data/CP-SCALE-AU-L002"),
                    "--input-dir", str(root / "inputs/graphs"),
                    "--output-root", str(root),
                    "--source", str(root / "source/stablesolver"),
                    "--binary", str(root / "source/stablesolver/build/src/stable/stablesolver_stable"),
                    "--graph-id", graph_id, "--seed", str(seed), "--arm", ARM, "--cpu", str(cpu)]
            with log_path.open("xb") as log:
                child = subprocess.Popen(argv, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                         start_new_session=True, preexec_fn=functools.partial(limit_and_pin, cpu),
                                         env=env)
            soft, hard = resource.prlimit(child.pid, resource.RLIMIT_AS)
            affinity = sorted(os.sched_getaffinity(child.pid))
            if (soft, hard) != (CAP_BYTES, CAP_BYTES) or affinity != [cpu]:
                child.terminate()
                child.wait()
                raise RuntimeError("spawned position lacks the registered RLIMIT_AS or CPU affinity")
            active[child.pid] = {"process": child, "arm": ARM, "graph_id": graph_id,
                                 "seed": seed, "cpu": cpu, "physical_core": physical_core(cpu),
                                 "rlimit_as_soft_bytes": soft, "rlimit_as_hard_bytes": hard,
                                 "started_at_utc": now(), "log": str(log_path.relative_to(root))}
            record["launched"] += 1
        for pid, task in list(active.items()):
            return_code = task["process"].poll()
            if return_code is None:
                continue
            public = {key: value for key, value in task.items() if key != "process"}
            public["return_code"] = return_code
            public["finished_at_utc"] = now()
            record["completed" if return_code == 0 else "failed"].append(public)
            record["finished"] += 1
            available.append(task["cpu"])
            del active[pid]
        available.sort()
        record["active"] = [{key: value for key, value in task.items() if key != "process"}
                            for task in active.values()]
        record["updated_at_utc"] = now()
        write_json(progress_path, record)
        if active:
            time.sleep(5)
    record["status"] = "complete" if not record["failed"] else "completed_with_failures"
    record["completed_at_utc"] = now()
    write_json(progress_path, record)
    print(json.dumps({key: record[key] for key in ("status", "scheduled", "finished", "failed")}))
    if record["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
