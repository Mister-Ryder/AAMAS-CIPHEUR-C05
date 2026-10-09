#!/usr/bin/env python3
"""Redraw search-progress charts from the public numeric trajectory CSVs.

The source files are checksum-verified against the release manifest. This
script does not read private solver receipts, graph files, model messages, or
cloud results. It leaves the original published PNG/PDF figures untouched.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


FORMAL = {
    "codex": ("C05-Codex", "#26689A", "native", "-"),
    "knn": ("C05-KNN", "#4E9C78", "native", "-"),
    "linucb": ("C05-LinUCB", "#BE7248", "native", "-"),
    "chils": ("CHILS formal final", "#8863A5", "native", "none"),
    "grasp": ("GRASP", "#A9864A", "outer", "-"),
    "fj": ("FJ formal final", "#4D7E8D", "outer", "none"),
    "fastwvc": ("FastWVC formal final", "#83823D", "outer", "none"),
    "lns": ("StableSolver LNS", "#845D80", "upper", "-"),
    "ls": ("StableSolver LS (9 GiB)", "#607884", "upper", "-"),
    "gwmin": ("GWMIN one pass", "#6F7276", "outer", "none"),
}
OBSERVED = {
    "chils_telemetry": ("CHILS observed rerun", "#A46AC0", "native", "--"),
    "fj_telemetry": ("FJ observed rerun", "#1D8098", "native_process", "--"),
    "fastwvc_telemetry": ("FastWVC observed rerun", "#9B9235", "upper", "--"),
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def table(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def load(root: Path) -> tuple[dict, dict[str, list[dict[str, str]]]]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "cipheur_900s_public_numeric_trajectories_v1":
        raise ValueError("Not a public numeric trajectory release")
    for name, expected in manifest["files_sha256"].items():
        path = (root / name).resolve(strict=True)
        if not path.is_relative_to(root) or sha(path) != expected:
            raise ValueError(f"Public release file hash differs: {name}")
    names = {
        "formal_events": "formal_900s/observed_improvements.csv",
        "formal_final": "formal_900s/audited_final_positions.csv",
        "formal_curve": "formal_900s/observed_curve_1s.csv",
        "observed_events": "observation_reruns_900s/observation_events.csv",
        "observed_final": "observation_reruns_900s/observation_final_positions.csv",
        "observed_curve": "observation_reruns_900s/observation_curve_1s.csv",
    }
    return manifest, {key: table(root / rel) for key, rel in names.items()}


def aggregate_series(rows: list[dict[str, str]], method: str, scope: str,
                     clock: str | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    selected = [r for r in rows if r["method"] == method and r["scope"] == scope and
                (clock is None or r.get("clock", r.get("clock_group")) == clock)]
    selected.sort(key=lambda r: int(r["elapsed_seconds"]))
    if not selected:
        return tuple(np.array([]) for _ in range(4))  # type: ignore[return-value]
    return tuple(np.array([float(r[name]) for r in selected])
                 for name in ("elapsed_seconds", "mean_contact_seconds",
                              "empirical_q25", "empirical_q75"))  # type: ignore[return-value]


def final_marker(rows: list[dict[str, str]], method: str, graph: str | None) -> tuple[float, float] | None:
    selected = [r for r in rows if r["method"] == method and
                (graph is None or r["graph_id"] == graph)]
    if not selected:
        return None
    score = sum(int(r["value_ticks"]) for r in selected) / len(selected) / 1_000_000
    if method in {"chils", "gwmin"}:
        times = [float(r["measured_final_time_seconds"]) for r in selected]
        return sum(times) / len(times), score
    return 900.0, score


def run_events(data: dict[str, list[dict[str, str]]], method: str,
               graph: str, clock: str) -> list[list[tuple[float, float]]]:
    groups: dict[int, list[tuple[float, float]]] = defaultdict(list)
    if method in FORMAL:
        for row in data["formal_events"]:
            if row["method"] == method and row["graph_id"] == graph:
                groups[int(row["replicate"])].append(
                    (float(row["elapsed_seconds"]), int(row["value_ticks"]) / 1_000_000))
    else:
        field = "outer_alignment_bound_seconds" if clock == "upper" else "native_elapsed_seconds"
        for row in data["observed_events"]:
            if row["method"] == method and row["graph_id"] == graph:
                groups[int(row["seed"])].append((float(row[field]),
                                                  int(row["value_ticks"]) / 1_000_000))
    return [sorted(points) for _, points in sorted(groups.items())]


def visible_run(points: list[tuple[float, float]], left: float,
                right: float) -> tuple[list[float], list[float]]:
    before = [point for point in points if point[0] <= left]
    after = [point for point in points if left < point[0] <= right]
    used = ([ (left, before[-1][1]) ] if before else []) + after
    if not used:
        return [], []
    if used[-1][0] < right:
        used.append((right, used[-1][1]))
    return [p[0] for p in used], [p[1] for p in used]


def draw(ax, data: dict, graph: str | None, clock_group: str | None,
         *, band: bool, individual: bool) -> None:
    for method, (label, color, clock, style) in FORMAL.items():
        if clock_group is not None and clock != clock_group:
            continue
        if style == "none":
            marker = final_marker(data["formal_final"], method, graph)
            if marker:
                ax.scatter(*marker, marker="D", facecolors="white", edgecolors=color,
                           linewidths=1.35, s=29, label=label, zorder=6)
            continue
        x, mean, q25, q75 = aggregate_series(data["formal_curve"], method,
                                               graph or "overall")
        if not len(x):
            continue
        visible = (x >= (300 if graph else 0)) & (x <= 900)
        if individual and graph:
            for points in run_events(data, method, graph, clock):
                xx, yy = visible_run(points, 300, 900)
                if xx:
                    ax.step(xx, yy, where="post", color=color, alpha=.11, linewidth=.55)
        if band and graph:
            ax.fill_between(x[visible], q25[visible], q75[visible],
                            color=color, alpha=.13, step="post", linewidth=0)
        ax.step(x[visible], mean[visible], where="post", color=color,
                linestyle=style, linewidth=1.45, label=label)
    for method, (label, color, default_group, style) in OBSERVED.items():
        if method == "fastwvc_telemetry":
            group = "native_process" if clock_group == "native_process" else "upper"
            score_clock = "native" if group == "native_process" else "outer_upper_bound"
        else:
            group = default_group
            score_clock = "native"
        if clock_group is not None and group != clock_group:
            continue
        x, mean, q25, q75 = aggregate_series(data["observed_curve"], method,
                                               graph or "overall", score_clock)
        if not len(x):
            continue
        visible = (x >= (300 if graph else 0)) & (x <= 900)
        if individual and graph:
            for points in run_events(data, method, graph, group):
                xx, yy = visible_run(points, 300, 900)
                if xx:
                    ax.step(xx, yy, where="post", color=color, alpha=.11, linewidth=.55)
        if band and graph:
            ax.fill_between(x[visible], q25[visible], q75[visible],
                            color=color, alpha=.13, step="post", linewidth=0)
        ax.step(x[visible], mean[visible], where="post", color=color,
                linestyle=style, linewidth=1.55, label=label)
    ax.set_xlim((300 if graph else 0), 918)
    ax.set_xticks([300, 450, 600, 750, 900] if graph else [0, 150, 300, 450, 600, 750, 900])
    ax.ticklabel_format(axis="y", style="plain", useOffset=False)
    ax.grid(axis="y", color="#DCE3E7", linewidth=.55)
    ax.set_axisbelow(True)
    ax.set_box_aspect(1)


def save(fig, output: Path, stem: str) -> None:
    fig.savefig(output / f"{stem}.png", dpi=220)
    fig.savefig(output / f"{stem}.pdf")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to replace existing figure directory: {output}")
    if not output.parent.is_dir():
        raise ValueError("Output parent must exist")
    manifest, data = load(root)
    output.mkdir()
    fig = plt.figure(figsize=(17.2, 10.2), dpi=160)
    ax = fig.add_axes([.06, .12, .49, .80])
    draw(ax, data, None, None, band=False, individual=False)
    ax.set_xlabel("Recorded elapsed seconds (method-specific clocks)")
    ax.set_ylabel("Mean contact duration (s), 40 positions/method")
    fig.suptitle("CP-SCALE-AU-L002 · observed search progress, 0–900 s",
                 x=.06, y=.97, ha="left", fontsize=16, weight="bold")
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(.58, .88),
               frameon=False, ncol=2, fontsize=8.6)
    fig.text(.59, .40,
             "One physical source in eight constraint views; five runs/view.\n"
             "Lines: observed feasible incumbents. Open diamonds: formal\n"
             "final-only observations. New telemetry cohorts are separate reruns.\n\n"
             "C05/CHILS: native solver clock; FJ: native child clock;\n"
             "GRASP: position wall clock; StableSolver/FastWVC:\n"
             "conservative outer upper bound. These clocks have different\n"
             "origins; x alignment is descriptive.",
             fontsize=9.0, color="#45515A", linespacing=1.5, va="top")
    save(fig, output, "01_all_methods_0_900_public_replot")
    titles = {"native": "C05 / CHILS · native solver",
              "native_process": "FJ / FastWVC · native process",
              "outer": "GRASP / final-only · position wall",
              "upper": "StableSolver / FastWVC · outer upper bound"}
    for clock, title in titles.items():
        fig, axes = plt.subplots(2, 4, figsize=(19.2, 10.8), dpi=160)
        for ax, graph in zip(axes.flat, manifest["views"]):
            draw(ax, data, graph, clock, band=True, individual=True)
            ax.set_title(graph.removeprefix("CP-SCALE-AU-L002__"),
                         loc="left", fontsize=9.3, weight="bold")
            ax.tick_params(labelsize=7.6)
        handles, labels = axes.flat[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(.5, .945),
                   frameon=False, ncol=4, fontsize=8.3)
        fig.suptitle(title + " · 300–900 s", x=.045, y=.99, ha="left",
                     fontsize=14.3, weight="bold")
        fig.supxlabel("Elapsed seconds on the labeled clock", y=.06)
        fig.supylabel("Mean contact duration (s)", x=.015)
        fig.text(.5, .025, "Faint lines: five actual runs/view; colored band: empirical 25th–75th percentile (not a confidence interval).",
                 ha="center", fontsize=8.3, color="#45515A")
        fig.tight_layout(rect=(.035, .075, 1, .90))
        save(fig, output, f"02_eight_views_300_900_{clock}_public_replot")
    print(json.dumps({"output": str(output), "panels": len(titles) + 1}))


if __name__ == "__main__":
    main()
