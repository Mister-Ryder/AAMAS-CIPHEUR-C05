"""Certificates for the exact DEPLOYMENT action boundary, not vertex preferences."""
from __future__ import annotations
from dataclasses import asdict
from collections import defaultdict, deque
from pathlib import Path
import hashlib
import json
import time
from .actions import State, Action, ActionConfig, propose_actions, affected_disagreement, verify_action
from .graph import load_graph
from .oracle import solve_region
from .dsl import Program, Meter


def potential_bounds(g, state, action, **oracle_kwargs):
    """Q(a) = common + delta(a). Full affected components cancel exactly.

    These are bounds on OPTIMAL neighbourhood opportunity, NOT on the result
    returned by finite-time CHILS. Both bounds retain the true boundary.
    """
    verify_action(g, state, action)
    affected = affected_disagreement(g, state, action)
    base = solve_region(g, affected, **oracle_kwargs)
    expanded = solve_region(g, affected | action.extra, **oracle_kwargs)
    wb = g.weight(action.blockers)
    # Combining the base feasible witness with B is feasible by consensus.
    if expanded.lower < base.lower + wb:
        expanded.lower = base.lower + wb
        expanded.selected = sorted(set(base.selected) | action.blockers)
        expanded.exact = expanded.lower == expanded.upper
    lower = max(0, expanded.lower-base.upper-wb)
    upper = expanded.upper-base.lower-wb
    if upper < lower:
        raise AssertionError("Invalid action bounds")
    return {"action": action.to_json(), "affected_D": sorted(affected),
            "base": base.to_json(), "expanded": expanded.to_json(),
            "removed_consensus_ticks": wb, "delta_lower": lower,
            "delta_upper": upper, "exact": lower == upper}


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            if line.strip():
                try: yield json.loads(line)
                except ValueError as exc: raise ValueError(f"{path}:{number}: {exc}") from exc


def snapshot_graph(row):
    if row.get("split") != "train":
        raise ValueError("Only TRAIN states may enter certificate authoring")
    g = load_graph(row["graph_path"])
    if g.fingerprint() != row["graph_fingerprint"]:
        raise ValueError("Snapshot graph or metadata changed")
    state = State.from_json(row["state"])
    state.validate(g)
    return g, state


def certify_snapshots(snapshots, output, seconds=.05, max_nodes=20_000, max_vertices=180):
    """Write every evaluated action, including unknown comparisons and costs."""
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    total = {"states": 0, "actions": 0, "strict": 0, "exact_ties": 0, "unknown": 0}
    started = time.perf_counter()
    with output.open("x", encoding="utf-8") as f:
        for source in read_jsonl(snapshots):
            g, state = snapshot_graph(source)
            actions = propose_actions(g, state, ActionConfig(**source["action_config"]), source["seed"])
            t = time.perf_counter()
            bounds = [potential_bounds(g, state, a, seconds=seconds,
                        max_nodes=max_nodes, max_vertices=max_vertices) for a in actions]
            strict, ties, unknown = [], [], 0
            for i in range(len(bounds)):
                for j in range(i+1, len(bounds)):
                    a, b = bounds[i], bounds[j]
                    if a["delta_lower"] > b["delta_upper"]: strict.append([i, j])
                    elif b["delta_lower"] > a["delta_upper"]: strict.append([j, i])
                    elif a["exact"] and b["exact"] and a["delta_lower"] == b["delta_lower"]:
                        ties.append([i, j])
                    else: unknown += 1
            row = dict(source, bounds=bounds, strict=strict, exact_ties=ties,
                       unknown_comparisons=unknown, certificate_wall_seconds=time.perf_counter()-t,
                       oracle_config={"seconds_per_solve": seconds, "max_nodes": max_nodes, "max_vertices": max_vertices})
            f.write(json.dumps(row)+"\n")
            total["states"] += 1; total["actions"] += len(actions)
            total["strict"] += len(strict); total["exact_ties"] += len(ties); total["unknown"] += unknown
    return dict(total, wall_seconds=time.perf_counter()-started)


def quotient_diagnostics(edges):
    """Strict edges join *equal demanded vectors*, across ALL observed states."""
    adjacency = defaultdict(set); indegree = defaultdict(int)
    aliases = []
    for left, right, witness in edges:
        left, right = tuple(left), tuple(right)
        indegree.setdefault(left, 0); indegree.setdefault(right, 0)
        if left == right: aliases.append(witness)
        if right not in adjacency[left]:
            adjacency[left].add(right); indegree[right] += 1
    queue = deque(sorted(v for v in indegree if indegree[v] == 0))
    removed = 0
    while queue:
        u = queue.popleft(); removed += 1
        for v in sorted(adjacency[u]):
            indegree[v] -= 1
            if indegree[v] == 0: queue.append(v)
    residual = [v for v, deg in indegree.items() if deg > 0]
    return {"acyclic": removed == len(indegree), "quotient_vertices": len(indegree),
            "alias_violations": len(aliases), "alias_witnesses": aliases[:12],
            "cyclic_residual_vectors": residual[:24]}


def assess_program(program: Program, certificates, operation_limit=1_000_000):
    edges, failures = [], []
    strict_total = strict_fit = ties = tie_fit = feature_work = states = 0
    for position, row in enumerate(read_jsonl(certificates)):
        g, state = snapshot_graph(row)
        actions = [Action.from_json(b["action"]) for b in row["bounds"]]
        # Certificates must describe precisely the current shared action builder.
        expected = propose_actions(g, state, ActionConfig(**row["action_config"]), row["seed"])
        if actions != expected: raise ValueError("Certificate action catalogue no longer matches deployment")
        meter = Meter(limit=operation_limit)
        scored = [program.score(g, state, action, meter) for action in actions]
        feature_work += meter.operations; states += 1
        for a, b in row["strict"]:
            sa, va = scored[a]; sb, vb = scored[b]
            witness = {"state_position": position, "preferred": a, "other": b,
                       "preferred_vector": list(map(str, va)), "other_vector": list(map(str, vb)),
                       "preferred_bounds": [row["bounds"][a]["delta_lower"], row["bounds"][a]["delta_upper"]],
                       "other_bounds": [row["bounds"][b]["delta_lower"], row["bounds"][b]["delta_upper"]]}
            edges.append((list(map(str, va)), list(map(str, vb)), witness))
            strict_total += 1; strict_fit += sa > sb
            if sa <= sb and len(failures) < 24: failures.append(witness)
        for a, b in row["exact_ties"]:
            ties += 1; tie_fit += scored[a][0] == scored[b][0]
    quotient = quotient_diagnostics(edges)
    return {"program_sha256": program.fingerprint(), "states": states,
            "strict_total": strict_total, "strict_fit": strict_fit,
            "exact_ties": ties, "tie_fit_diagnostic_only": tie_fit,
            "feature_operations": feature_work, "demanded_features": program.demanded,
            "quotient": quotient, "rule_failures": failures,
            "scope": "Observed TRAIN action opportunity; no finite-time gain guarantee"}
