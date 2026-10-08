// Derived from CIPHEUR 0.3 (preserved in native_cex). v0.4: certified exchanges and a new persistent search controller.
// The fast ILS kernel is the UNMODIFIED, credited CHILS local_search.c (MIT).
// This file does not call chils_run, construct a population difference core,
// or claim optimized crossover / weighted bipartite min-cut as new theory.
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <deque>
#include <limits>
#include <map>
#include <memory>
#include <numeric>
#include <queue>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>
#include <omp.h>
extern "C" {
#include "local_search.h"
volatile sig_atomic_t keep_running = 1;
}
using i64 = long long;
using Vec = std::vector<int>;
static double now() { return omp_get_wtime(); }
static thread_local std::string error_message;
static void require(bool b,const char* s){if(!b)throw std::runtime_error(s);}
static void canon(Vec& x){std::sort(x.begin(),x.end());x.erase(std::unique(x.begin(),x.end()),x.end());}
static uint64_t mix(uint64_t x){x+=0x9e3779b97f4a7c15ULL;x=(x^(x>>30))*0xbf58476d1ce4e5b9ULL;x=(x^(x>>27))*0x94d049bb133111ebULL;return x^(x>>31);}
struct Budget {
    double end; i64 limit, used=0;
    void tick(i64 n=1){used+=n;if(used>limit || ((used&255)==0 && now()>=end))throw std::runtime_error("work_budget");}
    bool expired()const{return used>=limit || now()>=end;}
};
struct Arc {int v, rev;i64 cap,orig;};
// Dinic with iterative blocking-path search (no recursion proportional to graph size).
struct Flow {
    std::vector<std::vector<Arc>> e;Vec level,it;int s,t;i64 value=0;bool complete=false;
    Flow(int n,int a,int b):e(n),level(n),it(n),s(a),t(b){}
    int add(int u,int v,i64 c){int pos=e[u].size();e[u].push_back({v,(int)e[v].size(),c,c});e[v].push_back({u,pos,0,0});return pos;}
    bool bfs(Budget& b){std::fill(level.begin(),level.end(),-1);Vec q{ s };level[s]=0;
        for(size_t j=0;j<q.size();++j)for(const auto& a:e[q[j]]){b.tick();if(a.cap>0 && level[a.v]<0){level[a.v]=level[q[j]]+1;q.push_back(a.v);}}
        return level[t]>=0;}
    i64 path(Budget& b){Vec nodes{s},edges;std::vector<i64> bottleneck{std::numeric_limits<i64>::max()};
        while(!nodes.empty()){
            int u=nodes.back();b.tick();
            if(u==t){i64 f=bottleneck.back();for(size_t k=0;k<edges.size();++k){auto& a=e[nodes[k]][edges[k]];a.cap-=f;e[a.v][a.rev].cap+=f;}return f;}
            bool found=false;
            while(it[u]<(int)e[u].size()){b.tick();auto& a=e[u][it[u]];
                if(a.cap>0 && level[a.v]==level[u]+1){edges.push_back(it[u]);nodes.push_back(a.v);bottleneck.push_back(std::min(bottleneck.back(),a.cap));found=true;break;}++it[u];}
            if(!found){level[u]=-1;nodes.pop_back();bottleneck.pop_back();if(!edges.empty()){edges.pop_back();if(!nodes.empty())++it[nodes.back()];}}
        }return 0;}
    void run(Budget& b){try{while(!b.expired()){
                if(!bfs(b)){complete=true;return;}
                std::fill(it.begin(),it.end(),0);i64 f;
                while((f=path(b))>0){value+=f;if(b.expired())return;}
            }}catch(const std::runtime_error& x){if(std::string(x.what())!="work_budget")throw;}}
    Vec reachable()const{Vec r(e.size(),0),q{s};r[s]=1;for(size_t j=0;j<q.size();++j)for(const auto& a:e[q[j]])if(a.cap>0&&!r[a.v]){r[a.v]=1;q.push_back(a.v);}return r;}
};
struct Node {i64 op,a,b,c,d;};
struct Operator {int gen,score,anchor,cap;};
struct Certificate {
    Vec donor,blockers,chosen;std::vector<std::array<i64,3>> packing;
    i64 gain=0,upper=0,flow=0,work=0;bool exact=false,cache=false;int op=-1,anchor=-1;
    double seconds=0;Vec state;std::vector<i64> features;i64 priority=0;std::vector<Vec> alternatives;
};
static void array_json(std::ostream& o,const Vec& v){o<<'[';for(size_t i=0;i<v.size();++i){if(i)o<<',';o<<v[i];}o<<']';}
static void cert_json(std::ostream& o,const Certificate& c){
    o<<"{\"donor\":";array_json(o,c.donor);o<<",\"blockers\":";array_json(o,c.blockers);o<<",\"chosen_region\":";array_json(o,c.chosen);
    o<<",\"state\":";array_json(o,c.state);o<<",\"packing\":[";
    for(size_t i=0;i<c.packing.size();++i){if(i)o<<',';o<<'['<<c.packing[i][0]<<','<<c.packing[i][1]<<','<<c.packing[i][2]<<']';}
    o<<"],\"gain_ticks\":"<<c.gain<<",\"upper_gain_ticks\":"<<c.upper<<",\"flow_ticks\":"<<c.flow<<",\"exact\":"<<(c.exact?"true":"false")
     <<",\"work\":"<<c.work<<",\"seconds\":"<<c.seconds<<",\"operator\":"<<c.op<<",\"anchor\":"<<c.anchor;
    o<<",\"selector_features\":[";for(size_t i=0;i<c.features.size();++i){if(i)o<<',';o<<c.features[i];}o<<"],\"selector_score\":"<<c.priority<<",\"alternatives\":[";
    for(size_t i=0;i<c.alternatives.size();++i){if(i)o<<',';array_json(o,c.alternatives[i]);}o<<"]}";
}
struct Engine {
    graph g{};std::vector<i64> weights,ptr,starts;Vec edges,antenna,satellite;std::vector<local_search*> pool;
    std::vector<Node> nodes;std::vector<Operator> operators;int selector=-1;unsigned action_demands=0;std::unordered_map<int,Vec> gen_cache;
    std::array<std::unordered_map<int,Vec>,2> resources;
    struct Point{double elapsed;i64 value;int stage;};std::vector<Certificate> events;std::vector<Point> trace;
    Vec best_set;i64 best_value=0;double origin=now();uint64_t rng;
    std::map<std::pair<Vec,Vec>,bool> cache;std::deque<std::pair<Vec,Vec>> cache_fifo;
    i64 cycles=0,ls_calls=0,generated=0,exact_count=0,committed=0,cache_hits=0,rejected=0,flow_work=0,gen_work=0,pair_count=0,plateaus=0;
    double ls_seconds=0,exchange_seconds=0;int trace_cap=0;bool retain=false;
    std::string output,last;Vec marker;unsigned int stamp=0;
    Engine(int n,const i64* w,const i64* v,const int* e,const int* initial,int p,unsigned int seed,const i64* tm,const int* ant,const int* sat):rng(seed),marker(n,0){
        require(n>=0&&p>=1&&p<=64,"invalid dimensions");
        weights.assign(w,w+n);ptr.assign(v,v+n+1);require(ptr[0]==0,"invalid CSR origin");
        require(ptr[n]>=0,"invalid CSR size");edges.assign(e,e+ptr[n]);
        g={n,ptr[n],ptr.data(),edges.data(),weights.data()};
        __int128 total=0;for(int u=0;u<n;++u){require(w[u]>0&&ptr[u]<=ptr[u+1],"invalid weights/CSR");total+=w[u];
            int prev=-1;for(i64 k=ptr[u];k<ptr[u+1];++k){int z=edges[k];require(z>=0&&z<n&&z!=u&&z>prev,"invalid/unsorted edge");prev=z;}}
        require(total<=std::numeric_limits<i64>::max()/4,"native integer range exceeded");
        for(int u=0;u<n;++u)for(i64 k=ptr[u];k<ptr[u+1];++k){int z=edges[k];require(std::binary_search(edges.begin()+ptr[z],edges.begin()+ptr[z+1],u),"asymmetric graph");}
        ends.assign(tm,tm+n);ground_gaps.assign(n,-1);satellite_gaps.assign(n,-1);
        starts.assign(tm,tm+n);antenna.assign(ant,ant+n);satellite.assign(sat,sat+n);
        for(int u=0;u<n;++u){if(ant[u]>=0)resources[0][ant[u]].push_back(u);if(sat[u]>=0)resources[1][sat[u]].push_back(u);}
        for(auto& groups:resources)for(auto& kv:groups)std::sort(kv.second.begin(),kv.second.end(),[&](int a,int b){return std::make_pair(starts[a],a)<std::make_pair(starts[b],b);});
        Vec chosen;for(int u=0;u<n;++u){require(initial[u]==0||initial[u]==1,"invalid initial bits");if(initial[u])chosen.push_back(u);}validate_independent(chosen);
        omp_set_dynamic(0);omp_set_num_threads(1);keep_running=1;
        for(int i=0;i<p;++i){local_search* ls=local_search_init(&g,seed+i);ls->max_queue=32+4*i;
            for(int u:chosen){local_search_add_vertex(&g,ls,u);}pool.push_back(ls);}
        best_set=chosen;best_value=weight(chosen);trace.push_back({now()-origin,best_value,0});
    }
    ~Engine(){for(auto* s:pool)local_search_free(s);}
    i64 weight(const Vec& v)const{i64 w=0;for(int u:v)w+=weights[u];return w;}
    uint64_t random(){rng=mix(rng);return rng;}
    Vec selected(local_search* s)const{Vec v;for(int i=0;i<g.n;++i)if(s->independent_set[i])v.push_back(i);return v;}
    void validate_independent(const Vec& vs)const{Vec z=vs;canon(z);require(z.size()==vs.size(),"duplicate vertices");for(int u:z){require(u>=0&&u<g.n,"vertex out of range");for(i64 k=ptr[u];k<ptr[u+1];++k)require(!std::binary_search(z.begin(),z.end(),edges[k]),"not an independent set");}}
    int best_slot()const{int best=0;for(size_t i=1;i<pool.size();++i)if(pool[i]->cost>pool[best]->cost)best=i;return best;}
    void update(int stage){auto* s=pool[best_slot()];if(s->cost>best_value){best_value=s->cost;best_set=selected(s);trace.push_back({now()-origin,best_value,stage});}}
    Vec blockers(local_search* s,const Vec& a)const{Vec b;for(int u:a)for(i64 k=ptr[u];k<ptr[u+1];++k)if(s->independent_set[edges[k]])b.push_back(edges[k]);canon(b);return b;}
    // Packed score is only a heuristic priority. Objective and certificates use exact original weights.
    static i64 sat(__int128 x){const i64 M=(1LL<<61)-1;return x>M?M:(x< -(__int128)M?-M:(i64)x);}
    i64 score(int id,int u,int root,local_search* s,Budget& b){b.tick();const auto& n=nodes.at(id);
        if(n.op==20)return n.a;
        if(n.op==21){switch(n.a){
            case 0:return weights[u];case 1:return ptr[u+1]-ptr[u];case 2:return s->tightness[u];case 3:return s->adjacent_weight[u];
            case 4:{i64 x=0;for(i64 k=ptr[u];k<ptr[u+1];++k){b.tick();int z=edges[k];if(s->independent_set[z] && (z==root || std::binary_search(edges.begin()+ptr[root],edges.begin()+ptr[root+1],z)))++x;}return x;}
            case 5:return antenna[u]>=0&&antenna[u]==antenna[root];case 6:return satellite[u]>=0&&satellite[u]==satellite[root];
            case 8:return sat((__int128)ends[u]-starts[u]);
            case 9:return ground_gaps[u];case 10:return satellite_gaps[u];
            case 7:{__int128 d=(__int128)starts[u]-starts[root];return sat(d<0?-d:d);}
            default:throw std::runtime_error("unknown feature");}}
        i64 a=score(n.a,u,root,s,b),c=n.op==27?0:score(n.b,u,root,s,b);
        switch(n.op){case 22:return sat((__int128)a+c);case 23:return sat((__int128)a-c);case 24:return sat((__int128)a*c);
            case 25:return a/(1+(c<0?-c:c));case 26:return std::min(a,c);case 27:return -a;case 28:return std::max(a,c);default:throw std::runtime_error("invalid score instruction");}
    }
    Vec sorted_score(Vec v,int sid,int root,local_search* s,Budget& b,bool shuffle=false){
        std::vector<std::pair<std::pair<i64,uint64_t>,int>> order;order.reserve(v.size());uint64_t salt=random();
        for(int u:v){i64 z=shuffle?0:score(sid,u,root,s,b);order.push_back({{z,mix(salt+u)},u});}
        b.tick((i64)order.size()*std::max(1,(int)std::log2(order.size()+1)));
        std::sort(order.begin(),order.end(),[](const auto& a,const auto& c){return a.first>c.first;});v.clear();for(const auto& x:order)v.push_back(x.second);return v;
    }
    // Demand-driven feature work, retained from RX's successful interface design.
    // Collection/probe computes all features to expose independently checkable evidence.
    std::vector<i64> region_features(local_search* s,const Vec& a,const Vec& blockers,Budget* budget=nullptr,bool demanded_only=false){
        unsigned mask=demanded_only?action_demands:4095u;std::vector<i64> f(12,0);
        auto need=[&](int i){return bool(mask&(1u<<i));};
        if(need(0)){f[0]=a.size();}if(need(1)){f[1]=blockers.size();}
        if(need(2)){if(budget)budget->tick(a.size());f[2]=weight(a);}
        if(need(3)){if(budget)budget->tick(blockers.size());f[3]=weight(blockers);}
        bool relation=mask&((1u<<4)|(1u<<5)|(1u<<6)|(1u<<7)|(1u<<8));
        if(!relation&&!need(9)&&!need(10)&&!need(11))return f;
        std::map<int,int> counts;std::set<int> ants,sats;i64 best=-(1LL<<61);__int128 repeated=0;
        for(int u:a){if(budget)budget->tick();
            if(need(9))best=std::max(best,weights[u]-s->adjacent_weight[u]);
            if(need(10)&&antenna[u]>=0)ants.insert(antenna[u]);
            if(need(11)&&satellite[u]>=0)sats.insert(satellite[u]);
            if(relation)for(i64 k=ptr[u];k<ptr[u+1];++k){if(budget)budget->tick();int v=edges_at(k);if(s->independent_set[v]){++counts[v];++f[4];repeated+=weights[v];}}}
        for(auto [u,n]:counts){if(n>1){++f[5];f[6]+=weights[u];}else f[7]+=weights[u];}
        if(need(8))f[8]=sat(repeated-weight(blockers));
        if(need(9)){f[9]=a.empty()?0:best;}if(need(10)){f[10]=ants.size();}if(need(11)){f[11]=sats.size();}
        return f;
    }
    int edges_at(i64 k)const{return this->edges[k];}
    i64 region_score(int id,const std::vector<i64>& f,Budget* budget=nullptr){
        if(budget){budget->tick();}const auto& n=nodes.at(id);if(n.op==20)return n.a;
        if(n.op==21){require(n.a>=100&&n.a<112,"invalid action feature");return f.at(n.a-100);}
        i64 a=region_score(n.a,f,budget),c=n.op==27?0:region_score(n.b,f,budget);
        switch(n.op){case 22:return sat((__int128)a+c);case 23:return sat((__int128)a-c);case 24:return sat((__int128)a*c);
            case 25:return a/(1+(c<0?-c:c));case 26:return std::min(a,c);case 27:return -a;case 28:return std::max(a,c);default:throw std::runtime_error("bad action score");}
    }
    Vec generate(int id,int root,local_search* s,Budget& b,int max_set){
        auto it=gen_cache.find(id);if(it!=gen_cache.end()){b.tick(1+it->second.size());return it->second;}
        Vec result=generate_impl(id,root,s,b,max_set);gen_cache[id]=result;return result;
    }
    Vec generate_impl(int id,int root,local_search* s,Budget& b,int max_set){
        b.tick();const auto& n=nodes.at(id);Vec out;
        if(n.op==0)return Vec{root};
        Vec a=generate(n.a,root,s,b,max_set);
        switch(n.op){
        case 1:case 2:
            for(int u:a){b.tick(ptr[u+1]-ptr[u]);for(i64 k=ptr[u];k<ptr[u+1];++k)if(n.op==1||s->independent_set[edges[k]])out.push_back(edges[k]);
                if(out.size()>(size_t)max_set*4){canon(out);require(out.size()<=(size_t)max_set,"set_budget");}}canon(out);break;
        case 3:for(int u:a){b.tick();if(!s->independent_set[u])out.push_back(u);}break;
        case 4:case 5:{Vec c=generate(n.b,root,s,b,max_set);b.tick(a.size()+c.size());canon(a);canon(c);
            if(n.op==4)std::set_union(a.begin(),a.end(),c.begin(),c.end(),std::back_inserter(out));else std::set_intersection(a.begin(),a.end(),c.begin(),c.end(),std::back_inserter(out));break;}
        case 6:a=sorted_score(std::move(a),n.c,root,s,b);if(a.size()>(size_t)n.b)a.resize(n.b);out=std::move(a);canon(out);break;
        case 7:case 8:{int key=n.b;const auto& labels=key==0?antenna:satellite;
            for(int u:a){b.tick();require(labels[u]>=0,"missing_resource_metadata");const auto& group=resources[key].at(labels[u]);
                __int128 low=(__int128)starts[u]-n.c,high=(__int128)starts[u]+n.c;
                if(n.op==8){i64 gap=key==0?ground_gaps.at(u):satellite_gaps.at(u);require(gap>=0&&ends.at(u)>=starts[u],"missing_timing_metadata");
                    __int128 margin=(__int128)n.c*gap+(__int128)n.d*(ends[u]-starts[u]);low=(__int128)starts[u]-margin;high=(__int128)ends[u]+margin;}
                auto lo=std::lower_bound(group.begin(),group.end(),low,[&](int z,__int128 t){return (__int128)starts[z]<t;});
                for(auto it=lo;it!=group.end() && (__int128)starts[*it]<=high;++it){b.tick();out.push_back(*it);}
                canon(out);require(out.size()<=(size_t)max_set,"set_budget");}break;}
        default:throw std::runtime_error("invalid generator instruction");}
        require(out.size()<=(size_t)max_set,"set_budget");return out;
    }
    Vec donor_for(local_search* s,int op,int root,Budget& b,int max_set,bool shuffle){
        gen_cache.clear();const auto& o=operators.at(op);Vec candidates=generate(o.gen,root,s,b,max_set);
        for(int u:candidates)require(!s->independent_set[u],"generator_must_output_unselected");
        candidates=sorted_score(std::move(candidates),o.score,root,s,b,shuffle);
        // Vertex stamps only affect proposal construction; the full blocker set is never truncated.
        if(++stamp>2000000000u){std::fill(marker.begin(),marker.end(),0);stamp=1;}Vec a;
        for(int u:candidates){b.tick();if(marker[u]==(int)stamp)continue;a.push_back(u);if((int)a.size()>=o.cap)break;
            for(i64 k=ptr[u];k<ptr[u+1];++k){b.tick();marker[edges[k]]=stamp;}}
        canon(a);return a;
    }
    Certificate exchange(local_search* s,Vec a,double deadline,i64 max_work,int region_cap,bool exact,bool save_state){
        double begin=now();canon(a);validate_independent(a);for(int u:a)require(!s->independent_set[u],"donor intersects incumbent");
        Certificate c;c.donor=a;c.blockers=blockers(s,a);if(save_state)c.state=selected(s);
        require(c.donor.size()+c.blockers.size()<=(size_t)region_cap,"region_budget");
        i64 wa=weight(a),wb=weight(c.blockers);c.gain=std::max(0LL,wa-wb);c.chosen=wa>wb?a:c.blockers;c.upper=wa;
        if(a.empty()){c.exact=true;c.features=region_features(s,a,c.blockers);c.priority=selector>=0?region_score(selector,c.features):0;c.seconds=now()-begin;return c;}
        if(exact){int na=a.size(),nb=c.blockers.size(),src=na+nb,sink=src+1;Flow f(sink+1,src,sink);
            for(int i=0;i<na;++i)f.add(src,i,weights[a[i]]);
            for(int j=0;j<nb;++j)f.add(na+j,sink,weights[c.blockers[j]]);
            std::vector<std::array<int,4>> loc; // donor original, blocker original, left index, edge index
            for(int i=0;i<na;++i)for(i64 k=ptr[a[i]];k<ptr[a[i]+1];++k){int v=edges[k];if(!s->independent_set[v])continue;
                auto it=std::lower_bound(c.blockers.begin(),c.blockers.end(),v);int j=it-c.blockers.begin();
                int p=f.add(i,na+j,wa+wb+1);loc.push_back({a[i],v,i,p});}
            Budget b{deadline,max_work};f.run(b);c.work=b.used;c.flow=f.value;c.upper=wa-f.value;
            // A packing remains feasible even if max-flow was interrupted.
            for(auto z:loc){const auto& e=f.e[z[2]][z[3]];i64 v=e.orig-e.cap;if(v)c.packing.push_back({z[0],z[1],v});}
            if(f.complete || f.value==wa){Vec r=f.reachable();c.chosen.clear();
                for(int i=0;i<na;++i)if(r[i])c.chosen.push_back(a[i]);
                for(int j=0;j<nb;++j)if(!r[na+j])c.chosen.push_back(c.blockers[j]);
                canon(c.chosen);c.gain=weight(c.chosen)-wb;c.exact=true;require(c.gain==c.upper,"flow/cut mismatch");}
            else c.exact=c.gain==c.upper;
        }
        c.exact=c.gain==c.upper;require(c.gain>=0&&c.upper>=c.gain,"invalid exchange bounds");if(save_state){c.features=region_features(s,c.donor,c.blockers);c.priority=selector>=0?region_score(selector,c.features):0;}c.seconds=now()-begin;return c;
    }
    void commit(local_search* s,const Certificate& c,bool ties){
        if(c.gain<=0&&!ties)return;
        if(c.chosen==c.blockers)return;
        i64 old=s->cost;s->log_enabled=0;s->log_count=0;
        for(int u:c.blockers)if(s->independent_set[u]&&!std::binary_search(c.chosen.begin(),c.chosen.end(),u))local_search_remove_vertex(&g,s,u);
        for(int u:c.chosen)if(!s->independent_set[u])local_search_add_vertex(&g,s,u);
        require(s->cost==old+c.gain,"commit objective mismatch");if(c.gain>0)++committed;else ++plateaus;update(2);
    }
    bool attempt(local_search* s,Vec a,int op,int anchor,double end,i64 work,int region_cap,int cache_cap,bool use_cache,bool exact,bool shadow){
        if(a.empty()){return false;}canon(a);Vec b=blockers(s,a);auto key=std::make_pair(a,b);
        if(use_cache&&cache.count(key)){++cache_hits;return false;}
        auto c=exchange(s,std::move(a),end,work,region_cap,exact,retain&&(int)events.size()<trace_cap);c.op=op;c.anchor=anchor;
        flow_work+=c.work;if(c.exact)++exact_count;
        if(use_cache&&cache_cap>0&&c.upper==0){if(!cache.count(key)){cache[key]=true;cache_fifo.push_back(key);}
            while((int)cache.size()>cache_cap){cache.erase(cache_fifo.front());cache_fifo.pop_front();}}
        if(retain&&(int)events.size()<trace_cap)events.push_back(c);
        if(!shadow){commit(s,c,true);}return c.gain>0;
    }
    void run(double seconds,double slice,double quota,i64 rounds,i64 ls_steps,int flags,int anchors,i64 work,int max_set,int region_cap,int cache_cap,int event_cap){
        require(std::isfinite(seconds)&&seconds>=0&&std::isfinite(slice)&&slice>0&&quota>=0&&quota<=1,"invalid time budget");
        require(rounds>=0&&ls_steps>=0&&anchors>=1&&anchors<=64&&work>0&&region_cap>0&&event_cap>=0,"invalid work budget");
        trace_cap=event_cap;retain=event_cap>0;double begin=now(),end=begin+seconds,charged=0;bool generate_flag=flags&1,merge_flag=flags&2,use_cache=flags&4,exact=flags&8,shadow=flags&16,shuffle=flags&32;
        if(g.n==0)return;
        for(i64 r=0;r<rounds && now()<end;++r){++cycles;int slot=r%pool.size();auto* s=pool[slot];
            double t=now();if(ls_steps>0){local_search_explore(&g,s,std::min(slice,end-t),ls_steps,0);++ls_calls;}ls_seconds+=now()-t;update(1);
            if(now()>=end||charged>=seconds*quota)continue;
            double stage=now(),local_end=std::min(end,std::min(stage+std::max(.002,slice*.5),stage+seconds*quota-charged));
            try{
                if(merge_flag&&pool.size()>1){int other=(slot+1+(r/pool.size())%(pool.size()-1))%pool.size();Vec a;
                    for(int u=0;u<g.n;++u)if(pool[other]->independent_set[u]&&!s->independent_set[u])a.push_back(u);
                    ++pair_count;attempt(s,std::move(a),-1,-1,local_end,work,region_cap,cache_cap,use_cache,exact,shadow);}
                if(generate_flag&&!operators.empty()){
                    struct Proposal{Vec a;int op,root;i64 score;uint64_t tie;};std::vector<Proposal> proposals;
                    for(int j=0;j<anchors && now()<local_end;++j){int oi=(r*anchors+j)%operators.size();const auto& o=operators[oi];int root=-1;
                        for(int trial=0;trial<128;++trial){int v=random()%g.n;if(o.anchor==2 || (bool)s->independent_set[v]==(o.anchor==1)){root=v;break;}}
                        if(root<0)continue;
                        Budget b{local_end,work};Vec a;
                        try{a=donor_for(s,oi,root,b,max_set,shuffle);Vec blocked=blockers(s,a);
                            require(a.size()+blocked.size()<=(size_t)region_cap,"region_budget");
                            // Proven-saturated actions do not occupy the winning selector slot.
                            if(use_cache&&cache.count({a,blocked})){++cache_hits;gen_work+=b.used;continue;}
                            auto f=region_features(s,a,blocked,&b,true);i64 rank=shuffle?0:region_score(selector,f,&b);
                            if(!a.empty())proposals.push_back({std::move(a),oi,root,rank,random()});
                        }catch(const std::runtime_error& x){gen_work+=b.used;std::string m=x.what();if(m=="work_budget"||m=="set_budget"||m=="region_budget"){++rejected;continue;}throw;}
                        gen_work+=b.used;++generated;
                    }
                    if(!proposals.empty()&&now()<local_end){
                        if(flags&64){for(size_t k=proposals.size();k>1;--k)std::swap(proposals[k-1].score,proposals[random()%k].score);}
                        std::sort(proposals.begin(),proposals.end(),[](const auto& a,const auto& b){return std::make_pair(a.score,a.tie)>std::make_pair(b.score,b.tie);});
                        // Fixed epsilon exploration is shared across learned and hand programs.
                        int pick=random()%8==0?random()%proposals.size():0;auto& a=proposals[pick];size_t old=events.size();
                        attempt(s,a.a,a.op,a.root,local_end,work,region_cap,cache_cap,use_cache,exact,shadow);
                        if(events.size()>old)for(auto& z:proposals)events.back().alternatives.push_back(z.a);
                    }
                }
            }catch(const std::runtime_error& x){std::string msg=x.what();if(msg=="work_budget"||msg=="set_budget"||msg=="region_budget")++rejected;else throw;}
            charged+=now()-stage;exchange_seconds+=now()-stage;
        }
    }
    // v0.4: independent logging RNG and a shared-budget, resumable audition.
    std::vector<i64> ends,ground_gaps,satellite_gaps;
    int probe_topk=4,probe_quantum=25000,gate_patience=8,cooldown_cycles=16;
    double program_share=.5;
    i64 decision_seen=0,program_windows=0,program_skipped=0,program_positive=0,program_gain=0,probed=0,pruned=0;
    double generation_seconds=0,probe_seconds=0;
    uint64_t log_rng=0x465856040ULL;
    int zero_streak=0; i64 next_program_cycle=0;
    std::vector<std::string> decisions;
    struct Proposal {Vec a;int op,root;i64 score;uint64_t tie;};
    struct Job {
        Certificate c;std::unique_ptr<Flow> f;std::vector<std::array<int,4>> loc;
        Vec parent; i64 wa=0,wb=0; bool prepared=false;
        int find(int x){while(parent[x]!=x){parent[x]=parent[parent[x]];x=parent[x];}return x;}
        void unite(int a,int b){a=find(a);b=find(b);if(a!=b)parent[a]=b;}
    };
    Job prepare_job(local_search* s,const Proposal& p,double deadline,i64 quota,int cap){
        double t=now();Job j;auto& c=j.c;c.donor=p.a;c.op=p.op;c.anchor=p.root;c.priority=p.score;
        c.blockers=blockers(s,p.a);require(c.donor.size()+c.blockers.size()<=(size_t)cap,"region_budget");
        j.wa=weight(c.donor);j.wb=weight(c.blockers);c.gain=std::max(0LL,j.wa-j.wb);c.upper=j.wa;
        c.chosen=j.wa>j.wb?c.donor:c.blockers;
        Budget b{deadline,quota};
        try{
            i64 visits=p.a.size()+c.blockers.size();for(int v:p.a)visits+=ptr[v+1]-ptr[v];b.tick(visits);
            require(!b.expired(),"work_budget");
            int na=p.a.size(),nb=c.blockers.size(),src=na+nb;
            j.parent.resize(na+nb);std::iota(j.parent.begin(),j.parent.end(),0);
            j.f=std::make_unique<Flow>(src+2,src,src+1);
            for(int i=0;i<na;++i){b.tick();j.f->add(src,i,weights[p.a[i]]);}
            for(int k=0;k<nb;++k){b.tick();j.f->add(na+k,src+1,weights[c.blockers[k]]);}
            for(int i=0;i<na;++i)for(i64 k=ptr[p.a[i]];k<ptr[p.a[i]+1];++k){b.tick();int v=edges[k];
                if(!s->independent_set[v])continue;
                int z=std::lower_bound(c.blockers.begin(),c.blockers.end(),v)-c.blockers.begin();
                int edge=j.f->add(i,na+z,j.wa+j.wb+1);j.loc.push_back({p.a[i],v,i,edge});j.unite(i,na+z);
            }
            // Connected components are independent; whole-side selection in each
            // component is a cheap feasible lower bound, NOT an exact MWIS claim.
            std::vector<i64> left(na+nb),right(na+nb);
            for(int i=0;i<na;++i){b.tick();left[j.find(i)]+=weights[p.a[i]];}
            for(int k=0;k<nb;++k){b.tick();right[j.find(na+k)]+=weights[c.blockers[k]];}
            c.chosen.clear();
            for(int i=0;i<na;++i)if(left[j.find(i)]>right[j.find(i)])c.chosen.push_back(p.a[i]);
            for(int k=0;k<nb;++k)if(left[j.find(na+k)]<=right[j.find(na+k)])c.chosen.push_back(c.blockers[k]);
            canon(c.chosen);c.gain=weight(c.chosen)-j.wb;j.prepared=true;
        }catch(const std::runtime_error& ex){if(std::string(ex.what())!="work_budget")throw;j.f.reset();j.loc.clear();}
        c.exact=c.gain==c.upper;c.work=b.used;c.seconds=now()-t;return j;
    }
    void refresh(Job& j){
        auto& c=j.c;if(!j.prepared)return;auto& f=*j.f;
        c.flow=f.value;c.upper=j.wa-f.value;c.packing.clear();
        for(auto z:j.loc){const auto& e=f.e[z[2]][z[3]];i64 x=e.orig-e.cap;if(x)c.packing.push_back({z[0],z[1],x});}
        if(f.complete||f.value==j.wa){Vec reach=f.reachable();c.chosen.clear();int na=c.donor.size();
            for(int i=0;i<na;++i)if(reach[i])c.chosen.push_back(c.donor[i]);
            for(size_t k=0;k<c.blockers.size();++k)if(!reach[na+k])c.chosen.push_back(c.blockers[k]);
            canon(c.chosen);c.gain=weight(c.chosen)-j.wb;
        }
        c.exact=c.gain==c.upper;require(c.gain>=0&&c.gain<=c.upper,"audition bound violation");
    }
    void advance(Job& j,double deadline,i64 quota){
        if(j.c.exact||!j.prepared||quota<=0||now()>=deadline)return;
        double t=now();Budget b{deadline,quota};j.f->run(b);j.c.work+=b.used;refresh(j);j.c.seconds+=now()-t;
    }
    void remember(const Certificate& c,int cap,bool enabled){
        if(!enabled||cap<=0||c.upper!=0)return;
        auto key=std::make_pair(c.donor,c.blockers);
        if(!cache.count(key)){cache[key]=true;cache_fifo.push_back(key);}
        while((int)cache.size()>cap){cache.erase(cache_fifo.front());cache_fifo.pop_front();}
    }
    int sample_slot(int cap){
        ++decision_seen;if(cap<=0)return -1;
        if((int)decisions.size()<cap){decisions.emplace_back();return decisions.size()-1;}
        log_rng=mix(log_rng);i64 k=log_rng%decision_seen;return k<cap?(int)k:-1;
    }
    void observe_program_result(i64 gain,i64 round,int flags){
        if(gain>0){++program_positive;program_gain+=gain;zero_streak=0;}
        else if(++zero_streak>=gate_patience){zero_streak=0;if(flags&256)next_program_cycle=round+cooldown_cycles+1;}
    }
    void program_stage(local_search* s,i64 round,double deadline,i64 work,int anchors,int max_set,int region_cap,int cache_cap,int event_cap,int flags){
        ++program_windows;const bool audition=flags&128,use_cache=flags&4,shadow=flags&16,shuffle=flags&32;
        double begin=now();i64 used=0;std::vector<Proposal> ps;std::set<Vec> seen;
        // Reserving time for verification/probes prevents a complex generator from
        // consuming the whole decision slice. Both modes share this boundary.
        double gen_end=begin+(deadline-begin)*.4;
        for(int z=0;z<anchors&&used<work&&now()<gen_end;++z){
            int oi=(round*anchors+z)%operators.size();const auto& op=operators[oi];int root=-1;
            for(int trial=0;trial<128;++trial){int v=random()%g.n;if(op.anchor==2||(bool)s->independent_set[v]==(op.anchor==1)){root=v;break;}}
            if(root<0)continue;Budget b{gen_end,work-used};
            try{
                Vec a=donor_for(s,oi,root,b,max_set,shuffle);auto blockers_=blockers(s,a);
                i64 visits=a.size();for(int u:a)visits+=ptr[u+1]-ptr[u];b.tick(visits);
                require(a.size()+blockers_.size()<=(size_t)region_cap,"region_budget");
                if(use_cache&&cache.count({a,blockers_})){++cache_hits;used+=b.used;continue;}
                auto f=region_features(s,a,blockers_,&b,true);i64 rank=shuffle?0:region_score(selector,f,&b);
                if(!a.empty()&&seen.insert(a).second)ps.push_back({std::move(a),oi,root,rank,random()});
                ++generated;
            }catch(const std::runtime_error& x){auto m=std::string(x.what());if(m!="work_budget"&&m!="set_budget"&&m!="region_budget")throw;++rejected;}
            used+=b.used;
        }
        gen_work+=used;double gen_elapsed=now()-begin;generation_seconds+=gen_elapsed;
        if(ps.empty()||now()>=deadline||used>=work){observe_program_result(0,round,flags);return;}
        if(flags&64)for(size_t k=ps.size();k>1;--k)std::swap(ps[k-1].score,ps[random()%k].score);
        std::sort(ps.begin(),ps.end(),[](const Proposal& a,const Proposal& b){return std::make_pair(a.score,a.tie)>std::make_pair(b.score,b.tie);});
        // Sampling happens only after an evaluated decision exists: no empty JSON slots.
        std::vector<Certificate> cs;std::vector<int> indices;int winner=-1;i64 gain=0;i64 eval_work=0;double probe_begin=now();bool random_pick=false;
        if(!audition){
            int pick=0;if(flags&512){pick=random()%ps.size();random_pick=true;}
            // Legacy ratio is a deterministic ranking control here, not v0.3's
            // unrecorded 1/8 exploration. v0.3 remains separately executable.
            const auto& p=ps[pick];auto c=exchange(s,p.a,deadline,std::max(1LL,work-used),region_cap,true,false);
            c.op=p.op;c.anchor=p.root;c.priority=p.score;eval_work+=c.work;cs.push_back(std::move(c));indices.push_back(pick);winner=0;
        }else{
            std::vector<Job> jobs;int top=std::min(probe_topk,(int)ps.size());
            for(int k=0;k<top&&now()<deadline&&used+eval_work<work;++k){
                Job j=prepare_job(s,ps[k],deadline,work-used-eval_work,region_cap);eval_work+=j.c.work;
                gain=std::max(gain,j.c.gain);jobs.push_back(std::move(j));indices.push_back(k);
            }
            // Fair warm-up probes. Every remaining action gets a bounded quantum
            // before highest-upper-bound continuation; each residual is retained.
            for(auto& j:jobs){if(now()>=deadline||used+eval_work>=work)break;
                if(j.c.upper<=gain){++pruned;continue;}
                i64 old=j.c.work;advance(j,deadline,std::min<i64>(probe_quantum,work-used-eval_work));
                eval_work+=j.c.work-old;gain=std::max(gain,j.c.gain);
            }
            while(now()<deadline&&used+eval_work<work){
                int k=-1;for(size_t i=0;i<jobs.size();++i){auto& j=jobs[i];if(!j.prepared||j.c.exact||j.c.upper<=gain)continue;
                    if(k<0||std::make_pair(j.c.upper,j.c.priority)>std::make_pair(jobs[k].c.upper,jobs[k].c.priority))k=i;}
                if(k<0)break;i64 old=jobs[k].c.work;
                advance(jobs[k],deadline,std::min<i64>(probe_quantum,work-used-eval_work));
                eval_work+=jobs[k].c.work-old;gain=std::max(gain,jobs[k].c.gain);if(jobs[k].c.work==old)break;
            }
            for(auto& j:jobs)cs.push_back(std::move(j.c));
            for(size_t i=0;i<cs.size();++i)if(winner<0||std::make_pair(cs[i].gain,cs[i].priority)>std::make_pair(cs[winner].gain,cs[winner].priority))winner=i;
        }
        flow_work+=eval_work;probed+=cs.size();double elapsed=now()-probe_begin;probe_seconds+=elapsed;
        for(const auto& c:cs){if(c.exact)++exact_count;remember(c,cache_cap,use_cache);}
        if(winner<0){observe_program_result(0,round,flags);return;}
        int log_slot=sample_slot(event_cap);Vec state;if(log_slot>=0)state=selected(s);
        auto& c=cs[winner];gain=c.gain;
        if(log_slot>=0){
            std::ostringstream o;o.precision(17);o<<"{\"schema\":4,\"event_index\":"<<decision_seen-1<<",\"cycle\":"<<round<<",\"kind\":\"program\",\"state\":";array_json(o,state);
            o<<",\"selection_mode\":\""<<(audition?"audition":(random_pick?"random":"rank"))<<"\",\"random_exploration\":"<<(random_pick?"true":"false")<<",\"pool\":[";
            for(size_t k=0;k<ps.size();++k){if(k)o<<',';o<<"{\"donor\":";array_json(o,ps[k].a);o<<",\"operator\":"<<ps[k].op<<",\"anchor\":"<<ps[k].root<<",\"score\":"<<ps[k].score<<'}';}
            o<<"],\"evaluations\":[";
            for(size_t k=0;k<cs.size();++k){if(k)o<<',';cs[k].state=state;o<<"{\"pool_index\":"<<indices[k]<<",\"certificate\":";cert_json(o,cs[k]);o<<'}';}
            o<<"],\"chosen_pool_index\":"<<indices[winner]<<",\"executed_gain_ticks\":"<<gain<<",\"program_shadow\":"<<(shadow?"true":"false")
             <<",\"generation_work\":"<<used<<",\"evaluation_work\":"<<eval_work<<",\"generation_seconds\":"<<gen_elapsed<<",\"probe_seconds\":"<<elapsed
             <<",\"remaining_stage_seconds\":"<<std::max(0.,deadline-now())<<",\"offline_optimum_not_assumed\":true}";decisions[log_slot]=o.str();
        }
        if(!shadow)commit(s,c,true);
        observe_program_result(gain,round,flags);
    }
    void run4(double seconds,double slice,double quota,i64 rounds,i64 ls_steps,int flags,int anchors,i64 work,int max_set,int region_cap,int cache_cap,int event_cap){
        require(std::isfinite(seconds)&&seconds>=0&&std::isfinite(slice)&&slice>0&&quota>=0&&quota<=1,"invalid budget");
        require(rounds>=0&&ls_steps>=0&&anchors>=1&&anchors<=64&&work>0&&region_cap>0&&event_cap>=0,"invalid work budget");
        double start=now(),end=start+seconds,charged=0;retain=false;
        if(g.n==0)return;
        for(i64 r=0;r<rounds&&now()<end;++r){++cycles;int slot=r%pool.size();auto* s=pool[slot];double t=now();
            if(ls_steps>0){local_search_explore(&g,s,std::min(slice,end-t),ls_steps,0);++ls_calls;}ls_seconds+=now()-t;update(1);
            if(now()>=end||charged>=seconds*quota)continue;
            double begin=now(),deadline=std::min(end,std::min(begin+std::max(.002,slice*.5),begin+seconds*quota-charged));
            bool gen=(flags&1)&&!operators.empty();if(gen&&(flags&256)&&r<next_program_cycle){gen=false;++program_skipped;}
            double merge_end=gen?begin+(deadline-begin)*(1-program_share):deadline;
            try{
                if((flags&2)&&pool.size()>1&&now()<merge_end){int other=(slot+1+(r/pool.size())%(pool.size()-1))%pool.size();Vec a;
                    for(int u=0;u<g.n;++u)if(pool[other]->independent_set[u]&&!s->independent_set[u])a.push_back(u);
                    ++pair_count;attempt(s,std::move(a),-1,-1,merge_end,work,region_cap,cache_cap,flags&4,true,false);}
                if(gen&&now()<deadline)program_stage(s,r,deadline,work,anchors,max_set,region_cap,cache_cap,event_cap,flags);
            }catch(const std::runtime_error& x){auto m=std::string(x.what());if(m=="work_budget"||m=="set_budget"||m=="region_budget")++rejected;else throw;}
            double dt=now()-begin;charged+=dt;exchange_seconds+=dt;
        }
    }

