#!/usr/bin/env python3
"""Read-only evaluation of the APPROVED start conditions of the full acceptance gate (the ones recorded since the recovery stages: kernel memory pressure level 1, system swap
used < 1 GB, free memory >= 60 %, >= 15 GB free disk, the test ports free, no MineGuard server running, a clean git tree). It measures and reports; it never changes a limit, never
signals a process and never touches storage. Exit 0 only if EVERY condition passes; otherwise 1 and the verdict is RESOURCE_BLOCKED. The limits below are constants on purpose: the
gate is not made to pass by editing them.
Usage: gate_preconditions.py [--json]"""
import json
import re
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMITS = {"pressure_level": 1, "swap_used_mb_max": 1024, "free_percent_min": 60, "disk_free_gb_min": 15}
PORTS = (8000, 8793, 9333, 9334, 9335, 9336)


def run(cmd, cwd=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=8, cwd=cwd).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def measure() -> dict:
    lvl = run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip()
    sw = run(["sysctl", "-n", "vm.swapusage"])
    mp = run(["memory_pressure"])
    free = re.search(r"free percentage:\s*(\d+)%", mp)
    used = re.search(r"used = ([\d.]+)M", sw)
    total = re.search(r"total = ([\d.]+)M", sw)
    df = run(["df", "-g", "/System/Volumes/Data"]).strip().splitlines()
    busy = []
    for p in PORTS:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", p)) == 0:
                busy.append(p)
    return {"pressure_level": int(lvl) if lvl.isdigit() else None, "swap_used_mb": float(used.group(1)) if used else None, "swap_total_mb": float(total.group(1)) if total else None,
            "free_percent": int(free.group(1)) if free else None, "disk_free_gb": int(df[-1].split()[3]) if len(df) > 1 else None, "busy_ports": busy,
            "mineguard_server_running": bool(run(["pgrep", "-f", "uvicorn mineguard.app"]).strip()),
            "git_clean": run(["git", "--no-optional-locks", "status", "--porcelain"], cwd=ROOT).strip() == "", "git_head": run(["git", "rev-parse", "HEAD"], cwd=ROOT).strip()}


def evaluate(m: dict, limits: dict = LIMITS) -> list[dict]:
    """Pure: one row per condition, `ok` False whenever the measurement is missing (an unmeasured condition is never a pass)."""
    def row(name, value, rule, ok):
        return {"condition": name, "measured": value, "rule": rule, "ok": bool(ok)}
    lvl, swap, free, disk = m.get("pressure_level"), m.get("swap_used_mb"), m.get("free_percent"), m.get("disk_free_gb")
    return [row("kernel memory pressure level", lvl, f"== {limits['pressure_level']}", lvl is not None and lvl == limits["pressure_level"]),
            row("system swap used (MB)", swap, f"< {limits['swap_used_mb_max']}", swap is not None and swap < limits["swap_used_mb_max"]),
            row("system free memory (%)", free, f">= {limits['free_percent_min']}", free is not None and free >= limits["free_percent_min"]),
            row("free disk (GB)", disk, f">= {limits['disk_free_gb_min']}", disk is not None and disk >= limits["disk_free_gb_min"]),
            row("test ports in use", m.get("busy_ports"), "none", m.get("busy_ports") == []),
            row("a MineGuard server is running", m.get("mineguard_server_running"), "no", m.get("mineguard_server_running") is False),
            row("git working tree clean", m.get("git_clean"), "yes", m.get("git_clean") is True)]


def main() -> int:
    m = measure()
    rows = evaluate(m)
    ok = all(r["ok"] for r in rows)
    if "--json" in sys.argv:
        print(json.dumps({"verdict": "READY" if ok else "RESOURCE_BLOCKED", "measured": m, "limits": LIMITS, "rows": rows}, indent=1))
    else:
        for r in rows:
            print(("PASS " if r["ok"] else "FAIL ") + f"{r['condition']}: {r['measured']} (rule: {r['rule']})")
        print("VERDICT:", "READY" if ok else "RESOURCE_BLOCKED (not retried automatically; limits are fixed)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
