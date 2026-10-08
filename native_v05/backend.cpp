// v0.5 composes the BYTE-UNCHANGED v0.4 engine. All upstream notices retained.
// No generated C++ is executed. This adds checkpoint control and safe actions.
#include "../native_v04/backend.cpp"

static int target_slot(Engine* x,int target) {
    if(target==1)return x->best_slot();
    if(target==2){int k=0;for(size_t i=1;i<x->pool.size();++i)if(x->pool[i]->cost<x->pool[k]->cost)k=i;return k;}
    return x->cycles%x->pool.size();
}
static void copy_ls(graph* g,local_search* d,const local_search* s){
    d->cost=s->cost;d->size=s->size;d->time=s->time;d->time_ref=s->time_ref;
    d->queue_count=s->queue_count;d->max_queue=s->max_queue;
    d->log_enabled=s->log_enabled;d->log_count=s->log_count;d->seed=s->seed;
    std::copy(s->independent_set,s->independent_set+g->n,d->independent_set);
    std::copy(s->queue,s->queue+g->n,d->queue);std::copy(s->in_queue,s->in_queue+g->n,d->in_queue);
    std::copy(s->prev_queue,s->prev_queue+g->n,d->prev_queue);std::copy(s->in_prev_queue,s->in_prev_queue+g->n,d->in_prev_queue);
    std::copy(s->adjacent_weight,s->adjacent_weight+g->n,d->adjacent_weight);
    std::copy(s->tabu,s->tabu+g->n,d->tabu);std::copy(s->tightness,s->tightness+g->n,d->tightness);
    std::memcpy(d->temp,s->temp,sizeof(int)*2*g->n);std::copy(s->mask,s->mask+g->n,d->mask);
    if(d->log_alloc<s->log_alloc){auto* p=(int*)realloc(d->log,sizeof(int)*s->log_alloc);if(!p)throw std::bad_alloc();d->log=p;d->log_alloc=s->log_alloc;}
    std::copy(s->log,s->log+s->log_count,d->log);
}
static std::string snapshot5(Engine* x,bool include_sets){
    std::ostringstream o;o.precision(17);o<<"{\"cycles\":"<<x->cycles<<",\"value_ticks\":"<<x->best_value<<",\"best_slot\":"<<x->best_slot()<<",\"slots\":[";
    for(size_t k=0;k<x->pool.size();++k){if(k)o<<',';o<<"{\"slot\":"<<k<<",\"value_ticks\":"<<x->pool[k]->cost<<",\"size\":"<<x->pool[k]->size;
        if(include_sets){o<<",\"selected\":";array_json(o,x->selected(x->pool[k]));}o<<'}';}
    i64 differing=0,union_n=0;for(int v=0;v<x->g.n;++v){int c=0;for(auto* s:x->pool)c+=s->independent_set[v];union_n+=c>0;differing+=c>0&&c<(int)x->pool.size();}
    o<<"],\"diversity_ppm\":"<<(union_n?1000000*differing/union_n:0)<<",\"generated\":"<<x->generated<<",\"committed\":"<<x->committed<<",\"ils_calls\":"<<x->ls_calls
     <<",\"program_windows\":"<<x->program_windows<<",\"program_positive\":"<<x->program_positive<<",\"program_gain_ticks\":"<<x->program_gain
     <<",\"cache_hits\":"<<x->cache_hits<<",\"generation_seconds\":"<<x->generation_seconds<<",\"exchange_seconds\":"<<x->exchange_seconds<<'}';return o.str();
}
extern "C" {
const char* cx5_snapshot(void* h,int full){try{auto* x=(Engine*)h;x->last=snapshot5(x,full);return x->last.c_str();}catch(const std::exception& e){error_message=e.what();return nullptr;}}
// Fresh clones contain the SAME solutions, queues, tabu, RNG, and cache state.
// Diagnostic history is not copied (it cannot affect search). Time caps remain
// hardware-dependent: only completed fixed-cycle comparisons are work-matched.
void* cx5_clone(void* h){try{auto* x=(Engine*)h;Vec bits(x->g.n,0);for(int v:x->best_set)bits[v]=1;
    auto y=std::make_unique<Engine>(x->g.n,x->weights.data(),x->ptr.data(),x->edges.data(),bits.data(),x->pool.size(),1,x->starts.data(),x->antenna.data(),x->satellite.data());
    for(size_t k=0;k<x->pool.size();++k)copy_ls(&y->g,y->pool[k],x->pool[k]);
    y->nodes=x->nodes;y->operators=x->operators;y->selector=x->selector;y->action_demands=x->action_demands;
    y->best_set=x->best_set;y->best_value=x->best_value;y->rng=x->rng;y->cache=x->cache;y->cache_fifo=x->cache_fifo;
    y->marker=x->marker;y->stamp=x->stamp;y->ends=x->ends;y->ground_gaps=x->ground_gaps;y->satellite_gaps=x->satellite_gaps;
    y->cycles=x->cycles;y->probe_topk=x->probe_topk;y->probe_quantum=x->probe_quantum;y->gate_patience=x->gate_patience;y->cooldown_cycles=x->cooldown_cycles;
    y->program_share=x->program_share;y->zero_streak=x->zero_streak;y->next_program_cycle=x->next_program_cycle;y->log_rng=x->log_rng;
    return y.release();
}catch(const std::exception& e){error_message=e.what();return nullptr;}}
// New epoch loop uses GLOBAL cycles. v0.4's local r is not reset every checkpoint.
int cx5_epoch(void* h,double seconds,double slice,double quota,i64 rounds,i64 steps,int flags,int anchors,i64 work,int max_set,int cap,int cache_cap,int event_cap,int target,int donor_rule,int queue){try{
    auto* x=(Engine*)h;require(std::isfinite(seconds)&&seconds>=0&&std::isfinite(slice)&&slice>0&&std::isfinite(quota)&&quota>=0&&quota<=1,"invalid epoch time");
    require(rounds>=0&&steps>=0&&anchors>=1&&anchors<=64&&work>0&&max_set>0&&cap>0&&cache_cap>=0&&event_cap>=0&&event_cap<=1000,"invalid epoch work");
    require(target>=0&&target<=2&&donor_rule>=0&&donor_rule<=2&&queue>=1&&queue<=4096,"invalid control");
    if(!x->g.n)return 0;double end=now()+seconds,charged=0;
    for(i64 j=0;j<rounds&&now()<end;++j){i64 r=x->cycles;int slot=target_slot(x,target);++x->cycles;auto* s=x->pool[slot];s->max_queue=queue+4*slot;
        double t=now();if(steps>0){local_search_explore(&x->g,s,std::max(0.,std::min(slice,end-t)),steps,0);++x->ls_calls;}x->ls_seconds+=now()-t;x->update(1);
        if(now()>=end||charged>=seconds*quota)continue;
        double begin=now(),deadline=std::min(end,std::min(begin+std::max(.002,slice*.5),begin+seconds*quota-charged));
        bool gen=(flags&1)&&!x->operators.empty();if(gen&&(flags&256)&&r<x->next_program_cycle){gen=false;++x->program_skipped;}
        double merge_end=gen?begin+(deadline-begin)*(1-x->program_share):deadline;
        try{
            if((flags&2)&&x->pool.size()>1&&now()<merge_end){int other=(slot+1+(r/x->pool.size())%(x->pool.size()-1))%x->pool.size();
                if(donor_rule==1&&x->best_slot()!=slot)other=x->best_slot();
                if(donor_rule==2){int best_d=-1;for(size_t k=0;k<x->pool.size();++k)if((int)k!=slot){int d=0;for(int v=0;v<x->g.n;++v)d+=s->independent_set[v]!=x->pool[k]->independent_set[v];if(d>best_d){best_d=d;other=k;}}}
                Vec a;for(int u=0;u<x->g.n;++u)if(x->pool[other]->independent_set[u]&&!s->independent_set[u])a.push_back(u);
                ++x->pair_count;x->attempt(s,std::move(a),-1,-1,merge_end,work,cap,cache_cap,flags&4,true,flags&16);
            }
            if(gen&&now()<deadline)x->program_stage(s,r,deadline,work,anchors,max_set,cap,cache_cap,event_cap,flags);
        }catch(const std::runtime_error& e){auto z=std::string(e.what());if(z=="work_budget"||z=="set_budget"||z=="region_budget")++x->rejected;else throw;}
        double dt=now()-begin;charged+=dt;x->exchange_seconds+=dt;
    }return 0;
}catch(const std::exception& e){error_message=e.what();return -1;}}
// Construct a population seed with the active program's node-priority expression.
// Transactional: timeout before complete construction leaves the slot untouched.
const char* cx5_seed(void* h,int slot,int op,int root,double seconds,i64 work){try{auto* x=(Engine*)h;
    require(slot>0&&slot<(int)x->pool.size(),"seed never replaces protected slot zero");require(op>=0&&op<(int)x->operators.size(),"invalid seed operator");
    require(root>=0&&root<x->g.n&&seconds>=0&&work>0,"invalid seed budget");
    auto* dst=x->pool[slot];std::unique_ptr<local_search,decltype(&local_search_free)> s(local_search_init(&x->g,dst->seed),local_search_free);
    Budget b{now()+seconds,work};Vec v(x->g.n);std::iota(v.begin(),v.end(),0);
    // sorted_score draws from engine RNG. Preserve it on rejected construction.
    auto rng=x->rng;bool installed=false;const auto before=x->selected(dst);std::string reason="installed";
    try{v=x->sorted_score(v,x->operators[op].score,root,s.get(),b);for(int u:v){b.tick(1+x->ptr[u+1]-x->ptr[u]);if(!s->tightness[u])local_search_add_vertex(&x->g,s.get(),u);}if(b.expired())throw std::runtime_error("work_budget");
        copy_ls(&x->g,dst,s.get());x->update(5);installed=true;
    }catch(const std::runtime_error& e){if(std::string(e.what())!="work_budget")throw;reason="budget_rejected";x->rng=rng;}
    std::ostringstream o;o<<"{\"installed\":"<<(installed?"true":"false")<<",\"changed\":"<<(before!=x->selected(dst)?"true":"false")<<",\"slot\":"<<slot<<",\"work\":"<<b.used<<",\"reason\":\""<<reason<<"\"}";x->last=o.str();return x->last.c_str();
}catch(const std::exception& e){error_message=e.what();return nullptr;}}
const char* cx5_kick(void* h,int slot,int count,int loss_bps,double seconds){try{auto* x=(Engine*)h;
    require(slot>=0&&slot<(int)x->pool.size()&&count>=1&&count<=64&&loss_bps>=0&&loss_bps<=10000&&seconds>=0,"invalid kick");
    if(x->g.n==0||slot==x->best_slot()){x->last="{\"accepted\":false,\"reason\":\"protected_best_or_empty\"}";return x->last.c_str();}
    auto* dst=x->pool[slot];std::unique_ptr<local_search,decltype(&local_search_free)> s(local_search_init(&x->g,dst->seed),local_search_free);copy_ls(&x->g,s.get(),dst);
    i64 old=dst->cost;Vec before=x->selected(dst);double end=now()+seconds;int done=0;
    s->log_enabled=0;s->log_count=0;while(done<count&&now()<end){local_search_perturbe(&x->g,s.get());++done;}
    bool accept=done>0&&(__int128)s->cost*10000>=(__int128)old*(10000-loss_bps);if(accept){copy_ls(&x->g,dst,s.get());x->update(6);}
    std::ostringstream o;o<<"{\"accepted\":"<<(accept?"true":"false")<<",\"changed\":"<<(before!=x->selected(dst)?"true":"false")<<",\"count\":"<<done<<",\"before_ticks\":"<<old<<",\"candidate_ticks\":"<<s->cost<<",\"slot\":"<<slot<<'}';x->last=o.str();return x->last.c_str();
}catch(const std::exception& e){error_message=e.what();return nullptr;}}
const char* cx5_exchange(void* h,int slot,const int* ids,int n,double seconds,i64 work,int cap,int commit){try{auto* x=(Engine*)h;
    require(slot>=0&&slot<(int)x->pool.size()&&n>=0&&seconds>=0&&work>=0,"invalid exchange");Vec a(ids,ids+n);
    auto c=x->exchange(x->pool[slot],a,now()+seconds,work,cap,true,true);if(commit)x->commit(x->pool[slot],c,false);
    std::ostringstream o;o.precision(17);cert_json(o,c);x->last=o.str();return x->last.c_str();
}catch(const std::exception& e){error_message=e.what();return nullptr;}}
// Re-certify frozen observed boundary, without drawing the search RNG or mutating it.
const char* cx5_probe_at(void* h,const int* bits,const int* ids,int n,double seconds,i64 work,int cap){try{auto* x=(Engine*)h;
    Vec state;for(int v=0;v<x->g.n;++v){require(bits[v]==0||bits[v]==1,"invalid state bits");if(bits[v])state.push_back(v);}x->validate_independent(state);
    std::unique_ptr<local_search,decltype(&local_search_free)> s(local_search_init(&x->g,1),local_search_free);for(int v:state)local_search_add_vertex(&x->g,s.get(),v);
    require(n>=0&&seconds>=0&&work>=0,"invalid probe budget");Vec a(ids,ids+n);auto c=x->exchange(s.get(),a,now()+seconds,work,cap,true,true);
    std::ostringstream o;o.precision(17);cert_json(o,c);x->last=o.str();return x->last.c_str();
}catch(const std::exception& e){error_message=e.what();return nullptr;}}
// Used by general-region repair after Python checks a complete blocker closure.
// Compare-and-swap prevents stale local solutions being installed unexamined.
int cx5_install(void* h,int slot,const int* expected,const int* ids,int n){try{auto* x=(Engine*)h;
    require(slot>=0&&slot<(int)x->pool.size()&&n>=0,"invalid install");auto* s=x->pool[slot];for(int v=0;v<x->g.n;++v)require(expected[v]==s->independent_set[v],"stale boundary");
    Vec a(ids,ids+n);x->validate_independent(a);require(x->weight(a)>=s->cost,"non-improving install rejected");unsigned seed=s->seed;
    local_search_reset(&x->g,s);s->seed=seed;for(int v:a)local_search_add_vertex(&x->g,s,v);x->update(7);return 0;
}catch(const std::exception& e){error_message=e.what();return -1;}}
}
