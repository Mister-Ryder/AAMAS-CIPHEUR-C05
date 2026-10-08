#!/usr/bin/env python3
"""Make an isolated C05 source copy with the shared 900-second call cap.

The frozen C05 source itself is never edited. This script changes exactly one
validation limit, allowing 21 scheduled selection opportunities. It rejects
unexpected original source bytes or pre-existing output.
"""
import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_CONTRACTS_SHA256 = "5ef17551a54b601deafbd2ff81273df3ffc7611b1cb5aba6db83965e263ee5eb"
OLD_LINE = "not 0<=self.max_calls<=12"
NEW_LINE = "not 0<=self.max_calls<=21"


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tree_sha256(path):
    h = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if not item.is_file() or "__pycache__" in item.parts:
            continue
        relative = item.relative_to(path).as_posix()
        h.update(relative.encode("utf-8") + b"\0")
        h.update(bytes.fromhex(sha256(item)))
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-source", required=True, type=Path)
    parser.add_argument("--out-root", required=True, type=Path)
    args = parser.parse_args()
    frozen = args.frozen_source.resolve(strict=True)
    root = args.out_root.resolve()
    destination = root / "source_c05_controls_900s"
    record_path = root / "registrations" / "c05_source_patch.json"
    if destination.exists() or record_path.exists():
        raise FileExistsError("Isolated C05 source or patch record already exists")
    original_contracts = frozen / "cipheur_v06" / "contracts.py"
    if sha256(original_contracts) != EXPECTED_CONTRACTS_SHA256:
        raise ValueError("Frozen C05 contracts.py hash mismatch")
    original_tree = tree_sha256(frozen)
    shutil.copytree(frozen, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    target = destination / "cipheur_v06" / "contracts.py"
    source_text = target.read_text(encoding="utf-8")
    if source_text.count(OLD_LINE) != 1 or NEW_LINE in source_text:
        raise ValueError("Expected one and only one frozen max_calls ceiling")
    target.write_text(source_text.replace(OLD_LINE, NEW_LINE), encoding="utf-8")
    receipt = frozen.parent / "source_receipt.json"
    record = {
        "schema": "cipheur_c05_900s_source_patch_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_copy": str(destination),
        "frozen_source": str(frozen),
        "frozen_source_tree_sha256": original_tree,
        "patched_source_tree_sha256": tree_sha256(destination),
        "contracts_relpath": "cipheur_v06/contracts.py",
        "frozen_contracts_sha256": EXPECTED_CONTRACTS_SHA256,
        "patched_contracts_sha256": sha256(target),
        "exact_source_change": OLD_LINE + " -> " + NEW_LINE,
        "frozen_source_receipt_sha256": sha256(receipt) if receipt.exists() else None,
        "unchanged": "All remaining copied source files are byte-identical to the frozen candidate.",
    }
    record_path.parent.mkdir(parents=True, exist_ok=True)
    with record_path.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
