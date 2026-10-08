"""Shared action construction for BOTH deployment and certification."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from functools import cached_property
import random
from .graph import Graph


@dataclass(frozen=True)
class ActionConfig:
    anchors: int = 16
    max_extra: int = 64
    max_unlocked: int = 32
    resource_horizon_ticks: int = 300_000_000

    def __post_init__(self):
        if min(self.anchors, self.max_extra, self.max_unlocked) < 1 or self.resource_horizon_ticks < 0:
            raise ValueError("Invalid action configuration")


@dataclass(frozen=True)
class State:
    votes: tuple[int, ...]
    population: int
    incumbent: frozenset[int]
    iteration: int = 0

    @cached_property
    def classes(self):
        d = frozenset(v for v, t in enumerate(self.votes) if 0 < t < self.population)
        c = frozenset(v for v, t in enumerate(self.votes) if t == self.population)
        z = frozenset(v for v, t in enumerate(self.votes) if t == 0)
        return d, c, z

    def sets(self):
        return self.classes

    def validate(self, g):
        if self.population < 2 or len(self.votes) != g.n or any(t < 0 or t > self.population for t in self.votes):
            raise ValueError("Invalid population state")
        d, c, z = self.sets()
        if not g.feasible(self.incumbent) or not c <= self.incumbent or self.incumbent & z:
            raise ValueError("Invalid incumbent/votes")
        if any(g.adj[u] & d for u in c):
            raise ValueError("Consensus-selected vertices cannot touch disagreement vertices")

    def to_json(self):
        return {"votes": self.votes, "population": self.population, "incumbent": sorted(self.incumbent), "iteration": self.iteration}

    @classmethod
    def from_json(cls, obj):
        return cls(tuple(obj["votes"]), int(obj["population"]), frozenset(obj["incumbent"]), int(obj.get("iteration", 0)))


@dataclass(frozen=True)
class Action:
    kind: str
    anchor: int
    unlocked: frozenset[int]
    blockers: frozenset[int]

    @property
    def extra(self):
        return self.unlocked | self.blockers

    def to_json(self):
        return {"kind": self.kind, "anchor": self.anchor, "unlocked": sorted(self.unlocked), "blockers": sorted(self.blockers)}

    @classmethod
    def from_json(cls, obj):
        return cls(obj["kind"], int(obj["anchor"]), frozenset(obj["unlocked"]), frozenset(obj["blockers"]))


def make_action(g, state, unlocked, kind="custom", anchor=-1):
    d, c, z = state.sets()
    u = frozenset(unlocked)
    if not u <= z:
        raise ValueError("Only unanimous-excluded vertices may be unlocked")
    b = frozenset().union(*(g.adj[v] & c for v in u)) if u else frozenset()
    return Action(kind, anchor, u, b)


def verify_action(g, state, action):
    d, c, z = state.sets()
    if not action.unlocked <= z or not action.blockers <= c:
        raise ValueError("Action outside consensus classes")
    expected = frozenset().union(*(g.adj[v] & c for v in action.unlocked)) if action.unlocked else frozenset()
    if action.blockers != expected:
        raise ValueError("Missing or extraneous boundary blockers")
    region = d | action.extra
    outside = c - action.blockers
    if any(g.adj[u] & outside for u in region):
        raise ValueError("Boundary closure violated")
    return region


def propose_actions(g: Graph, state: State, cfg: ActionConfig, seed: int):
    d, c, z = state.sets()
    rng = random.Random((int(seed) << 32) ^ state.iteration)
    anchors = rng.sample(sorted(z), min(cfg.anchors, len(z)))
    actions = [Action("noop", -1, frozenset(), frozenset())]
    seen = {frozenset()}
    def add(u, kind, anchor):
        u = frozenset(u)
        if not u or len(u) > cfg.max_unlocked:
            return
        a = make_action(g, state, u, kind, anchor)
        if len(a.extra) > cfg.max_extra or a.extra in seen:
            return  # Reject WHOLE action. Never silently truncate a core/closure.
        seen.add(a.extra); actions.append(a)
    for v in anchors:
        add({v}, "single", v)
        b = g.adj[v] & c
        shared = {v}
        for x in b:
            shared.update(g.adj[x] & z)
        add(shared, "shared_blockers", v)
        two = {v}
        for x in g.adj[v]:
            two.update(g.adj[x] & z)
            if len(two) > cfg.max_unlocked:
                break
        add(two, "two_hop", v)
        # Time-window actions are available only when real metadata exists.
        # IDs determine equality only; their spelling is never a scoring feature.
        if "start_ticks" in g.meta:
            starts = g.meta["start_ticks"]
            for key in ("antenna_id", "satellite_id"):
                if key in g.meta:
                    resource = g.meta[key]
                    group = {u for u in z if resource[u] == resource[v] and abs(int(starts[u])-int(starts[v])) <= cfg.resource_horizon_ticks}
                    add(group, key+"_window", v)
    return actions


def components(g, vertices):
    remaining = set(vertices)
    out = []
    while remaining:
        v = min(remaining); remaining.remove(v)
        seen, todo = {v}, [v]
        while todo:
            u = todo.pop()
            new = g.adj[u] & remaining
            remaining.difference_update(new); seen.update(new); todo.extend(new)
        out.append(frozenset(seen))
    return out


def affected_disagreement(g, state, action):
    """All D-components touched by an action, not an arbitrary radius truncation."""
    d, _, _ = state.sets()
    touched = frozenset().union(*(g.adj[u] & d for u in action.extra)) if action.extra else frozenset()
    return frozenset().union(*(comp for comp in components(g, d) if comp & touched))
