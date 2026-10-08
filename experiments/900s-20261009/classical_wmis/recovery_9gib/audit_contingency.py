#!/usr/bin/env python3
"""Audit the gated 9 GiB contingency with native limit 850 seconds."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ARM = "stablesolver_local_search"
PROTOCOL_SHA256 = "f04dfa2ce06b57d931f1bb77ef47eb66b93f4e225167b90e214562245701d9c9"
MANIFEST_SHA256 = "ed5faf8dd093c7b2cc40e38bc2a12596b12cca2724d6a1afe57439a402d405a3"
CAP_BYTES = 9 * 1024 ** 3
MAX_WORKERS = 8


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def result_matches_launch(root: Path, launch_row: dict) -> bool:
    try:
        path = root / "results" / ARM / (launch_row["graph_id"] + f"__s{launch_row['seed']:04d}.json")
        result = json.loads(path.read_text(encoding="utf-8"))
        return (result.get("cpu_id") == launch_row.get("cpu") and
                result.get("cpu_affinity") == [launch_row.get("cpu")] and
                result.get("graph_id") == launch_row.get("graph_id") and
                result.get("seed") == launch_row.get("seed"))
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def six_gib_gate_was_valid(root: Path, protocol: dict, progress: dict) -> bool:
    six = root.parent / protocol["contingency"]["parent_six_gib_directory"]
    lineage = protocol["contingency"]
    try:
        six_progress_path = six / "batch_progress.json"
        six_execution_path = six / "analysis/recovery_execution_audit.json"
        six_independent_path = six / "analysis/classical_900s_independent_audit.json"
        six_progress = json.loads(six_progress_path.read_text(encoding="utf-8"))
        six_execution = json.loads(six_execution_path.read_text(encoding="utf-8"))
        six_independent = json.loads(six_independent_path.read_text(encoding="utf-8"))
        count = six_independent.get("audited_valid_positions")
        return all((
            sha256(six_progress_path) == progress.get("six_gib_batch_progress_sha256_at_launch"),
            sha256(six_execution_path) == progress.get("six_gib_execution_audit_sha256_at_launch"),
            sha256(six_independent_path) == progress.get("six_gib_independent_audit_sha256_at_launch"),
            sha256(six / "preregistration.json") == lineage["parent_six_gib_preregistration_sha256"],
            sha256(six / "inputs/graphs/metis_manifest.json") == lineage["parent_six_gib_graph_manifest_sha256"],
            six_progress.get("status") in ("complete", "completed_with_failures"),
            six_progress.get("scheduled") == six_progress.get("launched") == six_progress.get("finished") == 8,
            not six_progress.get("active"),
            six_execution.get("mode") == "complete_results",
            six_execution.get("status") == "invalid",
            six_execution.get("checks", {}).get("eight_independently_audited_positions") is False,
            six_independent.get("status") in ("incomplete_or_invalid", "invalid"),
            six_independent.get("expected_positions") == 8,
            isinstance(count, int) and 0 <= count < 8,
            count == progress.get("six_gib_audited_valid_positions_at_launch"),
        ))
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--inputs-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    analysis = root / "analysis"
    analysis.mkdir(exist_ok=True)
    command = [sys.executable, str(root / "audit_classical_900s.py"),
               "--protocol", str(root / "preregistration.json"),
               "--data-dir", str(root / "inputs/data/CP-SCALE-AU-L002"),
               "--graph-dir", str(root / "inputs/graphs"),
               "--out-dir", str(analysis)]
    if not args.inputs_only:
        command.extend(["--results-root", str(root)])
    independent = subprocess.run(command, check=False, capture_output=True, text=True)
    audit_path = analysis / "classical_900s_independent_audit.json"
    independent_report = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.is_file() else {}
    protocol = json.loads((root / "preregistration.json").read_text(encoding="utf-8"))
    checks = {
        "independent_auditor_exit_zero": independent.returncode == 0,
        "registration_frozen_hash": sha256(root / "preregistration.json") == PROTOCOL_SHA256,
        "graph_manifest_frozen_hash": sha256(root / "inputs/graphs/metis_manifest.json") == MANIFEST_SHA256,
        "all_graphs_exactly_equivalent": independent_report.get("all_graphs_exactly_equivalent") is True,
        "manifest_protocol_binding": independent_report.get("conversion_manifest_protocol_hash_matches") is True,
        "both_original_failure_receipts_preserved":
            len(protocol["recovery"]["parent_failure_receipts"]) == 2 and
            all(sha256(root / receipt["result_relative_path"]) == receipt["result_sha256"] and
                sha256(root / receipt["native_log_relative_path"]) == receipt["native_log_sha256"]
                for receipt in protocol["recovery"]["parent_failure_receipts"]),
    }
    if not args.inputs_only:
        progress = json.loads((root / "batch_progress.json").read_text(encoding="utf-8"))
        complete = progress.get("completed", [])
        expected = {(ARM, graph_id, 0) for graph_id in protocol["graph_sha256"]}
        observed = {(row.get("arm"), row.get("graph_id"), row.get("seed")) for row in complete}
        core_by_cpu = {row["cpu"]: (row["package"], row["core"])
                       for row in progress.get("physical_cores", [])}
        results_match_launch = all(result_matches_launch(root, row) for row in complete)
        cpu_max = progress.get("cgroup", {}).get("cpu_max", "")
        try:
            quota, period = map(int, cpu_max.split())
            cpu_quota_is_24 = quota == 24 * period
        except (TypeError, ValueError):
            cpu_quota_is_24 = False
        checks.update({
            "eight_independently_audited_positions":
                independent_report.get("status") == "complete_valid" and
                independent_report.get("audited_valid_positions") == 8,
            "batch_all_eight_completed": progress.get("status") == "complete" and
                progress.get("scheduled") == progress.get("launched") == progress.get("finished") == 8 and
                len(complete) == 8 and not progress.get("failed") and not progress.get("active") and
                observed == expected,
            "batch_frozen_protocol_and_manifest":
                progress.get("registration_sha256") == PROTOCOL_SHA256 and
                progress.get("graph_manifest_sha256") == MANIFEST_SHA256,
            "batch_launcher_hash":
                progress.get("launcher_sha256") == sha256(root / "launch_contingency.py"),
            "one_line_native_850_runner_variant":
                sha256(root / "implementations/run_stablesolver.py") ==
                protocol["method_identity"][ARM]["runner_sha256"] and
                protocol["method_time_controls"][ARM] ==
                "native --time-limit 850 seconds; outer end-to-end 900-second cap" and
                all(json.loads((root / "results" / ARM /
                                (row["graph_id"] + f"__s{row['seed']:04d}.json")).read_text(encoding="utf-8"))
                    .get("config", {}).get("native_time_limit_seconds") == 850.0
                    for row in complete),
            "results_match_launch_cpu_and_identity": results_match_launch,
            "actual_rlimit_nine_gib_for_every_position":
                progress.get("position_rlimit_as_bytes") == CAP_BYTES and
                all(row.get("rlimit_as_soft_bytes") == row.get("rlimit_as_hard_bytes") == CAP_BYTES
                    for row in complete),
            "same_72_gib_memory_and_24_core_cgroup":
                progress.get("cgroup", {}).get("memory_max_bytes") == 72 * 1024 ** 3 and
                cpu_quota_is_24,
            "at_most_eight_distinct_physical_cores":
                1 <= progress.get("workers", 0) <= MAX_WORKERS and
                len(core_by_cpu) == progress.get("workers") and
                len(set(core_by_cpu.values())) == progress.get("workers") and
                all(tuple(row.get("physical_core", [])) == core_by_cpu.get(row.get("cpu"))
                    for row in complete),
            "primary_batch_was_terminal_before_launch":
                progress.get("primary_batch_progress_sha256_at_launch") ==
                sha256(root.parent / "batch_progress.json"),
            "original_primary_failures_unchanged":
                all(sha256(root.parent / "results" / ARM / (receipt["graph_id"] + "__s0000.json")) ==
                    receipt["result_sha256"]
                    for receipt in protocol["recovery"]["parent_failure_receipts"]),
            "six_gib_terminal_and_independent_audit_incomplete_at_launch":
                six_gib_gate_was_valid(root, protocol, progress),
        })
    report = {
        "schema": "cipheur_stablesolver_local_search_9gib_native850_contingency_audit_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "inputs_only" if args.inputs_only else "complete_results",
        "protocol_sha256": PROTOCOL_SHA256,
        "manifest_sha256": MANIFEST_SHA256,
        "original_primary_protocol_sha256": protocol["recovery"]["parent_preregistration_sha256"],
        "independent_graph_and_certificate_audit": str(audit_path.relative_to(root)),
        "checks": checks,
        "status": "valid" if all(checks.values()) else "invalid",
        "independent_auditor_stdout": independent.stdout.strip(),
        "independent_auditor_stderr": independent.stderr.strip(),
    }
    path = analysis / "recovery_execution_audit.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "mode": report["mode"], "checks": checks},
                     separators=(",", ":")))
    if report["status"] != "valid":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
