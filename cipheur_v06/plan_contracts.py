"""Bind a data-only choice to the exact finite plans in a frozen observation."""
import copy
from cipheur_v05.schema import digest

def validate_choice(obj,packet):
    if not isinstance(obj,dict) or set(obj)!={'snapshot_id','plan_id','hypothesis','evidence'}:
        raise ValueError('Exact plan choice fields required')
    if obj['snapshot_id']!=packet['snapshot_id']: raise ValueError('Snapshot binding mismatch')
    if not isinstance(obj['plan_id'],str) or obj['plan_id'] not in packet['plan_library']:
        raise ValueError('Unknown frozen plan')
    if not isinstance(obj['hypothesis'],str) or len(obj['hypothesis'])>1500: raise ValueError('hypothesis')
    if not isinstance(obj['evidence'],list) or len(obj['evidence'])>8 or any(not isinstance(x,str) or len(x)>160 for x in obj['evidence']):
        raise ValueError('evidence')
    return copy.deepcopy(obj)

def plan_identity(descriptor):
    return digest({'steps':descriptor['steps'],'operation':descriptor.get('operation')})

def resolve_choice(obj,packet,registry):
    choice=validate_choice(obj,packet)
    if choice['plan_id'] not in registry: raise ValueError('Missing bound native descriptor')
    descriptor=copy.deepcopy(registry[choice['plan_id']])
    if packet['plan_library'][choice['plan_id']].get('identity')!=plan_identity(descriptor):
        raise ValueError('Frozen plan identity differs')
    return choice,descriptor
