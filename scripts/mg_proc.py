#!/usr/bin/env python3
"""Start and stop MineGuard test servers by VERIFIED IDENTITY, never by name (MG-PROC-001).

  mg_proc.py start <record.json> [--log FILE] -- <command...>   start a detached process and record its identity
  mg_proc.py stop  <record.json> [--grace SECONDS]              stop exactly that process, only if it is still the same one

The record holds the pid, the process creation time (`ps lstart`, so a reused pid is not mistaken for the original), the exact command
line, the working directory and the run-owner token. `stop` signals the pid only if ALL of these still match; otherwise it signals
nothing and exits 3. The owner token (MG_RUN_OWNER) is generated per run by the caller and is inherited through the environment, so a
look-alike process that was started elsewhere carries no matching token. No pattern matching on command lines is ever used to choose
a target. Standard library only.

Exit codes: 0 stopped (or already gone) | 2 usage | 3 identity verification failed, nothing was signalled | 4 still running after SIGKILL | 5 no record
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER_VAR = "MG_RUN_OWNER"


def _ps(pid: int, *fmt: str) -> str:
    return subprocess.run(["ps", *fmt, "-p", str(pid)], capture_output=True, text=True).stdout.strip()


def _cwd(pid: int) -> str:
    out = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, text=True).stdout
    return next((line[1:] for line in out.splitlines() if line.startswith("n")), "")


def identity(pid: int) -> dict | None:
    """What can be observed about a live pid right now; None if there is no such process (a zombie counts as gone)."""
    state = _ps(pid, "-o", "stat=")
    if not state or state.startswith("Z"):
        return None
    return {"pid": pid, "lstart": _ps(pid, "-o", "lstart="), "command": _ps(pid, "-ww", "-o", "command="),
            "cwd": os.path.realpath(_cwd(pid)) if _cwd(pid) else "", "env_has_owner": None}


def _owner_present(pid: int, owner: str) -> bool:
    # `ps -E` (capital) appends the environment on macOS; the token must appear as a whole NAME=value word
    return f" {OWNER_VAR}={owner}" in " " + _ps(pid, "-E", "-ww", "-o", "command=") + " "


def verify(rec: dict) -> tuple[bool, str]:
    cur = identity(rec["pid"])
    if cur is None:
        return False, "gone"
    for key in ("lstart", "command", "cwd"):
        if cur[key] != rec[key]:
            return False, f"{key} differs (recorded {rec[key]!r}, now {cur[key]!r}): pid {rec['pid']} is not the recorded process"
    if not _owner_present(rec["pid"], rec["owner"]):
        return False, "run-owner token not present in the process environment: not started by this run"
    return True, "verified"


def start(record: Path, log: str | None, cmd: list[str]) -> int:
    owner = os.environ.get(OWNER_VAR)
    if not owner or not cmd:
        print(f"refusing to start: {OWNER_VAR} must be set (the run owner token) and a command given", file=sys.stderr)
        return 2
    out = open(log, "ab") if log else subprocess.DEVNULL
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, start_new_session=True, env=dict(os.environ))
    for _ in range(50):  # wait until the process shows its final command line
        cur = identity(p.pid)
        if cur and cur["command"] and cur["cwd"]:
            break
        time.sleep(0.1)
    cur = identity(p.pid)
    if cur is None:
        print("the process exited immediately", file=sys.stderr)
        return 4
    rec = {"pid": p.pid, "lstart": cur["lstart"], "command": cur["command"], "cwd": cur["cwd"], "owner": owner, "argv": cmd}
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(rec, indent=1))
    ok, why = verify(rec)
    if not ok:  # could not establish positive identity: do not leave an unverifiable process running
        print(f"identity of the started process could not be verified ({why}); stopping it directly because this helper just created it", file=sys.stderr)
        try:
            p.terminate()
        except OSError:
            pass
        return 4
    print(p.pid)
    return 0


def stop(record: Path, grace: float) -> int:
    if not record.is_file():
        print(f"no record at {record}", file=sys.stderr)
        return 5
    try:
        rec = json.loads(record.read_text())
        rec["pid"], rec["owner"], rec["lstart"], rec["command"], rec["cwd"]  # noqa: B018 (all fields must be present)
    except (ValueError, KeyError, TypeError):
        print("malformed process record: nothing signalled", file=sys.stderr)
        return 3
    if not isinstance(rec["pid"], int) or isinstance(rec["pid"], bool) or rec["pid"] <= 1:
        print("invalid pid in record: nothing signalled", file=sys.stderr)
        return 3
    ok, why = verify(rec)
    if not ok and why == "gone":
        print(f"pid {rec['pid']} is already gone")
        return 0
    if not ok:
        print(f"identity verification failed, nothing signalled: {why}", file=sys.stderr)
        return 3
    os.kill(rec["pid"], signal.SIGTERM)
    end = time.time() + grace
    while time.time() < end:
        if identity(rec["pid"]) is None:
            print(f"stopped pid {rec['pid']}")
            return 0
        time.sleep(0.2)
    ok, why = verify(rec)  # re-verify immediately before the stronger signal
    if not ok:
        print(f"pid {rec['pid']} changed identity before SIGKILL, not signalled: {why}", file=sys.stderr)
        return 0 if why == "gone" else 3
    os.kill(rec["pid"], signal.SIGKILL)
    time.sleep(1)
    if identity(rec["pid"]) is None:
        print(f"killed pid {rec['pid']}")
        return 0
    print(f"pid {rec['pid']} is still running after SIGKILL", file=sys.stderr)
    return 4


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "start" and "--" in argv:
        i = argv.index("--")
        opts = argv[2:i]
        log = opts[opts.index("--log") + 1] if "--log" in opts else None
        return start(Path(argv[1]), log, argv[i + 1:])
    if len(argv) >= 2 and argv[0] == "stop":
        grace = float(argv[argv.index("--grace") + 1]) if "--grace" in argv else 10.0
        return stop(Path(argv[1]), grace)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
