"""Recompute exported C05 means using integer arithmetic and Python's standard library.

Run: python results/recompute_means.py
On the controls branch, its 240 additional rows are included automatically.
"""
from collections import defaultdict
import csv
from fractions import Fraction
from pathlib import Path


def main():
    directory = Path(__file__).resolve().parent
    files = [directory / "c05_llm_runs.csv"]
    if (directory / "c05_control_runs.csv").is_file():
        files.append(directory / "c05_control_runs.csv")
    rows = []
    for file in files:
        with file.open(encoding="utf-8", newline="") as stream:
            rows.extend(csv.DictReader(stream))
    keys = {(r["mode"], r["graph_id"], int(r["seed"])) for r in rows}
    if len(keys) != len(rows):
        raise ValueError("Duplicate run keys in the exported C05 records.")
    grouped = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[row["mode"]][row["graph_id"]].append(int(row["value_ticks"]))
    scores = []
    for mode, views in grouped.items():
        if len(views) != 8 or any(len(values) != 5 for values in views.values()):
            raise ValueError(f"Expected eight views and five seeds per view for {mode}.")
        score = sum((Fraction(sum(values), len(values) * 1_000_000)
                     for values in views.values()), Fraction()) / len(views)
        scores.append((mode, score))
    for mode, score in sorted(scores, key=lambda item: item[1], reverse=True):
        print(f"{mode:10s} {float(score):,.9f} contact-seconds  (exact: {score})")


if __name__ == "__main__":
    main()
