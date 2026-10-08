#!/usr/bin/env python3
"""Independent four-arm audit and exact-integer summary for the 900 s study.

Reads only the new experiment root. NPZ weights and edges are inspected directly,
without importing any optimizer graph or feasibility implementation.
"""
import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import numpy as np

ARMS = ("codex", "knn", "linucb", "chils_p4_custom")
TPS = 1_000_000


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def dump_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    temp.replace(path)


def dump_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["empty"]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def seconds_string(ticks):
    if ticks is None:
        return None
    return format(Decimal(ticks.numerator) / Decimal(ticks.denominator) / Decimal(TPS), "f")


def mean_exact(values):
    return Fraction(sum(values), len(values)) if values else None


def graph_arrays(path, expected_sha256):
    digest = sha256(path)
    if digest != expected_sha256:
        raise ValueError("Graph SHA256 differs from preregistration: " + path.name)
    with np.load(path, allow_pickle=False) as z:
        weights = z["weight_ticks"].astype(np.int64, copy=True)
        edge_u = z["edge_u"].astype(np.int32, copy=True)
        edge_v = z["edge_v"].astype(np.int32, copy=True)
    if len(edge_u) != len(edge_v) or len(weights) == 0 or np.any(weights <= 0):
        raise ValueError("Malformed graph arrays: " + path.name)
    return weights, edge_u, edge_v


def check_selected(result, arrays, arm):
    weights, edge_u, edge_v = arrays
    key = "selected_zero_based" if arm == "chils_p4_custom" else "selected"
    selected = result.get(key)
    errors = []
    if not isinstance(selected, list):
        return ["missing_selected_list"], None
    if any(type(v) is not int for v in selected):
        return ["noninteger_selected_id"], None
    if len(selected) != len(set(selected)):
        errors.append("duplicate_selected_id")
    if any(v < 0 or v >= len(weights) for v in selected):
        errors.append("selected_id_out_of_range")
        return errors, None
    chosen = np.zeros(len(weights), dtype=np.bool_)
    chosen[selected] = True
    if bool(np.any(chosen[edge_u] & chosen[edge_v])):
        errors.append("selected_graph_conflict")
    recomputed = sum(int(weights[v]) for v in selected)
    if type(result.get("value_ticks")) is not int:
        errors.append("noninteger_value_ticks")
    elif recomputed != result["value_ticks"]:
        errors.append("integer_objective_mismatch")
    return errors, recomputed


def discover_results(root, arm):
    folder = root / ("chils_p4/results" if arm == "chils_p4_custom" else f"results/{arm}")
    if not folder.exists():
        return [], folder
    return sorted(folder.glob("*.json")), folder


def expected_identity(protocol):
    return {(arm, graph_id, seed) for arm in ARMS for graph_id in protocol["graph_sha256"]
            for seed in protocol["seeds"]}


