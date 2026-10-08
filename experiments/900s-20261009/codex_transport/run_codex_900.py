"""Run the registered C05 Codex arm on six isolated cloud CPU workers."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import multiprocessing as mp
import os
import random
import sys
import time


ROOT = Path(os.environ["CIPHEUR_EXPERIMENT_ROOT"]).resolve()
SOURCE = Path(os.environ.get("CIPHEUR_C05_SOURCE", str(ROOT / "source_c05_controls_900s"))).resolve()
REGISTRATION = Path(os.environ.get("CIPHEUR_PREREGISTRATION", str(ROOT / "registrations" / "preregistration.json"))).resolve()
PATCH_RECORD = Path(os.environ.get("CIPHEUR_C05_PATCH_RECORD", str(ROOT / "registrations" / "c05_source_patch.json"))).resolve()
INPUTS = Path(os.environ["CIPHEUR_INPUT_DIR"]).resolve()
RESULTS = ROOT / "results" / "codex"
COMPLETION = ROOT / "analysis" / "codex_900_completion.json"
LAUNCH = ROOT / "registrations" / "codex_900_launch.json"
RELAY_SECRETS = Path(os.environ["CIPHEUR_RELAY_SHARED_DIR"]).resolve()
RELAY_ENDPOINT = os.environ.get("CIPHEUR_RELAY_ENDPOINT", "https://127.0.0.1:24488/v1/chat/completions")
SEED_ORDER = 20261009
CPUS = tuple(int(value) for value in os.environ["CIPHEUR_CODEX_CPUS"].split(","))
EXPECTED_PATCH_SHA256 = "9a5e63d345e097ac35f3914ff438deb492202579e5313013f80d503300c53e69"
CPU = None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save_exclusive(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(obj, stream, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def worker_init(cpu_queue) -> None:
    global CPU
    CPU = cpu_queue.get()
    os.sched_setaffinity(0, {CPU})
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SOURCE))


def work(task):
    graph_id, view, seed = task
    graph_path = INPUTS / (view + ".npz")
    stem = "{}__llm__seed{}".format(graph_id, seed)
    output = RESULTS / (stem + ".json")
    events = RESULTS / (stem + ".events.jsonl")
    if output.exists() or events.exists():
        raise FileExistsError("Existing Codex position: " + stem)
    started_utc = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    with events.open("x", encoding="utf-8") as stream:
        def event(obj):
            stream.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        event({"event": "started", "graph_id": graph_id, "mode": "llm", "seed": seed,
               "cpu": CPU, "started_utc": started_utc})
        try:
            from cipheur_v06.contracts import Config
            from cipheur_v06.plan_provider import PlanProvider
            from cipheur_v06.plan_solver import solve
            config = Config(seconds=900, seed=seed, max_calls=21)
            provider = PlanProvider(timeout=45., thinking="enabled", reasoning_effort="low", max_tokens=8192)
            result = solve(graph_path, "llm", config, provider, on_event=event, graph_id=graph_id)
            result.update(candidate="c05", phase="seed_replication_900s_20261009",
                          arm="codex", requested_model="gpt-6-luna", worker_cpu=CPU,
                          worker_affinity=sorted(os.sched_getaffinity(0)),
                          started_utc=started_utc,
                          finished_utc=datetime.now(timezone.utc).isoformat(),
                          source_patch_sha256=EXPECTED_PATCH_SHA256,
                          launch_registration_sha256=sha256(LAUNCH))
            assert result["status"] == "ok" and result["feasible"] is True
            assert result["input_sha256"] == sha256(graph_path)
            assert result["target_seconds"] == 900
            assert result["config"]["max_calls"] == 21
        except Exception as exc:
            message = "{}: {}".format(type(exc).__name__, exc)
            key = os.environ.get("CIPHEUR_API_KEY")
            if key:
                message = message.replace(key, "[REDACTED]")
            result = {"status": "failed", "feasible": False, "graph_id": graph_id,
                      "mode": "llm", "seed": seed, "arm": "codex", "worker_cpu": CPU,
                      "started_utc": started_utc,
                      "finished_utc": datetime.now(timezone.utc).isoformat(),
                      "error": message[:1600]}
        save_exclusive(output, result)
        event({"event": "finished", "status": result["status"],
               "value_ticks": result.get("value_ticks"),
               "deployment_llm_calls": result.get("deployment_llm_calls"),
               "valid_model_proposals": result.get("valid_model_proposals")})
    return {"graph_id": graph_id, "seed": seed, "status": result["status"],
            "value_ticks": result.get("value_ticks"), "worker_cpu": CPU,
            "model_calls": result.get("deployment_llm_calls"),
            "valid_proposals": result.get("valid_model_proposals"),
            "wall_seconds": result.get("wall_seconds"),
            "driver_seconds": round(time.perf_counter() - t0, 3),
            "output_sha256": sha256(output)}


def main() -> None:
    assert len(CPUS) == 6 and len(set(CPUS)) == 6
    assert set(CPUS) <= os.sched_getaffinity(0)
    assert sha256(SOURCE / "cipheur_v06" / "contracts.py") == EXPECTED_PATCH_SHA256
    assert PATCH_RECORD.is_file()
    assert (SOURCE / "build" / "libcex5.so").is_file()
    assert all(path.is_file() for path in (REGISTRATION, RELAY_SECRETS / "relay_cert.pem", RELAY_SECRETS / "relay_token.txt"))
    registration = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    assert registration["seeds"] == [67, 71, 73, 79, 83]
    assert registration["budget"]["wall_seconds_per_position"] == 900
    assert registration["c05_selection_protocol"]["max_model_calls_per_position"] == 21
    graph_ids = list(registration["graph_sha256"])
    assert len(graph_ids) == 8
    for graph_id in graph_ids:
        view = graph_id.split("__", 1)[1]
        assert sha256(INPUTS / (view + ".npz")) == registration["graph_sha256"][graph_id]
    assert not LAUNCH.exists() and not COMPLETION.exists()
    RESULTS.mkdir(parents=True, exist_ok=False)
    os.environ["CIPHEUR_API_KEY"] = (RELAY_SECRETS / "relay_token.txt").read_text(encoding="utf-8").strip()
    os.environ["CIPHEUR_ENDPOINT"] = RELAY_ENDPOINT
    os.environ["CIPHEUR_MODEL"] = "gpt-6-luna"
    os.environ["SSL_CERT_FILE"] = str(RELAY_SECRETS / "relay_cert.pem")
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    tasks = [(graph_id, graph_id.split("__", 1)[1], seed)
             for seed in registration["seeds"] for graph_id in graph_ids]
    random.Random(SEED_ORDER).shuffle(tasks)
    launch = {"schema": "cipheur_c05_codex_900s_launch_v1",
              "preregistration_sha256": sha256(REGISTRATION),
              "source_patch_sha256": EXPECTED_PATCH_SHA256,
              "source_root": str(SOURCE), "native_library_sha256": sha256(SOURCE / "build" / "libcex5.so"),
              "runner_sha256": sha256(Path(__file__)),
              "relay_endpoint": RELAY_ENDPOINT, "relay_model": "gpt-6-luna",
              "relay_certificate_sha256": sha256(RELAY_SECRETS / "relay_cert.pem"),
              "relay_mapping": "result.model_calls[*].receipt.response_id=codex-c05-relay-<request_id>; private archive file <request_id>.json.gz",
              "relay_raw_io_archive": "private local per-request gzip, full input and raw output; sanitized manifest to follow",
              "seeds": registration["seeds"], "graphs": graph_ids,
              "seconds": 900, "max_calls": 21, "first_decision_seconds": 20,
              "decision_interval_seconds": 40, "population": 4,
              "workers": len(CPUS), "worker_cpus": list(CPUS), "order_seed": SEED_ORDER,
              "expected_positions": len(tasks),
              "started_utc": datetime.now(timezone.utc).isoformat()}
    save_exclusive(LAUNCH, launch)
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    for cpu in CPUS:
        queue.put(cpu)
    outcomes = []
    with ProcessPoolExecutor(max_workers=len(CPUS), mp_context=ctx, initializer=worker_init,
                             initargs=(queue,)) as pool:
        futures = [pool.submit(work, task) for task in tasks]
        for future in as_completed(futures):
            try:
                outcome = future.result()
            except Exception as exc:
                outcome = {"status": "worker_failed", "error_type": type(exc).__name__,
                           "error": str(exc)[:600]}
            outcomes.append(outcome)
            print(json.dumps({"event": "position_finished", **outcome},
                             ensure_ascii=False, sort_keys=True), flush=True)
    completion = {"schema": "cipheur_c05_codex_900s_completion_v1",
                  "expected_positions": len(tasks), "completed_positions": len(outcomes),
                  "all_ok": len(outcomes) == len(tasks) and all(x["status"] == "ok" for x in outcomes),
                  "outcomes": outcomes, "finished_utc": datetime.now(timezone.utc).isoformat()}
    save_exclusive(COMPLETION, completion)
    print(json.dumps({"event": "codex_arm_complete", "all_ok": completion["all_ok"]},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
