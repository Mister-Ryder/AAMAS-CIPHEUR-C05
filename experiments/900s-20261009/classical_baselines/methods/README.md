# Frozen control implementation files

`plain_grasp/` contains the exact self-contained C++ method source, position runner and independent original-NPZ auditor for the corrected v2 run. `convert_npz_to_metis.py` is the shared exact converter, SHA-256 `47c05d9cacacc910210ccccc90353e7923e0fe490d43e14aeb7f8b65b3f835ec`.

`published_mwvc/` contains the common FJ/FastWVC measured-position runner and original-NPZ auditor. The FJ adapter and FastWVC GPL patch are under `third_party/`; their pinned upstream sources are linked there. The package does not contain the full adapted FastWVC source.

`stablesolver_lns/`, `stablesolver_ls/` and `stablesolver_gwmin/` contain the respective frozen wrappers and auditors. They call the pinned official StableSolver binary described in `third_party/StableSolver/SOURCE_POINTER.md`. The local-search wrapper is the registered native-850-second/9-GiB variant; the GWMIN wrapper is the fresh timed-conversion one-pass variant with 40 new process repeats.

The original private registrations and full raw receipts are required for an exact replay. `methods.json` and `audit_public.json` state the public method parameters, hashes, graph frame and independently audited outcomes without private machine paths or native selected-set dumps.
