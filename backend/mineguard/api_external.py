"""MG-EVIDENCE-10 routes: explicit, owner-driven import and read-only review of an EXTERNAL SAM 3.1 result (see `meta_import.py` for the contract).
Storage: `<runs>/external_results/<ext_id>/{result.bin, manifest.json, image.bin?, review.json}` -- the source bytes exactly as received. Nothing here touches `runs/run_*`, alerts, feeds, the
risk engine or any provider configuration; the module imports no transport. Every read re-derives the review from the stored bytes, so a modified stored copy is reported, never trusted."""
from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import StringConstraints

from . import meta_import as mi
from . import safedir
from .api_mvp2 import Strict, body_model
from .canon import loads_strict

STORE = "external_results"
ID_RE = re.compile(r"ext_[0-9a-f]{16}")
DEMO_DIR = Path(__file__).resolve().parent / "demo_external"
DEMO_STEM = "SYNTHETIC_FIXTURE_sam31"
MAX_BODY = 12 * 1024 * 1024
B64 = Annotated[str, StringConstraints(max_length=7_600_000)]


class ImportReq(Strict):
    manifest_b64: Annotated[str, StringConstraints(max_length=30_000)]
    result_b64: B64
    image_b64: B64 | None = None


def _b64(s: str, code: str) -> bytes:
    try:
        return base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        raise mi.ImportRefused(code)


# `integrity` answers ONE question: are the stored source bytes still the bytes that passed validation at import (re-derived from them on every read)? It is NOT provenance authenticity
# (see `verification.state`) and it is only ever reported for a payload that passed the strict grammar; a refused payload is never stored and has no integrity value at all.
INTEGRITY_SCOPE = "STORED_BYTES_REVALIDATED_NOT_AUTHENTICITY"


def demo_files() -> tuple[bytes, bytes, bytes]:
    return ((DEMO_DIR / f"{DEMO_STEM}.sse").read_bytes(), (DEMO_DIR / f"{DEMO_STEM}.manifest.json").read_bytes(), (DEMO_DIR / f"{DEMO_STEM}.png").read_bytes())


def synthetic_digests() -> frozenset:
    """sha256 of every bundled synthetic stream: those bytes can never be imported as a declared provider result."""
    out = set()
    for p in DEMO_DIR.glob("*.sse"):
        out.add(mi.sha256(p.read_bytes()))
    return frozenset(out)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def import_bytes(root: Path, result: bytes, manifest: bytes, image: bytes | None) -> tuple[dict, bool]:
    """Validate, then store the source bytes unchanged. -> (review, newly_stored). Raises ImportRefused: nothing is written in that case."""
    review = mi.build_review(result, manifest, image, synthetic_digests=synthetic_digests(), imported_utc=_now())
    iid = mi.import_id(mi.sha256(result), mi.sha256(manifest), review["verification"]["input_image_sha256"] if image is not None else None)
    root.mkdir(mode=0o700, exist_ok=True)
    with safedir.owned_dir(root, iid) as fd:
        existing = _read_dir(fd, iid)
        if existing is not None:
            return existing, False
        safedir.write_new_bytes(fd, "result.bin", result)
        safedir.write_new_bytes(fd, "manifest.json", manifest)
        if image is not None:
            safedir.write_new_bytes(fd, "image.bin", image)
        review = {**review, "import_id": iid}
        safedir.write_new_bytes(fd, "review.json", json.dumps(review, sort_keys=True, indent=1).encode())     # last: its presence marks a complete import
    return {**review, "integrity": "VERIFIED", "integrity_scope": INTEGRITY_SCOPE, "has_image": image is not None}, True


