#!/usr/bin/env python3
"""Independently check selected vertices, exact objective, and trace for a pilot."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np


def check(mode: str, pilot_dir: Path, npz_path: Path, seconds: float) -> dict:
    result = json.loads((pilot_dir / f"{mode}_g0340_s67.json").read_text(encoding="utf-8"))
    selected = result["selected"]
    if any(type(v) is not int for v in selected) or len(selected) != len(set(selected)):
        raise ValueError(f"{mode}: noninteger or duplicate selected ID")
    with np.load(npz_path, allow_pickle=False) as z:
        weights = z["weight_ticks"]
        edge_u = z["edge_u"]
        edge_v = z["edge_v"]
    if any(v < 0 or v >= len(weights) for v in selected):
        raise ValueError(f"{mode}: selected ID out of range")
    bits = np.zeros(len(weights), dtype=np.bool_)
    bits[selected] = True
    if bool(np.any(bits[edge_u] & bits[edge_v])):
        raise ValueError(f"{mode}: conflict edge selected")
    exact = sum(int(weights[v]) for v in selected)
    if exact != result["value_ticks"]:
        raise ValueError(f"{mode}: wrong exact objective")
    with (pilot_dir / f"{mode}_g0340_s67.csv").open("r", encoding="utf-8", newline="") as stream:
        trajectory = list(csv.DictReader(stream))
    times = [float(row["elapsed_seconds"]) for row in trajectory]
    values = [int(row["value_ticks"]) for row in trajectory]
    if not trajectory or any(b <= a for a, b in zip(values, values[1:])):
        raise ValueError(f"{mode}: trajectory values not strictly increasing")
    if any(b < a for a, b in zip(times, times[1:])) or times[0] < 0 or times[-1] > seconds + 0.5:
        raise ValueError(f"{mode}: invalid trajectory timestamps")
    if values[-1] != exact:
        raise ValueError(f"{mode}: final trajectory value differs from result")
    return {"mode": mode, "valid": True, "selected": len(selected), "value_ticks": exact,
            "improvements": len(trajectory), "last_improvement_seconds": times[-1],
            "native_elapsed_seconds": result["native_elapsed_seconds"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilots", type=Path, required=True)
    parser.add_argument("--npz", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=10.0)
    args = parser.parse_args()
    report = [check(mode, args.pilots, args.npz, args.seconds) for mode in ("grasp", "sa")]
    print(json.dumps(report, separators=(",", ":")))


if __name__ == "__main__":
    main()
