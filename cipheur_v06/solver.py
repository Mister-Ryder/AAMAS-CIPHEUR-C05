"""Original C05 action descriptions; the legacy v0.6 solver is not used."""
from .actions import BASELINE_ACTION

LIBRARY={
 'exploit':'Concentrate longer ILS slices on the protected best; no exchange or reseeding.',
 'spread':'Round-robin ILS on all four trajectories with a wide perturbation queue.',
 'merge':'Audition exchanges into the weakest trajectory using a distant donor.',
 'relay':'Follow two rounds of blocker/neighbour structure; exact exchange validates proposals.',
 'antenna':'Neighbourhood follows shared antenna cooldown/duration span around anchors.',
 'satellite':'Neighbourhood follows shared satellite cooldown/duration span around anchors.',
 'kick':'On entry perturb a nonbest weak trajectory twice, bounded loss; then round-robin ILS.',
 'reseed':'On entry rebuild one eligible weak trajectory with weight priority; then recover with ILS.',
 BASELINE_ACTION:'Exact diversity-threshold v0.5 hand adaptive: worst/distant/80 below 0.2 diversity; otherwise round-robin/cyclic/32.'}
