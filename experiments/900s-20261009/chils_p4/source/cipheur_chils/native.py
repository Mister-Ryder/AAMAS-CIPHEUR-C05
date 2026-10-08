"""Persistent in-process CHILS population, with synchronous, budget-charged hooks."""
from __future__ import annotations
from dataclasses import dataclass, asdict, field, replace
from pathlib import Path
import ctypes as ct
import hashlib
import json
import random
import threading
import time
import numpy as np
from .graph import Graph, load_graph, sha256_file
from .actions import State, ActionConfig, propose_actions, verify_action
from .dsl import Program, Meter, EvaluationBudgetError, hand_program

ROOT = Path(__file__).resolve().parents[1]
_LOCK = threading.Lock()
PI = ct.POINTER(ct.c_int)
PL = ct.POINTER(ct.c_longlong)
PD = ct.POINTER(ct.c_double)
CALLBACK = ct.CFUNCTYPE(ct.c_int, ct.c_int, ct.c_longlong, ct.c_int, ct.c_int, PI, PI, PI, ct.c_double, ct.c_double)
METHODS = {"original", "observe", "hand", "random", "dsl", "permuted", "score_only"}


@dataclass(frozen=True)
class RunConfig:
    seconds: float = 10.0
    population: int = 4
    threads: int = 1
    seed: int = 2
    step: float = 0.05
    cycles: int = 2**63-1
    ls_iterations: int = 2**63-1
    stagnation: int = 2
    guidance_fraction: float = 0.15
    hook_seconds: float = 0.05
    feature_operations: int = 100_000
    max_snapshots: int = 24
    actions: ActionConfig = field(default_factory=ActionConfig)

    def __post_init__(self):
        if self.seconds<0 or self.step<0 or min(self.population,self.threads,self.cycles,self.ls_iterations)<1:
            raise ValueError("Invalid search budget/configuration")
        if self.seed<0 or self.seed>2**32-1 or self.stagnation<0 or not 0<=self.guidance_fraction<=1:
            raise ValueError("Invalid seed/guidance configuration")
        if self.hook_seconds<=0 or self.feature_operations<1 or self.max_snapshots<0:
            raise ValueError("Invalid hook budget")

    def to_json(self): return asdict(self)

    @classmethod
    def from_json(cls, data):
        obj=dict(data)
        if "actions" in obj: obj["actions"]=ActionConfig(**obj["actions"])
        return cls(**obj)


def _library(original=False):
    path=ROOT/"build"/("libchils_original.so" if original else "libcipheur.so")
    if not path.exists(): raise FileNotFoundError("Run python scripts/build_native.py first")
    lib=ct.CDLL(str(path), mode=ct.RTLD_LOCAL)
    lib.cc_run.argtypes=[ct.c_int,PL,PL,PI,ct.c_int,ct.c_int,ct.c_uint,ct.c_double,ct.c_double,
                        ct.c_longlong,ct.c_longlong,PI,CALLBACK,ct.c_int,PI,PD]
    lib.cc_run.restype=ct.c_int
    return lib,path


