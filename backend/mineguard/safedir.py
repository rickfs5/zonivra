"""Owned-directory file operations without path races (cycle-21 MG-PATH-021).

The application keeps small metadata files in directories it owns (`runs/feeds`, `runs/alerts`, a stored session's directory). A path-based check followed by a path-based write can be
defeated by replacing the directory with a symlink in between. Here the directory is opened ONCE with O_NOFOLLOW (a symlink is refused, never followed), verified to be a real
directory owned by the current user, and every later operation is relative to that open descriptor (`dir_fd`), so swapping the path afterwards changes nothing: the operation still
lands in the directory that was verified. Leaf names are validated by the callers; leaves are opened with O_NOFOLLOW and must be regular files."""
from __future__ import annotations

import os
import secrets
import stat
from contextlib import contextmanager
from typing import Iterator


class UnsafeDir(Exception):
    """The directory is a symlink, not a directory, not owned by this user, or otherwise not what the application created."""


def open_owned_dir(parent: str | os.PathLike, name: str, create: bool = True) -> int:
    """Descriptor of `parent/name`. `parent` is the configured storage root (trusted); `name` must be a plain name and is never followed if it is a symlink."""
    if not name or "/" in name or name in (".", ".."):
        raise UnsafeDir("bad directory name")
    base = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=base)
            except FileExistsError:
                pass
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base)
        except FileNotFoundError:
            raise
        except OSError as e:   # ELOOP (symlink), ENOTDIR
            raise UnsafeDir(f"not a plain directory: {e.errno}")
    finally:
        os.close(base)
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        os.close(fd)
        raise UnsafeDir("not a directory owned by this user")
    return fd


@contextmanager
def owned_dir(parent, name: str, create: bool = True) -> Iterator[int]:
    fd = open_owned_dir(parent, name, create)
    try:
        yield fd
    finally:
        os.close(fd)


def _check_leaf(name: str):
    if not name or "/" in name or name in (".", "..") or "\x00" in name:
        raise UnsafeDir("bad file name")


def write_atomic(fd: int, name: str, data: str) -> None:
    """Temp file + fsync + rename, all inside the pinned directory. The temporary name is UNPREDICTABLE and created with O_CREAT|O_EXCL|O_NOFOLLOW, so an attacker cannot pre-create it (as a
    symlink OR as a hard link to someone else's file) to have that file truncated and overwritten (cycle-22 MG-PATH-022); the final rename replaces the directory entry, never the inode behind it."""
    _check_leaf(name)
    tmp = f".{name}.{secrets.token_hex(8)}.part"
    f = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(f, "w") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.rename(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=fd)
        except OSError:
            pass
        raise


def read_regular(fd: int, name: str, limit: int = 4_000_000) -> str:
    _check_leaf(name)
    f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        if not stat.S_ISREG(os.fstat(f).st_mode):
            raise UnsafeDir("not a regular file")
        data = os.read(f, limit + 1)
        if len(data) > limit:
            raise UnsafeDir("file too large")
        return data.decode("utf-8")
    finally:
        os.close(f)


def read_regular_bytes(fd: int, name: str, limit: int = 4_000_000) -> bytes:
    """The exact bytes of a regular file of the pinned directory (no decoding), bounded; a symlink or an oversized file is refused."""
    _check_leaf(name)
    f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        if not stat.S_ISREG(os.fstat(f).st_mode):
            raise UnsafeDir("not a regular file")
        chunks, total = [], 0
        while True:
            c = os.read(f, 1 << 16)
            if not c:
                break
            total += len(c)
            if total > limit:
                raise UnsafeDir("file too large")
            chunks.append(c)
        return b"".join(chunks)
    finally:
        os.close(f)


def write_new_bytes(fd: int, name: str, data: bytes) -> bool:
    """Creates `name` with exactly `data`, NEVER replacing an existing entry: the bytes are written and fsynced under an unpredictable temporary name (O_EXCL|O_NOFOLLOW) and published with a hard
    link, which fails if `name` exists, so a partial file never carries the final name. True if created, False if the name already existed."""
    _check_leaf(name)
    tmp = f".{name}.{secrets.token_hex(8)}.part"
    f = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(f, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            return True
        except FileExistsError:
            return False
    finally:
        try:
            os.unlink(tmp, dir_fd=fd)
        except OSError:
            pass


def list_names(fd: int) -> list[str]:
    return os.listdir(fd)


def lstat_leaf(fd: int, name: str) -> os.stat_result:
    _check_leaf(name)
    return os.stat(name, dir_fd=fd, follow_symlinks=False)


def unlink_regular(fd: int, name: str) -> None:
    """Removes a regular file of the pinned directory; refuses anything else (symlink, directory, device)."""
    st = lstat_leaf(fd, name)
    if not stat.S_ISREG(st.st_mode):
        raise UnsafeDir("not a regular file")
    os.unlink(name, dir_fd=fd)
