"""Compact, honest search evidence shared by model and numerical controllers.

Raw epochs stay in solver results. The decision packet summarizes the whole
observed trajectory and retains recent contiguous action/installation episodes.
Temporal association after an intervention is never labelled a causal effect.
"""
import collections
import numpy as np
from cipheur_v05.schema import digest


def mean(values):
    return sum(values) / len(values) if values else 0.


def _start(row):
    if 'start_elapsed_seconds' in row:
        return row['start_elapsed_seconds']
    return row['elapsed_seconds'] - row.get('wall_seconds', 0.)


def _window(history, elapsed, seconds):
    # Gains are only known at epoch boundaries. No fabricated fractional gain.
    rows = [h for h in history if elapsed - seconds < h['elapsed_seconds'] <= elapsed]
    wall = sum(h.get('wall_seconds', 0.) for h in rows)
    gain = sum(h.get('gain_ticks', 0) for h in rows)
    pool_gain = sum(h.get('pool_gain_ticks', 0) for h in rows)
    return {'gain_ticks': gain, 'pool_gain_ticks': pool_gain,
            'observed_search_seconds': round(wall, 6),
            'gain_ticks_per_search_second': round(gain / wall, 3) if wall else None,
            'positive_epochs': sum(h.get('gain_ticks', 0) > 0 for h in rows),
            'epochs': len(rows)}


def summarize_history(history, elapsed, limit=8):
    """Return bounded episodes plus summaries computed from all original epochs."""
    episodes = []
    outcomes = collections.defaultdict(lambda: {
        'seconds': 0., 'gain_ticks': 0, 'pool_gain_ticks': 0,
        'cycles': 0, 'zero_blocks': 0, 'blocks': 0, 'interventions': 0})
    previous_diversity = None
    recoveries = []
    for position, h in enumerate(history):
        action = h['action']
        key = (action, h.get('origin'), h.get('install_index'), h.get('status', 'ok'))
        out = outcomes[action]
        out['seconds'] += h.get('wall_seconds', 0.)
        out['gain_ticks'] += h.get('gain_ticks', 0)
        out['pool_gain_ticks'] += h.get('pool_gain_ticks', 0)
        out['cycles'] += h.get('cycles', 0)
        out['zero_blocks'] += h.get('gain_ticks', 0) == 0
        out['blocks'] += 1
        interventions = h.get('interventions', [])
        out['interventions'] += len(interventions)
        if not episodes or episodes[-1]['_key'] != key:
            episodes.append({'_key': key, 'index': h.get('index', position),
                'last_index': h.get('index', position), 'action': action,
                'origin': h.get('origin'), 'install_index': h.get('install_index'),
                'status': h.get('status', 'ok'), 'start_elapsed_seconds': _start(h),
                'elapsed_seconds': h['elapsed_seconds'], 'wall_seconds': 0.,
                'gain_ticks': 0, 'pool_gain_ticks': 0, 'cycles': 0, 'epochs': 0,
                'value_start_ticks': h.get('value_ticks', 0) - h.get('gain_ticks', 0),
                'value_end_ticks': h.get('value_ticks'),
                'diversity_start_ppm': h.get('diversity_before_ppm', previous_diversity),
                'diversity_end_ppm': h.get('diversity_ppm'), 'intervention_count': 0})
        e = episodes[-1]
        e.update(last_index=h.get('index', position), elapsed_seconds=h['elapsed_seconds'],
                 value_end_ticks=h.get('value_ticks'), diversity_end_ppm=h.get('diversity_ppm'))
        for field in ('wall_seconds', 'gain_ticks', 'pool_gain_ticks', 'cycles'):
            e[field] += h.get(field, 0)
        e['epochs'] += 1
        e['intervention_count'] += len(interventions)
        previous_diversity = h.get('diversity_ppm')
        for intervention in interventions:
            if intervention.get('type') not in ('kick', 'reseed', 'directed_reseed'):
                continue
            at = _start(h)
            following = history[position:]
            first_best = next((x['elapsed_seconds'] for x in following if x.get('gain_ticks', 0) > 0), None)
            first_pool = next((x['elapsed_seconds'] for x in following if x.get('pool_gain_ticks', 0) > 0), None)
            recoveries.append({'action': action, 'index': h.get('index', position),
                'start_elapsed_seconds': round(at, 6),
                'effect_report': {k: intervention[k] for k in ('type', 'slot', 'accepted', 'installed', 'status') if k in intervention},
                'first_later_best_gain_delay_seconds': round(first_best - at, 6) if first_best is not None else None,
                'first_later_pool_gain_delay_seconds': round(first_pool - at, 6) if first_pool is not None else None,
                'observed_followup_seconds': round(max(0., elapsed - at), 6)})
    stagnant = 0.
    for h in reversed(history):
        if h.get('gain_ticks', 0) > 0:
            break
        stagnant += h.get('wall_seconds', 0.)
    recent = []
    for episode in episodes[-max(1, min(12, int(limit))):]:
        episode = {k: v for k, v in episode.items() if k != '_key'}
        for field in ('start_elapsed_seconds', 'elapsed_seconds', 'wall_seconds'):
            episode[field] = round(episode[field], 6)
        recent.append(episode)
    for out in outcomes.values():
        out['seconds'] = round(out['seconds'], 6)
    summary = {'epochs_observed': len(history), 'episodes_observed': len(episodes),
               'episodes_retained': len(recent), 'stagnation_search_seconds': round(stagnant, 6),
               'windows': {str(s): _window(history, elapsed, s) for s in (10, 40, 120)},
               'recent_intervention_followup': recoveries[-4:],
               'uncertainty': 'Single observed path; action outcomes are state-dependent, not randomized causal estimates. Windows include whole completed epochs; no fractional gain is inferred. Null recovery delay is right-censored or unobserved, not zero.'}
    return recent, dict(outcomes), summary


