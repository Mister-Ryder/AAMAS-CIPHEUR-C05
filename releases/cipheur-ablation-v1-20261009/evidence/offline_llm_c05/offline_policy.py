"""Deterministic selector for legal C05 structural intervention plans."""


def _number(value, default=0.0):
    """Return a finite float, falling back for malformed optional values."""
    try:
        result = float(value)
        if result != result or result == float("inf") or result == float("-inf"):
            return default
        return result
    except (TypeError, ValueError, OverflowError):
        return default


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _steps(descriptor):
    value = _mapping(descriptor).get("steps")
    return value if isinstance(value, list) else []


def _is_directed(descriptor):
    operation = _mapping(_mapping(descriptor).get("operation"))
    steps = _steps(descriptor)
    if not operation or not isinstance(operation.get("slot"), (str, int)):
        return False
    if not isinstance(operation.get("root"), (str, int)):
        return False
    if operation.get("template") not in ("antenna", "satellite", "mixed"):
        return False
    if len(steps) != 2:
        return False
    first = _mapping(steps[0]).get("action", _mapping(steps[0]).get("type"))
    second = _mapping(steps[1]).get("action", _mapping(steps[1]).get("type"))
    return first == "directed_reseed" and second in ("spread", "merge")


def _action_name(descriptor):
    steps = _steps(descriptor)
    if len(steps) == 1:
        step = _mapping(steps[0])
        action = step.get("action", step.get("type"))
        if isinstance(action, str):
            return action
    return ""


def select_plan(packet, plans):
    """Choose one registered plan using structure, telemetry, and observed outcomes."""
    registry = plans if isinstance(plans, dict) and plans else {}
    if not registry:
        return ""

    # Normalize observations; absent or malformed telemetry is neutral.
    packet = _mapping(packet)
    telemetry = _mapping(packet.get("telemetry"))
    remaining = max(0.0, _number(telemetry.get("remaining_seconds")))
    stagnation = max(0.0, _number(telemetry.get("stagnation_seconds")))
    diversity = max(0.0, min(1_000_000.0, _number(telemetry.get("diversity_ppm"))))
    value_ticks = _number(telemetry.get("value_ticks"))
    stagnant = min(1.0, stagnation / 120.0)
    low_diversity = max(0.0, min(1.0, (250_000.0 - diversity) / 250_000.0))

    # Aggregate whole-interval results by action family, with light shrinkage.
    outcomes = packet.get("plan_outcomes")
    if not isinstance(outcomes, list):
        outcomes = []
    family_gain = {}
    family_weight = {}
    id_gain = {}
    id_weight = {}
    for outcome in outcomes:
        outcome = _mapping(outcome)
        pid = outcome.get("plan_id")
        desc = _mapping(outcome.get("descriptor"))
        gain = _number(outcome.get("gain_ticks"), None)
        wall = _number(outcome.get("wall_seconds"), None)
        if not isinstance(pid, str) or gain is None or wall is None or wall < 0:
            continue
        action = "directed" if _is_directed(desc) else _action_name(desc)
        if not action:
            continue
        # Favor gain rate while discounting noisy single observations.
        rate = gain / max(1.0, wall)
        weight = min(1.0, wall / 30.0)
        family_gain[action] = family_gain.get(action, 0.0) + rate * weight
        family_weight[action] = family_weight.get(action, 0.0) + weight
        id_gain[pid] = id_gain.get(pid, 0.0) + rate * weight
        id_weight[pid] = id_weight.get(pid, 0.0) + weight

    ids = sorted(key for key in registry if isinstance(key, str))
    if not ids:
        return next(iter(registry))
    best_id = ids[0]
    best_score = None
    for pid in ids:
        descriptor = _mapping(registry[pid])
        context = _mapping(descriptor.get("context"))
        directed = _is_directed(descriptor)
        action = "directed" if directed else _action_name(descriptor)
        score = 0.0

        # Observed per-plan rate and family rate guide reuse without predicting unrun gains.
        if pid in id_weight:
            score += 0.65 * id_gain[pid] / max(0.25, id_weight[pid])
        if action in family_weight:
            score += 0.35 * family_gain[action] / max(0.5, family_weight[action])

        if directed:
            # Blocker and weight evidence describe opportunity, not guaranteed objective gain.
            blockers = max(0.0, _number(context.get("blocker_count")))
            weight = max(0.0, _number(context.get("weight_ticks")))
            blocker_weight = max(0.0, _number(context.get("blocker_weight_ticks")))
            same_a = max(0.0, _number(context.get("same_antenna_blockers")))
            same_s = max(0.0, _number(context.get("same_satellite_blockers")))
            slot_gap = max(0.0, _number(context.get("slot_gap_ticks")))
            reuse = max(0.0, min(1.0, _number(context.get("slot_blocker_reuse_fraction"))))
            overlap = max(0.0, min(1_000_000.0, _number(context.get("slot_jaccard_with_best_ppm")))) / 1_000_000.0
            anchor = _number(context.get("anchor_selected"))
            evidence = min(1.0, blockers / 4.0)
            evidence += 0.25 * min(1.0, weight / 100.0)
            evidence += 0.20 * min(1.0, blocker_weight / 100.0)
            evidence += 0.15 * min(1.0, (same_a + same_s) / 4.0)
            evidence += 0.10 * min(1.0, slot_gap / 100.0)
            evidence += 0.15 * reuse
            evidence += 0.10 * (1.0 - overlap)
            evidence += 0.10 if anchor > 0 else 0.0
            budget = min(1.0, remaining / 60.0)
            # Stagnation and low diversity raise recovery priority; budget gates feasibility.
            score += budget * (0.35 + 0.90 * stagnant + 0.30 * low_diversity) * evidence
            if remaining < 15.0:
                score -= 0.50
        else:
            # Single-plan fallback favors productive progress when the run is not stalled.
            score += 0.10 * (1.0 - stagnant) + 0.05 * min(1.0, max(0.0, value_ticks) / 100.0)
            if action == "spread":
                score += 0.08 * low_diversity
            elif action == "merge":
                score += 0.04 * stagnant
            elif action in ("exploit", "kick", "reseed", "antenna", "satellite", "relay", "v05_nonllm_adaptive"):
                score += 0.02

        if best_score is None or score > best_score:
            best_id, best_score = pid, score
    return best_id
