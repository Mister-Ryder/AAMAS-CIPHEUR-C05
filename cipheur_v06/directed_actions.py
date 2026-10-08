"""Explicit slot/root/resource-priority reseeding over the unchanged native API.

An operation is data. Invalid requests never choose a replacement root or slot.
Each temporal step seeds once, then spends its remaining wall cap on portfolio
ILS. Native objective weights and feasibility constraints remain unchanged.
"""
from __future__ import annotations
import copy
import math
import time

from cipheur_v04.program import Program
from cipheur_v05.schema import digest
from .actions import _statistics, stage_for

TEMPLATES = ('antenna', 'satellite', 'mixed')


def program_for(template, graph):
    """Compile the registry's exact rooted priority; no alternative formula."""
    from .plans import priority_for
    priority = priority_for(template)
    program = Program({'schema': 4, 'name': 'V06_DIRECTED_RESEED_' + template.upper(),
                       'operators': [{'generator': {'op': 'seed'}, 'priority': priority,
                                      'anchor': 'any', 'donor_limit': 256}],
                       'selector': {'op': 'sub', 'a': {'feature': 'weight_donor'},
                                    'b': {'feature': 'weight_blockers'}},
                       'provenance': {'origin': 'registry_rooted_resource_priority', 'template': template}})
    program.validate_graph(graph)
    return program


def _validate(operation, engine, snapshot, enter):
    if not isinstance(operation, dict) or set(operation) != {'slot', 'root', 'template'}:
        return 'rejected_invalid_operation', 'exact_slot_root_template_fields_required'
    slot, root, template = operation['slot'], operation['root'], operation['template']
    if type(slot) is not int or not 1 <= slot < 4:
        return 'rejected_invalid_operation', 'slot_must_be_nonzero_and_in_population'
    if type(root) is not int or not 0 <= root < engine.g.n:
        return 'rejected_invalid_operation', 'root_out_of_graph_bounds'
    if template not in TEMPLATES:
        return 'rejected_invalid_operation', 'unknown_resource_priority_template'
    if enter and slot == snapshot['best_slot']:
        return 'rejected_invalid_operation', 'protected_current_best_slot'
    required = {'start_ticks', 'end_ticks'}
    if template in ('antenna', 'mixed'):
        required.update(('antenna_id', 'ground_gap_by_node_ticks'))
    if template in ('satellite', 'mixed'):
        required.update(('satellite_id', 'satellite_gap_by_node_ticks'))
    absent = sorted(required - set(engine.g.meta))
    if absent:
        return 'unavailable_metadata', 'missing:' + ','.join(absent)
    if any(len(engine.g.meta[key]) != engine.g.n for key in required):
        return 'unavailable_metadata', 'metadata_length_differs_from_graph'
    return 'ok', None


