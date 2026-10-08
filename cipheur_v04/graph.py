"""Keep v0.3 lossless transport; add explicit satellite gap metadata.

No assumption that weight equals duration is made by the optimizer. Dataset
sensitivity construction separately enforces the duration-objective contract.
"""
from pathlib import Path
import json
import numpy as np
from cipheur_common.graph import Graph,from_edges,strict_int,sha256_file
from cipheur_common.graph import load_graph as load_v03

def load_graph(path):
    g=load_v03(path)
    if Path(path).suffix.lower()=='.npz':
        with np.load(path,allow_pickle=False) as z:
            if 'satellite_gap_by_node_ticks' in z:
                g.meta['satellite_gap_by_node_ticks']=[strict_int(x) for x in z['satellite_gap_by_node_ticks'].tolist()]
            elif 'satellite_gap_ticks' in z:
                g.meta['satellite_gap_by_node_ticks']=[strict_int(z['satellite_gap_ticks'].item())]*g.n
    validate_times(g)
    return g

def validate_times(g):
    limit=(2**63-1)//4
    for key in ('start_ticks','end_ticks','ground_gap_by_node_ticks','satellite_gap_by_node_ticks'):
        if key not in g.meta:continue
        values=[strict_int(x) for x in g.meta[key]]
        if len(values)!=g.n or any(abs(x)>limit for x in values):raise ValueError('Native metadata range/length: '+key)
        if 'gap' in key and any(x<0 for x in values):raise ValueError('Negative resource gap')
        g.meta[key]=values
    if 'start_ticks' in g.meta and 'end_ticks' in g.meta:
        if any(b<a for a,b in zip(g.meta['start_ticks'],g.meta['end_ticks'])):raise ValueError('Negative duration')
    return g
