#!/usr/bin/env python3
"""Draw the eight audited 9 GiB/native850 traces at recorded event times."""

from __future__ import annotations

import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent
ARM = "stablesolver_local_search"
OUT = ROOT / "analysis" / "recorded_trajectories"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_one(view: str) -> tuple[list[Decimal], list[int], dict]:
    stem = f"{view}__s0000"
    receipt_path = ROOT / "results" / ARM / f"{stem}.json"
    trace_path = ROOT / "traces" / ARM / f"{stem}.csv"
    receipt = read_json(receipt_path)
    require(receipt["status"] == "ok" and receipt["graph_id"] == view
            and receipt["seed"] == 0 and receipt["arm"] == ARM,
            f"Unusable recovery receipt: {receipt_path}")
    require(receipt["config"]["native_time_limit_seconds"] == 850.0,
            f"Unexpected native time limit: {view}")
    require(receipt["trajectory_path"] == str(trace_path.relative_to(ROOT)).replace("\\", "/"),
            f"Trace path differs from receipt: {view}")
    require(receipt["trajectory_alignment_method"] ==
            "native_process_wall_minus_final_native_time_upper_bound" and
            receipt["config"]["trajectory_offset_is_observation_upper_bound"] is True,
            f"Conservative native graph-load alignment missing: {view}")
    times, native_times, ticks = [], [], []
    with trace_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames == ["elapsed_seconds", "native_time_seconds", "value_ticks"],
                f"Unexpected trace columns: {view}")
        for row in reader:
            times.append(Decimal(row["elapsed_seconds"]))
            native_times.append(Decimal(row["native_time_seconds"]))
            ticks.append(int(row["value_ticks"]))
    require(bool(times) and len(times) == len(native_times) == len(ticks),
            f"Empty or malformed trace: {view}")
    require(all(Decimal(0) <= t <= Decimal("900.1") for t in times),
            f"Event outside 900-second budget: {view}")
    require(all(a <= b for a, b in zip(times, times[1:])),
            f"Event timestamps go backward: {view}")
    require(all(a < b for a, b in zip(ticks, ticks[1:])),
            f"Incumbent objective fails strict increase: {view}")
    require(ticks[-1] == receipt["value_ticks"],
            f"Final recorded event differs from audited receipt: {view}")
    offsets = [t - n for t, n in zip(times, native_times)]
    require(all(v >= 0 for v in offsets) and
            max(offsets) - min(offsets) <= Decimal("0.000000002"),
            f"Graph-load offset is inconsistent: {view}")
    receipt_offset = Decimal(str(receipt["native_graph_load_offset_seconds"]))
    # The trace includes wrapper startup before the native child begins.
    # Bound that term by the recorded position wall minus native process wall.
    startup_upper_bound = (Decimal(str(receipt["wall_seconds"]))
                           - Decimal(str(receipt["native_process_wall_seconds"])))
    require(-Decimal("0.000000002") <= offsets[0] - receipt_offset
            <= startup_upper_bound + Decimal("0.000000002"),
            f"Recorded elapsed offset is inconsistent with wrapper/native timing: {view}")
    return times, ticks, {
        "graph_id": view,
        "event_count": len(times),
        "first_recorded_elapsed_seconds": str(times[0]),
        "last_recorded_elapsed_seconds": str(times[-1]),
        "conservative_graph_load_offset_seconds": str(offsets[0]),
        "native_graph_load_offset_seconds": str(receipt_offset),
        "last_value_ticks": ticks[-1],
        "last_value_contact_seconds": str(Decimal(ticks[-1]) / Decimal(1_000_000)),
        "receipt_path": str(receipt_path.relative_to(ROOT)).replace("\\", "/"),
        "receipt_sha256": digest(receipt_path),
        "trace_path": str(trace_path.relative_to(ROOT)).replace("\\", "/"),
        "trace_sha256": digest(trace_path),
    }


