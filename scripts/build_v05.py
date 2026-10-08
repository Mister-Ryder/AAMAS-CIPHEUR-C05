#!/usr/bin/env python3
"""Build the new CEX controller + credited, unchanged upstream fast ILS kernel."""
from pathlib import Path
import subprocess,hashlib,json,os,argparse,sys
ROOT=Path(__file__).resolve().parents[1]
def build(sanitize=False,baselines=False):
    out=ROOT/'build';out.mkdir(exist_ok=True)
    upstream=ROOT/'third_party/CHILS'
    pins={'src/graph.c':'76f939f6c5a426b4c7b85e01207df7faac191098','src/local_search.c':'b144cb94e255e507e3f0dafc79620389045e0fa1'}
    for p,h in pins.items():
        data=(upstream/p).read_bytes()
        if hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()!=h:raise ValueError('Upstream source changed: '+p)
    cc=os.environ.get('CC','gcc');cxx=os.environ.get('CXX','g++');suffix='_ubsan' if sanitize else ''
    flags=['-O1','-g','-fsanitize=undefined','-fno-sanitize-recover=undefined'] if sanitize else ['-O3','-DNDEBUG']
    cmds=[];objs=[]
    for name in ('graph','local_search'):
        obj=out/(name+'_cex5'+suffix+'.o');objs.append(obj)
        cmds.append([cc,'-std=gnu17',*flags,'-fPIC','-fopenmp','-I',str(upstream/'include'),'-c',str(upstream/'src'/f'{name}.c'),'-o',str(obj)])
    lib=out/('libcex5'+suffix+'.so')
    cmds.append([cxx,'-std=c++17',*flags,'-Wall','-Wextra','-fPIC','-fopenmp','-shared','-I',str(upstream/'include'),str(ROOT/'native_v05/backend.cpp'),*map(str,objs),'-o',str(lib)])
    for c in cmds:subprocess.run(c,check=True)
    receipt={'algorithm':'CIPHEUR-LLM-v05','kernel':'unchanged CHILS BASELINE ILS','chils_run_linked':False,'commands':cmds,
        'compiler':subprocess.check_output([cxx,'--version'],text=True).splitlines()[0],
        'sources':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'native_v05/backend.cpp',ROOT/'native_v04/backend.cpp',*[upstream/p for p in pins]]},
        'binary_sha256':hashlib.sha256(lib.read_bytes()).hexdigest()}
    (out/(lib.name+'.receipt.json')).write_text(json.dumps(receipt,indent=2)+'\n')
    if baselines:
        subprocess.run([sys.executable,str(ROOT/'scripts/build_v04.py')],check=True)
        subprocess.run([sys.executable,str(ROOT/'scripts/build_cex.py')],check=True)
        subprocess.run([sys.executable,str(ROOT/'scripts/build_native.py')],check=True)
        subprocess.run([sys.executable,str(ROOT/'scripts/build_rx.py')],check=True)
    return receipt
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--sanitize',action='store_true');ap.add_argument('--with-baselines',action='store_true');a=ap.parse_args();print(json.dumps(build(a.sanitize,a.with_baselines),indent=2))
