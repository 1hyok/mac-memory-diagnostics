#!/usr/bin/env python3
"""Capture read-only macOS memory snapshots without touching running apps."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def run(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=10)
    if result.returncode:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise RuntimeError(f"{args[0]} failed: {detail}")
    return result.stdout


def size_bytes(value: str) -> int | None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?)B?\s*", value, re.IGNORECASE)
    if not match:
        return None
    powers = {"": 0, "K": 1, "M": 2, "G": 3, "T": 4}
    return int(float(match.group(1)) * (1024 ** powers[match.group(2).upper()]))


def parse_vm_stat(output: str) -> dict:
    page_size_match = re.search(r"page size of (\d+) bytes", output)
    page_size = int(page_size_match.group(1)) if page_size_match else None
    pages: dict[str, int] = {}
    for line in output.splitlines():
        match = re.match(r"\s*([^:]+):\s*([\d,]+)\.?\s*$", line)
        if match:
            key = re.sub(r"[^a-z0-9]+", "_", match.group(1).strip().lower()).strip("_")
            pages[key] = int(match.group(2).replace(",", ""))
    byte_counts = {f"{key}_bytes": value * page_size for key, value in pages.items()} if page_size else {}
    return {"page_size_bytes": page_size, "pages": pages, "byte_counts": byte_counts}


def parse_swap(output: str) -> dict | None:
    values = {key: size_bytes(value) for key, value in re.findall(r"(total|used|free)\s*=\s*([\d.]+\s*[KMGT]?B?)", output, re.IGNORECASE)}
    return values if values else None


def parse_top(output: str) -> list[dict]:
    rows: list[dict] = []
    started = False
    for line in output.splitlines():
        if re.match(r"\s*PID\s+MEM\s*$", line):
            started = True
            continue
        if not started:
            continue
        fields = line.split()
        if len(fields) < 2 or not fields[0].isdigit():
            continue
        footprint = size_bytes(fields[1])
        if footprint is not None:
            rows.append({"pid": int(fields[0]), "footprint_bytes": footprint, "footprint_display": fields[1]})
    if not started:
        raise RuntimeError("top output did not contain the expected PID/MEM columns")
    return rows


def process_table() -> dict[int, dict]:
    processes: dict[int, dict] = {}
    output = run("ps", "-axo", "pid=,ppid=,rss=,etime=,%cpu=,comm=")
    for line in output.splitlines():
        fields = line.strip().split(None, 5)
        if len(fields) != 6:
            continue
        try:
            pid = int(fields[0])
            processes[pid] = {
                "pid": pid,
                "ppid": int(fields[1]),
                "rss_bytes": int(fields[2]) * 1024,
                "elapsed": fields[3],
                "cpu_percent": float(fields[4]),
                "executable": fields[5],
            }
        except ValueError:
            continue
    return processes


def launchd_labels() -> dict[int, str]:
    labels: dict[int, str] = {}
    for line in run("launchctl", "list").splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and fields[0].isdigit():
            labels[int(fields[0])] = fields[2]
    return labels


def app_name(executable: str) -> str | None:
    match = re.search(r"/([^/]+)\.app/", executable)
    return match.group(1) if match else None


def owner_details(pid: int, processes: dict[int, dict], labels: dict[int, str]) -> tuple[str, str | None]:
    chain: list[dict] = []
    current = processes.get(pid)
    for _ in range(8):
        if current is None:
            break
        chain.append(current)
        if current["ppid"] in (0, 1, current["pid"]):
            break
        current = processes.get(current["ppid"])
    apps = [app_name(item["executable"]) for item in chain]
    apps = [name for name in apps if name]
    owner = apps[-1] if apps else (Path(chain[-1]["executable"]).name if chain else "unknown")
    label = next((labels[item["pid"]] for item in chain if item["pid"] in labels), None)
    return owner, label


def role_hint(executable: str) -> str:
    name = Path(executable).name.lower()
    if "gradledaemon" in name:
        return "build daemon"
    if "kotlincompiledaemon" in name:
        return "compiler daemon"
    if "renderer" in name or "webcontent" in name:
        return "application rendering helper"
    if "windowserver" in name:
        return "macOS window and display service"
    if "virtualization" in executable.lower():
        return "virtual machine support process"
    return "role not identified from executable metadata"


def capture(top_n: int) -> dict:
    processes = process_table()
    labels = launchd_labels()
    top_rows = parse_top(run("top", "-l", "1", "-n", str(top_n), "-o", "mem", "-stats", "pid,mem"))[:top_n]
    for row in top_rows:
        process = processes.get(row["pid"])
        if process is None:
            row["process"] = None
            continue
        owner, label = owner_details(row["pid"], processes, labels)
        row["process"] = {
            "name": Path(process["executable"]).name,
            "owner": owner,
            "role_hint": role_hint(process["executable"]),
            "ppid": process["ppid"],
            "rss_bytes": process["rss_bytes"],
            "cpu_percent": process["cpu_percent"],
            "elapsed": process["elapsed"],
            "launchd_label": label,
            "necessity": "unknown from process metadata alone",
        }

    swap = parse_swap(run("sysctl", "vm.swapusage"))
    physical_memory = run("sysctl", "-n", "hw.memsize").strip()
    system_version = run("sw_vers", "-productVersion").strip()
    return {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "macos_version": system_version,
        "physical_memory_bytes": int(physical_memory) if physical_memory.isdigit() else None,
        "swap_bytes": swap,
        "vm_stat": parse_vm_stat(run("vm_stat")),
        "process_memory_basis": {
            "footprint": "top mem physical memory footprint",
            "rss": "ps rss resident set size; reported separately from top mem",
        },
        "processes": top_rows,
        "interpretation": "A large value alone does not establish that the memory is required, idle cache, or a leak. Compare snapshots with the workload and system paging counters.",
        "safety": "read-only snapshot; no process signals, app/window control, inspector activation, or memory-pressure simulation",
    }


def human_report(snapshot: dict) -> str:
    total_gb = snapshot["physical_memory_bytes"] / (1024 ** 3) if snapshot["physical_memory_bytes"] else None
    lines = [f"mac-memory-diagnostics | {snapshot['captured_at']} | macOS {snapshot['macos_version']}"]
    if total_gb is not None:
        lines.append(f"Physical memory: {total_gb:.1f} GiB")
    swap = snapshot.get("swap_bytes") or {}
    if swap:
        lines.append(f"Swap used: {swap.get('used', 0) / (1024 ** 3):.2f} GiB / {swap.get('total', 0) / (1024 ** 3):.2f} GiB")
    vm = snapshot["vm_stat"]
    selected = ("pages_free", "pages_wired_down", "pages_used_by_vm_compressor", "pageins", "pageouts", "swapins", "swapouts")
    values = [f"{key}={vm['pages'][key]}" for key in selected if key in vm["pages"]]
    if values:
        lines.append("vm_stat totals: " + ", ".join(values))
    lines.append("Top processes by physical footprint (top mem), with resident RSS (ps rss):")
    for row in snapshot["processes"]:
        proc = row.get("process")
        if proc is None:
            lines.append(f"  PID {row['pid']}: {row['footprint_display']} footprint; process ended during capture")
            continue
        lines.append(
            f"  PID {row['pid']} {proc['name']}: footprint {row['footprint_display']}, "
            f"RSS {proc['rss_bytes'] / (1024 ** 2):.0f} MiB, CPU {proc['cpu_percent']:.1f}%, "
            f"owner {proc['owner']}, role {proc['role_hint']}"
        )
    lines.append(snapshot["interpretation"])
    lines.append(snapshot["safety"])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only macOS memory snapshots. No process control or memory-pressure simulation.")
    parser.add_argument("--top", type=int, default=15, help="number of high-footprint processes to include (default: 15)")
    parser.add_argument("--json", action="store_true", help="print each snapshot as JSON Lines")
    parser.add_argument("--output", type=Path, help="append JSON Lines to this local file; no file is written by default")
    parser.add_argument("--interval", type=int, default=0, help="seconds between repeated snapshots (default: one snapshot)")
    parser.add_argument("--count", type=int, default=1, help="number of snapshots (default: 1)")
    args = parser.parse_args()
    if args.top < 1 or args.count < 1 or args.interval < 0:
        parser.error("--top and --count must be positive; --interval cannot be negative")
    if args.count > 1 and args.interval < 1:
        parser.error("--interval must be at least 1 when --count is greater than 1")

    try:
        for index in range(args.count):
            snapshot = capture(args.top)
            serialized = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
            if args.output:
                destination = args.output.expanduser()
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("a", encoding="utf-8") as output:
                    output.write(serialized + "\n")
            print(serialized if args.json else human_report(snapshot))
            if index + 1 < args.count:
                time.sleep(args.interval)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"mac-memory-diagnostics: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
