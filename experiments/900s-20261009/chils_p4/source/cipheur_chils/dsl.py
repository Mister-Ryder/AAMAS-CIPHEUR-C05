"""Bounded typed JSON expression language; no eval/exec, IO, IDs, or arbitrary code."""
from __future__ import annotations
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import time

SCALARS = {"n_U", "n_B", "n_D", "w_U", "w_B", "edges_U", "edges_extra", "cut_D", "is_noop"}
NODE_ATTRS = {"w", "degree", "degree_U", "degree_B", "degree_D", "neighbor_w_U", "neighbor_w_B", "same_ground_U", "same_satellite_U", "duration", "ground_gap"}
SETS = {"U", "B", "EXTRA"}
ARITY = {"add": 2, "sub": 2, "mul": 2, "div": 2, "min": 2, "max": 2, "neg": 1, "abs": 1}


class DSLValidationError(ValueError): pass
class EvaluationBudgetError(RuntimeError): pass


@dataclass
class Meter:
    operations: int = 0
    limit: int = 100_000
    deadline: float = float("inf")

    def charge(self, amount=1):
        self.operations += amount
        if self.operations > self.limit or time.perf_counter() > self.deadline:
            raise EvaluationBudgetError("Feature/score evaluation budget exhausted")


def checked(x):
    x = Fraction(x)
    if x.numerator.bit_length() > 512 or x.denominator.bit_length() > 512:
        raise EvaluationBudgetError("Rational-size limit exceeded")
    return x


def validate_expr(e, features, scope="feature", inside_sum=False, depth=0, count=None):
    if count is None: count = [0]
    count[0] += 1
    if count[0] > 128 or depth > 10:
        raise DSLValidationError("AST size/depth exceeded")
    if type(e) is int:
        if abs(e) > 1_000_000: raise DSLValidationError("Literal too large")
        return set()
    if not isinstance(e, dict): raise DSLValidationError("Expression must be int or object")
    if set(e) == {"scalar"} and scope == "feature" and e["scalar"] in SCALARS:
        return set()
    if set(e) == {"node"} and scope == "feature" and inside_sum and e["node"] in NODE_ATTRS:
        return set()
    if set(e) == {"feature"} and scope == "score" and e["feature"] in features:
        return {e["feature"]}
    if set(e) == {"sum", "body"} and scope == "feature" and not inside_sum and e["sum"] in SETS:
        return validate_expr(e["body"], features, scope, True, depth+1, count)
    if set(e) == {"op", "args"} and e["op"] in ARITY and isinstance(e["args"], list) and len(e["args"]) == ARITY[e["op"]]:
        result = set()
        for arg in e["args"]:
            result |= validate_expr(arg, features, scope, inside_sum, depth+1, count)
        return result
    raise DSLValidationError(f"Disallowed or mistyped expression: {str(e)[:160]}")


class Context:
    def __init__(self, graph, state, action, meter):
        self.g, self.s, self.a, self.m = graph, state, action, meter
        d, _, _ = state.sets()
        self.sets = {"U": action.unlocked, "B": action.blockers, "EXTRA": action.extra, "D": d}
        self.cache = {}

    def scalar(self, key):
        if key in self.cache: return self.cache[key]
        g, a, d = self.g, self.a, self.sets["D"]
        if key == "n_U": value = len(a.unlocked)
        elif key == "n_B": value = len(a.blockers)
        elif key == "n_D": value = len(d)
        elif key == "w_U": value = g.weight(a.unlocked); self.m.charge(len(a.unlocked))
        elif key == "w_B": value = g.weight(a.blockers); self.m.charge(len(a.blockers))
        elif key == "is_noop": value = int(not a.extra)
        elif key in ("edges_U", "edges_extra"):
            nodes = a.unlocked if key == "edges_U" else a.extra
            self.m.charge(sum(len(g.adj[v]) for v in nodes))
            value = sum(len(g.adj[v] & nodes) for v in nodes)//2
        elif key == "cut_D":
            self.m.charge(sum(len(g.adj[v]) for v in a.extra))
            value = sum(len(g.adj[v] & d) for v in a.extra)
        else: raise DSLValidationError(key)
        self.cache[key] = Fraction(value)
        return self.cache[key]

    def node(self, key, v):
        cache_key = (key, v)
        if cache_key in self.cache: return self.cache[cache_key]
        g = self.g
        if key == "w": value = g.weights[v]
        elif key == "degree": value = len(g.adj[v])
        elif key.startswith("degree_"):
            self.m.charge(len(g.adj[v])); value = len(g.adj[v] & self.sets[key[7:]])
        elif key.startswith("neighbor_w_"):
            self.m.charge(len(g.adj[v])); value = g.weight(g.adj[v] & self.sets[key[11:]])
        elif key in ("same_ground_U", "same_satellite_U"):
            resource = "antenna_id" if key == "same_ground_U" else "satellite_id"
            if resource not in g.meta: raise DSLValidationError(f"Required metadata absent: {resource}")
            self.m.charge(len(self.a.unlocked))
            value = sum(g.meta[resource][u] == g.meta[resource][v] for u in self.a.unlocked)
        elif key == "duration":
            if not {"start_ticks", "end_ticks"} <= g.meta.keys(): raise DSLValidationError("Required timing metadata absent")
            value = int(g.meta["end_ticks"][v])-int(g.meta["start_ticks"][v])
        elif key == "ground_gap":
            if "ground_gap_by_node_ticks" not in g.meta: raise DSLValidationError("Required ground-gap metadata absent")
            value = int(g.meta["ground_gap_by_node_ticks"][v])
        else: raise DSLValidationError(key)
        self.cache[cache_key] = Fraction(value)
        return self.cache[cache_key]


