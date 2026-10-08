"""Target-slot evidence for bounded repair plans, without changing search.

Only the existing decision packet's six anchor witnesses are inspected. The
selected sets remain private to this function; exact graph edges and original
integer weights determine every target-specific blocker count and weight.
"""
import copy
from numbers import Integral


def _vertex(value, n):
    if isinstance(value, bool) or not isinstance(value, Integral) or not 0 <= int(value) < n:
        raise ValueError('Anchor/selected vertex must be an integer in the original graph')
    return int(value)


def _resource_array(g, key):
    values = g.meta.get(key)
    if values is None:
        return None
    if len(values) != g.n:
        raise ValueError('Resource metadata length differs from the original graph')
    return values


def augment_packet(packet, snapshot, g):
    """Return a detached full packet with canonical non-best repair descriptors.

    This describes evidence, not permission to mutate a slot. The native solver
    still validates any proposed operation. Scalar packets stay scalar.
    """
    result = copy.deepcopy(packet)
    resources = packet.get('resources')
    if not isinstance(resources, dict):
        return result
    anchors, seen = [], set()
    for rank, witness in enumerate(resources.get('barrier_witnesses', [])[:6], 1):
        vertex = _vertex(witness['vertex'], g.n)
        if vertex not in seen:
            seen.add(vertex)
            anchors.append((rank, vertex))
    slots = sorted(snapshot['slots'], key=lambda row: row['slot'])
    if len({s['slot'] for s in slots}) != len(slots):
        raise ValueError('Snapshot has duplicate slot IDs')
    best = next((s for s in slots if s['slot'] == snapshot['best_slot']), None)
    if best is None:
        raise ValueError('Protected best slot is absent from the snapshot')
    selected = {s['slot']: {_vertex(v, g.n) for v in s['selected']} for s in slots}
    best_set = selected[best['slot']]
    best_value = int(best['value_ticks'])
    ant, sat = _resource_array(g, 'antenna_id'), _resource_array(g, 'satellite_id')
    descriptors = []
    for slot in slots:
        if slot['slot'] == best['slot']:
            continue
        slot_set = selected[slot['slot']]
        union_size = len(slot_set | best_set)
        # Integer half-up rounding avoids conversion of original weights to float.
        overlap = ((len(slot_set & best_set) * 1000000 + union_size // 2) // union_size) if union_size else 1000000
        witnesses = []
        for rank, vertex in anchors:
            blockers = sorted(set(g.adj[vertex]) & slot_set)
            blocker_weight = sum(int(g.weights[u]) for u in blockers)
            witnesses.append({'vertex': vertex, 'rank': rank,
                'anchor_selected': vertex in slot_set, 'weight_ticks': int(g.weights[vertex]),
                'blocker_count': len(blockers), 'blocker_weight_ticks': blocker_weight,
                'blocker_ids_sample': blockers[:16],
                'same_antenna_blockers': sum(bool(ant[vertex] == ant[u]) for u in blockers) if ant is not None else None,
                'same_satellite_blockers': sum(bool(sat[vertex] == sat[u]) for u in blockers) if sat is not None else None,
                'weight_minus_blockers_ticks': int(g.weights[vertex]) - blocker_weight})
        samples = [u for w in witnesses for u in w['blocker_ids_sample']]
        distinct = len(set(samples))
        shared_pairs = []
        for i, left in enumerate(witnesses):
            for right in witnesses[i + 1:]:
                shared = len(set(left['blocker_ids_sample']) & set(right['blocker_ids_sample']))
                if shared:
                    shared_pairs.append([left['vertex'], right['vertex'], shared])
        descriptors.append({'slot': int(slot['slot']), 'value_ticks': int(slot['value_ticks']),
            'value_gap_ticks': best_value - int(slot['value_ticks']),
            'jaccard_with_best_ppm': int(overlap), 'anchor_witnesses': witnesses,
            'blocker_reuse_sample': {'occurrences': len(samples), 'distinct': distinct,
                'reuse_fraction': round(1 - distinct / len(samples), 6) if samples else 0.,
                'shared_pairs': shared_pairs[:12]},
            'resource_metadata_available': {'antenna': ant is not None, 'satellite': sat is not None}})
    result['repair_slots'] = descriptors
    result['repair_slot_scope'] = {
        'protected_best_slot': int(best['slot']),
        'anchor_scope': 'At most six existing best-slot barrier vertices; target-slot blockers recomputed from original adjacency and selected sets. No new anchors or full selected sets are exposed.',
        'blocker_scope': 'Counts/weights use every blocker; IDs capped at 16 per anchor and reuse statistics use those ID samples.',
        'interpretation': 'A weight-minus-blockers arithmetic margin is not an observed repair gain or a causal estimate. Descriptors do not grant mutation permission.'}
    return result
