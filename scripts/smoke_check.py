#!/usr/bin/env python3
"""Offline integration check using a declared fixture; no model/API calls."""
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
from cipheur_v05.provider import ScriptedProvider


def choose_spread(payload):
    return {"snapshot_id": payload["observation"]["snapshot_id"], "plan_id": "spread",
            "hypothesis": "Offline integration fixture", "evidence": []}


with tempfile.TemporaryDirectory(prefix="cipheur-c05-smoke-") as folder:
    graph = Path(folder) / "toy.json"
    graph.write_text(json.dumps({"weights": [8, 7, 10, 6, 9, 5],
                                "edges": [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [0, 5]]}), encoding="utf-8")
    result = solve(graph, "llm", Config(seconds=3, seed=67, first_decision=0, request_timeout=.1,
                                        decision_interval=.2, epoch_seconds=.05, max_calls=1),
                   ScriptedProvider([choose_spread]), graph_id="offline-toy")
    assert result["feasible"] and result["status"] == "ok"
    assert result["valid_model_proposals"] == 1 and result["deployment_llm_calls"] == 0
    assert result["installed_contracts"][0]["contract"]["plan_id"] == "spread"
    assert all(interval.get("gain_ticks", 0) >= 0 for interval in result["plan_intervals"])
    print(json.dumps({"check": "offline fixture integration", "status": "passed", "feasible": True,
                      "installed_fixture_plans": 1, "real_model_calls": 0,
                      "native_sha256": result["native_sha256"], "value_ticks": result["value_ticks"]}, indent=2))
