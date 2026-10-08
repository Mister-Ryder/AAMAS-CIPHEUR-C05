"""Finite, evidence-bound temporal plans shared by every C03 selector.

Directed repair priorities are predeclared native DSL expressions, not new
objective weights. A plan carries actual slot/root IDs from one full observation;
the executor must recheck its current best protection on entry. The registry is
built before any information-ablation mask. It performs no native/model work.
"""
from __future__ import annotations

import copy
import math
from collections import Counter
from numbers import Integral, Real

import numpy as np


SINGLE_ACTIONS = (
    "exploit", "spread", "merge", "relay", "antenna", "satellite",
    "kick", "reseed", "v05_nonllm_adaptive",
)
TEMPLATES = ("antenna", "satellite", "mixed")
FOLLOWUPS = ("spread", "merge")


def _feature(name):
    return {"feature": name}


def _binary(op, a, b):
    return {"op": op, "a": a, "b": b}


def _priority(template):
    weight = _feature("weight")
    if template in ("antenna", "satellite"):
        same = _feature("same_" + template)
        gap = _feature("ground_gap" if template == "antenna" else "satellite_gap")
        boost = _binary("add", {"const": 1}, _binary("mul", {"const": 2}, same))
    elif template == "mixed":
        boost = _binary("add", {"const": 1},
                        _binary("add", _feature("same_antenna"), _feature("same_satellite")))
        gap = _binary("max", _feature("ground_gap"), _feature("satellite_gap"))
    else:
        raise ValueError("Unknown directed repair priority template")
    relative_time = _binary("div", _feature("time_distance"),
                            _binary("add", _feature("duration"), gap))
    return _binary("div", _binary("mul", weight, boost), relative_time)


PRIORITY_TEMPLATES = {name: _priority(name) for name in TEMPLATES}
PRIORITY_SEMANTICS = (
    "Signed saturating native integer priorities only; div(a,b)=a/(1+abs(b)) "
    "with integer truncation. Root-relative resource/time fields affect seed "
    "ordering. Original graph weights and final objective are unchanged."
)


def priority_for(template):
    """Return a detached exact DSL template for the directed executor."""
    if template not in PRIORITY_TEMPLATES:
        raise ValueError("Unknown directed repair priority template")
    return copy.deepcopy(PRIORITY_TEMPLATES[template])


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _sequence(value):
    return value if isinstance(value, (list, tuple)) else ()


def _number(value, default=0.):
    if isinstance(value, bool) or not isinstance(value, Real):
        return default
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _integer(value):
    return int(value) if not isinstance(value, bool) and isinstance(value, Integral) else None


def _bounded(value, low=0., high=1.):
    return min(high, max(low, _number(value)))


def _best_slot(packet):
    scope = _mapping(packet.get("repair_slot_scope"))
    explicit = _integer(scope.get("protected_best_slot"))
    if explicit is not None:
        return explicit
    rows = [s for s in _sequence(packet.get("population"))
            if isinstance(s, dict) and _integer(s.get("slot")) is not None]
    if not rows:
        return None
    # This fallback uses the native engine's smallest-slot tie convention.
    return max(rows, key=lambda s: (_number(s.get("value_ticks")), -int(s["slot"])))["slot"]