def validate_position(result, arm, graph_id, seed, graph_hash, arrays, protocol_hash, provenance):
    errors = []
    if result.get("graph_id") != graph_id or result.get("seed") != seed:
        errors.append("identity_mismatch")
    if result.get("status") != "ok":
        errors.append("status_not_ok")
    if result.get("feasible") is not True:
        errors.append("result_feasible_flag_not_true")
    if result.get("input_sha256") != graph_hash:
        errors.append("result_graph_hash_mismatch")
    if arm in ("knn", "linucb"):
        if result.get("mode") != arm or result.get("arm") != arm:
            errors.append("selector_mismatch")
        if result.get("phase") != "seed_replication_900s_20261009":
            errors.append("phase_mismatch")
        if result.get("protocol_sha256") != protocol_hash:
            errors.append("protocol_hash_mismatch")
        if result.get("source_patch_record_sha256") != provenance.get("source_patch_record_sha256"):
            errors.append("source_patch_record_hash_mismatch")
        if result.get("runner_sha256") != provenance.get("controls_runner_sha256"):
            errors.append("controls_runner_hash_mismatch")
    elif arm == "codex":
        if result.get("mode") != "llm":
            errors.append("codex_mode_not_llm")
        if result.get("phase") != "seed_replication_900s_20261009" or result.get("arm") != "codex":
            errors.append("codex_identity_or_phase_mismatch")
        if result.get("source_patch_sha256") != provenance.get("patched_contracts_sha256"):
            errors.append("codex_source_patch_hash_mismatch")
        if result.get("launch_registration_sha256") != provenance.get("codex_launch_sha256"):
            errors.append("codex_launch_hash_mismatch")
        if result.get("requested_model") != "gpt-6-luna":
            errors.append("codex_model_label_mismatch")
        if type(result.get("deployment_llm_calls")) is not int or result["deployment_llm_calls"] < 1:
            errors.append("no_recorded_codex_model_call")
        if type(result.get("valid_model_proposals")) is not int or result["valid_model_proposals"] < 1:
            errors.append("no_valid_codex_plan_proposal")
    else:
        if result.get("arm") != "CHILS-p4-original-step10" or result.get("method") != "original":
            errors.append("chils_method_mismatch")
        if result.get("registration_sha256") != provenance.get("chils_registration_sha256"):
            errors.append("chils_registration_hash_mismatch")
    config = result.get("config") or {}
    if arm == "chils_p4_custom":
        if config.get("seconds") != 900 or config.get("population") != 4 or config.get("threads") != 1 or config.get("step") != 10:
            errors.append("chils_configuration_mismatch")
        if config.get("seed") != seed:
            errors.append("chils_seed_mismatch")
    else:
        if result.get("target_seconds") != 900 or config.get("seconds") != 900:
            errors.append("c05_budget_mismatch")
        if config.get("population") != 4 or config.get("max_calls") != 21:
            errors.append("c05_population_or_call_cap_mismatch")
        if config.get("first_decision") != 20 or config.get("decision_interval") != 40:
            errors.append("c05_decision_cadence_mismatch")
    if not isinstance(result.get("wall_seconds"), (int, float)) or result["wall_seconds"] <= 0:
        errors.append("wall_seconds_invalid")
    selection_errors, recomputed = check_selected(result, arrays, arm)
    errors.extend(selection_errors)
    return errors, recomputed


def _archive_entries(manifest):
    if isinstance(manifest, list):
        return manifest
    for key in ("entries", "records", "calls", "archives"):
        if isinstance(manifest.get(key), list):
            return manifest[key]
    raise ValueError("Archive manifest needs a list of entries")


