"""ctypes boundary. One active native search per process (upstream global state)."""
from __future__ import annotations
from pathlib import Path
import ctypes as C
import hashlib,json,os,threading,math
import numpy as np
from cipheur_v04.program import Program,default_program
from cipheur_v04.graph import validate_times
from cipheur_cex.native import _LOCK
ROOT=Path(__file__).resolve().parents[1]
I=C.c_int;L=C.c_longlong;D=C.c_double;V=C.c_void_p
PI=C.POINTER(I);PL=C.POINTER(L)

def arr(x,typ):return np.ascontiguousarray(x,dtype=typ)
def ip(x):return x.ctypes.data_as(PI)
def lp(x):return x.ctypes.data_as(PL)
class BaseEngine:
    def __init__(self,g,initial=None,population=4,seed=2,program=None):
        if type(population) is not int or not 1<=population<=64:raise ValueError('population 1..64')
        if type(seed) is not int or not 0<=seed<2**32:raise ValueError('seed uint32')
        validate_times(g)
        self.g=g;self.program=program or default_program();self.program.validate_graph(g)
        chosen=g.degree_seed() if initial is None else set(initial)
        if not g.feasible(chosen):raise ValueError('Infeasible initial')
        library=Path(os.environ.get('CIPHEUR_V05_LIBRARY',ROOT/'build/libcex5.so'))
        if not library.exists():raise FileNotFoundError('Run python scripts/build_v05.py first')
        self.lib=C.CDLL(str(library));self.binary_sha256=hashlib.sha256(library.read_bytes()).hexdigest()
        self.lib.cx_error.restype=C.c_char_p
        self.lib.cx_create.argtypes=[I,PL,PL,PI,PI,I,C.c_uint,PL,PI,PI];self.lib.cx_create.restype=V
        self.lib.cx_free.argtypes=[V]
        self.lib.cx_program.argtypes=[V,PL,I,PI,I,I];self.lib.cx_program.restype=I
        self.lib.cx_run.argtypes=[V,D,D,D,L,L,I,I,L,I,I,I,I];self.lib.cx_run.restype=I
        self.lib.cx_report.argtypes=[V];self.lib.cx_report.restype=C.c_char_p
        self.lib.cx_probe.argtypes=[V,PI,I,I,I,D,L,I,I,I];self.lib.cx_probe.restype=C.c_char_p
        self.lib.cx4_metadata.argtypes=[V,PL,PL,PL];self.lib.cx4_metadata.restype=I
        self.lib.cx4_options.argtypes=[V,I,I,I,I,D];self.lib.cx4_options.restype=I
        self.lib.cx4_audition.argtypes=[V,PI,PI,I,D,L,I];self.lib.cx4_audition.restype=C.c_char_p
        w,p,e=g.csr();bits=np.zeros(g.n,np.int32);bits[list(chosen)]=1
        tm=arr(g.meta.get('start_ticks',[0]*g.n),np.int64)
        def labels(key):
            if key not in g.meta:return np.full(g.n,-1,np.int32)
            ids={};return arr([ids.setdefault(x,len(ids)) for x in g.meta[key]],np.int32)
        ant=labels('antenna_id');sat=labels('satellite_id');self.handle=None
        # Lock covers the complete engine lifetime. Multiprocessing is supported;
        # threads are intentionally serialized, never misreported as multicore speedup.
        _LOCK.acquire();self.locked=True
        try:
            self.handle=self.lib.cx_create(g.n,lp(w),lp(p),ip(e),ip(bits),population,seed,lp(tm),ip(ant),ip(sat))
            if not self.handle:raise RuntimeError(self.error())
            ends=arr(g.meta.get('end_ticks',tm),np.int64)
            gg=arr(g.meta.get('ground_gap_by_node_ticks',[-1]*g.n),np.int64)
            sg=arr(g.meta.get('satellite_gap_by_node_ticks',[-1]*g.n),np.int64)
            if self.lib.cx4_metadata(self.handle,lp(ends),lp(gg),lp(sg))<0:raise RuntimeError(self.error())
            self.set_program(self.program)
        except Exception:self.close();raise
    def error(self):return self.lib.cx_error().decode('utf-8')
    def check(self):
        if not self.handle:raise RuntimeError("Native engine is closed")
    def set_program(self,program):
        self.check()
        fresh=Program(program.obj)
        if not (np.array_equal(program.code,fresh.code) and np.array_equal(program.ops,fresh.ops) and program.selector==fresh.selector):raise ValueError("Program mutated after compilation")
        program.validate_graph(self.g)
        if self.lib.cx_program(self.handle,lp(program.code),len(program.code),ip(program.ops),len(program.ops),program.selector)<0:raise RuntimeError(self.error())
        self.program=program
    def options(self,probe_topk=4,probe_quantum=25000,gate_patience=8,cooldown_cycles=16,program_share=.5):
        self.check()
        if self.lib.cx4_options(self.handle,probe_topk,probe_quantum,gate_patience,cooldown_cycles,program_share)<0:raise RuntimeError(self.error())
    def audition(self,donors,seconds=.05,work=300000,region_cap=16384):
        """Non-mutating fixed-boundary audition, intended for testing/diagnosis."""
        self.check()
        if not 1<=len(donors)<=64 or not math.isfinite(seconds) or seconds<0 or type(work) is not int or work<0:raise ValueError('Invalid audition arguments')
        flat=[];offset=[0]
        for d in donors:
            if len(set(d))!=len(d) or any(type(v) is not int or v<0 or v>=self.g.n for v in d):raise ValueError('Invalid donor list')
            flat.extend(d);offset.append(len(flat))
        ids=arr(flat,np.int32);ptr=arr(offset,np.int32)
        raw=self.lib.cx4_audition(self.handle,ip(ids),ip(ptr),len(donors),seconds,work,region_cap)
        if not raw:raise RuntimeError(self.error())
        return json.loads(raw)
    def run(self,seconds=1,slice_seconds=.025,quota=.2,max_cycles=1_000_000,ls_steps=128,flags=15,anchors=4,work=300_000,max_set=8192,region_cap=16384,cache_size=2048,events=0):
        self.check()
        if self.lib.cx_run(self.handle,seconds,slice_seconds,quota,max_cycles,ls_steps,flags,anchors,work,max_set,region_cap,cache_size,events)<0:raise RuntimeError(self.error())
        return self.report()
    def report(self):
        self.check()
        raw=self.lib.cx_report(self.handle)
        if not raw:raise RuntimeError(self.error())
        return json.loads(raw)
    def probe(self,donor=None,operator=None,anchor=-1,seconds=.05,work=1_000_000,max_set=8192,region_cap=16384,shuffle=False):
        self.check()
        if not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or seconds<0 or type(work) is not int or work<0:raise ValueError("Invalid probe budget")
        if donor is not None and operator is not None:raise ValueError('Choose explicit donor OR program operator')
        vs=list(donor) if donor is not None else []
        if any(type(x) is not int or x<0 or x>=self.g.n for x in vs) or len(set(vs))!=len(vs):raise ValueError('Invalid donor vertex list')
        if operator is not None and (type(operator) is not int or not 0<=operator<len(self.program.ops)):raise ValueError('Operator out of bounds')
        if operator is not None and not 0<=anchor<self.g.n:raise ValueError('Anchor out of bounds')
        a=arr(vs,np.int32)
        raw=self.lib.cx_probe(self.handle,ip(a),len(a),operator if operator is not None else -1,anchor,seconds,work,max_set,region_cap,int(shuffle))
        if not raw:raise RuntimeError(self.error())
        return json.loads(raw)
    def close(self):
        if self.handle:self.lib.cx_free(self.handle);self.handle=None
        if getattr(self,'locked',False):self.locked=False;_LOCK.release()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()

