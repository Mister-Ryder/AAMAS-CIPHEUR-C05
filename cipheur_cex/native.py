"""ctypes boundary. One active native search per process (upstream global state)."""
from __future__ import annotations
from pathlib import Path
import ctypes as C
import hashlib,json,os,threading,math
import numpy as np
from .program import Program,default_program
ROOT=Path(__file__).resolve().parents[1]
I=C.c_int;L=C.c_longlong;D=C.c_double;V=C.c_void_p
PI=C.POINTER(I);PL=C.POINTER(L)
_LOCK=threading.RLock()
def arr(x,typ):return np.ascontiguousarray(x,dtype=typ)
def ip(x):return x.ctypes.data_as(PI)
def lp(x):return x.ctypes.data_as(PL)
class Engine:
    def __init__(self,g,initial=None,population=4,seed=2,program=None):
        if type(population) is not int or not 1<=population<=64:raise ValueError('population 1..64')
        if type(seed) is not int or not 0<=seed<2**32:raise ValueError('seed uint32')
        self.g=g;self.program=program or default_program();self.program.validate_graph(g)
        chosen=g.degree_seed() if initial is None else set(initial)
        if not g.feasible(chosen):raise ValueError('Infeasible initial')
        library=Path(os.environ.get('CIPHEUR_CEX_LIBRARY',ROOT/'build/libcex.so'))
        if not library.exists():raise FileNotFoundError('Run python scripts/build_cex.py first')
        self.lib=C.CDLL(str(library));self.binary_sha256=hashlib.sha256(library.read_bytes()).hexdigest()
        self.lib.cx_error.restype=C.c_char_p
        self.lib.cx_create.argtypes=[I,PL,PL,PI,PI,I,C.c_uint,PL,PI,PI];self.lib.cx_create.restype=V
        self.lib.cx_free.argtypes=[V]
        self.lib.cx_program.argtypes=[V,PL,I,PI,I,I];self.lib.cx_program.restype=I
        self.lib.cx_run.argtypes=[V,D,D,D,L,L,I,I,L,I,I,I,I];self.lib.cx_run.restype=I
        self.lib.cx_report.argtypes=[V];self.lib.cx_report.restype=C.c_char_p
        self.lib.cx_probe.argtypes=[V,PI,I,I,I,D,L,I,I,I];self.lib.cx_probe.restype=C.c_char_p
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
