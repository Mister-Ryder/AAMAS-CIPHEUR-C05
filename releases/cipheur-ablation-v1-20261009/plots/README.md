# Recreate the four published log figures

Place `plot_public_csv.py` beside a `csv/` directory containing the eight
numeric CSV files listed below. Run:

```sh
python plot_public_csv.py --input-dir csv --output-dir regenerated
```

The script checks each input against its frozen SHA-256, checks the numeric
relationships available within the CSVs, then writes four PDF and four PNG
figures. It requires Python, NumPy, and Matplotlib. The original source
figures were made from independently audited private run JSONs; the public
CSVs are published numeric derivatives. This script regenerates the plotted
figures from those derivatives but cannot repeat the private raw-run audit.

| Input CSV | Role |
| --- | --- |
| `anytime_curve.csv` | 20–900 s relative incumbent curve |
| `aoc_by_position.csv`, `aoc_summary.csv` | Three-arm 40-position AOC numbers and checks |
| `attainment_difference.csv` | Two unsmoothed 40-position heatmaps |
| `siu_event_samples.csv`, `siu_event_summary.csv` | Run-weighted directed-event curve and checks |
| `async_overhead_summary.csv` | Forty-run timing table checks |
| `async_timeline_example.csv` | First graph and seed, 0–140 s timeline |

The anytime and AOC frames contain only CIPHEUR-online, CIPHEUR-off-EXP,
and CIPHEUR-off-VAL. CHILS has audited final values but no intermediate
trajectory in this frame. The timing summary aggregates 40 runs; its full
latency distribution cannot be recovered from the published 140-second
example timeline alone.
