#!/usr/bin/env python3
"""Prepare byte-identical graph links for the gated native-850 contingency."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PARENT = ROOT.parent
PROTOCOL_SHA256 = "f04dfa2ce06b57d931f1bb77ef47eb66b93f4e225167b90e214562245701d9c9"
MANIFEST_SHA256 = "ed5faf8dd093c7b2cc40e38bc2a12596b12cca2724d6a1afe57439a402d405a3"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def hardlink(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not os.path.samefile(source, target):
            raise FileExistsError(f"existing target is not a link to the original input: {target}")
    else:
        os.link(source, target)
    if not os.path.samefile(source, target):
        raise RuntimeError(f"hardlink identity failed: {target}")


def symlink(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        if target.resolve(strict=True) != source.resolve(strict=True):
            raise FileExistsError(f"existing symlink points elsewhere: {target}")
    elif target.exists():
        raise FileExistsError(f"expected symlink, found independent file: {target}")
    else:
        target.symlink_to(os.path.relpath(source, target.parent), target_is_directory=source.is_dir())


def main() -> None:
    protocol_path = ROOT / "preregistration.json"
    manifest_path = ROOT / "inputs/graphs/metis_manifest.json"
    if sha256(protocol_path) != PROTOCOL_SHA256 or sha256(manifest_path) != MANIFEST_SHA256:
        raise ValueError("recovery registration or graph manifest differs from frozen SHA256")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["protocol_sha256"] != PROTOCOL_SHA256 or len(manifest["graphs"]) != 8:
        raise ValueError("recovery graph manifest does not bind eight views to registration")
    if sha256(PARENT / "preregistration.json") != protocol["recovery"]["parent_preregistration_sha256"]:
        raise ValueError("original primary registration changed")
    if sha256(PARENT / "inputs/graphs/metis_manifest.json") != protocol["recovery"]["parent_graph_manifest_sha256"]:
        raise ValueError("original graph manifest changed")
    six = PARENT / protocol["contingency"]["parent_six_gib_directory"]
    if sha256(six / "preregistration.json") != protocol["contingency"]["parent_six_gib_preregistration_sha256"]:
        raise ValueError("6 GiB predecessor registration changed")
    if sha256(six / "inputs/graphs/metis_manifest.json") != protocol["contingency"]["parent_six_gib_graph_manifest_sha256"]:
        raise ValueError("6 GiB predecessor graph manifest changed")

    for row in manifest["graphs"]:
        graph_id = row["graph_id"]
        if row["input_sha256"] != protocol["graph_sha256"][graph_id]:
            raise ValueError(f"source graph hash differs from registration: {graph_id}")
        for parent_relative, target_relative, expected in (
            (f"inputs/data/CP-SCALE-AU-L002/{row['input_name']}",
             f"inputs/data/CP-SCALE-AU-L002/{row['input_name']}", row["input_sha256"]),
            (f"inputs/graphs/{row['dimacs_name']}", f"inputs/graphs/{row['dimacs_name']}", row["dimacs_sha256"]),
            (f"inputs/graphs/{row['metis_name']}", f"inputs/graphs/{row['metis_name']}", row["metis_sha256"]),
        ):
            source = PARENT / parent_relative
            target = ROOT / target_relative
            if sha256(source) != expected:
                raise ValueError(f"original input hash mismatch: {source}")
            hardlink(source, target)
    runner = ROOT / "implementations/run_stablesolver.py"
    original_runner = PARENT / "implementations/run_stablesolver.py"
    original_bytes = original_runner.read_bytes()
    if (sha256(original_runner) != protocol["method_identity"]["stablesolver_local_search"]["runner_parent_sha256"]
            or original_bytes.count(b"native_limit = 895.0") != 1
            or runner.read_bytes() != original_bytes.replace(
                b"native_limit = 895.0", b"native_limit = 850.0")):
        raise ValueError("runner is not the frozen one-line 850-second variant")
    symlink(PARENT / "source/stablesolver", ROOT / "source/stablesolver")
    method = protocol["method_identity"]["stablesolver_local_search"]
    if sha256(ROOT / "implementations/run_stablesolver.py") != method["runner_sha256"]:
        raise ValueError("unchanged official runner hash mismatch")
    if sha256(ROOT / "source/stablesolver/build/src/stable/stablesolver_stable") != method["binary_sha256"]:
        raise ValueError("unchanged official binary hash mismatch")
    for receipt in protocol["recovery"]["parent_failure_receipts"]:
        for path_key, hash_key in (("result_relative_path", "result_sha256"),
                                   ("native_log_relative_path", "native_log_sha256")):
            if sha256(ROOT / receipt[path_key]) != receipt[hash_key]:
                raise ValueError(f"preserved original failure receipt changed: {receipt[path_key]}")
    print("Prepared 8 NPZ, 8 DIMACS and 8 METIS hardlinks; official runner/source symlinks verified.")


if __name__ == "__main__":
    main()
