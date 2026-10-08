"""Search-plan contracts. Model output is data, never eval/exec/native source."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import copy, hashlib, json, math, re
from cipheur_v04.program import Program, default_program, digest

ROLES = ('initialization','neighborhood','control','local')
FEATURES = ('stagnation_epochs','diversity_ppm','spent_ppm','remaining_ms',
            'program_success_ppm','epochs','mean_degree_milli','n')
MODES = ('portfolio','merge','rank','audition')
TARGETS = ('round_robin','best','worst')
DONORS = ('cyclic','best','distant')
STAGE_DEFAULTS = dict(program='base',mode='audition',target='round_robin',donor='cyclic',
    slice_seconds=.025,exchange_fraction=.20,ls_steps=128,anchors=4,work=300000,
    probe_topk=4,program_share=.5,queue=32,kick_count=0,kick_loss_bps=0)

def strict_json(text):
    if not isinstance(text,str): raise ValueError('Expected JSON text')
    if len(text.encode())>2_000_000: raise ValueError('JSON byte cap')
    def pairs(items):
        d={}
        for k,v in items:
            if k in d:raise ValueError('Duplicate JSON key: '+k)
            d[k]=v
        return d
    return json.loads(text,object_pairs_hook=pairs,parse_constant=lambda v:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))

def integer(v,lo,hi,name):
    if type(v) is not int or not lo<=v<=hi:raise ValueError(f'{name}: integer [{lo},{hi}] required')
    return v

def number(v,lo,hi,name):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:
        raise ValueError(f'{name}: finite number [{lo},{hi}] required')
    return v

def fields(obj,allowed,required=()):
    if not isinstance(obj,dict) or set(obj)-set(allowed) or set(required)-set(obj):
        raise ValueError('Unexpected or missing fields; allowed: '+str(sorted(allowed)))

def identifier(x):
    if not isinstance(x,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}',x):raise ValueError('Invalid local plan identifier')
    return x

def initializer_program(seed):
    return Program({'schema':4,'name':'initializer_priority','operators':[{'generator':{'op':'unselected','of':{'op':'seed'}},
        'priority':seed['priority'],'anchor':'any','donor_limit':1}], 'selector':{'const':0}})

class Plan:
    def __init__(self,obj):
        fields(obj,{'schema','name','programs','initialization','stages','rules','default_stage','provenance'},
               {'schema','programs','initialization','stages','rules','default_stage'})
        if obj['schema']!=5:raise ValueError('Expected schema 5 search plan')
        self.obj=copy.deepcopy(obj)
        if len(json.dumps(obj).encode())>256000:raise ValueError('Plan byte cap')
        if not isinstance(obj['programs'],dict) or not 1<=len(obj['programs'])<=4:raise ValueError('One to four programs')
        self.programs={identifier(k):Program(v) for k,v in obj['programs'].items()}
        for p in self.programs.values():
            for op in p.obj['operators']:
                if op['generator'].get('op')!='unselected':raise ValueError('v05 generator must have explicit outermost unselected filter')
        if not isinstance(obj['stages'],dict) or not 1<=len(obj['stages'])<=8:raise ValueError('One to eight stages')
        stages={}
        for name,raw in obj['stages'].items():
            identifier(name);fields(raw,set(STAGE_DEFAULTS),{'program'})
            s={**STAGE_DEFAULTS,**raw}
            if s['program'] not in self.programs or s['mode'] not in MODES or s['target'] not in TARGETS or s['donor'] not in DONORS:raise ValueError('Unknown stage reference')
            number(s['slice_seconds'],.001,.25,'slice');number(s['exchange_fraction'],0,.8,'exchange_fraction')
            number(s['program_share'],.05,1,'program_share')
            for k,lo,hi in [('ls_steps',0,4096),('anchors',1,32),('work',128,2000000),('probe_topk',1,16),('queue',8,256),('kick_count',0,16),('kick_loss_bps',0,2000)]:integer(s[k],lo,hi,k)
            stages[name]=s
        self.obj['stages']=stages
        if obj['default_stage'] not in stages:raise ValueError('Missing default stage')
        if not isinstance(obj['initialization'],list) or len(obj['initialization'])>8:raise ValueError('Initialization cap')
        used=set()
        for seed in self.obj['initialization']:
            fields(seed,{'slot','program','operator','anchor','priority'},{'slot','program','operator','anchor'})
            integer(seed['slot'],1,63,'seed slot')
            if seed['slot'] in used:raise ValueError('Duplicate seed slot')
            used.add(seed['slot'])
            if seed['program'] not in self.programs or seed['anchor'] not in {'degree','weight'}:raise ValueError('Invalid initializer')
            integer(seed['operator'],0,len(self.programs[seed['program']].ops)-1,'seed operator')
            if 'priority' not in seed:seed['priority']=copy.deepcopy(self.programs[seed['program']].obj['operators'][seed['operator']]['priority'])
            # Compile this role independently; removing neighborhood synthesis must
            # not also remove the learned initializer's ranking rule.
            initializer_program(seed)
        if not isinstance(obj['rules'],list) or len(obj['rules'])>16:raise ValueError('Rule cap')
        for r in obj['rules']:
            fields(r,{'when','stage'},{'when','stage'})
            if r['stage'] not in stages or not isinstance(r['when'],list) or not 1<=len(r['when'])<=6:raise ValueError('Invalid rule')
            for test in r['when']:
                fields(test,{'feature','op','value'},{'feature','op','value'})
                if test['feature'] not in FEATURES or test['op'] not in {'lt','le','ge','gt','eq'}:raise ValueError('Unknown guard')
                integer(test['value'],0,10**12,'guard value')
        self.obj['programs']={k:v.obj for k,v in self.programs.items()}
    def fingerprint(self):return digest({k:v for k,v in self.obj.items() if k not in {'name','provenance'}})
    def validate_graph(self,g):
        for p in self.programs.values():p.validate_graph(g)
        for seed in self.obj['initialization']:initializer_program(seed).validate_graph(g)
    def choose(self,state):
        compare={'lt':lambda a,b:a<b,'le':lambda a,b:a<=b,'ge':lambda a,b:a>=b,'gt':lambda a,b:a>b,'eq':lambda a,b:a==b}
        for i,r in enumerate(self.obj['rules']):
            if all(compare[t['op']](state[t['feature']],t['value']) for t in r['when']):return r['stage'],self.obj['stages'][r['stage']],i
        name=self.obj['default_stage'];return name,self.obj['stages'][name],None
    def save(self,path):
        p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(self.obj,indent=2,ensure_ascii=False)+'\n')

def reference_plan(resource=False):
    p=default_program(resource).obj
    return Plan(dict(schema=5,name='hand_reference_NOT_LLM',programs={'base':p},initialization=[],
        stages={'normal':dict(STAGE_DEFAULTS)},rules=[],default_stage='normal',provenance={'origin':'hand_reference'}))

def demonstrate_plan(resource=False):
    """A reference exercising all hooks, NOT a claimed learned policy."""
    o=reference_plan(resource).obj
    o['name']='hook_demonstration_NOT_LLM';o['provenance']={'origin':'hand_demonstration'}
    relay=copy.deepcopy(o['programs']['base']);relay['name']='HAND_RELAY_NOT_LLM'
    relay['operators'][0]['generator']={'op':'unselected','of':{'op':'neighbors','of':{'op':'blockers','of':{'op':'neighbors','of':{'op':'seed'}}}}}
    relay['operators'][0]['priority']={'feature':'weight'};relay['operators'][0]['donor_limit']=128
    o['programs']['relay']=relay
    o['initialization']=[{'slot':i,'program':'base','operator':i%len(o['programs']['base']['operators']),'anchor':'degree' if i%2 else 'weight'} for i in (1,2,3)]
    o['stages']['explore']={**STAGE_DEFAULTS,'program':'relay','target':'worst','donor':'distant','queue':80,'anchors':8,'kick_count':2,'kick_loss_bps':500}
    o['stages']['intensify']={**STAGE_DEFAULTS,'target':'best','donor':'best','exchange_fraction':.35,'program_share':.75}
    o['rules']=[{'when':[{'feature':'spent_ppm','op':'ge','value':750000}],'stage':'intensify'},
                {'when':[{'feature':'stagnation_epochs','op':'ge','value':2}],'stage':'explore'}]
    return Plan(o)

def ablate_plan(plan,roles):
    roles=set(roles)
    if roles-set(ROLES):raise ValueError('Unknown role')
    obj=copy.deepcopy(plan.obj)
    if 'initialization' not in roles:obj['initialization']=[]
    if 'neighborhood' not in roles:
        obj['programs']={k:default_program().obj for k in obj['programs']}
        for s in obj['initialization']:s['operator']=0
    if 'control' not in roles:
        first=next(iter(obj['programs']));obj['stages']={'normal':{**STAGE_DEFAULTS,'program':first}};obj['rules']=[];obj['default_stage']='normal'
    needed={s['program'] for s in obj['stages'].values()}|{s['program'] for s in obj['initialization']}
    obj['programs']={k:v for k,v in obj['programs'].items() if k in needed}
    obj['provenance']={'origin':'role_ablation','parent':plan.fingerprint(),'roles':sorted(roles)}
    return Plan(obj)

def read_plan(path):
    obj=strict_json(Path(path).read_text())
    if obj.get('frozen'):
        plan=Plan(obj['plan'])
        if plan.fingerprint()!=obj['plan_sha256']:raise ValueError('Frozen plan hash mismatch')
        return plan
    return Plan(obj)

@dataclass(frozen=True)
class Config:
    seconds:float=10.
    seed:int=2
    population:int=4
    epoch_seconds:float=.25
    max_epochs:int=10000
    max_cycles_per_epoch:int=1000000
    events:int=4
    max_set:int=8192
    region_cap:int=16384
    cache_size:int=2048
    probe_quantum:int=25000
    gate_patience:int=8
    cooldown_cycles:int=16
    initialization_seconds:float=.05
    initialization_work:int=2000000
    local_seconds:float=.04
    local_work:int=500000
    local_region_cap:int=128
    general_region_cap:int=64
    query_seconds:float=.06
    trial_cycles:int=16
    max_calls:int=4
    max_tools_per_reply:int=2
    max_tool_rounds:int=2
    agent_timeout:float=15.
    agent_interval_epochs:int=4
    min_remaining_seconds:float=.15
    stale_epochs:int=12
    context_nodes:int=96
    context_edges:int=512
    context_bytes:int=180000
    input_mode:str='rich'
    roles:tuple=ROLES
    shadow:bool=False
    def __post_init__(self):
        for k,lo,hi in [('seconds',.001,86400),('epoch_seconds',.005,5),('initialization_seconds',0,1),('local_seconds',0,2),('query_seconds',0,2),('agent_timeout',.01,600),('min_remaining_seconds',0,600)]:number(getattr(self,k),lo,hi,k)
        for k,lo,hi in [('seed',0,2**32-1),('population',1,64),('max_epochs',1,1000000),('max_cycles_per_epoch',1,100000000),('events',0,1000),('max_set',1,1000000),('region_cap',1,100000),('cache_size',0,100000),('probe_quantum',128,100000000),('gate_patience',1,10000),('cooldown_cycles',0,1000000),('initialization_work',1,100000000),('local_work',1,100000000),('local_region_cap',1,2048),('general_region_cap',1,180),('trial_cycles',1,2048),('max_calls',0,128),('max_tools_per_reply',1,4),('max_tool_rounds',0,8),('agent_interval_epochs',1,10000),('stale_epochs',0,100000),('context_nodes',8,4096),('context_edges',0,100000),('context_bytes',8192,2000000)]:integer(getattr(self,k),lo,hi,k)
        if self.input_mode not in {'rich','structural','objective'}:raise ValueError('Unknown input mode')
        if not isinstance(self.roles,(list,tuple)) or set(self.roles)-set(ROLES) or len(self.roles)!=len(set(self.roles)):raise ValueError('Invalid role set')
        if type(self.shadow) is not bool:raise ValueError('shadow bool')
    def json(self):return asdict(self)