def build_plans(packet):
    """Return deterministic ID -> frozen descriptor, using genuine slot evidence.

    Single IDs remain their old action names. b1..b6 are the existing witness
    ranks, not invented graph vertices. Composite contexts contain target-slot
    blocker counts, never a best-slot margin relabelled as target-slot evidence.
    Protected native slot zero and the current best are excluded. Missing target
    evidence/metadata leaves the original single-action registry available.
    """
    packet = _mapping(packet)
    plans = {}
    for action in SINGLE_ACTIONS:
        plans[action] = {"plan_id": action,
            "steps": [{"action": action, "seconds": 24 if action == "spread" else 16,
                       "until": "time"}],
            "context": {"kind": "single", "action": action}}
    best = _best_slot(packet)
    population_ids = {_integer(s.get("slot")) for s in _sequence(packet.get("population"))
                      if isinstance(s, dict)} - {None}
    n = _integer(_mapping(packet.get("static")).get("n"))
    anchors, seen = [], set()
    resources = _mapping(packet.get("resources"))
    for rank, witness in enumerate(_sequence(resources.get("barrier_witnesses"))[:6], 1):
        if not isinstance(witness, dict):
            continue
        root = _integer(witness.get("vertex"))
        if root is None or root < 0 or (n is not None and root >= n) or root in seen:
            continue
        seen.add(root)
        anchors.append((rank, root))
    slots, slot_seen = [], set()
    for slot in _sequence(packet.get("repair_slots")):
        if not isinstance(slot, dict):
            continue
        sid = _integer(slot.get("slot"))
        if sid is None or sid <= 0 or sid == best or sid in slot_seen:
            continue
        if population_ids and sid not in population_ids:
            continue
        slot_seen.add(sid)
        slots.append(slot)
    for slot in sorted(slots, key=lambda s: int(s["slot"])):
        sid = int(slot["slot"])
        by_root = {}
        for witness in _sequence(slot.get("anchor_witnesses")):
            if isinstance(witness, dict):
                root = _integer(witness.get("vertex"))
                if root is not None and root not in by_root:
                    by_root[root] = witness
        available = _mapping(slot.get("resource_metadata_available"))
        for rank, root in anchors:
            witness = by_root.get(root)
            if witness is None:
                continue
            # An explicit unavailable resource cannot be silently guessed.
            ant_ok = available.get("antenna", witness.get("same_antenna_blockers") is not None) is True
            sat_ok = available.get("satellite", witness.get("same_satellite_blockers") is not None) is True
            for template in TEMPLATES:
                if (template in ("antenna", "mixed") and not ant_ok) or \
                   (template in ("satellite", "mixed") and not sat_ok):
                    continue
                for followup in FOLLOWUPS:
                    pid = f"repair_s{sid}_b{rank}_{template}_{followup}"
                    plans[pid] = {"plan_id": pid,
                        "steps": [{"action": "directed_reseed", "seconds": 8, "until": "time"},
                                  {"action": followup, "seconds": 16, "until": "time"}],
                        "operation": {"slot": sid, "root": root, "template": template},
                        "context": {"kind": "directed_reseed", "anchor_rank": rank,
                            "slot_value_ticks": slot.get("value_ticks"),
                            "slot_gap_ticks": slot.get("value_gap_ticks"),
                            "slot_jaccard_with_best_ppm": slot.get("jaccard_with_best_ppm"),
                            "slot_blocker_reuse_fraction": _mapping(slot.get("blocker_reuse_sample")).get("reuse_fraction"),
                            "anchor_selected": witness.get("anchor_selected"),
                            "weight_ticks": witness.get("weight_ticks"),
                            "blocker_count": witness.get("blocker_count"),
                            "blocker_weight_ticks": witness.get("blocker_weight_ticks"),
                            "same_antenna_blockers": witness.get("same_antenna_blockers"),
                            "same_satellite_blockers": witness.get("same_satellite_blockers"),
                            "weight_minus_blockers_ticks": witness.get("weight_minus_blockers_ticks"),
                            "target_specific_blocker_evidence": True,
                            "protected_best_slot_at_proposal": best}}
    return plans


GLOBAL_FEATURE_NAMES = (
    "bias", "spent", "remaining", "diversity", "stagnation_120",
    "recent_rate_log", "earlier_rate_log", "recent_pool_change",
    "best_blocker_reuse", "donor_cross_conflict",
)
CANDIDATE_FEATURE_NAMES = tuple("single_" + x for x in SINGLE_ACTIONS) + (
    "directed", "template_antenna", "template_satellite", "template_mixed",
    "followup_spread", "followup_merge",
)
SPECIFIC_FEATURE_NAMES = (
    "slot_value_gap", "slot_distance_from_best", "root_duration",
    "target_blocker_count", "root_weight_blocker_ratio", "root_arithmetic_margin",
    "target_antenna_blocker_fraction", "target_satellite_blocker_fraction",
    "target_blocker_reuse", "root_already_selected",
)
INTERACTION_GLOBALS = ("spent", "diversity", "stagnation_120", "recent_rate_log", "earlier_rate_log", "recent_pool_change")
INTERACTION_KINDS = ("spread", "merge", "recovery", "exploit")
FEATURE_NAMES = GLOBAL_FEATURE_NAMES + CANDIDATE_FEATURE_NAMES + SPECIFIC_FEATURE_NAMES + tuple(
    g + "_x_" + kind for kind in INTERACTION_KINDS for g in INTERACTION_GLOBALS)


