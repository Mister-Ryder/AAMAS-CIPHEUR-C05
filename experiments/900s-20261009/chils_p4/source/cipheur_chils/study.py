"""Source-grouped manifests, matched experiments and an explicit freeze boundary."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
import hashlib
import json
import random
import time
from .graph import load_graph, sha256_file
from .native import RunConfig, run_instance
from .dsl import Program
from .certificates import read_jsonl, assess_program


def dump_json(path, obj):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")


def canonical_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def source_code_hash():
    root = Path(__file__).resolve().parents[1]
    files = sorted([*root.joinpath("cipheur_chils").glob("*.py"), *root.joinpath("native").glob("*.[ch]"),
                    *root.joinpath("third_party/CHILS").rglob("*.[ch]"), root/"scripts"/"build_native.py"])
    return canonical_hash({str(p.relative_to(root)): sha256_file(p) for p in files})


def load_manifest(path):
    path = Path(path).resolve()
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema") != 1 or not isinstance(obj.get("graphs"), list) or not obj["graphs"]:
        raise ValueError("Manifest requires schema=1 and nonempty graphs")
    rows, names, source_splits, hashes = [], set(), {}, {}
    for raw in obj["graphs"]:
        if not {"id", "path", "source_id", "split"} <= raw.keys():
            raise ValueError("Each graph needs id,path,source_id,split")
        row = dict(raw)
        if row["id"] in names or row["split"] not in {"train", "val", "test"} or not row["source_id"]:
            raise ValueError("Duplicate id or invalid source/split")
        names.add(row["id"])
        if row["source_id"] in source_splits and source_splits[row["source_id"]] != row["split"]:
            raise ValueError("Physical source crosses split boundary")
        source_splits[row["source_id"]] = row["split"]
        graph_path = (path.parent/row["path"]).resolve()
        h = sha256_file(graph_path)
        if row.get("sha256") and row["sha256"] != h: raise ValueError("Graph bytes changed")
        if h in hashes: raise ValueError("Duplicate graph bytes in manifest; do not multiply identical samples")
        hashes[h] = row["split"]
        row.update(path=str(graph_path), sha256=h)
        rows.append(row)
    return {"schema": 1, "graphs": rows, "manifest_sha256": sha256_file(path), "manifest_path": str(path)}


def collect(manifest, output, config, seeds):
    data = load_manifest(manifest); output = Path(output)
    if output.exists(): raise FileExistsError("Use a new snapshot file; old evidence is not overwritten")
    rows = []
    for graph in data["graphs"]:
        if graph["split"] != "train": continue
        for seed in seeds:
            receipt = run_instance(graph["path"], "observe", replace(config, seed=seed),
                       snapshot_output=output, split="train", source_id=graph["source_id"])
            receipt.update(graph_id=graph["id"], source_id=graph["source_id"], split="train")
            rows.append(receipt)
    if not rows: raise ValueError("Manifest has no TRAIN graphs")
    dump_json(str(output)+".collection.json", {"manifest": data, "runs": rows,
              "warning": "Instrumentation is charged; these are not untouched baseline timings."})
    return {"runs": len(rows), "snapshots": sum(r["collection_snapshots"] for r in rows)}


def read_program(path):
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    return Program(obj.get("program", obj))


def _run_position(graph, arm, cfg, program):
    started = time.perf_counter()
    try:
        receipt = run_instance(graph["path"], arm["method"], cfg, program)
        receipt["position_wall_seconds"] = time.perf_counter()-started
    except Exception as exc:
        receipt = {"status": "error", "value_ticks": None, "errors": [f"{type(exc).__name__}: {exc}"],
                   "wall_seconds": time.perf_counter()-started, "config": cfg.to_json()}
    receipt.update(graph_id=graph["id"], source_id=graph["source_id"], split=graph["split"],
                   arm=arm["name"], seed=cfg.seed, input_path=graph["path"], input_sha256=graph["sha256"])
    return receipt


def grouped_quality(rows):
    """Macro mean of per-source means, with full-member/full-seed completeness.

    Quality here is reward/total-input-weight, NOT an optimality gap.
    No failed or missing value is replaced by zero.
    """
    grouped = defaultdict(list)
    for row in rows: grouped[row["source_id"]].append(row)
    values, incomplete = {}, []
    for source, positions in sorted(grouped.items()):
        if any(r.get("status") != "ok" or r.get("value_ticks") is None for r in positions):
            incomplete.append(source); continue
        if any(r.get("total_weight_ticks", 0) <= 0 for r in positions):
            incomplete.append(source); continue
        vals = [Fraction(r["value_ticks"], r["total_weight_ticks"]) for r in positions]
        values[source] = str(sum(vals, Fraction())/len(vals))
    macro = sum((Fraction(x) for x in values.values()), Fraction())/len(values) if values and not incomplete else None
    return {"macro_quality_exact": str(macro) if macro is not None else None,
            "source_quality_exact": values, "incomplete_sources": incomplete,
            "source_count": len(grouped), "positions": len(rows)}


def evaluate_candidates(manifest, candidate_paths, output, config, seeds, certificates=None):
    out = Path(output); out.mkdir(parents=True, exist_ok=False)
    data = load_manifest(manifest)
    graphs = [r for r in data["graphs"] if r["split"] in {"train", "val"}]
    if {r["split"] for r in graphs} != {"train", "val"}: raise ValueError("Evaluation needs TRAIN and VAL")
    if certificates:
        from .certificates import snapshot_graph
        allowed = {(r["source_id"], str(Path(r["path"]).resolve())): r for r in graphs if r["split"] == "train"}
        for row in read_jsonl(certificates):
            key = (row["source_id"], str(Path(row["graph_path"]).resolve()))
            if key not in allowed:
                raise ValueError("Certificate source is not in this manifest's TRAIN split")
            snapshot_graph(row)
            if row["action_config"] != config.to_json()["actions"] or row["state"]["population"] != config.population:
                raise ValueError("Certificate decision boundary differs from requested deployment configuration")
    candidates = {}
    for path in candidate_paths:
        program = read_program(path)
        candidates.setdefault(program.fingerprint(), {"program": program.obj, "path": str(Path(path).resolve())})
    if not candidates: raise ValueError("No valid candidates")
    baselines = [{"name": "p1", "method": "original", "population": 1},
                 {"name": "p4", "method": "original", "population": 4},
                 {"name": "hand", "method": "hand", "population": config.population},
                 {"name": "random", "method": "random", "population": config.population}]
    arms = baselines + [{"name": k, "method": "dsl", "population": config.population} for k in candidates]
    positions = [(graph, arm, seed) for graph in graphs for seed in seeds for arm in arms]
    random.Random(1907).shuffle(positions)  # Prevent systematic all-baseline-first thermal/order effects.
    results = []
    with (out/"runs.jsonl").open("x", encoding="utf-8") as f:
        for graph, arm, seed in positions:
            program = Program(candidates[arm["name"]]["program"]) if arm["name"] in candidates else None
            cfg = replace(config, seed=seed, population=arm["population"])
            row = _run_position(graph, arm, cfg, program)
            f.write(json.dumps(row)+"\n"); f.flush(); results.append(row)
    summaries = {arm["name"]: {split: grouped_quality([r for r in results if r["arm"] == arm["name"] and r["split"] == split])
                              for split in ("train", "val")} for arm in arms}
    for key, item in candidates.items():
        try: item["certificate_assessment"] = assess_program(Program(item["program"]), certificates) if certificates else None
        except Exception as exc: item["certificate_assessment"] = {"error": f"{type(exc).__name__}: {exc}"}
    result = {"schema": 1, "manifest": data, "config": config.to_json(), "seeds": seeds,
              "source_code_sha256": source_code_hash(), "candidates": candidates, "summaries": summaries,
              "certificate_sha256": sha256_file(certificates) if certificates else None,
              "runs_sha256": sha256_file(out/"runs.jsonl"), "selection_excludes_test": True}
    dump_json(out/"evaluation.json", result)
    return {"positions": len(results), "candidates": len(candidates), "evaluation": str(out/"evaluation.json")}


def freeze(evaluation, output, min_strict_fit=1.0, allow_uninformative=False):
    """Gate on TRAIN certificates, select by complete VAL schedule quality.

    No automatic fallback is renamed a certified LLM winner. The explicit
    allow_uninformative option creates a DIAGNOSTIC artifact, never a claim.
    """
    if not 0 <= min_strict_fit <= 1: raise ValueError("min_strict_fit must be in [0,1]")
    obj = json.loads(Path(evaluation).read_text(encoding="utf-8"))
    if obj["source_code_sha256"] != source_code_hash(): raise ValueError("Code changed since candidate evaluation")
    raw_runs = Path(evaluation).parent/"runs.jsonl"
    if sha256_file(raw_runs) != obj["runs_sha256"]:
        raise ValueError("Raw evaluation runs changed")
    actual_rows = list(read_jsonl(raw_runs))
    for arm, splits in obj["summaries"].items():
        for split, recorded in splits.items():
            expected = grouped_quality([r for r in actual_rows if r["arm"] == arm and r["split"] == split])
            if expected != recorded: raise ValueError("Evaluation summary differs from raw runs")
    eligible, rejected = [], {}
    for key, item in obj["candidates"].items():
        report = item.get("certificate_assessment") or {}
        train = obj["summaries"][key]["train"]["macro_quality_exact"]
        val = obj["summaries"][key]["val"]["macro_quality_exact"]
        informative = report.get("strict_total", 0) > 0
        gate = informative and report.get("quotient", {}).get("acyclic", False) and report.get("strict_fit", 0)/report["strict_total"] >= min_strict_fit
        # An informative contradictory gate can never be bypassed by this option.
        permitted = gate or (allow_uninformative and not informative and "error" not in report)
        if train is None or val is None or not permitted:
            rejected[key] = {"complete_train": train is not None, "complete_val": val is not None,
                             "informative": informative, "gate_passed": gate}; continue
        eligible.append((Fraction(val), Fraction(train), -report.get("feature_operations", 0), key, gate))
    if not eligible:
        dump_json(str(output)+".rejected.json", rejected)
        raise ValueError("No eligible candidate. Retain this failed selection; refine TRAIN, not TEST.")
    _, _, _, selected, gate = max(eligible)
    val = Fraction(obj["summaries"][selected]["val"]["macro_quality_exact"])
    references = [Fraction(obj["summaries"][b]["val"]["macro_quality_exact"]) for b in ("p1", "p4", "hand", "random")
                  if obj["summaries"][b]["val"]["macro_quality_exact"] is not None]
    artifact = {"schema": 1, "frozen": True, "program": obj["candidates"][selected]["program"],
                "program_sha256": selected, "config": obj["config"], "source_code_sha256": obj["source_code_sha256"],
                "manifest_sha256": obj["manifest"]["manifest_sha256"],
                "dataset_sha256": canonical_hash([{k: r[k] for k in ("id", "source_id", "split", "sha256")} for r in obj["manifest"]["graphs"]]), "evaluation_sha256": sha256_file(evaluation),
                "certificate_sha256": obj["certificate_sha256"], "gate_passed": gate,
                "diagnostic_only": not gate, "min_strict_fit": min_strict_fit,
                "promotion_recommended": bool(gate and len(references) == 4 and val > max(references)),
                "selection": "TRAIN information gate, then VAL source-macro quality, TRAIN quality, feature cost, hash",
                "selected_summaries": obj["summaries"][selected], "rejected": rejected}
    artifact["artifact_sha256"] = canonical_hash(artifact)
    if Path(output).exists(): raise FileExistsError("Refusing to overwrite frozen artifact")
    dump_json(output, artifact)
    return {"program_sha256": selected, "gate_passed": gate, "promotion_recommended": artifact["promotion_recommended"]}


def check_frozen(path, manifest, config):
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    digest = obj.pop("artifact_sha256", None)
    if digest != canonical_hash(obj) or not obj.get("frozen"): raise ValueError("Invalid frozen artifact digest")
    if obj["source_code_sha256"] != source_code_hash(): raise ValueError("Code changed after freeze")
    if obj["manifest_sha256"] != manifest["manifest_sha256"]: raise ValueError("Manifest changed after freeze")
    dataset = canonical_hash([{k: r[k] for k in ("id", "source_id", "split", "sha256")} for r in manifest["graphs"]])
    if obj["dataset_sha256"] != dataset: raise ValueError("Dataset bytes or split assignments changed after freeze")
    # Time budgets and seed may be varied in a declared evaluation grid; the
    # decision mechanism cannot silently change after freeze.
    frozen, requested = dict(obj["config"]), config.to_json()
    for key in ("seconds", "seed", "threads", "cycles", "ls_iterations"):
        frozen.pop(key, None); requested.pop(key, None)
    if requested != frozen: raise ValueError("Decision/search configuration changed after freeze")
    program = Program(obj["program"])
    if program.fingerprint() != obj["program_sha256"]: raise ValueError("Policy digest mismatch")
    return program, obj


def benchmark(manifest, output, config, seeds, split="test", frozen_policy=None):
    data = load_manifest(manifest); out = Path(output); out.mkdir(parents=True, exist_ok=False)
    program, frozen = check_frozen(frozen_policy, data, config) if frozen_policy else (None, None)
    arms = [{"name": "CHILS-p1", "method": "original", "population": 1, "step": config.step},
            {"name": "CHILS-p4-positive", "method": "original", "population": 4, "step": config.step},
            {"name": "CHILS-p4-historical-s0", "method": "original", "population": 4, "step": 0.0},
            {"name": "CHILS-p4-default-interval", "method": "original", "population": 4, "step": 10.0},
            {"name": "observe-only", "method": "observe", "population": config.population, "step": config.step},
            {"name": "hand-unlock", "method": "hand", "population": config.population, "step": config.step},
            {"name": "random-unlock", "method": "random", "population": config.population, "step": config.step}]
    if program:
        arms += [{"name": label, "method": method, "population": config.population, "step": config.step}
                 for label, method in (("frozen-policy", "dsl"), ("permuted-scores", "permuted"), ("score-only", "score_only"))]
    graphs = [r for r in data["graphs"] if r["split"] == split]
    if not graphs: raise ValueError("Requested split has no graphs")
    positions = [(g, a, seed) for g in graphs for seed in seeds for a in arms]
    random.Random(2309).shuffle(positions)
    rows = []
    with (out/"runs.jsonl").open("x", encoding="utf-8") as f:
        for graph, arm, seed in positions:
            cfg = replace(config, seed=seed, population=arm["population"], step=arm["step"])
            row = _run_position(graph, arm, cfg, program)
            row["frozen_policy_origin"] = frozen["program"].get("provenance") if frozen else None
            f.write(json.dumps(row)+"\n"); f.flush(); rows.append(row)
    summary = {a["name"]: grouped_quality([r for r in rows if r["arm"] == a["name"]]) for a in arms}
    report = {"manifest_sha256": data["manifest_sha256"], "source_code_sha256": source_code_hash(),
              "split": split, "config": config.to_json(), "seeds": seeds, "arms": arms,
              "frozen_policy": frozen, "summaries": summary,
              "rows_sha256": sha256_file(out/"runs.jsonl"), "planned_positions": len(positions),
              "paired_source_relative_percent": {}}
    # Compare only identical complete physical-source cohorts. These descriptive
    # paired summaries do not treat graph variants/seeds as independent samples.
    if program:
        proposal = summary["frozen-policy"]["source_quality_exact"]
        for name in summary:
            if name == "frozen-policy": continue
            ref = summary[name]["source_quality_exact"]
            report["paired_source_relative_percent"][name] = {
                s: float(100*(Fraction(proposal[s])-Fraction(ref[s]))/Fraction(ref[s]))
                for s in sorted(proposal.keys() & ref.keys()) if Fraction(ref[s])}
    dump_json(out/"summary.json", report)
    return {"positions": len(rows), "output": str(out), "policy_origin": program.obj.get("provenance") if program else None}
