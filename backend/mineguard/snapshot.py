"""MG-EVENT-032: an immutable, bounded snapshot of the files a stored source's events derive from.

Every dependency is opened ONCE through a pinned directory descriptor (a symlink anywhere on the leaf path is refused, never followed), checked to be a regular file, size-bounded, read fully
into bytes and hashed. Parsing and derivation use ONLY those bytes, and the cache identity is the hash of those same bytes, so a document of one revision is never combined with another
revision's observations and a later change of the path cannot alter what was derived. `matches_disk()` re-reads the current files so a caller can refuse a result whose inputs moved while it was
being derived (an ABA swap that restores the original bytes still leaves the transient bytes in the snapshot, which then differ from the restored disk state)."""
from __future__ import annotations

import copy
import errno
import hashlib
import threading
import os
import stat
from pathlib import Path

from .canon import loads_strict

DEPENDENCIES = ("export.json", "observations.json", "source.json", "manifest.json", "risk/zones.json", "media_deleted.json")
MAX_FILE_BYTES = 256 * 1024 * 1024        # per dependency; a larger file is refused as unsafe, never read
MISSING, UNSAFE = None, "unsafe"          # distinct, hashable marks in the key


class Snapshot:
    __slots__ = ("name", "raw", "_docs")

    def __init__(self, name: str, raw: dict):
        self.name, self.raw, self._docs = name, raw, {}

    @property
    def key(self) -> tuple:
        return (self.name, *((hashlib.sha256(v).hexdigest() if isinstance(v, bytes) else v) for v in (self.raw[n] for n in DEPENDENCIES)))

    def present(self, n: str) -> bool:
        return isinstance(self.raw[n], bytes)

    def doc(self, n: str):
        """Strict parse of the snapshot bytes (parsed once per snapshot). A missing or unsafe dependency raises like an unreadable file."""
        if n not in self._docs:
            v = self.raw[n]
            if not isinstance(v, bytes):
                raise OSError(errno.ENOENT, f"{n}: {v or 'missing'}")
            self._docs[n] = loads_strict(v)
        return self._docs[n]


def _read_leaf(dirfd: int, leaf: str):
    try:
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dirfd)
    except FileNotFoundError:
        return MISSING
    except OSError:
        return UNSAFE                      # ELOOP (symlink), EACCES, ...
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE_BYTES:
            return UNSAFE
        chunks, total = [], 0
        while True:
            c = os.read(fd, 1 << 20)
            if not c:
                break
            total += len(c)
            if total > MAX_FILE_BYTES:
                return UNSAFE              # grew past the bound while it was read
            chunks.append(c)
        return b"".join(chunks)
    finally:
        os.close(fd)


def take(d: Path) -> Snapshot:
    """Snapshot of the dependencies of the source directory `d` (a plain name under its parent; a symlinked source directory raises)."""
    base = os.open(d.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            top = os.open(d.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base)
        except OSError as e:
            raise OSError(e.errno, f"{d.name}: not a plain directory")
    finally:
        os.close(base)
    try:
        raw: dict = {}
        sub = None
        for n in DEPENDENCIES:
            if "/" not in n:
                raw[n] = _read_leaf(top, n)
                continue
            if sub is None:
                try:
                    sub = os.open("risk", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=top)
                except FileNotFoundError:
                    sub = -1
                except OSError:
                    sub = -2
            raw[n] = MISSING if sub == -1 else UNSAFE if sub == -2 else _read_leaf(sub, n.split("/", 1)[1])
        if sub is not None and sub >= 0:
            os.close(sub)
        return Snapshot(d.name, raw)
    finally:
        os.close(top)


def matches_disk(s: Snapshot, d: Path) -> bool:
    """True when the files now on disk are byte-identical to the snapshot."""
    try:
        return take(d).key == s.key
    except OSError:
        return False


class EventCache:
    """MG-EVENT-032: results keyed by the snapshot identity. Callers receive and store COPIES, so no consumer can alter a cached result and a cached result never aliases a live object; a lock makes
    get/put atomic across request threads. One entry per source: a changed identity replaces the old entry."""

    def __init__(self):
        self._d: dict = {}
        self._lock = threading.Lock()

    def get(self, name: str, key: tuple):
        with self._lock:
            hit = self._d.get(name)
            return copy.deepcopy(hit[1]) if hit and hit[0] == key else None

    def put(self, name: str, key: tuple, value) -> None:
        with self._lock:
            self._d[name] = (key, copy.deepcopy(value))
