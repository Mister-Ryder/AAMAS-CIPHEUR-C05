/* New synchronous adapter; the three upstream search source files are unchanged
   on disk. Patch injection is performed in a separate build directory. */
#include <omp.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include "bridge.h"
volatile sig_atomic_t keep_running = 1;
static cc_callback active_callback = NULL;
static int *votes_buffer = NULL;
static long long rejected_masks = 0;
static int extra_limit = 0;
static double callback_seconds = 0.0;
static int running = 0; /* A process must never enter cc_run concurrently. */

void cc_checkpoint(graph *g, chils *c, int stage, long long iteration,
                   double elapsed, double remaining)
{
    if (!active_callback) return;
    double begin = omp_get_wtime();
    for (int u=0; u<g->n; ++u) {
        int votes=0;
        for (int i=0; i<c->p; ++i) votes += c->LS[i]->independent_set[u];
        votes_buffer[u]=votes;
    }
    int *best = chils_get_best_independent_set(c);
    int rc = active_callback(stage, iteration, g->n, c->p, votes_buffer,
                             best, c->A, elapsed, remaining);
    if (stage == 0) {
        int valid = rc >= 0, extra = 0;
        for (int u=0; u<g->n && valid; ++u) {
            int d = votes_buffer[u]>0 && votes_buffer[u]<c->p;
            if ((c->A[u]!=0 && c->A[u]!=1) || (d && !c->A[u])) valid=0;
            if (c->A[u] && !d) ++extra;
            if (c->A[u]) for (long long k=g->V[u]; k<g->V[u+1]; ++k) {
                int v=g->E[k];
                if (votes_buffer[v]==c->p && !c->A[v]) { valid=0; break; }
            }
        }
        if (extra > extra_limit) valid=0;
        if (!valid) {
            ++rejected_masks;
            for (int u=0; u<g->n; ++u)
                c->A[u]=(votes_buffer[u]>0 && votes_buffer[u]<c->p);
        }
    }
    callback_seconds += omp_get_wtime()-begin;
}

int cc_run(int n, long long *W, long long *V, int *E,
           int population, int threads, unsigned int seed,
           double seconds, double step, long long cycles, long long ls_iterations,
           const int *initial, cc_callback callback, int max_extra,
           int *result, double *stats)
{
    if (running || n<0 || population<1 || threads<1 || seconds<0 || step<0) return -1;
    running=1;
    double begin=omp_get_wtime();
    graph g={.n=n, .m=V[n], .W=W, .V=V, .E=E};
    for (int u=0; u<n; ++u) result[u]=initial ? initial[u] : 0;
    if (n==0 || seconds==0) { memset(stats,0,4*sizeof(double)); running=0; return 0; }
    keep_running=1;
    omp_set_dynamic(0);
    omp_set_num_threads(threads>population ? population : threads);
    active_callback=callback;
    votes_buffer=malloc(sizeof(int)*n);
    if (!votes_buffer) {running=0;return -2;}
    extra_limit=max_extra; rejected_masks=0; callback_seconds=0;
    double solution_time=0;
    if (population==1) {
        local_search *ls=local_search_init(&g,seed);
        ls->max_queue=32;
        if (initial) for(int u=0;u<n;++u) if(initial[u]) local_search_add_vertex(&g,ls,u);
        double remaining=seconds-(omp_get_wtime()-begin);
        if(remaining>0) local_search_explore(&g,ls,remaining,ls_iterations,0);
        memcpy(result,ls->independent_set,sizeof(int)*n);
        solution_time=ls->time;
        local_search_free(ls);
    } else {
        chils *c=chils_init(&g,population,seed);
        c->step_time=step; c->step_count=ls_iterations;
        /* Matches the pinned main.c -i semantics: candidate zero stays cold. */
        if(initial) for(int i=1;i<population;++i) chils_set_solution(&g,c,i,initial);
        for(int i=0;i<population;++i) {
            c->LS[i]->max_queue=32+4*i;
            c->LS_core[i]->max_queue=32+4*i;
        }
        double remaining=seconds-(omp_get_wtime()-begin);
        if(remaining>0) chils_run(&g,c,remaining,cycles,0);
        memcpy(result,chils_get_best_independent_set(c),sizeof(int)*n);
        solution_time=c->time;
        chils_free(c);
    }
    stats[0]=omp_get_wtime()-begin;
    stats[1]=callback_seconds;
    stats[2]=(double)rejected_masks;
    stats[3]=solution_time;
    free(votes_buffer); votes_buffer=NULL; active_callback=NULL; running=0;
    return 0;
}
