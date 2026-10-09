# CIPHEUR AAMAS 2027 interim LaTeX snapshot

Frozen on 2026-10-09 at approximately 13:17 Asia/Shanghai. This directory is a compilable copy of the manuscript and the generated files that it currently includes. Subsequent working sources are outside this release.

## Contents

- `paper/`: AAMAS class, ACM bibliography style, bibliography, copyright graphic, manuscript source, all nine included paper fragments, four PDF figure assets, and the compiled eight-page PDF.
- `results/`: completed four-view control-screen table and paired-difference figure.
- `extension_8x5/analysis/`: the two completed 40-position framework-control rows and the completed 12-position framework screen summary and figure.
- `offline_llm_c05/analysis_l002_full/`: the completed 40-position CIPHEUR-off-GEN result fragment.
- `paper/fragments/heldout_source.tex`: three completed 12-position L003 cross-source arms (CHILS-p4-custom, CIPHEUR-off-EXP, CIPHEUR-off-VAL). Its non-use history is explicitly an author-provided attestation; the file evidence does not independently certify global non-use.

The L002 main table contains only methods with 40/40 audited positions. CIPHEUR-off-RAND is reported in the completed 12-position screen and has no deployment LLM calls; its 8×5 main-table row is withheld pending a complete audit. The L002 main table, current framework-control rows, and L003 partial frame are separately labeled in the manuscript.

## Not included as completed results

- The fresh matched eight-view, five-seed extension for CIPHEUR-online, CIPHEUR-off-RAND, Single-Op, and No-Struct is at 12/40 audited positions per arm in the copied status. This is distinct from the earlier completed 40-position registered CIPHEUR-online comparison in the main table.
- L003 CIPHEUR-online and the separately registered L003 CIPHEUR-off-GEN supplement have no completed 12-position audit in this snapshot. L004 has registration only and no result fragment.
- A synchronized-versus-asynchronous comparator has no completed result fragment.
- Raw solver receipts, private model exchanges, and scripts requiring the external experiment workspace are intentionally omitted. `paper/combined_table_manifest.json` records hashes of the external audit inputs used to generate the copied main table; `SHA256SUMS.txt` verifies the files actually supplied here.

## Build and verification

From `paper/`, run:

```text
pdflatex -interaction=nonstopmode -halt-on-error CIPHEUR_AAMAS2027.tex
bibtex CIPHEUR_AAMAS2027
pdflatex -interaction=nonstopmode -halt-on-error CIPHEUR_AAMAS2027.tex
pdflatex -interaction=nonstopmode -halt-on-error CIPHEUR_AAMAS2027.tex
```

This sequence succeeded inside the copied directory using TeX Live 2022. The output is eight pages, with no missing citations, undefined references, overfull horizontal boxes, or LaTeX errors. The log records one 1.54 pt overfull vertical box. All included file hashes are in `SHA256SUMS.txt`.
