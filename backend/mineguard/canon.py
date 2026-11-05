"""Canonical JSON for hashed artifacts (MG-DATA-001 CJ-1 subset: ints, strings, bools, null, arrays, objects)."""
import hashlib
import json
import unicodedata


class DuplicateKeyError(ValueError):
    """CJ-1: an object with a repeated key is ambiguous (parsers disagree on which value wins) and is rejected."""


class NotNfcError(ValueError):
    """CJ-1: every string, object member names included, must already be Unicode NFC. Rejected, never silently normalized."""


def _no_duplicates(pairs):
    seen, out = set(), {}
    for k, v in pairs:
        if unicodedata.normalize("NFC", k) != k:
            raise NotNfcError(f"object key not NFC: {k!r}")
        if k in seen:
            raise DuplicateKeyError(f"duplicate object key {k!r}")
        seen.add(k)
        out[k] = v
    return out


def _no_constants(name):
    raise ValueError(f"non-finite number {name} is not JSON")


def loads_strict(data: str | bytes):
    """json.loads that rejects duplicate keys at every nesting level and NaN/Infinity. Used for every stored artifact and import body."""
    return json.loads(data, object_pairs_hook=_no_duplicates, parse_constant=_no_constants)


def read_json_strict(path):
    from pathlib import Path

    return loads_strict(Path(path).read_bytes())


def _check(value, path="$"):
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int):
        if abs(value) > 2**53 - 1:
            raise ValueError(f"CJ-1: integer out of range at {path}")
        return
    if isinstance(value, float):
        raise ValueError(f"CJ-1: float not allowed at {path}")
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise NotNfcError(f"CJ-1: string not NFC at {path}")
        return
    if isinstance(value, list):
        for i, v in enumerate(value):
            _check(v, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"CJ-1: non-string key at {path}")
            if unicodedata.normalize("NFC", k) != k:
                raise NotNfcError(f"CJ-1: object key not NFC at {path}: {k!r}")
            _check(v, f"{path}.{k}")
        return
    raise ValueError(f"CJ-1: unsupported type {type(value).__name__} at {path}")


def canonical_bytes(value) -> bytes:
    _check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(value) -> str:
    return sha256_hex(canonical_bytes(value))


def digest16(hex_digest: str) -> str:
    return hex_digest[:16]


def file_sha256(path, chunk=1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()
