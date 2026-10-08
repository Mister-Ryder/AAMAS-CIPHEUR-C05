"""Frozen asynchronous transport primitives required by C05.

Unrelated v0.5 model and control providers are omitted from this release.
"""
import copy,json,threading,time,urllib.request
from .schema import digest

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Model endpoint redirects disabled')

class ScriptedProvider:
    """Tests and explicit user replay only. NEVER relabeled as an LLM call."""
    kind='scripted_fixture'
    def __init__(self,responses,delay=0):self.responses=list(responses);self.delay=delay;self.index=0
    def complete(self,payload,timeout=None):
        start=time.perf_counter();time.sleep(min(self.delay,timeout or self.delay) if self.delay else 0)
        raw=self.responses[min(self.index,len(self.responses)-1)] if self.responses else {'kind':'proposal','plan':None,'actions':[]}
        self.index+=1
        obj=raw(payload) if callable(raw) else copy.deepcopy(raw)
        obj.setdefault('snapshot_id',payload['observation']['snapshot_id'])
        return {'response':obj,'raw_response':json.dumps(obj),'receipt':{'origin':'scripted_fixture','http_calls':0,'model_calls':0,'status':'returned','wall_seconds':time.perf_counter()-start,'prompt_sha256':digest(payload)}}

class SlowLane:
    def __init__(self,provider):self.provider=provider;self.thread=None;self.result=None;self.pending=None;self.lock=threading.Lock()
    @property
    def busy(self):return self.pending is not None
    def start(self,payload,timeout,metadata):
        if self.busy:raise RuntimeError('One outstanding model call maximum')
        frozen=copy.deepcopy(payload);self.pending={'payload':frozen,'metadata':copy.deepcopy(metadata),'submitted':time.perf_counter()};self.result=None
        def work():
            try:result=self.provider.complete(frozen,timeout)
            except Exception as ex:result={'response':None,'raw_response':'','receipt':{'status':'error','origin':self.provider.kind,'http_calls':0,'model_calls':0,'error':type(ex).__name__+': '+str(ex),'usage_unknown':True}}
            with self.lock:self.result=result
        self.thread=threading.Thread(target=work,daemon=True,name='cipheur-model-http');self.thread.start()
    def poll(self):
        with self.lock:
            if self.result is None:return None
            result=self.result;result['submission']=self.pending;self.pending=None;self.result=None
            return result
    def cutoff(self):
        done=self.poll()
        if done is not None:return done
        if not self.busy:return None
        # Python cannot cancel an already transmitted remote computation. No handle,
        # solution, memory, or file is reachable by this worker after cutoff.
        pending=self.pending;self.pending=None
        return {'response':None,'raw_response':'','submission':pending,'receipt':{
            'status':'pending_at_deadline_discarded','origin':self.provider.kind,
            'http_calls':int(self.provider.kind=='llm_http'),'model_calls':int(self.provider.kind=='llm_http' and getattr(self.provider,'declared_kind','model')!='test_mock'),
            'usage_unknown':True,'wall_seconds_observed':time.perf_counter()-pending['submitted'],
            'remote_cost_may_continue':self.provider.kind=='llm_http'}}
