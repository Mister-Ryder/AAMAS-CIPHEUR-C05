#!/usr/bin/env python3
"""Regenerate four CIPHEUR log figures from the eight published numeric CSVs.

No private run JSON, prompts, replies, network connection, or solver is used.
These CSVs are audited numeric derivatives; this script reproduces the figures,
not the upstream 160-position audit from raw run records.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


EXPECTED_SHA256 = {
    "anytime_curve.csv": "a920534148c43ab72ca7c7319c3c2edc1f436ed82e40267d82f0a1101adb978a",
    "aoc_by_position.csv": "faf82ae38eca6727617a73fb0bf30de154c3434f9f43aab69ec3bba9f9b9c93b",
    "aoc_summary.csv": "149c7a489dce800c6a82fa142b919a0bdfa06e914f767f1e118a81707340286d",
    "attainment_difference.csv": "ebc02675051d790a15d9f57362ed0c71d180b41411b12a5fc1e29dcdecef02f8",
    "siu_event_samples.csv": "4565832ee3297a9e1ae7bf0613e5c79c7fb8b0dcb4970cb0c33b2cd9ee38c217",
    "siu_event_summary.csv": "8d6953e4e151914d2e56ee30bd3dfc7683b0cf2e570f7b7f054791f5c40c2f2f",
    "async_overhead_summary.csv": "ad987d1dfcadabf63aa6b54984c9504ebb1d5a3be6354475b040b4f4ff81abc3",
    "async_timeline_example.csv": "1f087dbcad39eaee734b2af4c3695010018d77b8c1904b5148ad83fd747dc3c2",
}
COLOR = {"online": "#163d64", "off_exp": "#ce7e32", "off_val": "#879056"}
TIMES = (20, 40, 60, 90, 120, 180, 240, 360, 480, 600, 720, 900)
THRESHOLDS = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.60, 1.0)
TAUS = (-10, -5, 0, 1, 5, 8, 12, 16, 24, 30)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def close(actual: float, expected: float, label: str, tol: float = 1e-9) -> None:
    require(math.isfinite(actual) and math.isclose(actual, expected, rel_tol=tol, abs_tol=tol),
            f"Inconsistent numeric derivative: {label}: {actual} versus {expected}")


def load_csv(directory: Path, name: str) -> list[dict[str, str]]:
    path = directory / name
    require(sha(path) == EXPECTED_SHA256[name], f"Published input SHA-256 mismatch: {name}")
    with path.open("r", newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def validate(all_rows: dict[str, list[dict[str, str]]]) -> None:
    curve = all_rows["anytime_curve.csv"]
    require(len(curve) == 881, "Anytime grid must have 881 recorded wall seconds")
    for t, row in enumerate(curve, start=20):
        close(float(row["wall_seconds"]), float(t), "anytime wall grid")
        close(float(row["on_minus_off_EXP_pct_of_reference"]),
              float(row["off_EXP_gap_pct"]) - float(row["on_gap_pct"]), "online minus off-EXP")
        close(float(row["on_minus_off_VAL_pct_of_reference"]),
              float(row["off_VAL_gap_pct"]) - float(row["on_gap_pct"]), "online minus off-VAL")

    by_position = all_rows["aoc_by_position.csv"]
    aoc_summary = all_rows["aoc_summary.csv"]
    require(len(by_position) == 120 and len(aoc_summary) == 3,
            "AOC requires 40 positions in each of three recorded-trajectory arms")
    require({r["arm"] for r in by_position} == {"codex", "knn", "linucb"},
            "AOC has no CHILS intermediate trajectory")
    for row in aoc_summary:
        subset = [r for r in by_position if r["arm"] == row["arm"]]
        require(len(subset) == 40 and int(row["positions"]) == 40,
                "AOC arm has an incomplete matched frame")
        aocs = [float(r["aoc_gap_pct_20_900s"]) for r in subset]
        close(float(row["mean_aoc_gap_pct"]), statistics.mean(aocs), "AOC mean")
        close(float(row["sd_aoc_gap_pct"]), statistics.stdev(aocs), "AOC sample SD")
        close(float(row["mean_final_contact_seconds"]),
              statistics.mean(int(r["final_value_ticks"]) for r in subset) / 1_000_000,
              "final objective mean")

    attainment = all_rows["attainment_difference.csv"]
    require(len(attainment) == 2 * len(TIMES) * len(THRESHOLDS), "Attainment grid incomplete")
    for arm in ("knn", "linucb"):
        seen = set()
        for row in attainment:
            if row["comparator"] != arm:
                continue
            t, threshold = int(row["wall_seconds"]), float(row["reference_gap_threshold_pct"])
            require(t in TIMES and threshold in THRESHOLDS and (t, threshold) not in seen,
                    "Attainment grid duplicate or unexpected cell")
            seen.add((t, threshold))
            require(int(row["positions"]) == 40, "Attainment cell lacks 40 positions")
            close(float(row["on_minus_off_percentage_points"]),
                  100.0 * (int(row["on_attained"]) - int(row["off_attained"])) / 40,
                  "attainment percentage points")
        require(len(seen) == len(TIMES) * len(THRESHOLDS), "Attainment comparator incomplete")

    samples = all_rows["siu_event_samples.csv"]
    event_summary = all_rows["siu_event_summary.csv"]
    require(len(event_summary) == len(TAUS) and len(samples) == 4009,
            "Directed-event CSV frame has changed")
    for row, tau in zip(event_summary, TAUS):
        require(int(row["tau_seconds"]) == tau, "Directed-event time order changed")
        subset = [r for r in samples if int(r["tau_seconds"]) == tau]
        grouped: dict[tuple[str, int], list[dict[str, str]]] = defaultdict(list)
        for item in subset:
            grouped[item["graph_id"], int(item["seed"])].append(item)
        require(len(subset) == int(row["events"]) and len(grouped) == int(row["runs"]),
                "Directed-event count mismatch")
        for sample_key, summary_key in (
            ("incumbent_delta_contact_seconds", "mean_incumbent_delta_contact_seconds"),
            ("mean_trajectory_delta_contact_seconds", "mean_trajectory_delta_contact_seconds"),
            ("population_diversity_pct", "mean_diversity_pct"),
        ):
            run_means = [statistics.mean(float(x[sample_key]) for x in group)
                         for group in grouped.values()]
            close(float(row[summary_key]), statistics.mean(run_means), summary_key)

    overhead = all_rows["async_overhead_summary.csv"]
    require(len(overhead) == 16 and {r["metric"]: r["value"] for r in overhead}["model_calls"] == "840",
            "Online timing summary frame changed")
    timeline = all_rows["async_timeline_example.csv"]
    require(timeline and all(r["kind"] in {"model_pending", "http", "native_action", "plan_install"}
                             and 0 <= float(r["start_seconds"]) <= float(r["end_seconds"]) <= 140
                             for r in timeline), "Timeline intervals invalid")


def save(fig, out: Path, name: str) -> None:
    fig.savefig(out / f"{name}.pdf")
    fig.savefig(out / f"{name}.png", dpi=210)
    plt.close(fig)


def plot_anytime(rows: list[dict[str, str]], out: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.9, 3.2), constrained_layout=True)
    t = [float(r["wall_seconds"]) for r in rows]
    ax.plot(t, [float(r["on_minus_off_EXP_pct_of_reference"]) for r in rows],
            color=COLOR["off_exp"], lw=1.8, label="vs CIPHEUR-off-EXP")
    ax.plot(t, [float(r["on_minus_off_VAL_pct_of_reference"]) for r in rows],
            color=COLOR["off_val"], lw=1.8, label="vs CIPHEUR-off-VAL")
    ax.axhline(0, color="#777777", lw=0.9)
    ax.set(xlim=(20.0, 900.0), xlabel="Wall-clock time (s)",
           ylabel="CIPHEUR-online minus offline\n(% of view reference)")
    ax.grid(axis="y", color="#d9dee3", lw=0.6)
    ax.legend(frameon=False, fontsize=8, loc="best")
    save(fig, out, "on_off_anytime_relative")


def plot_attainment(rows: list[dict[str, str]], out: Path) -> None:
    panels = {}
    for arm in ("knn", "linucb"):
        values = {(int(r["wall_seconds"]), float(r["reference_gap_threshold_pct"])):
                  float(r["on_minus_off_percentage_points"]) for r in rows if r["comparator"] == arm}
        panels[arm] = np.array([[values[t, threshold] for t in TIMES]
                                for threshold in THRESHOLDS], dtype=float)
    limit = max(5.0, 5.0 * math.ceil(max(abs(float(x)) for m in panels.values()
                                         for x in m.flat) / 5.0))
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.9), sharex=True, sharey=True,
                             constrained_layout=True)
    for ax, arm, title in zip(axes, ("knn", "linucb"),
                              ("CIPHEUR-online\n$-$ CIPHEUR-off-EXP",
                               "CIPHEUR-online\n$-$ CIPHEUR-off-VAL")):
        im = ax.imshow(panels[arm], origin="lower", aspect="auto", cmap="RdBu_r",
                       vmin=-limit, vmax=limit)
        ax.set_title(title, fontsize=9)
        ax.set_xticks(range(len(TIMES)), [str(t) for t in TIMES], rotation=45,
                      ha="right", fontsize=6)
        ax.set_yticks(range(len(THRESHOLDS)), [f"{e:g}" for e in THRESHOLDS], fontsize=7)
        ax.set_xlabel("Wall-clock time (s)", fontsize=8)
    axes[0].set_ylabel("Reference-gap target (%)", fontsize=8)
    fig.colorbar(im, ax=axes, label="CIPHEUR-online minus offline (pp)", shrink=.86, pad=.01)
    save(fig, out, "attainment_difference")


def plot_event(rows: list[dict[str, str]], out: Path) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(6.9, 5.8), sharex=True, constrained_layout=True)
    x = [float(r["tau_seconds"]) for r in rows]
    series = (
        ("mean_incumbent_delta_contact_seconds", "Incumbent change (contact-s)", COLOR["online"]),
        ("mean_trajectory_delta_contact_seconds", "Mean trajectory change (contact-s)", COLOR["off_exp"]),
        ("mean_diversity_pct", "Population diversity (%)", COLOR["off_val"]),
    )
    for ax, (field, ylabel, color) in zip(axes, series):
        ax.plot(x, [float(r[field]) for r in rows], marker="o", ms=3.2, lw=1.6, color=color)
        ax.axvline(0, color="#777777", lw=0.8)
        ax.grid(axis="y", color="#d9dee3", lw=0.6)
        ax.set_ylabel(ylabel, fontsize=8)
    axes[-1].set_xlabel("Seconds from actual directed-reseed entry; within installed interval")
    axes[-1].set_xticks([-10, 0, 5, 8, 12, 16, 24, 30])
    axes[-1].tick_params(axis="x", labelsize=8)
    save(fig, out, "siu_event_aligned")


def plot_timeline(rows: list[dict[str, str]], out: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.9, 2.45), constrained_layout=True)
    for row in rows:
        start, end = float(row["start_seconds"]), float(row["end_seconds"])
        if row["kind"] == "model_pending":
            ax.broken_barh([(start, end - start)], (1.04, 0.27),
                           facecolors="#b9cce0", edgecolors="none")
        elif row["kind"] == "http":
            ax.broken_barh([(start, end - start)], (1.11, 0.13),
                           facecolors=COLOR["online"], edgecolors="none")
        elif row["kind"] == "native_action":
            color = COLOR["off_exp"] if row["label"] == "directed_reseed" else COLOR["off_val"]
            ax.broken_barh([(start, max(0, end - start))], (0.30, 0.32),
                           facecolors=color, edgecolors="none")
        elif row["kind"] == "plan_install":
            ax.plot(start, 0.17, marker="v", ms=4.2, color="#222222")
    ax.set(xlim=(0, 140.0), ylim=(0, 1.55), xlabel="Wall-clock time (s)")
    ax.set_yticks([0.45, 1.17])
    ax.set_yticklabels(["Native search", "Model request"])
    ax.grid(axis="x", color="#d9dee3", lw=0.6)
    ax.set_axisbelow(True)
    ax.text(0.99, 0.03, "Black triangles: plan installations", transform=ax.transAxes,
            ha="right", fontsize=7)
    save(fig, out, "async_execution_timeline")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "regenerated")
    args = parser.parse_args()
    rows = {name: load_csv(args.input_dir, name) for name in EXPECTED_SHA256}
    validate(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plot_anytime(rows["anytime_curve.csv"], args.output_dir)
    plot_attainment(rows["attainment_difference.csv"], args.output_dir)
    plot_event(rows["siu_event_summary.csv"], args.output_dir)
    plot_timeline(rows["async_timeline_example.csv"], args.output_dir)
    output = {p.name: sha(p) for p in sorted(args.output_dir.iterdir())
              if p.suffix in (".pdf", ".png")}
    print(json.dumps({"input_sha256": EXPECTED_SHA256, "output_sha256": output},
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
