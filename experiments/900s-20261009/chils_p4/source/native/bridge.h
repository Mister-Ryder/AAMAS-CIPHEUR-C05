#pragma once
#include "chils_internal.h"
/* Pointers are borrowed for the duration of a synchronous callback only.
   stage 0: after D marking, before induced graph construction.
   stage 1: after native core search/cooperation/perturbation. */
typedef int (*cc_callback)(int stage, long long iteration, int n, int p,
                         const int *votes, const int *best, int *mask,
                         double elapsed, double remaining);
void cc_checkpoint(graph *g, chils *c, int stage, long long iteration,
                   double elapsed, double remaining);
int cc_run(int n, long long *W, long long *V, int *E,
           int population, int threads, unsigned int seed,
           double seconds, double step, long long cycles, long long ls_iterations,
           const int *initial, cc_callback callback, int max_extra,
           int *result, double *stats);
