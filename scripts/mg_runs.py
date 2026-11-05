#!/usr/bin/env python3
"""Isolated, test-owned run storage for the acceptance scripts (MG-PROC-002). Stdlib only.

  mg_runs.py create [--base DIR]        make a fresh unique directory with an owner marker; print '<absolute path> <token>'
  mg_runs.py remove DIR TOKEN [--base DIR]   delete DIR only if it is provably the directory `create` made (token matches) and holds nothing else

The user's own runs (<project>/runs) are never a valid target. `remove` refuses (exit 3, nothing deleted) unless ALL hold:
  - DIR is absolute, has no '..', and no path component is a symlink (realpath equals the given path)
  - DIR is a direct child of the allowed base (default <project>/logs/acceptance_runs) and is named runs_<UTC stamp>_<8 hex>
  - DIR holds a regular-file marker whose project and token equal this project and the token given
  - the tree holds only: the marker, run_<16 hex> directories, session_<16 hex> directories, _tmp_* directories and the application's own alerts/, feeds/ and external_results/ directories; no symlink, no other file name at the top level, one filesystem
Exit codes: 0 done, 2 usage, 3 refused.
"""
import json
import os
import re
import secrets
import shutil
import stat
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = ROOT / "logs" / "acceptance_runs"
MARKER = ".mg_acceptance_runs_owner"
NAME = re.compile(r"^runs_\d{8}T\d{6}Z_[0-9a-f]{8}$")
TOP_OK = re.compile(r"^(run_[0-9a-f]{16}|session_[0-9a-f]{16}|_tmp_[0-9a-f]{8}|alerts|feeds|external_results)$")   # alerts/ and feeds/ are created by the alert engine and the feed manager; external_results/ by the MG-EVIDENCE-10 importer (source bytes of imported EXTERNAL results)


class Refused(Exception):
    pass


def base_dir(argv: list[str]) -> Path:
    if "--base" in argv:
        return Path(argv[argv.index("--base") + 1])
    return DEFAULT_BASE


def create(base: Path):
    base.mkdir(parents=True, exist_ok=True)
    if base.is_symlink() or os.path.realpath(base) != str(base.absolute()):
        raise Refused(f"base is not a real directory path: {base}")
    token = secrets.token_hex(16)
    d = base / f"runs_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{secrets.token_hex(4)}"
    os.mkdir(d, 0o700)  # fails if it exists: never adopts an existing directory
    with open(d / MARKER, "x") as f:
        json.dump({"project": str(ROOT), "token": token, "created": time.time(), "pid": os.getpid()}, f)
    os.chmod(d / MARKER, 0o600)
    return d, token


def verify_owned(target: str, token: str, base: Path) -> Path:
    p = Path(target)
    if not p.is_absolute() or ".." in p.parts:
        raise Refused("target must be an absolute path without '..'")
    if os.path.realpath(p) != str(p) or p.is_symlink():
        raise Refused("target or one of its parents is a symlink or not canonical")
    if os.path.realpath(base) != str(base) or p.parent != base:
        raise Refused("target is not a direct child of the allowed base")
    if p == ROOT / "runs" or not NAME.match(p.name):
        raise Refused("target name is not an acceptance runs directory name")
    st = os.lstat(p)
    if not stat.S_ISDIR(st.st_mode):
        raise Refused("target is not a directory")
    m = p / MARKER
    try:
        mst = os.lstat(m)
        if not stat.S_ISREG(mst.st_mode):
            raise Refused("owner marker is not a regular file")
        info = json.loads(m.read_text())
    except (OSError, ValueError) as e:
        raise Refused(f"owner marker unreadable: {e}")
    if info.get("project") != str(ROOT) or not token or info.get("token") != token:
        raise Refused("owner marker does not match this project and token")
    for entry in os.scandir(p):
        if entry.name == MARKER:
            continue
        if not TOP_OK.match(entry.name):
            raise Refused(f"unexpected top-level entry: {entry.name}")
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            raise Refused(f"top-level entry is a symlink or not a directory: {entry.name}")
    for root, dirs, files in os.walk(p, followlinks=False):
        for n in dirs + files:
            q = os.path.join(root, n)
            qs = os.lstat(q)
            if stat.S_ISLNK(qs.st_mode):
                raise Refused(f"symlink inside the tree: {q}")
            if qs.st_dev != st.st_dev:
                raise Refused(f"different filesystem inside the tree: {q}")
    return p


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("create", "remove"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        if argv[0] == "create":
            d, token = create(base_dir(argv))
            print(d, token)  # "<path> <token>" on one line; the caller keeps the token in a variable
            return 0
        if len(argv) < 3:
            return 2
        p = verify_owned(argv[1], argv[2], base_dir(argv))
        shutil.rmtree(p)
        print(f"removed {p}")
        return 0
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
