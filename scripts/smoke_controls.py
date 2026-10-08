#!/usr/bin/env python3
"""Offline native integration check for all released classical C05 modes."""
import json
import os
from pathlib import Path
import sys
import tempfile

for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cipheur_v06.contracts import Config
from cipheur_v06.plan_solver import solve

checks = []
with tempfile.TemporaryDirectory(prefix="cipheur-c05-controls-") as folder:
    graph = Path(folder) / "toy.json"
    graph.write_text(json.dumps({"weights": [8, 7, 10, 6, 9, 5],
                                "edges": [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [0, 5]]}), encoding="utf-8")
    for mode in ("rule", "bandit", "linucb", "knn", "static", "adaptive"):
        result = solve(graph, mode, Config(seconds=.5, seed=67, first_decision=0,
                                          decision_interval=.1, epoch_seconds=.02, max_calls=3),
                       graph_id="offline-toy")
        assert result["feasible"] and result["status"] == "ok"
        assert result["deployment_llm_calls"] == 0
        if mode != "adaptive":
            choices = [d for d in result["classical_decisions"] if d["origin"] == "classical_" + mode]
            assert len(choices) == 3
        checks.append({"mode": mode, "status": "passed", "feasible": True, "real_model_calls": 0})
print(json.dumps(checks, indent=2))