def _rate_log(gain, value, seconds):
    rate = max(0., _number(gain)) / max(1., _number(value, 1.)) * 1e6 / max(.01, _number(seconds, 1.))
    return min(1., math.log1p(rate) / 10.)


def plan_features(packet, descriptor):
    """A fixed finite vector; no raw slot/root ID is a regression feature.

    Arithmetic blocker margin is accompanied by an already-selected indicator.
    It is not an exact prospective exchange gain. Native validation remains the
    authority. Missing/null/nonfinite observation fields map to neutral features.
    """
    packet, descriptor = _mapping(packet), _mapping(descriptor)
    t, res = _mapping(packet.get("telemetry")), _mapping(packet.get("resources"))
    structure = _mapping(res.get("structure"))
    windows = _mapping(_mapping(packet.get("history_summary")).get("windows"))
    recent, earlier = _mapping(windows.get("10")), _mapping(windows.get("40"))
    value = max(1., _number(t.get("value_ticks"), 1.))
    recent_gain = recent.get("gain_ticks", t.get("recent_gain_ticks", 0))
    recent_seconds = recent.get("observed_search_seconds", 10.)
    earlier_gain = t.get("earlier_gain_ticks")
    if earlier_gain is None:
        earlier_gain = _number(earlier.get("gain_ticks")) - _number(recent_gain)
    earlier_seconds = max(.01, _number(earlier.get("observed_search_seconds"), 40.) - _number(recent_seconds, 10.))
    pool_gain = recent.get("pool_gain_ticks", sum(_number(h.get("pool_gain_ticks"))
        for h in _sequence(packet.get("history")) if isinstance(h, dict)))
    global_values = [1., _bounded(_number(t.get("elapsed_seconds")) / 360.),
        _bounded(_number(t.get("remaining_seconds"), 360.) / 360.),
        _bounded(_number(t.get("diversity_ppm")) / 1e6),
        _bounded(_number(t.get("stagnation_seconds")) / 120.),
        _rate_log(recent_gain, value, recent_seconds), _rate_log(earlier_gain, value, earlier_seconds),
        _bounded(_number(pool_gain) / value * 100., -1., 1.),
        _bounded(structure.get("blocker_reuse_fraction")),
        _bounded(structure.get("donor_mean_cross_conflict_fraction"))]
    context, op = _mapping(descriptor.get("context")), _mapping(descriptor.get("operation"))
    steps = _sequence(descriptor.get("steps"))
    first = _mapping(steps[0]) if steps else {}
    action = first.get("action", context.get("action"))
    directed = action == "directed_reseed"
    template = op.get("template") if directed else None
    followup = _mapping(steps[-1]).get("action") if directed and steps else None
    candidate_values = [float(not directed and action == name) for name in SINGLE_ACTIONS] + [float(directed)] + \
        [float(template == name) for name in TEMPLATES] + [float(followup == name) for name in FOLLOWUPS]
    blockers = max(0., _number(context.get("blocker_count")))
    weight = max(0., _number(context.get("weight_ticks")))
    blocker_weight = max(0., _number(context.get("blocker_weight_ticks")))
    margin = context.get("weight_minus_blockers_ticks")
    margin = _number(margin, weight - blocker_weight)
    specific_values = [
        _bounded(_number(context.get("slot_gap_ticks")) / value * 10.),
        1. - _bounded(_number(context.get("slot_jaccard_with_best_ppm"), 1e6) / 1e6),
        _bounded(weight / 1e9), _bounded(blockers / 8.),
        _bounded(weight / max(1., blocker_weight) / 2.) if weight else 0.,
        _bounded(margin / max(1., weight + blocker_weight), -1., 1.),
        _bounded(_number(context.get("same_antenna_blockers")) / max(1., blockers)),
        _bounded(_number(context.get("same_satellite_blockers")) / max(1., blockers)),
        _bounded(context.get("slot_blocker_reuse_fraction")),
        float(context.get("anchor_selected") is True)]
    categories = {"spread": action == "spread" or followup == "spread",
                  "merge": action == "merge" or followup == "merge",
                  "recovery": directed or action in ("kick", "reseed"), "exploit": action == "exploit"}
    globals_by_name = dict(zip(GLOBAL_FEATURE_NAMES, global_values))
    interactions = [globals_by_name[g] * categories[kind] for kind in INTERACTION_KINDS for g in INTERACTION_GLOBALS]
    vector = np.asarray(global_values + candidate_values + specific_values + interactions, dtype=float)
    if vector.shape != (len(FEATURE_NAMES),) or not np.all(np.isfinite(vector)):
        raise AssertionError("Invalid finite-plan feature vector")
    return vector


