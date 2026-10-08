"""Predeclared bounded search actions over the preserved v0.5 native engine.

These are hand-authored actions, not learned policies. A controller may select
and sequence them. Every execute call charges preparation, seed/kick, native
execution, and measured effects to its one wall budget. Population stays four.
"""
from __future__ import annotations

import copy
import math
import time

from cipheur_v04.program import Program, default_program
from cipheur_v05.schema import STAGE_DEFAULTS

BASELINE_ACTION = "v05_nonllm_adaptive"
_ACTIONS = ("exploit", "spread", "merge", "relay", "antenna", "satellite", "kick", "reseed")


def action_names(include_baseline=False):
    return _ACTIONS + ((BASELINE_ACTION,) if include_baseline else ())


def _program(name, generator, priority=None, anchor="selected"):
    return Program({"schema": 4, "name": "V06_HAND_" + name.upper(),
                    "operators": [{"generator": {"op": "unselected", "of": generator},
                                   "priority": priority or {"op": "div", "a": {"feature": "weight"},
                                                            "b": {"feature": "blocker_weight"}},
                                   "anchor": anchor, "donor_limit": 256}],
                    "selector": {"op": "sub", "a": {"feature": "weight_donor"},
                                 "b": {"feature": "weight_blockers"}},
                    "provenance": {"origin": "predeclared_hand_action_not_llm", "action": name}})


def make_programs(g):
    base = default_program()
    seed = {"op": "seed"}
    relay = {"op": "neighbors", "of": {"op": "blockers", "of":
             {"op": "neighbors", "of": {"op": "blockers", "of": {"op": "neighbors", "of": seed}}}}}
    programs = {name: base for name in ("exploit", "spread", "merge", "kick")}
    programs["relay"] = _program("relay", relay)
    programs["reseed"] = _program("reseed", {"op": "neighbors", "of": seed},
                                  priority={"feature": "weight"}, anchor="any")
    for name, key, gap in (("antenna", "antenna_id", "ground_gap_by_node_ticks"),
                           ("satellite", "satellite_id", "satellite_gap_by_node_ticks")):
        required = {key, "start_ticks", "end_ticks", gap}
        programs[name] = _program(name, {"op": "resource_span", "of": seed, "key": key,
                                       "gap_multiplier": 2, "duration_multiplier": 1}, anchor="any") \
            if required <= set(g.meta) else None
    for program in programs.values():
        if program is not None:
            program.validate_graph(g)
    return {name: programs[name] for name in _ACTIONS}


def stage_for(action_name, snapshot):
    """The extra comparator reproduces NonLLMProvider's exact one-decision rule."""
    stage = dict(STAGE_DEFAULTS)
    if action_name == BASELINE_ACTION:
        if snapshot.get("diversity_ppm", 0) < 200000:
            stage.update(target="worst", donor="distant", queue=80)
        else:
            stage.update(target="round_robin", donor="cyclic", queue=32)
    elif action_name == "exploit":
        stage.update(mode="portfolio", target="best", donor="best", queue=16,
                     slice_seconds=0.05, ls_steps=256)
    elif action_name == "spread":
        stage.update(mode="portfolio", target="round_robin", donor="distant", queue=128)
    elif action_name == "merge":
        stage.update(mode="audition", target="worst", donor="distant", queue=64,
                     exchange_fraction=0.5, program_share=0.25)
    elif action_name == "relay":
        stage.update(mode="audition", target="worst", donor="distant", queue=96,
                     exchange_fraction=0.35, program_share=0.65, anchors=6)
    elif action_name in ("antenna", "satellite"):
        stage.update(mode="audition", target="round_robin", donor="distant", queue=64,
                     exchange_fraction=0.4, program_share=0.75)
    elif action_name in ("kick", "reseed"):
        stage.update(mode="portfolio", target="round_robin", queue=96)
    else:
        raise ValueError("Unknown v0.6 action: " + str(action_name))
    return stage


def _statistics(report):
    names = ("ils_calls", "generated", "exact_exchanges", "committed", "equal_value_moves",
             "cache_hits", "rejected_budget", "flow_work", "generator_work", "pair_attempts",
             "ils_seconds", "exchange_seconds")
    result = {key: report[key] for key in names if key in report}
    result.update({"v04_" + key: value for key, value in report.get("v04_stats", {}).items()
                   if isinstance(value, (int, float))})
    return result


