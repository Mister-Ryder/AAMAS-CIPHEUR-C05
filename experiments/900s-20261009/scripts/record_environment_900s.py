#!/usr/bin/env python3
"""Record read-only cloud quota, throttling counters and worker affinities."""
import argparse
import json
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

def read(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError):
        return None


def stat_lines(path):
    content = read(path)
    if content is None:
        return None
    out = {}
    for line in content.splitlines():
        pieces = line.split()
        if len(pieces) == 2:
            try:
                out[pieces[0]] = int(pieces[1])
            except ValueError:
                out[pieces[0]] = pieces[1]
    return out


def process_info(pid):
    status = read("/proc/{}/status".format(pid))
    if status is None:
        return {"pid": pid, "present": False}
    fields = {}
    for line in status.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            if key in ("Name", "State", "VmRSS", "Threads", "Cpus_allowed_list"):
                fields[key] = value.strip()
    try:
        affinity = sorted(os.sched_getaffinity(pid))
    except (ProcessLookupError, PermissionError):
        affinity = None
    child_text = read("/proc/{0}/task/{0}/children".format(pid)) or ""
    children = [int(x) for x in child_text.split() if x.isdigit()]
    return {"pid": pid, "present": True, "fields": fields, "affinity": affinity,
            "children": [process_info(child) for child in children]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch("[A-Za-z0-9_-]{1,60}", args.label):
        raise ValueError("Unsafe label")
    root = args.root.resolve()
    pids = {
        "c05_controls": root / "controls.pid",
        "c05_codex": root / "registrations/codex_master.pid",
        "chils_p4": root / "chils_p4/execution.pid",
    }
    groups = {}
    for name, path in pids.items():
        raw = read(path)
        groups[name] = process_info(int(raw)) if raw and raw.isdigit() else {"pid_file": str(path), "present": False}
    quota = read("/sys/fs/cgroup/cpu.max")
    quota_cores = None
    if quota:
        q = quota.split()
        if len(q) == 2 and q[0] != "max":
            quota_cores = int(q[0]) / int(q[1])
    obj = {
        "schema": "cipheur_c05_900s_cloud_environment_sample_v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "label": args.label,
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "kernel": platform.release(),
        "host_logical_cpus": os.cpu_count(),
        "cgroup_cpu_max": quota,
        "cgroup_cpu_quota_cores": quota_cores,
        "cgroup_cpu_stat": stat_lines("/sys/fs/cgroup/cpu.stat"),
        "cgroup_cpuset_effective": read("/sys/fs/cgroup/cpuset.cpus.effective"),
        "cgroup_memory_max_bytes": read("/sys/fs/cgroup/memory.max"),
        "cgroup_memory_current_bytes": read("/sys/fs/cgroup/memory.current"),
        "cgroup_memory_peak_bytes": read("/sys/fs/cgroup/memory.peak"),
        "process_groups": groups,
        "counter_scope": "cpu.stat is cumulative for the container cgroup; compare samples for a study-window delta.",
    }
    output = root / "evidence" / ("environment_" + args.label + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"output": str(output), "at": obj["captured_at_utc"],
                      "quota_cores": quota_cores,
                      "throttled_usec": (obj["cgroup_cpu_stat"] or {}).get("throttled_usec"),
                      "groups": {name: len(group.get("children", [])) for name, group in groups.items()}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
