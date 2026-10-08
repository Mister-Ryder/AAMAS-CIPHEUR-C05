"""A small data-only temporal contract; no generated code is executed."""
from dataclasses import dataclass, asdict
import copy, math
from cipheur_v05.schema import strict_json, digest

@dataclass(frozen=True)
class Config:
    seconds: float = 360.
    seed: int = 7
    population: int = 4
    epoch_seconds: float = 1.
    decision_interval: float = 40.
    first_decision: float = 20.
    request_timeout: float = 45.
    response_ttl: float = 60.
    max_calls: int = 8
    context_history: int = 30

    def __post_init__(self):
        if self.population != 4:
            raise ValueError('The preregistered v0.6 study requires population=4')
        for name in ('seconds','epoch_seconds','decision_interval','request_timeout','response_ttl'):
            x=getattr(self,name)
            if type(x) not in (int,float) or not math.isfinite(x) or x<=0: raise ValueError(name)
        if type(self.seed) is not int or not 0<=self.seed<2**32: raise ValueError('seed')
        if type(self.max_calls) is not int or not 0<=self.max_calls<=12: raise ValueError('max_calls')
        if type(self.first_decision) not in (int,float) or not math.isfinite(self.first_decision) or self.first_decision<0: raise ValueError('first_decision')
        if type(self.context_history) is not int or not 1<=self.context_history<=120: raise ValueError('context_history')
    def json(self): return asdict(self)

def validate(obj, snapshot_id, allowed_actions):
    """Reject incorrect binding, extra fields, nonfinite numbers and long plans."""
    if not isinstance(obj,dict) or set(obj)!={'snapshot_id','steps','hypothesis','evidence'}:
        raise ValueError('Required exact fields: snapshot_id, steps, hypothesis, evidence')
    if obj['snapshot_id']!=snapshot_id: raise ValueError('Snapshot binding mismatch')
    if not isinstance(obj['hypothesis'],str) or len(obj['hypothesis'])>1500: raise ValueError('hypothesis')
    if not isinstance(obj['evidence'],list) or len(obj['evidence'])>8 or any(not isinstance(x,str) or len(x)>160 for x in obj['evidence']): raise ValueError('evidence')
    steps=obj['steps']
    if not isinstance(steps,list) or not 1<=len(steps)<=3: raise ValueError('One to three temporal steps')
    for step in steps:
        if not isinstance(step,dict) or set(step)!={'action','seconds','until'}: raise ValueError('step fields')
        if step['action'] not in allowed_actions: raise ValueError('Unknown action')
        if type(step['seconds']) is not int or step['seconds'] not in (8,16,24): raise ValueError('seconds: 8,16,24')
        if step['until'] not in ('time','gain','stagnation','low_diversity'): raise ValueError('Unknown transition')
    if sum(step['seconds'] for step in steps)>32:
        raise ValueError('Total planned dwell must be at most 32 seconds')
    return copy.deepcopy(obj)

def single(action, snapshot_id, seconds=16, hypothesis='deterministic controller', evidence=()):
    return {'snapshot_id':snapshot_id,'steps':[{'action':action,'seconds':seconds,'until':'time'}],
            'hypothesis':hypothesis,'evidence':list(evidence)}

def identity(contract):
    return digest({'steps':contract['steps']})
