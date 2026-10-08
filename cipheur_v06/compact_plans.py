"""Lossless factorization of the shared finite plan library for HTTP transport.

This module computes no evidence, reward, selection, identity or permissions.
Every exact plan ID and descriptor field survives a JSON round trip. Classical
controllers can continue to read the full registry; the model receives the same
information with repeated steps and operation-specific context factored out.
"""
from __future__ import annotations

import copy
import json


SCHEMA = 'cipheur_v06_factored_plan_library_v1'
PLAN_COLUMNS = ['plan_id', 'template_id', 'operation_id', 'fields']


def _key(value):
    def check(item):
        if type(item) in (str, int, float, bool, type(None)):
            return
        if type(item) is list:
            for member in item:check(member)
            return
        if type(item) is dict and all(type(k) is str for k in item):
            for member in item.values():check(member)
            return
        raise ValueError('Ordinary JSON values and string object keys required')
    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def compact_plan_library(library):
    """Factor repeated steps and genuine unique operations without pruning IDs."""
    if not isinstance(library, dict) or any(not isinstance(k, str) or
            not isinstance(v, dict) for k, v in library.items()):
        raise ValueError('Plan library must map exact string IDs to descriptors')
    # Verify ordinary JSON transportability rather than inventing conversions.
    _key(library)
    templates, operations, rows = {}, {}, []
    template_keys, operation_keys, groups = {}, {}, {}
    for descriptor in library.values():
        if isinstance(descriptor.get('operation'), dict):
            groups.setdefault(_key(descriptor['operation']), []).append(descriptor)
    common_context = {}
    for op_key, group in groups.items():
        if all(isinstance(d.get('context'), dict) for d in group):
            first = group[0]['context']
            common_context[op_key] = {key: copy.deepcopy(value) for key, value in first.items()
                if all(key in d['context'] and _key(d['context'][key]) == _key(value)
                       for d in group[1:])}
    for plan_id, descriptor in library.items():
        residual = copy.deepcopy(descriptor)
        template_id = None
        if 'steps' in residual:
            block = {'steps': residual.pop('steps')}
            key = _key(block)
            if key not in template_keys:
                template_keys[key] = 't' + str(len(templates)).zfill(3)
                templates[template_keys[key]] = block
            template_id = template_keys[key]
        operation_id = None
        if isinstance(residual.get('operation'), dict):
            operation = residual.pop('operation')
            key = _key(operation)
            if key not in operation_keys:
                operation_keys[key] = 'o' + str(len(operations)).zfill(3)
                block = {'operation': operation}
                common = common_context.get(key)
                if common:
                    block['context'] = copy.deepcopy(common)
                operations[operation_keys[key]] = block
            operation_id = operation_keys[key]
            common = common_context.get(key)
            if common:
                # Empty context is retained, so its presence is exactly restored.
                residual['context'] = {k: v for k, v in residual['context'].items()
                                       if k not in common}
        rows.append([plan_id, template_id, operation_id, residual])
    return {'schema': SCHEMA, 'templates': templates, 'operations': operations,
            'plan_columns': list(PLAN_COLUMNS), 'plans': rows}


def expand_plan_library(library):
    """Decode a self-contained wire library, preserving every original field.

    A full uncompressed library is accepted as a detached identity operation for
    existing fixtures and saved evidence. Unknown schemas or broken references
    fail instead of silently changing a candidate's permissions.
    """
    if not isinstance(library, dict):
        raise ValueError('Plan library must be an object')
    if library.get('schema') != SCHEMA:
        if 'schema' in library:
            raise ValueError('Unknown factored plan library schema')
        if any(not isinstance(k, str) or not isinstance(v, dict) for k, v in library.items()):
            raise ValueError('Malformed full plan library')
        _key(library)
        return copy.deepcopy(library)
    _key(library)
    expected = {'schema', 'templates', 'operations', 'plan_columns', 'plans'}
    if set(library) != expected or library['plan_columns'] != PLAN_COLUMNS:
        raise ValueError('Unexpected factored plan library fields or columns')
    templates, operations = library['templates'], library['operations']
    if not isinstance(templates, dict) or not isinstance(operations, dict) or \
            not isinstance(library['plans'], list):
        raise ValueError('Malformed factored plan tables')
    for name, block in templates.items():
        if not isinstance(name, str) or not isinstance(block, dict) or set(block) != {'steps'}:
            raise ValueError('Malformed plan template')
    for name, block in operations.items():
        if not isinstance(name, str) or not isinstance(block, dict) or \
                set(block) - {'operation', 'context'} or not isinstance(block.get('operation'), dict) or \
                ('context' in block and not isinstance(block['context'], dict)):
            raise ValueError('Malformed plan operation')
    result = {}
    for row in library['plans']:
        if not isinstance(row, list) or len(row) != len(PLAN_COLUMNS):
            raise ValueError('Malformed plan row')
        plan_id, template_id, operation_id, fields = row
        if not isinstance(plan_id, str) or plan_id in result or not isinstance(fields, dict):
            raise ValueError('Invalid or duplicate exact plan ID')
        descriptor = {}
        if template_id is not None:
            if not isinstance(template_id, str) or template_id not in templates or 'steps' in fields:
                raise ValueError('Unknown or conflicting plan template reference')
            descriptor.update(copy.deepcopy(templates[template_id]))
        if operation_id is not None:
            if not isinstance(operation_id, str) or operation_id not in operations or 'operation' in fields:
                raise ValueError('Unknown or conflicting plan operation reference')
            descriptor.update(copy.deepcopy(operations[operation_id]))
        residual = copy.deepcopy(fields)
        if 'context' in descriptor and 'context' in residual:
            if not isinstance(residual['context'], dict) or set(descriptor['context']) & set(residual['context']):
                raise ValueError('Conflicting factored plan context fields')
            descriptor['context'].update(residual.pop('context'))
        descriptor.update(residual)
        result[plan_id] = descriptor
    _key(result)
    return result


def compact_packet(packet):
    """Return a detached packet; only its plan-library representation changes."""
    if not isinstance(packet, dict) or 'plan_library' not in packet:
        raise ValueError('Decision packet lacks its shared plan library')
    result = copy.deepcopy(packet)
    result['plan_library'] = compact_plan_library(packet['plan_library'])
    return result