class Observer:
    def __init__(self, g):
        self.g = g
        self.history_limit = 8
        self.weights = np.asarray(g.weights, dtype=np.int64)
        self._csr = g.csr()
        self.ptr, self.edges = self._csr[1], self._csr[2]
        self.degree = np.diff(self.ptr)
        self.ant = g.meta.get('antenna_id', [None] * g.n)
        self.sat = g.meta.get('satellite_id', [None] * g.n)
        self.starts = g.meta.get('start_ticks', [0] * g.n)
        self.gaps = g.meta.get('ground_gap_by_node_ticks', [0] * g.n)
        self.candidates = np.argsort(-self.weights, kind='stable')[:128].tolist()
        self.static = {'n': g.n, 'm': g.m, 'mean_degree': float(mean(self.degree.tolist())),
            'antennas': len(set(self.ant)), 'satellites': len(set(self.sat)),
            'ground_gaps_seconds': sorted({x / 1e6 for x in self.gaps}),
            'source_id': g.meta.get('source_id'),
            'objective': 'original integer contact-duration ticks; 1e6 ticks/second',
            'constraints': 'original graph plus per-node antenna and satellite cooldowns; never changed'}

    def packet(self, snapshot, history, elapsed, total, library, examples=(), input_mode='full'):
        recent, outcomes, summary = summarize_history(history, elapsed, self.history_limit)
        telemetry = {'elapsed_seconds': round(elapsed, 6), 'remaining_seconds': round(max(0, total - elapsed), 6),
            'value_ticks': snapshot['value_ticks'], 'diversity_ppm': snapshot['diversity_ppm'],
            'stagnation_seconds': summary['stagnation_search_seconds'], 'cycles': snapshot['cycles'],
            'recent_gain_ticks': summary['windows']['10']['gain_ticks'],
            'earlier_gain_ticks': summary['windows']['40']['gain_ticks'] - summary['windows']['10']['gain_ticks'],
            'generated': snapshot.get('generated', 0), 'committed': snapshot.get('committed', 0),
            'program_positive': snapshot.get('program_positive', 0)}
        obj = {'telemetry': telemetry,
               'population': [{k: v for k, v in s.items() if k != 'selected'} for s in snapshot['slots']],
               'static': self.static, 'action_library': library}
        if input_mode != 'scalar':
            obj.update(history=recent, history_summary=summary, action_outcomes=outcomes,
                       resources=self.resource(snapshot), prior_counterfactual_examples=list(examples))
        # Commit to all observed epochs without putting them into a model prompt.
        obj['snapshot_id'] = digest({'snapshot': snapshot, 'elapsed': elapsed, 'history': history})
        return obj

    def resource(self, snapshot):
        best = snapshot['best_slot']
        selected = set(next(s['selected'] for s in snapshot['slots'] if s['slot'] == best))
        bits = np.zeros(self.g.n, dtype=bool)
        bits[list(selected)] = True
        ant_weight, ant_count = collections.Counter(), collections.Counter()
        for v in selected:
            ant_weight[str(self.ant[v])] += int(self.weights[v])
            ant_count[str(self.ant[v])] += 1
        barriers = []
        for v in self.candidates:
            if v in selected:
                continue
            adjacent = self.edges[self.ptr[v]:self.ptr[v + 1]]
            blockers = adjacent[bits[adjacent]]
            if not len(blockers):
                continue
            blocker_weight = sum(int(self.weights[u]) for u in blockers)
            barriers.append({'vertex': int(v), 'weight_ticks': int(self.weights[v]),
                'blocker_count': len(blockers), 'blocker_weight_ticks': blocker_weight,
                'blocker_ids_sample': [int(u) for u in blockers[:16]],
                'same_antenna_blockers': sum(self.ant[v] == self.ant[u] for u in blockers),
                'same_satellite_blockers': sum(self.sat[v] == self.sat[u] for u in blockers),
                'antenna': str(self.ant[v]), 'satellite': str(self.sat[v]),
                'start_seconds': self.starts[v] / 1e6, 'ground_gap_seconds': self.gaps[v] / 1e6})
        barriers = sorted(barriers, key=lambda b: b['weight_ticks'] / max(1, b['blocker_weight_ticks']), reverse=True)[:6]
        all_sample_blockers = [u for b in barriers for u in b['blocker_ids_sample']]
        distinct = len(set(all_sample_blockers))
        shared_pairs = []
        for i, left in enumerate(barriers):
            for right in barriers[i + 1:]:
                shared = len(set(left['blocker_ids_sample']) & set(right['blocker_ids_sample']))
                if shared:
                    shared_pairs.append([left['vertex'], right['vertex'], shared])
        donor_pairs = []
        canonical_slots = sorted(snapshot['slots'], key=lambda s: s['slot'])
        for i, left in enumerate(canonical_slots):
            for right in canonical_slots[i + 1:]:
                lset, rset = set(left['selected']), set(right['selected'])
                exclusive = lset - rset
                sample = sorted(exclusive, key=lambda v: (-int(self.weights[v]), v))[:64]
                rbits = np.zeros(self.g.n, dtype=bool)
                rbits[list(rset - lset)] = True
                contacts_in_conflict = sum(bool(np.any(rbits[self.edges[self.ptr[v]:self.ptr[v + 1]]])) for v in sample)
                donor_pairs.append({'slots': [left['slot'], right['slot']],
                    'jaccard_ppm': round(1e6 * len(lset & rset) / max(1, len(lset | rset))),
                    'exclusive_sample_size': len(sample),
                    'cross_conflict_fraction': round(contacts_in_conflict / len(sample), 6) if sample else None})
        fractions = [p['cross_conflict_fraction'] for p in donor_pairs if p['cross_conflict_fraction'] is not None]
        return {'sample_scope': 'Six best duration/blocker ratios among at most 128 longest unselected contacts; blockers are exact original edges; sample is not exhaustive and a ratio does not establish profitable exchange.',
                'selected_contact_seconds_by_antenna': {a: round(w / 1e6, 6) for a, w in ant_weight.items()},
                'selected_counts_by_antenna': dict(ant_count), 'barrier_witnesses': barriers,
                'structure': {'sample_blocker_occurrences': len(all_sample_blockers),
                    'sample_distinct_blockers': distinct,
                    'blocker_reuse_fraction': round(1 - distinct / len(all_sample_blockers), 6) if all_sample_blockers else 0.,
                    'shared_blocker_pairs': shared_pairs[:12],
                    'donor_mean_cross_conflict_fraction': round(mean(fractions), 6) if fractions else None,
                    'donor_pairs': donor_pairs,
                    'scope': 'Blocker IDs capped at 16 per witness; shared counts are sample counts. Donor conflicts use at most 64 longest left-exclusive contacts against all right-exclusive contacts; direction is ordered by slot, not a global graph density.'}}
