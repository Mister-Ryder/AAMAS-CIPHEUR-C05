"""Executable research workflow. Run python -m cipheur_chils --help."""
from __future__ import annotations
import argparse
from dataclasses import replace
from pathlib import Path
import json
import sys
import time
from .native import RunConfig, run_instance, METHODS
from .dsl import hand_program
from .certificates import certify_snapshots, assess_program
from .study import (dump_json, collect, evaluate_candidates, freeze, benchmark,
                    read_program, load_manifest)
from .authoring import enumerate_control, propose


def add_config(parser):
    parser.add_argument("--config", help="JSON RunConfig; omitted values have documented defaults")
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--population", type=int)
    parser.add_argument("--threads", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--step", type=float)
    parser.add_argument("--cycles", type=int)
    parser.add_argument("--ls-iterations", type=int)
    parser.add_argument("--stagnation", type=int)


def get_config(args):
    cfg = RunConfig.from_json(json.loads(Path(args.config).read_text())) if args.config else RunConfig()
    updates = {k: getattr(args, k) for k in ("seconds", "population", "threads", "seed", "step", "cycles", "ls_iterations", "stagnation")
               if getattr(args, k) is not None}
    return replace(cfg, **updates)


def main(argv=None):
    p = argparse.ArgumentParser(description="CIPHEUR–CHILS research implementation; no claimed scheduling advantage")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="One graph; includes preprocessing and original-graph verification")
    r.add_argument("--graph", required=True); r.add_argument("--method", choices=sorted(METHODS), default="original")
    r.add_argument("--program"); r.add_argument("--output", required=True); add_config(r)
    r = sub.add_parser("validate-manifest")
    r.add_argument("--manifest", required=True)
    r = sub.add_parser("hand-program")
    r.add_argument("--output", required=True)
    r = sub.add_parser("collect", help="TRAIN-only native pre-core population states")
    r.add_argument("--manifest", required=True); r.add_argument("--output", required=True)
    r.add_argument("--seeds", type=int, nargs="+", default=[2]); add_config(r)
    r = sub.add_parser("certify", help="Bounds on actual expanded-core actions, unknowns retained")
    r.add_argument("--snapshots", required=True); r.add_argument("--output", required=True)
    r.add_argument("--oracle-seconds", type=float, default=.05)
    r.add_argument("--oracle-nodes", type=int, default=20_000)
    r.add_argument("--oracle-vertices", type=int, default=180)
    r = sub.add_parser("assess", help="TRAIN quotient and strict-rule diagnostics")
    r.add_argument("--program", required=True); r.add_argument("--certificates", required=True)
    r.add_argument("--output", required=True)
    r = sub.add_parser("enumerate", help="Independent, predeclared non-LLM catalogue control")
    r.add_argument("--output", required=True)
    r = sub.add_parser("author", help="One auditable OFFLINE model proposal; no remote code execution")
    r.add_argument("--certificates", required=True); r.add_argument("--output", required=True)
    r.add_argument("--parent"); r.add_argument("--endpoint"); r.add_argument("--model")
    r.add_argument("--key-env", default="CIPHEUR_API_KEY"); r.add_argument("--response-file")
    r.add_argument("--evaluation-feedback", help="Development evaluation.json; only TRAIN metrics are included")
    r.add_argument("--feedback", choices=["witness", "relations", "none"], default="witness")
    r = sub.add_parser("evaluate", help="Matched actual TRAIN/VAL schedules plus certificate assessment")
    r.add_argument("--manifest", required=True); r.add_argument("--candidates", nargs="+", required=True)
    r.add_argument("--certificates"); r.add_argument("--output", required=True)
    r.add_argument("--seeds", type=int, nargs="+", default=[2,3,5]); add_config(r)
    r = sub.add_parser("freeze", help="Gate on TRAIN; select by complete VAL scheduling quality")
    r.add_argument("--evaluation", required=True); r.add_argument("--output", required=True)
    r.add_argument("--min-strict-fit", type=float, default=1.0)
    r.add_argument("--allow-uninformative", action="store_true", help="Only for diagnostic artifacts; cannot bypass a contradictory gate")
    r = sub.add_parser("benchmark", help="Matched original kernels and guided ablations; TEST requires frozen policy for DSL")
    r.add_argument("--manifest", required=True); r.add_argument("--output", required=True)
    r.add_argument("--split", choices=["train", "val", "test"], default="test")
    r.add_argument("--frozen-policy"); r.add_argument("--seeds", type=int, nargs="+", default=[2,3,5]); add_config(r)
    args = p.parse_args(argv)
    try:
        cmd = args.command
        if cmd == "run":
            started = time.perf_counter()
            result = run_instance(args.graph, args.method, get_config(args), args.program)
            result["cli_before_write_wall_seconds"] = time.perf_counter()-started
            dump_json(args.output, result)
            # A separate sidecar measures the complete first result serialization.
            dump_json(args.output+".io.json", {"wall_through_result_write_seconds": time.perf_counter()-started})
            result = {k: result[k] for k in ("status", "value_ticks", "feasible", "wall_seconds", "cpu_seconds", "wall_overrun_seconds")}
        elif cmd == "validate-manifest":
            m = load_manifest(args.manifest)
            result = {"graphs": len(m["graphs"]), "physical_sources": len({g["source_id"] for g in m["graphs"]}), "sha256": m["manifest_sha256"]}
        elif cmd == "hand-program":
            dump_json(args.output, hand_program().obj); result = {"origin": "handcrafted", "path": args.output}
        elif cmd == "collect": result = collect(args.manifest, args.output, get_config(args), args.seeds)
        elif cmd == "certify": result = certify_snapshots(args.snapshots, args.output, args.oracle_seconds, args.oracle_nodes, args.oracle_vertices)
        elif cmd == "assess":
            result = assess_program(read_program(args.program), args.certificates); dump_json(args.output, result)
        elif cmd == "enumerate": result = enumerate_control(args.output)
        elif cmd == "author": result = propose(args.certificates, args.output, args.parent, args.endpoint, args.model,
                                                args.key_env, args.response_file, args.feedback, evaluation_feedback=args.evaluation_feedback)
        elif cmd == "evaluate": result = evaluate_candidates(args.manifest, args.candidates, args.output,
                                                get_config(args), args.seeds, args.certificates)
        elif cmd == "freeze": result = freeze(args.evaluation, args.output, args.min_strict_fit, args.allow_uninformative)
        elif cmd == "benchmark": result = benchmark(args.manifest, args.output, get_config(args), args.seeds, args.split, args.frozen_policy)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if isinstance(result, dict) and result.get("status") in {"failed", "error", "policy_error"} else 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__": raise SystemExit(main())