def execute(engine, action_name, cfg, seconds, *, enter=True):
    if action_name not in action_names(include_baseline=True):
        raise ValueError("Unknown v0.6 action: " + str(action_name))
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Finite nonnegative action wall budget required")
    if cfg.population != 4:
        raise ValueError("The predeclared v0.6 action library requires population=4")
    started, cpu_started = time.perf_counter(), time.process_time()
    deadline = started + seconds
    before = engine.snapshot(False)
    if len(before["slots"]) != 4:
        raise ValueError("Native population differs from registered population=4")
    before_stats = _statistics(engine.report())
    effects = []
    status = "ok"
    stage = stage_for(action_name, before)
    if not hasattr(engine, "_v06_action_programs"):
        engine._v06_action_programs = make_programs(engine.g)
    program = default_program() if action_name == BASELINE_ACTION else engine._v06_action_programs[action_name]
    if program is None:
        status = "unavailable_metadata"
        effects.append({"type": "program_unavailable", "action": action_name})
    elif time.perf_counter() >= deadline:
        status = "budget_exhausted_before_execution"
    else:
        changed = program.fingerprint() != engine.program.fingerprint()
        if changed:
            engine.set_program(program)
        effects.append({"type": "program", "changed": changed, "program_sha256": program.fingerprint()})
        if enter and action_name in ("kick", "reseed"):
            # Explicit intervention only. Installing or changing a control plan
            # never silently reinitializes all trajectories.
            full = engine.snapshot()
            eligible = [slot for slot in full["slots"] if slot["slot"] != full["best_slot"]
                        and (action_name != "reseed" or slot["slot"] != 0)]
            if eligible and time.perf_counter() < deadline:
                slot = min(eligible, key=lambda row: (row["value_ticks"], row["slot"]))["slot"]
                remaining = max(0.0, deadline - time.perf_counter())
                if action_name == "kick":
                    budget = min(cfg.local_seconds, seconds * 0.2, remaining)
                    effect = engine.kick(slot, 2, 500, budget)
                    effects.append({"type": "kick", "protected_best_slot": full["best_slot"],
                                    "allocated_seconds": budget, **effect})
                else:
                    root = max(range(engine.g.n), key=lambda v: (engine.g.weights[v], -v)) if engine.g.n else None
                    if root is not None:
                        budget = min(0.25, seconds * 0.2, max(0.0, deadline - time.perf_counter()))
                        effect = engine.seed_slot(slot, 0, root, budget, cfg.initialization_work)
                        effects.append({"type": "reseed", "protected_best_slot": full["best_slot"],
                                        "protected_zero_slot": True, "allocated_seconds": budget, **effect})
            else:
                effects.append({"type": action_name, "status": "no_eligible_slot_or_deadline"})
        remaining = max(0.0, deadline - time.perf_counter())
        if remaining > 0:
            epoch_start = time.perf_counter()
            engine.epoch(stage, cfg, remaining)
            effects.append({"type": "epoch", "allocated_seconds": remaining,
                            "wall_seconds": time.perf_counter() - epoch_start, "stage": copy.deepcopy(stage)})
    after = engine.snapshot(False)
    after_stats = _statistics(engine.report())
    if after["value_ticks"] < before["value_ticks"]:
        raise AssertionError("Historical incumbent was lost")
    used = time.perf_counter() - started
    pool_before = sum(slot["value_ticks"] for slot in before["slots"])
    pool_after = sum(slot["value_ticks"] for slot in after["slots"])
    return {"action": action_name, "status": status, "enter": bool(enter), "before": before, "after": after,
            "gain_ticks": after["value_ticks"] - before["value_ticks"],
            "pool_gain_ticks": pool_after - pool_before,
            "slot_gain_ticks": [right["value_ticks"] - left["value_ticks"]
                                for left, right in zip(before["slots"], after["slots"])],
            "diversity_delta_ppm": after["diversity_ppm"] - before["diversity_ppm"],
            "cycles": after["cycles"] - before["cycles"], "effects": effects,
            "statistics_delta": {key: after_stats[key] - value for key, value in before_stats.items()
                                 if key in after_stats},
            "program_sha256": program.fingerprint() if program else None, "stage": stage,
            "allocated_seconds": seconds, "wall_seconds": used, "usedwall": used,
            "cpu_seconds": time.process_time() - cpu_started, "overrun_seconds": max(0.0, used - seconds),
            "budget_scope": "program preparation, explicit seed/kick, native epoch and effects; soft wall cap"}
