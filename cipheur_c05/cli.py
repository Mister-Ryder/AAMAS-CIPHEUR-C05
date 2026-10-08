"""Run C05 with explicit output ownership and the registered default profile."""
import argparse
import json
import os
from pathlib import Path

# Set these before importing NumPy: one numerical thread per solver process.
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

from cipheur_v06.contracts import Config
from cipheur_v06.plan_provider import PlanProvider
from cipheur_v06.plan_solver import solve


def main():
    parser = argparse.ArgumentParser(description="C05 LLM selector; classical selectors are on the non-llm-controls branch.")
    parser.add_argument("graph", type=Path, help="Integer MWIS graph: NPZ, JSON, or weighted METIS")
    parser.add_argument("--out", required=True, type=Path, help="New result JSON; existing files are never overwritten")
    parser.add_argument("--seed", default=67, type=int)
    parser.add_argument("--seconds", default=360.0, type=float)
    parser.add_argument("--graph-id", help="Portable graph identifier; defaults to the input stem")
    args = parser.parse_args()
    if args.out.exists():
        parser.error("The output already exists; choose a fresh filename.")
    if not args.graph.is_file():
        parser.error("The input graph does not exist.")
    config = Config(seconds=args.seconds, seed=args.seed)
    provider = PlanProvider()
    result = solve(args.graph, "llm", config, provider, graph_id=args.graph_id or args.graph.stem)
    result["graph_path"] = args.graph.name
    result["public_release_entrypoint"] = "C05 LLM; original solve function with classical modes disabled"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in ("graph_id", "mode", "seed", "status", "value_ticks", "feasible", "wall_seconds", "deployment_llm_calls", "valid_model_proposals")}, indent=2))
