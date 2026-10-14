# Project instructions

- Keep collection read-only. Never send signals, terminate or quit processes, control app windows, activate an inspector, or simulate memory pressure.
- Use fixed system commands without a shell. Do not collect full process command lines, window titles, document names, or environment variables.
- Do not commit process snapshots or personal machine data. Public commits contain source and documentation only.
- Separate observed memory values from inferred role or need. Process metadata alone cannot establish that a process is unnecessary or leaking.
