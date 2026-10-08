"""Typed finite native bytecode. No model-generated C/C++/Python is executed.

Set operations are inherited conceptually from the RX generator DSL. Node
priorities are signed, saturating integer expressions; they NEVER replace the
original objective weights. The exact exchange backend is outside the language.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import copy,hashlib,json
import numpy as np
FEATURES=('weight','degree','blocker_count','blocker_weight','shared_blockers','same_antenna','same_satellite','time_distance')
ACTION_FEATURES=('n_donor','n_blockers','weight_donor','weight_blockers','conflict_edges','shared_count','shared_weight','private_weight','overlap_savings','best_single_margin','antenna_count','satellite_count')
SCORES={'add':22,'sub':23,'mul':24,'div':25,'min':26,'neg':27,'max':28}
SETS={'seed':0,'neighbors':1,'blockers':2,'unselected':3,'union':4,'intersect':5,'top':6,'resource':7}
ANCHORS={'unselected':0,'selected':1,'any':2}
LIMIT=(1<<61)-1

def canonical(obj):return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def digest(obj):return hashlib.sha256(canonical(obj).encode()).hexdigest()
def keys(o,required):
    if not isinstance(o,dict) or set(o)!=set(required):raise ValueError('Invalid fields; expected '+str(required))
def integer(x,lo,hi):
    if type(x) is not int or not lo<=x<=hi:raise ValueError(f'Integer outside [{lo},{hi}]')
    return x

class Program:
    def __init__(self,obj):
        if not isinstance(obj,dict) or set(obj)-{'schema','name','operators','selector','provenance'} or obj.get('schema')!=3:raise ValueError('Expected schema=3 native CEX program')
        self.obj=copy.deepcopy(obj);self.instructions=[];self.roots=[];self.metadata=set();self._cache={};self._nodes=0
        ops=obj.get('operators')
        if not isinstance(ops,list) or not 1<=len(ops)<=8:raise ValueError('One to eight operators required')
        for o in ops:
            keys(o,('generator','priority','anchor','donor_limit'))
            if o['anchor'] not in ANCHORS:raise ValueError('Invalid anchor mode')
            limit=integer(o['donor_limit'],1,2048)
            gen=self.compile(o['generator'],'set');score=self.compile(o['priority'],'score')
            self.roots.append([gen,score,ANCHORS[o['anchor']],limit])
        self.selector=self.compile(obj['selector'],'action')
        self.code=np.asarray(self.instructions,dtype=np.int64).reshape(-1,5)
        self.ops=np.asarray(self.roots,dtype=np.int32).reshape(-1,4)
    def compile(self,o,typ,depth=0):
        if depth>14:raise ValueError('Expression depth limit')
        self._nodes+=1
        if self._nodes>512:raise ValueError('Expression node limit')
        if not isinstance(o,dict):raise ValueError('Expression must be an object')
        ck=(typ,canonical(o))
        if ck in self._cache:return self._cache[ck]
        row=None
        if typ in ('score','action'):
            if set(o)=={'const'}:row=[20,integer(o['const'],-10**12,10**12),0,0,0]
            elif set(o)=={'feature'} and o['feature'] in (FEATURES if typ=='score' else ACTION_FEATURES):
                f=o['feature'];row=[21,FEATURES.index(f) if typ=='score' else 100+ACTION_FEATURES.index(f),0,0,0]
                if f in ('same_antenna','antenna_count'):self.metadata.add('antenna_id')
                if f in ('same_satellite','satellite_count'):self.metadata.add('satellite_id')
                if f=='time_distance':self.metadata.add('start_ticks')
            else:
                op=o.get('op')
                if op not in SCORES:raise ValueError('Invalid scalar operation')
                if op=='neg':keys(o,('op','of'));a=self.compile(o['of'],typ,depth+1);b=0
                else:keys(o,('op','a','b'));a=self.compile(o['a'],typ,depth+1);b=self.compile(o['b'],typ,depth+1)
                row=[SCORES[op],a,b,0,0]
        else:
            op=o.get('op')
            if op not in SETS:raise ValueError('Invalid set operation')
            if op=='seed':keys(o,('op',));row=[0,0,0,0,0]
            elif op in ('neighbors','blockers','unselected'):
                keys(o,('op','of'));row=[SETS[op],self.compile(o['of'],'set',depth+1),0,0,0]
            elif op in ('union','intersect'):
                keys(o,('op','args'))
                if not isinstance(o['args'],list) or len(o['args'])!=2:raise ValueError('Binary set composition required')
                row=[SETS[op],self.compile(o['args'][0],'set',depth+1),self.compile(o['args'][1],'set',depth+1),0,0]
            elif op=='top':
                keys(o,('op','of','k','priority'));row=[6,self.compile(o['of'],'set',depth+1),integer(o['k'],1,8192),self.compile(o['priority'],'score',depth+1),0]
            elif op=='resource':
                keys(o,('op','of','key','window_ticks'))
                if o['key'] not in ('antenna_id','satellite_id'):raise ValueError('Unknown resource')
                self.metadata.update((o['key'],'start_ticks'))
                row=[7,self.compile(o['of'],'set',depth+1),0 if o['key']=='antenna_id' else 1,integer(o['window_ticks'],0,86_400_000_000),0]
        idx=len(self.instructions);self.instructions.append(row);self._cache[ck]=idx;return idx
    def fingerprint(self):return digest({'schema':3,'operators':self.obj['operators'],'selector':self.obj['selector']})
    def validate_graph(self,g):
        missing=self.metadata-set(g.meta)
        if missing:raise ValueError('Required metadata missing: '+', '.join(sorted(missing)))
    def save(self,path):Path(path).write_text(json.dumps(self.obj,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

def feature(name):return {'feature':name}
def binary(op,a,b):return {'op':op,'a':a,'b':b}
def priority(name='release'):
    return binary('div',feature('weight'),feature('blocker_weight' if name=='release' else 'degree')) if name!='weight' else feature('weight')
def default_program(resource=False):
    # Handcrafted research starting point, NOT a trained LLM winner.
    seed={'op':'seed'};nei={'op':'neighbors','of':seed}
    around={'op':'union','args':[seed,{'op':'neighbors','of':{'op':'blockers','of':seed}}]}
    wider={'op':'union','args':[around,{'op':'neighbors','of':{'op':'blockers','of':around}}]}
    exprs=[('selected',nei,priority('ratio')),('unselected',around,priority('ratio')),('unselected',wider,priority('weight'))]
    if resource:
        for key in ('antenna_id','satellite_id'):
            exprs.append(('any',{'op':'resource','of':seed,'key':key,'window_ticks':300_000_000},priority('ratio')))
    return Program({'schema':3,'name':'CEX_RESOURCE_HAND' if resource else 'CEX_GRAPH_HAND',
        'operators':[{'generator':{'op':'unselected','of':e},'priority':s,'anchor':a,'donor_limit':256} for a,e,s in exprs],
        'selector':binary('div',binary('mul',feature('weight_donor'),{'const':1024}),feature('weight_blockers')),
        'provenance':{'origin':'handcrafted_not_llm'}})

def read_program(path):
    obj=json.loads(Path(path).read_text(encoding='utf-8'))
    return Program(obj.get('program',obj))

def migrate_rx(path):
    """Transfer RX's generator; explicitly change repair family and node priority.
    This is NOT a claim of identical RX semantics or inherited trained performance.
    """
    from cipheur_rx.program import read_program as read_rx
    rx=read_rx(path)
    def convert(e):
        e=copy.deepcopy(e)
        if 'of' in e:e['of']=convert(e['of'])
        if 'args' in e:e['args']=[convert(a) for a in e['args']]
        if e['op']=='top':
            rank=e.pop('rank');e['priority']={'op':'neg','of':feature('time_distance')} if rank=='near_time' else priority(rank)
        return e
    return Program({'schema':3,'name':'MIGRATED_RX_GENERATOR','operators':[{'generator':convert(rx.obj['generator']),
        'priority':priority('ratio'),'anchor':'unselected','donor_limit':256}],
        'selector':default_program().obj['selector'],
        'provenance':{'origin':'migrated_RX_not_a_new_llm_call','rx_sha256':rx.fingerprint(),
            'changed_semantics':'RX generated U -> independent donor projection + bipartite certified exchange; RX region ranker not transferred'}})

def language():
    return {'schema':3,'set_ops':list(SETS),'score_ops':list(SCORES),'features':list(FEATURES),'action_selector_features':list(ACTION_FEATURES),
        'scalar_semantics':'Exact signed integers saturate at +/-(2^61-1); div(a,b)=trunc(a/(1+abs(b))). Never change objective weights.',
        'constraints':'max 8 operators,512 AST nodes,depth14,8192 intermediate set,donor_limit<=2048; native solver controls all blockers and objective.',
        'semantics':'generator yields unselected candidates; priority orders greedy INDEPENDENT donor A. Exact exchange chooses BEST SUBSET of A jointly with blockers B. Equality of priorities is allowed; ties use seeded random hashes. No raw resource labels in programs.',
        'example':default_program().obj}


def rank_action(program,features):
    if len(features)!=len(ACTION_FEATURES):raise ValueError('Action feature vector size')
    demanded=set()
    def calc(e):
        if 'const' in e:return e['const']
        if 'feature' in e:
            i=ACTION_FEATURES.index(e['feature']);demanded.add(i);return features[i]
        op=e['op'];a=calc(e['of'] if op=='neg' else e['a']);b=0 if op=='neg' else calc(e['b'])
        if op=='add':v=a+b
        elif op=='sub':v=a-b
        elif op=='mul':v=a*b
        elif op=='div':v=(1 if a>=0 else -1)*(abs(a)//(1+abs(b)))
        elif op=='min':v=min(a,b)
        elif op=='max':v=max(a,b)
        elif op=='neg':v=-a
        else:raise ValueError('Invalid operation')
        return max(-LIMIT,min(LIMIT,v))
    score=calc(program.obj['selector'])
    return score,tuple(features[i] for i in sorted(demanded))