def _family(descriptor):
    operation = _mapping(descriptor.get("operation"))
    if operation:
        return "directed_" + str(operation.get("template")) + "_" + str(descriptor["steps"][-1]["action"])
    return str(descriptor["steps"][0]["action"])


def _structural_rank(descriptor):
    c, op = _mapping(descriptor.get("context")), _mapping(descriptor.get("operation"))
    blockers = max(1., _number(c.get("blocker_count"), 1.))
    template = op.get("template")
    ant, sat = _number(c.get("same_antenna_blockers")) / blockers, _number(c.get("same_satellite_blockers")) / blockers
    alignment = ant if template == "antenna" else sat if template == "satellite" else (ant + sat) / 2.
    ratio = min(2., max(0., _number(c.get("weight_ticks"))) / max(1., _number(c.get("blocker_weight_ticks"))))
    # Only a predeclared tie heuristic; a ratio is not a profitable-exchange proof.
    return (not bool(c.get("anchor_selected")), alignment,
            _number(c.get("slot_gap_ticks")), ratio,
            1e6 - _number(c.get("slot_jaccard_with_best_ppm"), 1e6))


class PlanController:
    """Matched classical selectors over all current single and directed plans.

    LinUCB shares one ridge model across candidate features; kNN also transfers
    observations across slot/root choices. Bandit shares each template/followup
    family. No directed warm observations are invented. The first decision is
    spread; subsequent argmax evaluates every valid plan, without serially
    initializing up to 117 independent arms. Rule/static are explicit baselines.
    """
    def __init__(self, kind="linucb", seed=7, alpha=.25, ridge=1.):
        if kind not in ("rule", "bandit", "linucb", "knn", "static"):
            raise ValueError("Unknown finite-plan controller")
        if _integer(seed) is None or not 0 <= int(seed) < 2 ** 32:
            raise ValueError("seed must be uint32")
        if not math.isfinite(alpha) or alpha < 0 or not math.isfinite(ridge) or ridge <= 0:
            raise ValueError("Finite nonnegative optimism and positive ridge required")
        self.kind, self.seed, self.alpha = kind, int(seed), float(alpha)
        self.inverse = np.eye(len(FEATURE_NAMES), dtype=float) / ridge
        self.b = np.zeros(len(FEATURE_NAMES), dtype=float)
        self.observations = 0
        self.samples = []
        self.family_counts = Counter()
        self.family_means = {}
        self.family_last_credit_elapsed = {}
        self.last_scores = {}

    def update(self, packet, descriptor, gain, seconds):
        """Use the actual whole decision-interval incumbent credit, not seed gain.

        This learning reward is log-scaled ppm per actual whole-interval wall
        second, including observations, entry work and concurrent HTTP waits;
        final
        experiment comparisons continue to use the original integer objective.
        Pool changes may inform context but never substitute for objective gain.
        """
        if _integer(gain) is None or gain < 0:
            raise ValueError("Whole-interval incumbent gain must be a nonnegative integer")
        if isinstance(seconds, bool) or not isinstance(seconds, Real) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Actual credited interval seconds must be finite and positive")
        x = plan_features(packet, descriptor)
        value = max(1., _number(_mapping(packet.get("telemetry")).get("value_ticks"), 1.))
        reward = math.log1p(int(gain) / value * 1e6 / max(.01, float(seconds)))
        projection = self.inverse @ x
        self.inverse -= np.outer(projection, projection) / (1. + float(x @ projection))
        self.inverse = (self.inverse + self.inverse.T) * .5
        self.b += reward * x
        self.samples.append((x.copy(), reward))
        self.samples = self.samples[-160:]
        family = _family(descriptor)
        count = self.family_counts[family]
        # Eight wall-spaced decisions cannot support a slow lifetime average.
        # This declared EMA forgets half the previous mean on an observed update;
        # choose() separately ages information for families not revisited.
        self.family_means[family] = reward if not count else .5 * self.family_means[family] + .5 * reward
        credited_elapsed = _number(_mapping(packet.get("telemetry")).get("elapsed_seconds")) + float(seconds)
        self.family_last_credit_elapsed[family] = max(credited_elapsed, self.family_last_credit_elapsed.get(family, credited_elapsed))
        self.family_counts[family] += 1
        self.observations += 1
        return {"reward": reward, "family": family, "credit_scope": "whole_decision_interval_original_incumbent_gain"}

    def choose(self, packet, plans):
        if not isinstance(plans, dict) or not plans:
            raise ValueError("A nonempty frozen plan registry is required")
        fallback = "spread" if "spread" in plans else min(plans)
        if self.kind == "static" or (not self.observations and self.kind not in ("rule",)):
            self.last_scores = {pid: {"score": None, "reason": "untrained_shared_model_spread_fallback"} for pid in plans}
            return fallback
        t = _mapping(packet.get("telemetry"))
        if self.kind == "rule":
            repairs = [p for p in plans.values() if p.get("operation")]
            if repairs and _number(t.get("stagnation_seconds")) >= 40. and \
               _number(t.get("diversity_ppm")) < 200000 and _number(t.get("remaining_seconds"), 360.) >= 32.:
                # Recovery on a collapsed persistent plateau, then diffuse again.
                spread_repairs = [p for p in repairs if p["steps"][-1]["action"] == "spread"] or repairs
                ordered = sorted(spread_repairs, key=lambda p: p["plan_id"])
                return max(ordered, key=_structural_rank)["plan_id"]
            return fallback
        theta = self.inverse @ self.b
        scores = {}
        for pid in sorted(plans):
            descriptor = plans[pid]
            x = plan_features(packet, descriptor)
            family = _family(descriptor)
            if self.kind == "bandit":
                age = max(0., _number(t.get("elapsed_seconds")) - self.family_last_credit_elapsed.get(family, _number(t.get("elapsed_seconds"))))
                # A two-cadence (80 wall-second) half-life is predeclared. Aging
                # an estimate is a controller prior, never a new measured gain.
                recency_weight = math.exp(-math.log(2.) * age / 80.)
                estimate = self.family_means.get(family, 0.) * recency_weight
                uncertainty = self.alpha * math.sqrt(math.log(2 + self.observations) / (1 + self.family_counts[family] * recency_weight))
            elif self.kind == "knn":
                neighbors = sorted(((float(np.linalg.norm(x - v)), reward) for v, reward in self.samples), key=lambda p: p[0])[:5]
                weights = [1. / (.2 + distance) for distance, _ in neighbors]
                estimate = sum(w * reward for w, (_, reward) in zip(weights, neighbors)) / max(1e-12, sum(weights))
                uncertainty = self.alpha / math.sqrt(1. + sum(weights))
            else:
                estimate = float(theta @ x)
                uncertainty = self.alpha * math.sqrt(max(0., float(x @ self.inverse @ x)))
            # A small decaying spread preference is a declared policy prior,
            # never presented as a measured warm-start observation.
            safety_prior = .1 / (1 + self.observations) if pid == fallback else 0.
            scores[pid] = {"score": estimate + uncertainty + safety_prior,
                           "estimate": estimate, "optimism": uncertainty,
                           "declared_spread_prior": safety_prior, "family": family}
            if self.kind == "bandit":
                scores[pid]["family_recency_weight"] = recency_weight
        self.last_scores = scores
        maximum = max(v["score"] for v in scores.values())
        ties = [pid for pid in sorted(scores) if math.isclose(scores[pid]["score"], maximum, abs_tol=1e-12, rel_tol=0.)]
        # Structural ties share parameters; they are not independent cold arms.
        return max(ties, key=lambda pid: (_structural_rank(plans[pid]), pid == fallback))