    std::string report(){std::ostringstream o;o.precision(17);o<<"{\"value_ticks\":"<<best_value<<",\"selected\":";array_json(o,best_set);
        o<<",\"cycles\":"<<cycles<<",\"ils_calls\":"<<ls_calls<<",\"generated\":"<<generated<<",\"exact_exchanges\":"<<exact_count
        <<",\"committed\":"<<committed<<",\"equal_value_moves\":"<<plateaus<<",\"cache_hits\":"<<cache_hits<<",\"rejected_budget\":"<<rejected
        <<",\"flow_work\":"<<flow_work<<",\"generator_work\":"<<gen_work<<",\"pair_attempts\":"<<pair_count
        <<",\"ils_seconds\":"<<ls_seconds<<",\"exchange_seconds\":"<<exchange_seconds<<",\"native_lifetime_seconds\":"<<now()-origin<<",\"events\":[";
        for(size_t i=0;i<events.size();++i){if(i)o<<',';cert_json(o,events[i]);}o<<"],\"incumbent_trace\":[";for(size_t i=0;i<trace.size();++i){if(i)o<<',';o<<"{\"elapsed\":"<<trace[i].elapsed<<",\"value_ticks\":"<<trace[i].value<<",\"stage\":"<<trace[i].stage<<'}';}o<<"],\"decision_events\":[";
        for(size_t i=0;i<decisions.size();++i){if(i)o<<',';o<<decisions[i];}
        o<<"],\"v04_stats\":{\"decision_events_seen\":"<<decision_seen<<",\"program_windows\":"<<program_windows
         <<",\"program_windows_skipped\":"<<program_skipped<<",\"program_positive\":"<<program_positive<<",\"program_gross_gain_ticks\":"<<program_gain
         <<",\"candidates_probed\":"<<probed<<",\"bound_pruned\":"<<pruned<<",\"generation_seconds\":"<<generation_seconds<<",\"probe_seconds\":"<<probe_seconds
         <<",\"sampling\":\"reservoir_with_separate_rng_program_events_only\"}}";return o.str();}
};
extern "C" {
const char* cx_error(){return error_message.c_str();}
void* cx_create(int n,const i64* w,const i64* v,const int* e,const int* init,int p,unsigned int seed,const i64* time,const int* ant,const int* sat){
    try{return new Engine(n,w,v,e,init,p,seed,time,ant,sat);}catch(const std::exception& x){error_message=x.what();return nullptr;}}
void cx_free(void* h){delete static_cast<Engine*>(h);}
int cx_program(void* h,const i64* code,int nn,const int* ops,int no,int selector){try{auto* x=static_cast<Engine*>(h);require(nn>=0&&nn<=2048&&no>=0&&no<=16,"invalid bytecode size");
    x->nodes.clear();x->operators.clear();for(int i=0;i<nn;++i)x->nodes.push_back({code[5*i],code[5*i+1],code[5*i+2],code[5*i+3],code[5*i+4]});
    // Python validates typing. C repeats index checks to exclude malformed native inputs.
    for(int i=0;i<nn;++i){auto n=x->nodes[i];auto idx=[&](i64 a){require(a>=0&&a<i,"bytecode must be acyclic/postorder");};
        if(n.op==0||n.op==20){continue;}if(n.op==21){require((n.a>=0&&n.a<=10)||(n.a>=100&&n.a<112),"feature id");continue;}
        idx(n.a);if(n.op==4||n.op==5||(n.op>=22&&n.op<=28&&n.op!=27))idx(n.b);
        if(n.op==6){idx(n.c);require(n.b>=1&&n.b<=8192,"top bound");}if(n.op==7)require((n.b==0||n.b==1)&&n.c>=0,"resource argument");
        if(n.op==8)require((n.b==0||n.b==1)&&n.c>=0&&n.c<=8&&n.d>=0&&n.d<=4,"span argument");
        require((n.op>=1&&n.op<=8)||(n.op>=22&&n.op<=28),"bad opcode");}
    std::vector<int> type(nn,-1);
    for(int i=0;i<nn;++i){const auto& n=x->nodes[i];
        if(n.op==0)type[i]=0;
        else if(n.op==20)type[i]=3;
        else if(n.op==21)type[i]=n.a<100?1:2;
        else if(n.op<=8){require(type[n.a]==0,"set expression type");
            if(n.op==4||n.op==5)require(type[n.b]==0,"set combination type");
            if(n.op==6){require(type[n.c]==1||type[n.c]==3,"top priority type");}type[i]=0;}
        else{int a=type[n.a],b=n.op==27?3:type[n.b];require(a!=0&&b!=0&&(a==b||a==3||b==3),"scalar expression type");type[i]=a==3?b:a;}}
    for(int i=0;i<no;++i){int a=ops[4*i],b=ops[4*i+1],c=ops[4*i+2],d=ops[4*i+3];require(a>=0&&a<nn&&b>=0&&b<nn&&c>=0&&c<=2&&d>0&&d<=8192,"invalid operator");require(type[a]==0&&(type[b]==1||type[b]==3),"operator root types");x->operators.push_back({a,b,c,d});}require(selector>=0&&selector<nn,"selector index");require(type[selector]==2||type[selector]==3,"selector root type");x->selector=selector;x->action_demands=0;
    Vec stack{selector};while(!stack.empty()){int j=stack.back();stack.pop_back();const auto& n=x->nodes[j];
        if(n.op==21)x->action_demands|=1u<<(n.a-100);
        else if(n.op>=22){stack.push_back(n.a);if(n.op!=27)stack.push_back(n.b);}}
    return 0;
    }catch(const std::exception& e){error_message=e.what();return -1;}}
int cx_run(void* h,double seconds,double slice,double quota,i64 rounds,i64 ls_steps,int flags,int anchors,i64 work,int max_set,int region_cap,int cache_cap,int event_cap){
    try{static_cast<Engine*>(h)->run4(seconds,slice,quota,rounds,ls_steps,flags,anchors,work,max_set,region_cap,cache_cap,event_cap);return 0;}catch(const std::exception& e){error_message=e.what();return -1;}}
const char* cx_report(void* h){try{auto* x=static_cast<Engine*>(h);x->output=x->report();return x->output.c_str();}catch(const std::exception& e){error_message=e.what();return nullptr;}}
// Standalone probe does NOT mutate the incumbent; used for certified paired counterfactuals.
const char* cx_probe(void* h,const int* donor,int count,int op,int anchor,double seconds,i64 work,int max_set,int region_cap,int shuffle){
    try{auto* x=static_cast<Engine*>(h);auto* s=x->pool[0];Vec a;double begin=now();Budget b{begin+seconds,work};
        if(op>=0){require(anchor>=0&&anchor<x->g.n,"anchor out of range");a=x->donor_for(s,op,anchor,b,max_set,shuffle);}
        else {require(count>=0,"invalid donor count");a.assign(donor,donor+count);}
        auto c=x->exchange(s,a,begin+seconds,work,region_cap,true,true);c.op=op;c.anchor=anchor;c.seconds=now()-begin;c.work+=b.used;
        std::ostringstream o;o.precision(17);cert_json(o,c);x->last=o.str();return x->last.c_str();
    }catch(const std::exception& e){error_message=e.what();return nullptr;}}
}