def _read_dir(fd: int, iid: str) -> dict | None:
    """The review of a stored import, RE-DERIVED from the stored source bytes; None if the directory holds no completed import."""
    try:
        stored = loads_strict(safedir.read_regular_bytes(fd, "review.json", 1_000_000))
    except FileNotFoundError:
        return None
    except (safedir.UnsafeDir, ValueError, OSError):
        return {"schema": mi.SCHEMA, "import_id": iid, "origin": mi.ORIGIN, "integrity": "STORED_COPY_MODIFIED", "integrity_scope": INTEGRITY_SCOPE}
    try:
        result = safedir.read_regular_bytes(fd, "result.bin", mi.MAX_RESULT_BYTES)
        manifest = safedir.read_regular_bytes(fd, "manifest.json", mi.MAX_MANIFEST_BYTES)
        try:
            image = safedir.read_regular_bytes(fd, "image.bin", mi.MAX_IMAGE_BYTES)
        except FileNotFoundError:
            image = None
        again = mi.build_review(result, manifest, image, synthetic_digests=synthetic_digests(), imported_utc=stored.get("imported_utc"))
        ok = again["digest"] == stored.get("digest") and mi.import_id(mi.sha256(result), mi.sha256(manifest), again["verification"]["input_image_sha256"] if image is not None else None) == iid
    except (safedir.UnsafeDir, mi.ImportRefused, FileNotFoundError, OSError):
        ok = False
    if not ok:
        return {"schema": mi.SCHEMA, "import_id": iid, "origin": mi.ORIGIN, "integrity": "STORED_COPY_MODIFIED", "integrity_scope": INTEGRITY_SCOPE}
    return {**again, "import_id": iid, "integrity": "VERIFIED", "integrity_scope": INTEGRITY_SCOPE, "has_image": image is not None}


def register_external(app, runs_dir: Path):
    root = Path(runs_dir) / STORE

    def refuse(e: mi.ImportRefused):
        return JSONResponse(status_code=422, content={"msg_id": f"mg.ext.refusal.{e.code}", "code": e.code})

    def ok_id(iid: str):
        if not ID_RE.fullmatch(iid):
            raise HTTPException(404, {"msg_id": "mg.refusal.unknown_external_result"})

    def get_review(iid: str) -> dict:
        ok_id(iid)
        if not root.is_dir():
            raise HTTPException(404, {"msg_id": "mg.refusal.unknown_external_result"})
        try:
            with safedir.owned_dir(root, iid, create=False) as fd:
                rec = _read_dir(fd, iid)
        except (FileNotFoundError, safedir.UnsafeDir):
            rec = None
        if rec is None:
            raise HTTPException(404, {"msg_id": "mg.refusal.unknown_external_result"})
        return rec

    @app.post("/api/external-results", status_code=201)
    async def external_import(request: Request):
        req = await body_model(request, ImportReq, MAX_BODY)
        try:
            review, new = import_bytes(root, _b64(req.result_b64, "result_empty"), _b64(req.manifest_b64, "manifest_not_json"),
                                       None if req.image_b64 is None else _b64(req.image_b64, "image_unsupported"))
        except mi.ImportRefused as e:
            return refuse(e)
        return JSONResponse(status_code=201 if new else 200, content={**review, "newly_imported": new})

    @app.post("/api/external-results/demo", status_code=201)
    def external_demo():
        """Imports the bundled SYNTHETIC_FIXTURE (fixed server-side files: no path or content comes from the caller). Works with the provider offline."""
        try:
            review, new = import_bytes(root, *demo_files())
        except mi.ImportRefused as e:
            return refuse(e)
        return JSONResponse(status_code=201 if new else 200, content={**review, "newly_imported": new})

    @app.get("/api/external-results")
    def external_list():
        rows = []
        if root.is_dir():
            for name in sorted(p.name for p in root.iterdir() if ID_RE.fullmatch(p.name)):
                try:
                    r = get_review(name)
                except HTTPException:
                    continue
                v = r.get("verification") or {}
                rows.append({"import_id": name, "integrity": r["integrity"], "provenance_class": r.get("provenance_class"), "verification_state": v.get("state"),
                             "objects": len(r.get("objects", [])), "imported_utc": r.get("imported_utc"), "origin": mi.ORIGIN})
        return {"schema": "mg.meta.import.list/1", "origin": mi.ORIGIN, "results": rows}

    @app.get("/api/external-results/{iid}")
    def external_get(iid: str):
        return get_review(iid)

    @app.get("/api/external-results/{iid}/image")
    def external_image(iid: str):
        rec = get_review(iid)
        if rec["integrity"] != "VERIFIED" or not rec.get("has_image"):
            raise HTTPException(404, {"msg_id": "mg.refusal.unknown_external_result"})
        with safedir.owned_dir(root, iid, create=False) as fd:
            data = safedir.read_regular_bytes(fd, "image.bin", mi.MAX_IMAGE_BYTES)
        fmt = (mi.image_facts(data) or {"format": "png"})["format"]
        return Response(data, media_type="image/png" if fmt == "png" else "image/jpeg", headers={"cache-control": "no-store", "content-disposition": "inline"})