def audit_archive(archive_manifest, codex_positions, archive_dir=None):
    if archive_manifest is None:
        return {"provided": False, "note": "Pass --archive-manifest after relay archive export."}
    manifest = json.loads(archive_manifest.read_text(encoding="utf-8"))
    entries = _archive_entries(manifest)
    by_id = {}
    duplicate_ids = []
    by_snapshot = defaultdict(list)
    by_position_call = defaultdict(list)
    total_bytes = 0
    archive_file_errors = []
    for entry in entries:
        request_id = entry.get("request_id")
        response_id = entry.get("response_id") or ("codex-c05-relay-" + request_id if isinstance(request_id, str) else None)
        if not response_id:
            continue
        if response_id in by_id:
            duplicate_ids.append(response_id)
        by_id[response_id] = entry
        snapshot = entry.get("snapshot_id")
        if snapshot is not None:
            by_snapshot[snapshot].append(entry)
        if entry.get("graph_id") is not None and entry.get("seed") is not None and entry.get("call_index") is not None:
            by_position_call[(entry["graph_id"], entry["seed"], entry["call_index"])].append(entry)
        total_bytes += int(entry.get("compressed_bytes") or 0)
        if archive_dir is not None:
            name = entry.get("archive_file")
            expected_hash = entry.get("archive_file_sha256")
            if not isinstance(name, str) or Path(name).name != name or not isinstance(expected_hash, str):
                archive_file_errors.append({"request_id": request_id, "error": "unsafe_or_missing_file_metadata"})
            else:
                archive_path = archive_dir / name
                if not archive_path.exists():
                    archive_file_errors.append({"request_id": request_id, "error": "archive_file_missing"})
                elif sha256(archive_path) != expected_hash:
                    archive_file_errors.append({"request_id": request_id, "error": "archive_file_sha256_mismatch"})
                elif archive_path.stat().st_size != entry.get("compressed_bytes"):
                    archive_file_errors.append({"request_id": request_id, "error": "compressed_byte_count_mismatch"})
                else:
                    with gzip.open(archive_path, "rb") as stream:
                        uncompressed = sum(len(block) for block in iter(lambda: stream.read(1 << 20), b""))
                    if uncompressed != entry.get("uncompressed_bytes"):
                        archive_file_errors.append({"request_id": request_id, "error": "uncompressed_byte_count_mismatch"})
    links, missing, snapshot_mismatch, identity_mismatch = [], [], [], []
    used = set()
    for (graph_id, seed), result in codex_positions.items():
        for call in result.get("model_calls") or []:
            receipt = call.get("receipt") or {}
            response_id = receipt.get("response_id")
            prompt = call.get("prompt") or {}
            observation = prompt.get("observation") or {}
            snapshot_id = observation.get("snapshot_id")
            call_index = call.get("index", call.get("call_index"))
            entry = by_id.get(response_id) if response_id else None
            mapping = "response_id" if entry is not None else None
            if entry is None and snapshot_id is not None:
                candidates = [x for x in by_snapshot.get(snapshot_id, []) if (x.get("response_id") or ("codex-c05-relay-" + x.get("request_id", ""))) not in used]
                if len(candidates) == 1:
                    entry = candidates[0]
                    mapping = "unique_snapshot_id"
            if entry is None:
                candidates = [x for x in by_position_call.get((graph_id, seed, call_index), [])
                              if (x.get("response_id") or ("codex-c05-relay-" + x.get("request_id", ""))) not in used]
                if len(candidates) == 1:
                    entry = candidates[0]
                    mapping = "graph_seed_call_index"
            if entry is None:
                missing.append({"graph_id": graph_id, "seed": seed, "call_index": call_index,
                                "response_id": response_id, "snapshot_id": snapshot_id,
                                "disposition": call.get("disposition")})
                continue
            matched_id = entry.get("response_id") or ("codex-c05-relay-" + entry["request_id"])
            used.add(matched_id)
            if snapshot_id is not None and entry.get("snapshot_id") not in (None, snapshot_id):
                snapshot_mismatch.append({"graph_id": graph_id, "seed": seed, "call_index": call_index,
                                          "result_snapshot": snapshot_id, "archive_snapshot": entry.get("snapshot_id")})
            if (entry.get("graph_id"), entry.get("seed"), entry.get("call_index")) != (graph_id, seed, call_index):
                identity_mismatch.append({"graph_id": graph_id, "seed": seed, "call_index": call_index,
                                          "archive_graph_id": entry.get("graph_id"),
                                          "archive_seed": entry.get("seed"),
                                          "archive_call_index": entry.get("call_index")})
            links.append({"graph_id": graph_id, "seed": seed, "call_index": call_index,
                          "response_id": matched_id, "request_id": entry.get("request_id"),
                          "snapshot_id": snapshot_id, "mapping": mapping,
                          "archive_sha256": entry.get("archive_file_sha256") or entry.get("sha256") or entry.get("file_sha256"),
                          "compressed_bytes": entry.get("compressed_bytes"),
                          "uncompressed_bytes": entry.get("uncompressed_bytes"),
                          "archive_status": entry.get("status")})
    return {"provided": True, "manifest_path": str(archive_manifest),
            "manifest_sha256": sha256(archive_manifest), "archive_entries": len(entries),
            "total_compressed_bytes": total_bytes, "linked_calls": len(links),
            "unlinked_result_calls": missing, "snapshot_mismatches": snapshot_mismatch,
            "identity_mismatches": identity_mismatch,
            "duplicate_response_ids": duplicate_ids,
            "archive_file_errors": archive_file_errors,
            "archive_files_verified": archive_dir is not None,
            "archive_entries_without_result_link": sorted(set(by_id) - used), "links": links}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--archive-manifest", type=Path)
    parser.add_argument("--archive-dir", type=Path,
                        help="private raw-I/O directory; verify every gzip byte count and SHA256")
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    protocol_path = args.protocol.resolve(strict=True) if args.protocol else root / "registrations/preregistration.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol.get("schema") != "cipheur_c05_900s_preregistration_v1":
        raise ValueError("Unexpected preregistration schema")
    if args.data_dir is None:
        parser.error("--data-dir is required")
    data_dir = args.data_dir.resolve(strict=True)
    arrays = {}
    for graph_id, digest in protocol["graph_sha256"].items():
        arrays[graph_id] = graph_arrays(data_dir / (graph_id.split("__", 1)[1] + ".npz"), digest)
    expected = expected_identity(protocol)
    seen = {}
    extra = []
    parse_errors = []
    rows = []
    codex_positions = {}
    protocol_hash = sha256(protocol_path)
    source_patch_record = root / "registrations/c05_source_patch.json"
    controls_registration = root / "registrations/c05_controls_900s.json"
    codex_launch = root / "registrations/codex_900_launch.json"
    chils_registration = root / "chils_p4/registration.json"
    provenance = {
        "source_patch_record_sha256": sha256(source_patch_record) if source_patch_record.exists() else None,
        "patched_contracts_sha256": json.loads(source_patch_record.read_text(encoding="utf-8")).get("patched_contracts_sha256")
        if source_patch_record.exists() else None,
        "controls_runner_sha256": json.loads(controls_registration.read_text(encoding="utf-8")).get("runner_sha256")
        if controls_registration.exists() else None,
        "codex_launch_sha256": sha256(codex_launch) if codex_launch.exists() else None,
        "chils_registration_sha256": sha256(chils_registration) if chils_registration.exists() else None,
    }
    for arm in ARMS:
        paths, folder = discover_results(root, arm)
        for path in paths:
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                parse_errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
                continue
            graph_id, seed = result.get("graph_id"), result.get("seed")
            key = (arm, graph_id, seed)
            if key not in expected:
                extra.append({"path": str(path), "arm": arm, "graph_id": graph_id, "seed": seed})
                continue
            if key in seen:
                extra.append({"path": str(path), "error": "duplicate_position", "arm": arm,
                              "graph_id": graph_id, "seed": seed})
                continue
            seen[key] = path
            errors, recomputed = validate_position(result, arm, graph_id, seed,
                                                   protocol["graph_sha256"][graph_id], arrays[graph_id], protocol_hash,
                                                   provenance)
            if arm == "codex":
                codex_positions[(graph_id, seed)] = result
            rows.append({"arm": arm, "graph_id": graph_id, "seed": seed, "path": str(path),
                         "result_sha256": sha256(path), "status": result.get("status"),
                         "audited_ok": not errors, "audit_errors": "|".join(errors),
                         "value_ticks": result.get("value_ticks"), "recomputed_value_ticks": recomputed,
                         "contact_seconds": seconds_string(Fraction(result["value_ticks"])) if type(result.get("value_ticks")) is int else None,
                         "feasible_reported": result.get("feasible"), "wall_seconds": result.get("wall_seconds"),
                         "cpu_seconds": result.get("cpu_seconds"),
                         "model_calls": result.get("deployment_llm_calls") if arm == "codex" else None,
                         "valid_model_proposals": result.get("valid_model_proposals") if arm == "codex" else None,
                         "native_library_sha256": result.get("native_library_sha256") if arm == "chils_p4_custom" else result.get("native_sha256"),
                         "input_sha256": result.get("input_sha256")})
    missing = [{"arm": arm, "graph_id": graph_id, "seed": seed} for arm, graph_id, seed in sorted(expected - set(seen))]
    valid = {(row["arm"], row["graph_id"], row["seed"]): row for row in rows if row["audited_ok"]}
    methods = []
    by_view = []
    for arm in ARMS:
        values = [valid[(arm, graph_id, seed)]["value_ticks"]
                  for graph_id in protocol["graph_sha256"] for seed in protocol["seeds"]
                  if (arm, graph_id, seed) in valid]
        valid_rows = [valid[(arm, graph_id, seed)] for graph_id in protocol["graph_sha256"]
                      for seed in protocol["seeds"] if (arm, graph_id, seed) in valid]
        walls = [x["wall_seconds"] for x in valid_rows if isinstance(x.get("wall_seconds"), (int, float))]
        cpus = [x["cpu_seconds"] for x in valid_rows if isinstance(x.get("cpu_seconds"), (int, float))]
        mean = mean_exact(values)
        methods.append({"arm": arm, "valid_positions": len(values), "expected_positions": 40,
                        "mean_ticks_exact": str(mean) if mean is not None else None,
                        "mean_contact_seconds": seconds_string(mean),
                        "min_contact_seconds": seconds_string(Fraction(min(values))) if values else None,
                        "max_contact_seconds": seconds_string(Fraction(max(values))) if values else None,
                        "mean_wall_seconds": sum(walls) / len(walls) if walls else None,
                        "max_wall_seconds": max(walls) if walls else None,
                        "mean_cpu_seconds": sum(cpus) / len(cpus) if cpus else None,
                        "total_model_calls": sum(x["model_calls"] for x in valid_rows if type(x.get("model_calls")) is int)
                        if arm == "codex" else None,
                        "total_valid_model_proposals": sum(x["valid_model_proposals"] for x in valid_rows
                                                           if type(x.get("valid_model_proposals")) is int)
                        if arm == "codex" else None})
        for graph_id in protocol["graph_sha256"]:
            group = [valid[(arm, graph_id, seed)]["value_ticks"] for seed in protocol["seeds"]
                     if (arm, graph_id, seed) in valid]
            by_view.append({"arm": arm, "graph_id": graph_id, "valid_seeds": len(group),
                            "mean_contact_seconds": seconds_string(mean_exact(group))})
    pair_rows = []
    pair_summary = []
    for other in ARMS[1:]:
        diffs = []
        for graph_id in protocol["graph_sha256"]:
            for seed in protocol["seeds"]:
                a, b = valid.get(("codex", graph_id, seed)), valid.get((other, graph_id, seed))
                if a is None or b is None:
                    continue
                delta = a["value_ticks"] - b["value_ticks"]
                diffs.append(delta)
                pair_rows.append({"graph_id": graph_id, "seed": seed, "reference": "codex",
                                  "comparison": other, "codex_ticks": a["value_ticks"],
                                  "comparison_ticks": b["value_ticks"], "delta_ticks": delta,
                                  "delta_contact_seconds": seconds_string(Fraction(delta))})
        pair_summary.append({"reference": "codex", "comparison": other, "paired_positions": len(diffs),
                             "mean_delta_ticks_exact": str(mean_exact(diffs)) if diffs else None,
                             "mean_delta_contact_seconds": seconds_string(mean_exact(diffs)),
                             "wins": sum(x > 0 for x in diffs), "ties": sum(x == 0 for x in diffs),
                             "losses": sum(x < 0 for x in diffs)})
    archive = audit_archive(args.archive_manifest.resolve(strict=True) if args.archive_manifest else None,
                            codex_positions,
                            args.archive_dir.resolve(strict=True) if args.archive_dir else None)
    complete = len(valid) == len(expected) and not missing and not extra and not parse_errors
    summary = {"schema": "cipheur_c05_900s_independent_audit_v1",
               "generated_at_utc": datetime.now(timezone.utc).isoformat(),
               "protocol_sha256": protocol_hash, "expected_positions": len(expected),
               "registration_hashes": provenance,
               "saved_positions": len(seen), "audited_valid_positions": len(valid),
               "complete_valid_frame": complete,
               "missing_positions": missing, "extra_or_duplicate_positions": extra,
               "parse_errors": parse_errors,
               "invalid_positions": [{"arm": x["arm"], "graph_id": x["graph_id"], "seed": x["seed"],
                                      "errors": x["audit_errors"]} for x in rows if not x["audited_ok"]],
               "method_summaries": methods, "by_view": by_view,
               "codex_paired_summaries": pair_summary,
               "native_hashes_by_arm": {arm: dict(Counter(x["native_library_sha256"] for x in rows if x["arm"] == arm)) for arm in ARMS},
               "codex_archive": archive}
    out = root / "analysis"
    dump_json(out / "c05_900s_independent_audit.json", summary)
    dump_csv(out / "c05_900s_positions.csv", rows)
    dump_csv(out / "c05_900s_by_view.csv", by_view)
    dump_csv(out / "c05_900s_paired_deltas.csv", pair_rows)
    dump_csv(out / "c05_900s_archive_links.csv", archive.get("links", []))
    print(json.dumps({"expected": len(expected), "saved": len(seen), "valid": len(valid),
                      "complete_valid_frame": complete, "methods": methods,
                      "archive_entries": archive.get("archive_entries"),
                      "linked_model_calls": archive.get("linked_calls")},
                     ensure_ascii=False, allow_nan=False))
    if not complete and not args.allow_incomplete:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