def solve_graph(g: Graph, method="original", config=None, program=None,
                graph_path=None, snapshot_output=None, split=None, source_id=None,
                _start=None, _cpu=None):
    cfg=config or RunConfig()
    if method not in METHODS: raise ValueError(f"Unknown method {method}")
    if method in {"dsl","permuted","score_only"} and program is None:
        raise ValueError("This method requires an explicitly supplied frozen/experimental program")
    if method != "original" and cfg.population<2:
        raise ValueError("Consensus-core guidance needs population >= 2")
    if snapshot_output and (split!="train" or not source_id or not graph_path):
        raise ValueError("Collection requires explicit TRAIN source_id and graph path")
    start=time.perf_counter() if _start is None else _start
    cpu=time.process_time() if _cpu is None else _cpu
    deadline=start+cfg.seconds
    initial=g.degree_seed()
    initial_value=g.weight(initial)
    fingerprint=g.fingerprint()
    lib,libpath=_library(method=="original")
    binary_sha=sha256_file(libpath)
    weights,ptr,edges=g.csr()
    initial_bits=np.zeros(g.n,dtype=np.int32)
    if initial: initial_bits[list(initial)]=1
    output=np.array(initial_bits,copy=True)
    stats=np.zeros(4,dtype=np.float64)
    events=[]; errors=[]; samples=[]
    policy=hand_program() if method=="hand" else (program if method in {"dsl","permuted","score_only"} else None)
    last_best=initial_value; stall=0; hook_work=0.0; feature_work=0
    pending=None
    if snapshot_output:
        Path(snapshot_output).parent.mkdir(parents=True,exist_ok=True)

    def handler(stage, iteration, n, population, votes, best, mask, elapsed, remaining):
        nonlocal last_best,stall,hook_work,feature_work,pending
        entered=time.perf_counter()
        event={"stage":int(stage),"iteration":int(iteration),"elapsed_e2e":entered-start}
        try:
            best_view=np.ctypeslib.as_array(best,shape=(n,))
            chosen=frozenset(int(v) for v in np.flatnonzero(best_view))
            value=g.weight(chosen)
            event["best_ticks"]=value
            if stage==1:
                event["post_core_delta_ticks"]=value-(pending if pending is not None else value)
                if value>last_best: stall=0
                last_best=max(last_best,value)
                return 0
            if value>last_best: stall=0
            else: stall+=1
            last_best=max(last_best,value);pending=value
            state=State(tuple(int(t) for t in np.ctypeslib.as_array(votes,shape=(n,))),population,chosen,int(iteration))
            state.validate(g)
            d,_,_=state.sets();event["d_core_n"]=len(d)
            if snapshot_output and len(samples)<cfg.max_snapshots:
                sample={"schema":1,"split":"train","source_id":source_id,
                        "graph_path":str(Path(graph_path).resolve()),"graph_fingerprint":fingerprint,
                        "input_sha256":g.source_sha256,"state":state.to_json(),"seed":cfg.seed,
                        "action_config":asdict(cfg.actions)}
                with open(snapshot_output,"a",encoding="utf-8") as f: f.write(json.dumps(sample)+"\n")
                samples.append(int(iteration))
            if method=="observe": event["decision"]="observe_only";return 0
            if stall<cfg.stagnation:
                event["decision"]="not_stagnant";return 0
            if time.perf_counter()>=deadline or hook_work>=cfg.seconds*cfg.guidance_fraction:
                event["decision"]="guidance_budget";return 0
            hook_deadline=min(deadline,entered+cfg.hook_seconds,
                              time.perf_counter()+max(0,cfg.seconds*cfg.guidance_fraction-hook_work))
            actions=propose_actions(g,state,cfg.actions,cfg.seed)
            if time.perf_counter()>hook_deadline:
                event["decision"]="proposal_budget";return 0
            rng=random.Random((cfg.seed<<40)^int(iteration)^0xCD_C0DE)
            if method=="random":
                index=rng.randrange(len(actions));vectors=[]
            else:
                meter=Meter(limit=cfg.feature_operations,deadline=hook_deadline)
                try:
                    scored=[policy.score(g,state,a,meter) for a in actions]
                finally:
                    feature_work+=meter.operations
                    event["feature_operations"]=meter.operations
                scores=[v[0] for v in scored];vectors=[v[1] for v in scored]
                if method=="permuted": rng.shuffle(scores)
                largest=max(scores)
                ties=[i for i,s in enumerate(scores) if s==largest]
                index=0 if 0 in ties else rng.choice(ties)
                event["chosen_score"]=str(scores[index])
                event["demanded_vector"]=[str(x) for x in vectors[index]]
                event["distinct_feature_vectors"]=len(set(vectors))
            action=actions[index]
            region=verify_action(g,state,action)
            event.update({"candidate_count":len(actions),"action":action.to_json(),"expanded_core_n":len(region)})
            if time.perf_counter()>hook_deadline:
                event["decision"]="verification_budget";return 0
            if method=="score_only":
                event["decision"]="scored_but_not_applied";return 0
            view=np.ctypeslib.as_array(mask,shape=(n,))
            for v in action.extra: view[v]=1
            event["decision"]="expanded" if action.extra else "abstain"
            return 1
        except EvaluationBudgetError as exc:
            event["decision"]="feature_budget";event["reason"]=str(exc);return 0
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            event["decision"]="policy_error";event["reason"]=errors[-1]
            # Never let Python exceptions escape a ctypes callback.
            return -1
        finally:
            duration=time.perf_counter()-entered
            hook_work+=duration;event["python_hook_seconds"]=duration;events.append(event)

    callback=CALLBACK(handler) if method!="original" else CALLBACK()
    remaining=max(0.0,deadline-time.perf_counter())
    if remaining>0:
        with _LOCK:
            # Recompute after lock acquisition; do not start a fresh full budget.
            remaining=max(0.0,deadline-time.perf_counter())
            rc=lib.cc_run(g.n,weights.ctypes.data_as(PL),ptr.ctypes.data_as(PL),edges.ctypes.data_as(PI),
                          cfg.population,cfg.threads,cfg.seed,remaining,cfg.step,cfg.cycles,cfg.ls_iterations,
                          initial_bits.ctypes.data_as(PI),callback,cfg.actions.max_extra,
                          output.ctypes.data_as(PI),stats.ctypes.data_as(PD))
        if rc: raise RuntimeError(f"Native bridge failed: {rc}")
    final=set(map(int,np.flatnonzero(output)))
    if not g.feasible(final): raise RuntimeError("Native output violates original conflict graph")
    if g.weight(final)<initial_value: final=set(initial)
    value=g.weight(final)
    # Verification is included; JSON serialization/writing is measured by CLI separately.
    wall=time.perf_counter()-start;used_cpu=time.process_time()-cpu
    status="policy_error" if errors or int(stats[2]) else "ok"
    return {"schema":1,"method":method,"status":status,"config":cfg.to_json(),
            "graph_fingerprint":fingerprint,"input_sha256":g.source_sha256,
            "n":g.n,"m":g.m,"initial_ticks":initial_value,"value_ticks":value if status=="ok" else None,
            "fallback_value_ticks":value if status!="ok" else None,
            "total_weight_ticks":g.weight(range(g.n)),"selected_zero_based":sorted(final),
            "feasible":True,"errors":errors,"wall_seconds":wall,"cpu_seconds":used_cpu,
            "wall_overrun_seconds":max(0,wall-cfg.seconds),"native_seconds":float(stats[0]),
            "native_callback_seconds":float(stats[1]),"rejected_native_masks":int(stats[2]),
            "native_solution_time":float(stats[3]),"python_hook_seconds":hook_work,
            "feature_operations":feature_work,"native_library_sha256":binary_sha,
            "program_sha256":policy.fingerprint() if policy else None,
            "program_provenance":policy.obj.get("provenance",{}) if policy else None,
            "collection_snapshots":len(samples),"events":events}


def run_instance(path, method="original", config=None, program=None, **kwargs):
    start=time.perf_counter();cpu=time.process_time()
    g=load_graph(path)
    if isinstance(program,(str,Path)):
        obj=json.loads(Path(program).read_text(encoding="utf-8"))
        obj=obj.get("program",obj)
        program=Program(obj)
    return solve_graph(g,method,config,program,graph_path=path,_start=start,_cpu=cpu,**kwargs)