def main() -> None:
    protocol_path = ROOT / "preregistration.json"
    audit_path = ROOT / "analysis" / "classical_900s_independent_audit.json"
    execution_path = ROOT / "analysis" / "recovery_execution_audit.json"
    protocol, audit, execution = map(read_json, (protocol_path, audit_path, execution_path))
    require(protocol["method_time_controls"][ARM] ==
            "native --time-limit 850 seconds; outer end-to-end 900-second cap",
            "Contingency method time control differs from registration")
    require(audit["status"] == "complete_valid" and audit["audited_valid_positions"] == 8,
            "Recovery graph and solution audit is not complete and valid")
    require(execution["status"] == "valid" and all(execution["checks"].values()),
            "Recovery launch and resource audit is not valid")
    views = list(protocol["graph_sha256"])
    require(len(views) == 8, "Recovery registration does not contain eight views")
    audited = {row["graph_id"]: row for row in audit["by_view"]
               if row["arm"] == ARM}
    require(set(audited) == set(views), "Audited recovery views differ from registration")
    traces = {}
    rows = []
    for view in views:
        times, ticks, record = load_one(view)
        require(Decimal(audited[view]["mean_contact_seconds"]) ==
                Decimal(ticks[-1]) / Decimal(1_000_000),
                f"Trace final value differs from independent audit: {view}")
        traces[view] = (times, ticks)
        rows.append(record)
    require({path.name for path in (ROOT / "traces" / ARM).glob("*.csv")} ==
            {f"{view}__s0000.csv" for view in views},
            "Unregistered recovery trace found")

    OUT.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(4, 2, figsize=(14, 14), sharex=True)
    for ax, view, row in zip(axes.flat, views, rows):
        times, ticks = traces[view]
        x = [float(t) for t in times]
        y = [v / 1_000_000 for v in ticks]
        ax.step(x, y, where="post", linewidth=1.5, color="#8053a5")
        ax.scatter([x[-1]], [y[-1]], color="#8053a5", s=20, zorder=4)
        ax.set_title(f"{view.split('__', 1)[1]}  |  {row['event_count']} recorded events")
        ax.set_xlim(0, 900)
        ax.set_ylabel("Contact seconds")
        ax.grid(alpha=0.25)
    axes[-1, 0].set_xlabel("Recorded position elapsed seconds")
    axes[-1, 1].set_xlabel("Recorded position elapsed seconds")
    fig.suptitle("StableSolver local-search, 9 GiB/native850 follow-up: recorded incumbents", fontsize=16)
    fig.text(0.5, 0.032,
             "Each curve ends at its final recorded improvement; no value is projected to 900 s. "
             "Elapsed time includes a conservative graph-load offset.",
             ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.06, 1, 0.97))
    png = OUT / "stablesolver_local_search_9gib_native850_by_view_900s.png"
    pdf = OUT / "stablesolver_local_search_9gib_native850_by_view_900s.pdf"
    fig.savefig(png, dpi=190)
    fig.savefig(pdf)
    plt.close(fig)

    manifest = {
        "schema": "cipheur_stablesolver_local_search_9gib_native850_recorded_trajectory_figure_v1",
        "method": ARM,
        "view_count": len(views),
        "position_count": len(rows),
        "total_recorded_events": sum(row["event_count"] for row in rows),
        "time_basis": "official native intermediary event time plus measured wrapper startup and conservative graph-load offset; no invented 900-second endpoint",
        "protocol_sha256": digest(protocol_path),
        "independent_audit_sha256": digest(audit_path),
        "execution_audit_sha256": digest(execution_path),
        "figure_png": png.name,
        "figure_png_sha256": digest(png),
        "figure_pdf": pdf.name,
        "figure_pdf_sha256": digest(pdf),
        "views": rows,
    }
    (OUT / "figure_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8")
    print(json.dumps({"png": str(png), "pdf": str(pdf),
                      "positions": len(rows), "events": manifest["total_recorded_events"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