extern "C" {
int cx4_metadata(void* h,const i64* end,const i64* gg,const i64* sg){try{auto* x=static_cast<Engine*>(h);int n=x->g.n;
    x->ends.assign(end,end+n);x->ground_gaps.assign(gg,gg+n);x->satellite_gaps.assign(sg,sg+n);
    for(int i=0;i<n;++i){require(x->ends[i]>=x->starts[i],"negative duration");require(gg[i]>=-1&&sg[i]>=-1,"negative gap");}return 0;
    }catch(const std::exception& ex){error_message=ex.what();return -1;}}
int cx4_options(void* h,int top,int quantum,int patience,int cooldown,double share){try{auto* x=static_cast<Engine*>(h);
    require(top>=1&&top<=64&&quantum>=128&&patience>=1&&cooldown>=0&&std::isfinite(share)&&share>0&&share<=1,"invalid audition options");
    x->probe_topk=top;x->probe_quantum=quantum;x->gate_patience=patience;x->cooldown_cycles=cooldown;x->program_share=share;return 0;
    }catch(const std::exception& ex){error_message=ex.what();return -1;}}
const char* cx4_audition(void* h,const int* ids,const int* offsets,int count,double seconds,i64 work,int region_cap){try{
    auto* x=static_cast<Engine*>(h);auto* s=x->pool[0];require(count>=1&&count<=64&&seconds>=0&&work>=0,"invalid audit input");
    double deadline=now()+seconds;i64 used=0,best=0;std::vector<Engine::Job> jobs;
    for(int i=0;i<count;++i){require(offsets[i+1]>=offsets[i],"bad offsets");Vec a(ids+offsets[i],ids+offsets[i+1]);
        x->validate_independent(a);for(int u:a)require(!s->independent_set[u],"donor intersects incumbent");canon(a);
        Engine::Proposal p{a,-1,-1,-i,0};auto j=x->prepare_job(s,p,deadline,std::max(0LL,work-used),region_cap);used+=j.c.work;
        best=std::max(best,j.c.gain);jobs.push_back(std::move(j));}
    bool progress=true;
    while(progress&&now()<deadline&&used<work){progress=false;for(auto& j:jobs){if(now()>=deadline||used>=work)break;
        if(!j.prepared||j.c.exact||j.c.upper<=best)continue;i64 old=j.c.work;x->advance(j,deadline,std::min<i64>(x->probe_quantum,work-used));
        used+=j.c.work-old;best=std::max(best,j.c.gain);progress|=j.c.work>old;}}
    std::ostringstream o;o<<"{\"certificates\":[";int pick=0;for(size_t i=0;i<jobs.size();++i){if(i)o<<',';jobs[i].c.state=x->selected(s);cert_json(o,jobs[i].c);
        if(jobs[i].c.gain>jobs[pick].c.gain)pick=i;}o<<"],\"chosen_index\":"<<pick<<",\"work\":"<<used<<'}';x->last=o.str();return x->last.c_str();
    }catch(const std::exception& ex){error_message=ex.what();return nullptr;}}
}
