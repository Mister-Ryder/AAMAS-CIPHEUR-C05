# CP-SCALE-AU-L002 search trajectories (900 s)

This directory publishes numeric inputs for the 0–900 s progress plot, the
eight-view 300–900 s detail plots, the five-run spread bands, and final-score
statistics. The eight views are configurations of one physical data source.
Each method has five independent timed positions per view. Objective units are
contact-seconds; the exact stored objective is integer `value_ticks`, with
1,000,000 ticks per contact-second.

`formal_900s/` is the original audited 400-position final-score cohort. Its
`observed_improvements.csv` contains actual recorded feasible incumbents for
C05-Codex, C05-KNN, C05-LinUCB, GRASP, StableSolver LNS, and StableSolver LS.
Original CHILS, Feasibility Jump, FastWVC, and one-pass GWMIN positions have
final-only observations. No intermediate values are invented for them.

`observation_reruns_900s/` contains separate instrumented 40-position
observation cohorts: chils_telemetry, fastwvc_telemetry, fj_telemetry. They give extra process-curve data but do
not replace the original formal final scores. Cohorts held out of the
comparative figures by the frozen v3 rule: none. No held cohort's
per-run data is included here.

The `native` clock starts at the respective solver's native entry. GRASP's
`outer` clock starts at position launch. StableSolver's `upper` clock and
FastWVC's `outer_upper_bound` are conservative position-time upper bounds.
FJ's native process and CHILS's native call clocks have different origins
from C05's clock. The one-axis overview labels these method-specific clocks;
horizontal alignment is descriptive, not a claim of identical wall time.
An observed incumbent is carried forward only after its actual event; the
period before a run's first event stays blank. Colored 25th–75th percentile
bands describe the five actual runs within a view, not confidence intervals.

`figures/` contains the audited final v3 PNG/PDF artifacts.
`scripts/plot_from_public_csv.py` redraws the one-axis and eight-view search
progress figures from the published numeric CSVs. Run from this directory:

```text
python scripts/plot_from_public_csv.py --root . --output <new-output-directory>
```

`manifest.json` contains only relative public file names, SHA-256 digests,
source digest values, method counts, and clock definitions. Private result
receipts, selected-set certificates, raw model inputs/outputs, cloud paths,
credentials, and solver binaries are not part of this numeric release.
