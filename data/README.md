# CP-SCALE-AU-L002 input graphs

The eight NPZ files are the exact graph inputs of the C05 seed replication. SHA256 values are recorded in [`results/data_manifest.csv`](../results/data_manifest.csv). Each view has 37,421 contact vertices; the edge set reflects its constraint configuration.

| File | Vertices | Undirected edges |
|---|---:|---:|
| `g0340.npz` | 37,421 | 492,851 |
| `g0680.npz` | 37,421 | 630,218 |
| `g1200.npz` | 37,421 | 865,904 |
| `g1800.npz` | 37,421 | 1,136,164 |
| `gW0340_gE1200_s0150.npz` | 37,421 | 679,643 |
| `gW0680_gE1200_s0150.npz` | 37,421 | 748,188 |
| `gW1200_gE0340_s0150.npz` | 37,421 | 679,112 |
| `gW1200_gE0680_s0150.npz` | 37,421 | 747,934 |

Load with `numpy.load(path, allow_pickle=False)`. The C05 loader is `cipheur_v04.graph.load_graph`.

| Fields | Meaning |
|---|---|
| `weight_ticks`, `ticks_per_second` | Integer vertex objective weights; 1,000,000 ticks per second |
| `edge_u`, `edge_v`, `edge_mask` | Conflict endpoints and constraint labels |
| `indptr`, `indices` | Compressed sparse row adjacency |
| `start_ticks`, `end_ticks` | Contact time interval |
| `contact_id`, `pass_id`, `satellite_id`, `site_id`, `antenna_id` | Contact and resource identifiers |
| `ground_gap_by_node_ticks`, `satellite_gap_ticks` | Ground and satellite transition gaps |
| `antenna_group_by_node`, `ground_gap_antenna_ids`, `ground_gap_antenna_ticks` | Ground-resource grouping and gap mapping |
| `source_id`, `source_group`, `geometry_id`, `replicate_id`, `split`, `config_id`, `builder_version` | Dataset construction metadata |

All eight views originate from the same physical source. The algorithm maximizes the sum of selected `weight_ticks` subject to graph independence.