class Engine(BaseEngine):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        try:self._bind5()
        except Exception:self.close();raise
    def _bind5(self):
        api={
            'cx5_snapshot':([V,I],C.c_char_p),'cx5_clone':([V],V),
            'cx5_epoch':([V,D,D,D,L,L,I,I,L,I,I,I,I,I,I,I],I),
            'cx5_seed':([V,I,I,I,D,L],C.c_char_p),'cx5_kick':([V,I,I,I,D],C.c_char_p),
            'cx5_exchange':([V,I,PI,I,D,L,I,I],C.c_char_p),
            'cx5_probe_at':([V,PI,PI,I,D,L,I],C.c_char_p),'cx5_install':([V,I,PI,PI,I],I)}
        for name,(args,ret) in api.items():getattr(self.lib,name).argtypes=args;getattr(self.lib,name).restype=ret
    def _json(self,raw):
        if not raw:raise RuntimeError(self.error())
        return json.loads(raw)
    def snapshot(self,full=True):
        self.check();return self._json(self.lib.cx5_snapshot(self.handle,int(full)))
    def clone(self):
        self.check();child=object.__new__(Engine);child.g=self.g;child.program=self.program;child.lib=self.lib;child.locked=False
        child.binary_sha256=self.binary_sha256;child.handle=self.lib.cx5_clone(self.handle)
        if not child.handle:raise RuntimeError(self.error())
        return child
    def epoch(self,stage,cfg,seconds):
        from .schema import TARGETS,DONORS
        self.check()
        flags={'portfolio':0,'merge':14,'rank':15|256,'audition':15|128|256}[stage['mode']]
        if cfg.shadow:flags|=16
        self.options(stage['probe_topk'],cfg.probe_quantum,cfg.gate_patience,cfg.cooldown_cycles,stage['program_share'])
        ret=self.lib.cx5_epoch(self.handle,max(0.,seconds),stage['slice_seconds'],stage['exchange_fraction'],cfg.max_cycles_per_epoch,
            stage['ls_steps'],flags,stage['anchors'],stage['work'],cfg.max_set,cfg.region_cap,cfg.cache_size,cfg.events,
            TARGETS.index(stage['target']),DONORS.index(stage['donor']),stage['queue'])
        if ret<0:raise RuntimeError(self.error())
    def seed_slot(self,slot,operator,anchor,seconds,work):
        self.check();return self._json(self.lib.cx5_seed(self.handle,slot,operator,anchor,max(0.,seconds),work))
    def kick(self,slot,count,loss_bps,seconds):
        self.check();return self._json(self.lib.cx5_kick(self.handle,slot,count,loss_bps,max(0.,seconds)))
    def _ids(self,ids):
        vs=list(ids)
        if len(vs)!=len(set(vs)) or any(type(v) is not int or not 0<=v<self.g.n for v in vs):raise ValueError('Invalid vertex set')
        return arr(vs,np.int32)
    def exchange_at(self,slot,donor,seconds,work,cap,commit=True):
        self.check();a=self._ids(donor)
        return self._json(self.lib.cx5_exchange(self.handle,slot,ip(a),len(a),max(0.,seconds),work,cap,int(commit)))
    def probe_at(self,state,donor,seconds,work,cap):
        self.check();st=self._ids(state);a=self._ids(donor);bits=np.zeros(self.g.n,np.int32);bits[st]=1
        return self._json(self.lib.cx5_probe_at(self.handle,ip(bits),ip(a),len(a),max(0.,seconds),work,cap))
    def install(self,slot,expected,selected):
        self.check();old=self._ids(expected);a=self._ids(selected);bits=np.zeros(self.g.n,np.int32);bits[old]=1
        if self.lib.cx5_install(self.handle,slot,ip(bits),ip(a),len(a))<0:raise RuntimeError(self.error())