def evaluate(e, ctx, features=None, node=None):
    ctx.m.charge()
    if type(e) is int: return Fraction(e)
    if "scalar" in e: return ctx.scalar(e["scalar"])
    if "node" in e: return ctx.node(e["node"], node)
    if "feature" in e: return features[e["feature"]]
    if "sum" in e:
        return checked(sum((evaluate(e["body"], ctx, features, v) for v in sorted(ctx.sets[e["sum"]])), Fraction()))
    args = [evaluate(x, ctx, features, node) for x in e["args"]]
    op = e["op"]
    if op == "add": value = args[0]+args[1]
    elif op == "sub": value = args[0]-args[1]
    elif op == "mul": value = args[0]*args[1]
    elif op == "div": value = args[0]/(1+abs(args[1]))  # Total, protected division.
    elif op == "min": value = min(args)
    elif op == "max": value = max(args)
    elif op == "abs": value = abs(args[0])
    elif op == "neg": value = -args[0]
    else: raise DSLValidationError(op)
    return checked(value)


class Program:
    def __init__(self, obj):
        if not isinstance(obj, dict) or not {"features", "score"} <= obj.keys():
            raise DSLValidationError("Program requires features and score")
        if set(obj)-{"features", "score", "name", "provenance"}:
            raise DSLValidationError("Unknown program fields")
        features = obj["features"]
        if not isinstance(features, dict) or not 1 <= len(features) <= 8:
            raise DSLValidationError("One to eight feature expressions required")
        if any(not isinstance(k, str) or not k.isidentifier() or len(k)>40 for k in features):
            raise DSLValidationError("Invalid feature name")
        for expression in features.values(): validate_expr(expression, features)
        self.demanded = sorted(validate_expr(obj["score"], features, scope="score"))
        if not self.demanded: raise DSLValidationError("Score must demand at least one feature")
        self.obj = obj

    def score(self, graph, state, action, meter=None):
        ctx = Context(graph, state, action, meter or Meter())
        values = {k: evaluate(self.obj["features"][k], ctx) for k in self.demanded}
        value = evaluate(self.obj["score"], ctx, values)
        return value, tuple(values[k] for k in self.demanded)

    def fingerprint(self):
        # The name/provenance cannot change semantic identity.
        return hashlib.sha256(json.dumps({"features": self.obj["features"], "score": self.obj["score"]}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def hand_program():
    return Program({"name": "HAND_NOT_LLM", "provenance": {"origin": "handcrafted"},
        "features": {"packing": {"sum": "U", "body": {"op": "div", "args": [{"node": "w"}, {"node": "degree_U"}]}},
                     "release_cost": {"scalar": "w_B"}},
        "score": {"op": "sub", "args": [{"feature": "packing"}, {"feature": "release_cost"}]}})


def language_description():
    return {"scalars": sorted(SCALARS), "node_attributes_inside_sum": sorted(NODE_ATTRS), "sets": sorted(SETS),
            "arithmetic_arities": ARITY, "div_semantics": "a/(1+abs(b))", "max_features": 8, "max_AST_nodes_per_expression": 128,
            "grammar": ["integer literal", '{"scalar":"w_U"}', '{"node":"w"} (inside sum only)',
                        '{"sum":"U","body":EXPR}', '{"op":"add","args":[EXPR,EXPR]}',
                        '{"feature":"feature_name"} (score only)'],
            "example_program": hand_program().obj}
