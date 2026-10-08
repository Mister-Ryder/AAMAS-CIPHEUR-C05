#!/usr/bin/env python3
"""Build a public metrics package from a completed private 900 s audit.

The private solver JSON files and model I/O archives are never read or copied.
Only an explicit allowlist of audited fields is exported.
"""

import argparse
import csv
import hashlib
import json
from pathlib import Path


ARMS = ("codex", "knn", "linucb", "chils_p4_custom")
POSITION_FIELDS = (
    "arm", "graph_id", "seed", "result_sha256", "status", "audited_ok",
    "value_ticks", "recomputed_value_ticks", "contact_seconds",
    "feasible_reported", "wall_seconds", "cpu_seconds", "model_calls",
    "valid_model_proposals", "native_library_sha256", "input_sha256",
)
VIEW_FIELDS = ("arm", "graph_id", "valid_seeds", "mean_contact_seconds")
PAIR_FIELDS = (
    "graph_id", "seed", "reference", "comparison", "codex_ticks",
    "comparison_ticks", "delta_ticks", "delta_contact_seconds",
)
METHOD_FIELDS = (
    "arm", "valid_positions", "expected_positions", "mean_ticks_exact",
    "mean_contact_seconds", "min_contact_seconds", "max_contact_seconds",
    "mean_wall_seconds", "max_wall_seconds", "mean_cpu_seconds",
    "total_model_calls", "total_valid_model_proposals",
)
PAIR_SUMMARY_FIELDS = (
    "reference", "comparison", "paired_positions", "mean_delta_ticks_exact",
    "mean_delta_contact_seconds", "wins", "ties", "losses",
)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows, fields):
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row[field] for field in fields} for row in rows)


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def only(row, fields):
    return {field: row[field] for field in fields}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--positions", required=True, type=Path)
    parser.add_argument("--by-view", required=True, type=Path)
    parser.add_argument("--paired-deltas", required=True, type=Path)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    paths = {key: getattr(args, key.replace("-", "_" )).resolve(strict=True)
             for key in ("audit", "positions", "by-view", "paired-deltas", "protocol")}
    audit = json.loads(paths["audit"].read_text(encoding="utf-8"))
    protocol = json.loads(paths["protocol"].read_text(encoding="utf-8"))
    rows = read_csv(paths["positions"])
    views = read_csv(paths["by-view"])
    pairs = read_csv(paths["paired-deltas"])

    if audit.get("complete_valid_frame") is not True or audit.get("audited_valid_positions") != 160:
        raise ValueError("The full 160-position strict audit has not passed")
    if audit.get("protocol_sha256") != digest(paths["protocol"]):
        raise ValueError("Protocol hash differs from the private audit")
    if len(rows) != 160 or len(views) != 32 or len(pairs) != 120:
        raise ValueError("Audited output cardinality differs from the registered frame")
    expected = {(arm, graph_id, str(seed)) for arm in ARMS
                for graph_id in protocol["graph_sha256"] for seed in protocol["seeds"]}
    observed = {(row["arm"], row["graph_id"], row["seed"]) for row in rows}
    if observed != expected:
        raise ValueError("Audited output identities differ from the registered frame")
    if any(row["audited_ok"].lower() != "true" or row["status"] != "ok" for row in rows):
        raise ValueError("At least one position did not pass audit")
    if any(row["value_ticks"] != row["recomputed_value_ticks"] for row in rows):
        raise ValueError("At least one integer objective differs from recomputation")
    archive = audit.get("codex_archive") or {}
    if not (archive.get("provided") is True and archive.get("archive_files_verified") is True):
        raise ValueError("Private model I/O files have not been verified")
    for key in ("unlinked_result_calls", "snapshot_mismatches", "identity_mismatches",
                "duplicate_response_ids", "archive_file_errors", "archive_entries_without_result_link"):
        if archive.get(key):
            raise ValueError("Private model I/O linkage has errors: " + key)
    if archive.get("linked_calls") != archive.get("archive_entries"):
        raise ValueError("Not every archived Codex call links to a result record")

    out = args.out_dir.resolve()
    if out.exists():
        raise FileExistsError("Output directory already exists: " + str(out))
    main_out, controls_out = out / "main_results", out / "controls_results"
    main_out.mkdir(parents=True)
    controls_out.mkdir()
    write_csv(main_out / "positions.csv", [row for row in rows if row["arm"] == "codex"],
              POSITION_FIELDS)
    write_csv(main_out / "by_view.csv", [row for row in views if row["arm"] == "codex"],
              VIEW_FIELDS)
    write_csv(controls_out / "positions.csv", [row for row in rows if row["arm"] != "codex"],
              POSITION_FIELDS)
    write_csv(controls_out / "by_view.csv", [row for row in views if row["arm"] != "codex"],
              VIEW_FIELDS)
    write_csv(controls_out / "paired_deltas.csv", pairs, PAIR_FIELDS)
    common = {
        "protocol_sha256": audit["protocol_sha256"],
        "private_audit_sha256": digest(paths["audit"]),
        "private_positions_csv_sha256": digest(paths["positions"]),
        "private_by_view_csv_sha256": digest(paths["by-view"]),
        "private_paired_deltas_csv_sha256": digest(paths["paired-deltas"]),
        "expected_positions": audit["expected_positions"],
        "audited_valid_positions": audit["audited_valid_positions"],
        "complete_valid_frame": audit["complete_valid_frame"],
    }
    main_audit = {
        "schema": "cipheur_c05_900s_public_codex_audit_v1",
        **common,
        "branch": "main",
        "registration_hashes": {key: audit["registration_hashes"][key] for key in
                                ("source_patch_record_sha256", "patched_contracts_sha256",
                                 "codex_launch_sha256")},
        "method_summary": only(next(row for row in audit["method_summaries"]
                                    if row["arm"] == "codex"), METHOD_FIELDS),
        "native_hashes": audit["native_hashes_by_arm"]["codex"],
        "private_model_io_manifest_sha256": archive["manifest_sha256"],
        "model_io_archive_entries": archive["archive_entries"],
        "model_io_compressed_bytes": archive["total_compressed_bytes"],
        "model_io_calls_linked": archive["linked_calls"],
        "model_io_files_verified": archive["archive_files_verified"],
    }
    controls_audit = {
        "schema": "cipheur_c05_900s_public_controls_audit_v1",
        **common,
        "branch": "non-llm-controls",
        "registration_hashes": audit["registration_hashes"],
        "method_summaries": [only(row, METHOD_FIELDS) for row in audit["method_summaries"]
                             if row["arm"] != "codex"],
        "codex_paired_summaries": [only(row, PAIR_SUMMARY_FIELDS)
                                    for row in audit["codex_paired_summaries"]],
        "native_hashes_by_arm": {arm: audit["native_hashes_by_arm"][arm]
                                 for arm in ARMS if arm != "codex"},
    }
    write_json(main_out / "audit_public.json", main_audit)
    write_json(controls_out / "audit_public.json", controls_audit)
    write_json(main_out / "SHA256SUMS.json", {
        name: digest(main_out / name) for name in ("positions.csv", "by_view.csv", "audit_public.json")
    })
    write_json(controls_out / "SHA256SUMS.json", {
        name: digest(controls_out / name) for name in
        ("positions.csv", "by_view.csv", "paired_deltas.csv", "audit_public.json")
    })
    print(json.dumps({"audited_positions": len(rows), "codex_public_positions": 40,
                      "control_public_positions": 120, "public_dir": str(out),
                      "private_audit_sha256": common["private_audit_sha256"]}))


if __name__ == "__main__":
    main()