def execute_directed(engine, operation, cfg, seconds, enter=True):
    """Return the actions.execute effect contract, with an explicit seed boundary.

Invalid operations return a distinct no-mutation result. The caller may choose
an explicit baseline fallback in a subsequent bounded step. There is no silent
remapping. ``enter=False`` never reseeds, even if the chosen slot became best.
"""
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError('Finite nonnegative directed-action wall budget required')
    if cfg.population != 4 or type(enter) is not bool:
        raise ValueError('Directed actions require population=4 and boolean enter')
    started, cpu_started = time.perf_counter(), time.process_time()
    deadline = started + seconds
    before = engine.snapshot(False)
    if len(before['slots']) != 4:
        raise ValueError('Native population differs from population=4')
    before_stats = _statistics(engine.report())
    status, reason = _validate(operation, engine, before, enter)
    effects, seed_boundary = [], None
    requested = copy.deepcopy(operation)
    program = None
    actual_root = actual_slot = None
    seed_installed = False
    stage = stage_for('reseed', before)
    if status != 'ok':
        effects.append({'type': 'directed_operation_rejected', 'reason': reason,
                        'status': status, 'requested_operation': requested})
    elif time.perf_counter() >= deadline:
        status, reason = 'budget_exhausted_before_execution', 'preparation_consumed_wall_cap'
    else:
        template = operation['template']
        if not hasattr(engine, '_v06_directed_programs'):
            engine._v06_directed_programs = {}
        if template not in engine._v06_directed_programs:
            engine._v06_directed_programs[template] = program_for(template, engine.g)
        program = engine._v06_directed_programs[template]
        if time.perf_counter() >= deadline:
            status, reason = 'budget_exhausted_before_execution', 'program_compilation_consumed_wall_cap'
        else:
            changed = program.fingerprint() != engine.program.fingerprint()
            if changed:
                engine.set_program(program)
            effects.append({'type': 'program', 'changed': changed, 'program_sha256': program.fingerprint()})
            if enter:
                remaining = max(0., deadline - time.perf_counter())
                budget = min(.25, seconds * .5, remaining)
                if budget <= 0:
                    status, reason = 'budget_exhausted_before_seed', 'program_installation_consumed_wall_cap'
                else:
                    actual_slot, actual_root = operation['slot'], operation['root']
                    work = min(10_000_000, cfg.initialization_work)
                    native = engine.seed_slot(actual_slot, 0, actual_root, budget, work)
                    seed_installed = native.get('installed') is True
                    reason = native.get('reason')
                    effects.append({'type': 'directed_reseed', 'requested_root': operation['root'],
                                    'actual_root': actual_root, 'requested_slot': operation['slot'],
                                    'actual_slot': actual_slot, 'template': template,
                                    'protected_best_slot': before['best_slot'], 'protected_zero_slot': True,
                                    'allocated_seconds': budget, 'allocated_work': work, **native})
                    full = engine.snapshot()
                    seeded = next(slot for slot in full['slots'] if slot['slot'] == actual_slot)
                    seed_boundary = copy.deepcopy(seeded)
                    seed_boundary['historical_value_ticks'] = full['value_ticks']
                    seed_boundary['diversity_ppm'] = full['diversity_ppm']
                    if not engine.g.feasible(seeded['selected']) or engine.g.weight(seeded['selected']) != seeded['value_ticks']:
                        raise AssertionError('Native directed seed failed original-graph integer audit')
                    if not seed_installed:
                        status = 'seed_not_installed'
            remaining = max(0., deadline - time.perf_counter())
            if remaining > 0:
                epoch_started = time.perf_counter()
                engine.epoch(stage, cfg, remaining)
                effects.append({'type': 'epoch', 'allocated_seconds': remaining,
                                'wall_seconds': time.perf_counter() - epoch_started, 'stage': copy.deepcopy(stage)})
    after = engine.snapshot(False)
    after_stats = _statistics(engine.report())
    if after['value_ticks'] < before['value_ticks']:
        raise AssertionError('Historical incumbent was lost')
    used = time.perf_counter() - started
    priority = program.obj['operators'][0]['priority'] if program is not None else None
    return {'action': 'directed_reseed', 'directed': True, 'status': status, 'reason': reason, 'enter': enter,
            'operation': requested, 'requested_operation': requested,
            'requested_root': operation.get('root') if isinstance(operation, dict) else None,
            'requested_slot': operation.get('slot') if isinstance(operation, dict) else None,
            'actual_root': actual_root, 'actual_slot': actual_slot,
            'seed_installed': seed_installed, 'seed_boundary': seed_boundary,
            'priority_ast': copy.deepcopy(priority), 'priority_sha256': digest(priority) if priority is not None else None,
            'program_sha256': program.fingerprint() if program is not None else None,
            'before': before, 'after': after, 'gain_ticks': after['value_ticks'] - before['value_ticks'],
            'pool_gain_ticks': sum(s['value_ticks'] for s in after['slots']) - sum(s['value_ticks'] for s in before['slots']),
            'slot_gain_ticks': [r['value_ticks'] - l['value_ticks'] for l, r in zip(before['slots'], after['slots'])],
            'diversity_delta_ppm': after['diversity_ppm'] - before['diversity_ppm'],
            'cycles': after['cycles'] - before['cycles'], 'effects': effects,
            'interventions': [e for e in effects if e['type'] == 'directed_reseed'],
            'statistics_delta': {key: after_stats[key] - value for key, value in before_stats.items() if key in after_stats},
            'stage': stage, 'allocated_seconds': seconds, 'wall_seconds': used, 'usedwall': used,
            'cpu_seconds': time.process_time() - cpu_started, 'overrun_seconds': max(0., used - seconds),
            'budget_scope': 'validation, program compilation/install, explicit seed, seed certificate, portfolio epoch and effects; soft wall cap',
            'priority_semantics': 'native signed saturating int64 DSL; div(a,b)=a/(1+abs(b)), truncating toward zero; original objective weights unchanged',
            'continuation_scope': 'round-robin portfolio over four original slots; only the entry seed has an explicitly selected slot'}
