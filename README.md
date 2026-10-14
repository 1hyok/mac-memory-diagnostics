# mac-memory-diagnostics

Read-only snapshots to help explain macOS memory use over time. The tool reports high-footprint processes, their resident RSS, basic ownership hints, and system virtual-memory and swap counters. It does not decide that a process is safe to stop.

## Requirements

- macOS with the standard `top`, `ps`, `vm_stat`, `sysctl`, `sw_vers`, and `launchctl` tools
- Python 3.10 or newer
- No third-party packages and no administrator privileges

## Capture a snapshot

```sh
python3 mac_memory_diagnostics.py
```

The default prints one snapshot and saves nothing. `top mem` is the process physical-memory footprint. `ps rss` is resident set size. The tool reports them separately instead of treating them as interchangeable. The report also includes `vm_stat` system totals and the current swap allocation.

Compare the report with what the Mac was doing at that time. For a short trend sample:

```sh
python3 mac_memory_diagnostics.py --interval 30 --count 10
```

To keep a local JSON Lines history, choose a path outside this repository:

```sh
python3 mac_memory_diagnostics.py \
  --json --interval 30 --count 10 \
  --output "$HOME/Library/Application Support/mac-memory-diagnostics/snapshots.jsonl"
```

The JSON history contains process names, PIDs, parent PIDs, launchd labels, and memory values. Treat it as private machine data. Do not commit it or share it without reviewing it. The program never writes a file unless `--output` is supplied.

## Interpreting results

- A single high footprint only identifies a process using memory at that sample. It does not prove that the usage is required, idle cache, or a leak.
- `ps rss` is a resident-set metric with a different definition from `top mem`; keep the values separate.
- `vm_stat` values are system-wide counters. Comparing samples taken during a known workload is more informative than reading one total in isolation.
- Repeated growth while the same task runs, together with page-in/page-out and swap changes, gives stronger evidence to investigate. It still does not identify the owning feature by itself.
- "Role hint" is based on executable and owner metadata. Whether a process is needed depends on the work the user wants to keep open.
- The report does not map Electron renderer processes to individual windows. It does not infer a one-to-one mapping from the number of processes and windows.
- `memory_pressure` is deliberately not used: its macOS man page describes applying real or simulated memory pressure, not a passive status query.

Apple's [memory performance guide](https://developer.apple.com/library/archive/documentation/Performance/Conceptual/PerformanceOverview/InitialEvaluation/InitialEvaluation.html) describes `top` memory categories, resident private/shared memory, and page-in/page-out trends. Apple's [virtual memory guide](https://developer.apple.com/library/archive/documentation/Performance/Conceptual/ManagingMemory/Articles/VMPages.html) explains `vm_stat` and why a reserved virtual-memory region does not necessarily represent resident physical pages. The installed macOS `top(1)`, `ps(1)`, `vm_stat(1)`, and `memory_pressure(1)` man pages provide version-specific command details.

## Safety and data handling

The tool only reads process and system statistics. It does not send signals, quit or close apps/windows, restart the Mac, activate app inspectors, or generate memory pressure. It avoids full process arguments because they can contain private data. It does not collect window titles or document names.

No process snapshot is checked into this repository. Keep any capture history outside the repository and inspect it before sharing.

## Limitations

This is a triage aid, not a memory profiler. It cannot determine from process metadata whether a workload still needs a process or whether an app has a leak. For app-level allocation analysis, use Xcode Instruments with the app and workload selected intentionally.
