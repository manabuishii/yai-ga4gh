"""yai-drs: DRS 1.5.0 (design/drs.md). Read-only (DRS-RO-01). Register with yai drs register."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from yai.common import startup
from yai.common.app import base_app
from yai.common.body import json_body
from yai.common.errors import ApiError
from yai.common.serviceinfo import service_info
from yai.common.timeutil import Clock, parse_rfc3339, rfc3339
from yai.drs.access import Credentials, DrsAccess
from yai.drs.store import DrsRecord, DrsStore

BASE = "/ga4gh/drs/v1"
ACCESS_ID = "a1"  # D-016: unique within the object (only one method)
NO_STORE = {"Cache-Control": "no-cache, no-store", "Pragma": "no-cache"}  # G-AAI-04
CHUNK = 64 * 1024
MAX_BODY = 1024 * 1024  # body limit
MAX_PASSPORTS = 10
MAX_PASSPORT_LEN = 64 * 1024


def create_app(config: dict[str, Any]) -> FastAPI:
    startup.check("drs", config)  # reject before building the gate (D-055)
    app = base_app("yai-drs")
    app.state.config = config
    root = Path(config.get("data_dir", "var/drs"))
    store = DrsStore(root)
    clock = Clock()
    access = DrsAccess(config, clock, root)
    hostname = config.get("hostname", "drs.yai.test")
    max_bulk = int(config.get("max_bulk_request_length", 100))
    test_endpoints = bool(config.get("test_endpoints", False))
    router = APIRouter(prefix=BASE)
    app.state.store, app.state.clock = store, clock

    def lookup(object_id: str) -> DrsRecord:
        record = store.get(object_id)
        if record is None:
            raise ApiError(404, "object not found")  # absent objects are 404 regardless of policy
        return record

    def access_methods(record: DrsRecord) -> list[dict[str, Any]]:
        """Access method shape comes from access_policy (table in drs.md section 2)."""
        if access.mode != "aai" or record.access_policy == "public":
            return [{"type": "https", "access_url": {"url": access.blob_url(record.id)}}]
        return [
            {
                "type": "https",
                "access_id": ACCESS_ID,
                "authorizations": access.authorizations(record, record.access_policy),
            }
        ]

    def drs_object(record: DrsRecord) -> dict[str, Any]:
        obj: dict[str, Any] = {
            "id": record.id,
            "name": record.name,
            "self_uri": f"drs://{hostname}/{record.id}",  # DRS-OBJ-03. No port
            "size": record.size,
            "created_time": rfc3339(record.created_time),
            "checksums": [{"type": t, "checksum": c} for t, c in record.checksums],
            "access_methods": access_methods(record),
        }
        if record.mime_type:
            obj["mime_type"] = record.mime_type
        if record.description:
            obj["description"] = record.description
        return obj

    def check_body(body: Any) -> dict[str, Any]:
        """Body shape. Size was limited while reading (json_body, D-042)."""
        if not isinstance(body, dict):
            raise ApiError(400, "body must be a JSON object")
        if "tokens" in body:
            raise ApiError(400, "send passports in 'passports', not 'tokens'")  # G-DRS-03
        passports = body.get("passports")
        if passports is not None:
            if not isinstance(passports, list) or not all(isinstance(p, str) for p in passports):
                raise ApiError(400, "passports must be an array of strings")
            if len(passports) > MAX_PASSPORTS or any(len(p) > MAX_PASSPORT_LEN for p in passports):
                raise ApiError(400, f"at most {MAX_PASSPORTS} passports of 64 KiB each")
        return body

    def resolve_access(record: DrsRecord, access_id: str, creds: Credentials) -> dict[str, Any]:
        if access.mode != "aai" or record.access_policy == "public" or access_id != ACCESS_ID:
            raise ApiError(404, "access id not found")  # DRS-ACC-02
        access.authorize(record.access_policy, record, creds, access=True)
        return {"url": access.signed_url(record.id, access_id)}  # no headers (C-03)

    # --- bulk (DRS-BULK-01/02, D-021: 200 even if none resolves) ---
    # register /objects/access before /objects/{object_id} (routes match in registration order)
    def ids_from(body: dict[str, Any], key: str) -> list[Any]:
        ids = body.get(key)
        if not isinstance(ids, list) or not ids:
            raise ApiError(400, f"{key} must be a non-empty array")
        if len(ids) > max_bulk:
            raise ApiError(413, f"at most {max_bulk} items")
        return ids

    def bulk_response(results: list[Any], failures: dict[int, list[str]], key: str):
        unresolved = [{"error_code": c, "object_ids": ids} for c, ids in sorted(failures.items())]
        n_bad = sum(len(v) for v in failures.values())
        return {
            "summary": {
                "requested": len(results) + n_bad,
                "resolved": len(results),
                "unresolved": n_bad,
            },
            "unresolved_drs_objects": unresolved,
            key: results,
        }

    # Handlers are def (sync). FastAPI runs them in threads, so key fetch, SQLite and Passport
    # checks do not block other requests. The body is read up to the limit, then
    # parsed as JSON (D-042).
    # When too large: 413 for bulk operations, 400 for single ones (413 is not in their responses)
    bulk_body = Depends(json_body(MAX_BODY, 413))
    one_body = Depends(json_body(MAX_BODY, 400))

    @router.options("/objects")
    def options_bulk(request: Request, body: Any = bulk_body) -> dict[str, Any]:
        body = check_body(body)
        found, failures = [], {}
        for oid in ids_from(body, "bulk_object_ids"):
            record = store.get(oid) if isinstance(oid, str) else None
            if record is None:
                failures.setdefault(404, []).append(str(oid))
            else:
                found.append(access.authorizations(record, record.metadata_policy))
        return bulk_response(found, failures, "resolved_drs_object")

    @router.post("/objects")
    def get_bulk_objects(request: Request, body: Any = bulk_body) -> dict[str, Any]:
        body = check_body(body)
        ids = ids_from(body, "bulk_object_ids")
        creds = access.credentials(request, body.get("passports"))
        found, failures = [], {}
        for oid in ids:
            record = store.get(oid) if isinstance(oid, str) else None
            try:
                if record is None:
                    raise ApiError(404, "not found")
                access.authorize(record.metadata_policy, record, creds, access=False)
                found.append(drs_object(record))
            except ApiError as exc:
                failures.setdefault(exc.status_code, []).append(str(oid))
        return bulk_response(found, failures, "resolved_drs_object")

    @router.post("/objects/access")
    def get_bulk_access(request: Request, body: Any = bulk_body) -> JSONResponse:
        body = check_body(body)
        items = ids_from(body, "bulk_object_access_ids")
        pairs = []
        for item in items:
            oid = item.get("bulk_object_id") if isinstance(item, dict) else None
            aids = item.get("bulk_access_ids") if isinstance(item, dict) else None
            if not isinstance(oid, str) or not isinstance(aids, list) or not aids:
                raise ApiError(400, "each item needs bulk_object_id and bulk_access_ids")
            pairs.append((oid, aids))
        if sum(len(aids) for _, aids in pairs) > max_bulk:  # inner counts are limited too
            raise ApiError(413, f"at most {max_bulk} access ids in total")
        creds = access.credentials(request, body.get("passports"))
        found, failures = [], {}
        for oid, aids in pairs:
            record = store.get(oid)
            for aid in aids:
                try:
                    if record is None:
                        raise ApiError(404, "not found")
                    url = resolve_access(record, str(aid), creds)["url"]
                    found.append({"drs_object_id": oid, "drs_access_id": aid, "url": url})
                except ApiError as exc:
                    failures.setdefault(exc.status_code, []).append(oid)
        body = bulk_response(found, failures, "resolved_drs_object_access_urls")
        return JSONResponse(body, headers=NO_STORE)

    @router.get("/service-info")
    def get_service_info() -> dict[str, Any]:
        count, total = store.counts()
        return service_info(
            service_id=config.get("service_id", "test.yai.drs"),
            name="yai-drs",
            artifact="drs",
            spec_version="1.5.0",
            maxBulkRequestLength=max_bulk,
            drs={"maxBulkRequestLength": max_bulk, "objectCount": count, "totalObjectSize": total},
        )

    @router.options("/objects/{object_id}")
    def options_object(object_id: str) -> dict[str, Any]:
        record = lookup(object_id)
        return access.authorizations(record, record.metadata_policy)

    @router.get("/objects/{object_id}")
    def get_object(object_id: str, request: Request) -> dict[str, Any]:
        creds = access.credentials(request)
        record = lookup(object_id)
        access.authorize(record.metadata_policy, record, creds, access=False)
        return drs_object(record)

    @router.post("/objects/{object_id}")
    def post_object(object_id: str, request: Request, body: Any = one_body) -> dict[str, Any]:
        body = check_body(body)
        creds = access.credentials(request, body.get("passports"))
        record = lookup(object_id)
        access.authorize(record.metadata_policy, record, creds, access=False)
        return drs_object(record)

    @router.get("/objects/{object_id}/access/{access_id}")
    def get_access(object_id: str, access_id: str, request: Request) -> JSONResponse:
        creds = access.credentials(request)
        record = lookup(object_id)
        return JSONResponse(resolve_access(record, access_id, creds), headers=NO_STORE)

    @router.post("/objects/{object_id}/access/{access_id}")
    def post_access(
        object_id: str, access_id: str, request: Request, body: Any = one_body
    ) -> JSONResponse:
        body = check_body(body)
        creds = access.credentials(request, body.get("passports"))
        record = lookup(object_id)
        return JSONResponse(resolve_access(record, access_id, creds), headers=NO_STORE)

    app.include_router(router)

    # --- byte delivery endpoint (yai-specific, drs.md section 2) ---
    @app.get("/yai/blobs/{object_id}")
    def deliver(object_id: str, request: Request) -> Response:
        record = lookup(object_id)
        q = request.query_params
        exp: int | None = None
        if access.mode == "aai" and record.access_policy != "public":
            if not {"exp", "aid", "sig"} <= set(q):
                raise ApiError(403, "signature required")  # no back door
            exp = access.check_signature(object_id, q["exp"], q["aid"], q["sig"])
        start, end = parse_range(request.headers.get("range"), record.size)
        path = store.blob_path(record.sha256)

        def stream():
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    if exp is not None and clock.now() >= exp:
                        return  # cut off at URL expiry even mid-stream (aai.md section 4)
                    chunk = f.read(min(CHUNK, left))
                    if not chunk:
                        return
                    left -= len(chunk)
                    yield chunk

        headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1)}
        status = 200
        if request.headers.get("range"):
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{record.size}"
        media = record.mime_type or "application/octet-stream"
        return StreamingResponse(stream(), status, headers=headers, media_type=media)

    if test_endpoints:
        # test clock. Used only by local tests (false when deployed, D-054 (4))
        @app.post("/yai/test/clock")
        async def test_clock(body: Any = one_body) -> dict[str, int]:
            if not isinstance(body, dict):
                raise ApiError(400, "body must be a JSON object")
            clock.offset = int(body.get("offset", 0))
            return {"now": clock.now()}

    return app


_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header: str | None, size: int) -> tuple[int, int]:
    """A single range only (bytes=a-b, a-, -n)."""
    if not header:
        return 0, max(size - 1, 0) if size else -1
    m = _RANGE.match(header.strip())
    if not m or (not m.group(1) and not m.group(2)):
        raise ApiError(416, "unsupported range")
    if m.group(1):
        start = int(m.group(1))
        end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    else:
        start, end = max(size - int(m.group(2)), 0), size - 1
    if start > end or start >= size:
        raise ApiError(416, "range not satisfiable")
    return start, end


def register_main(args: Any, config: dict[str, Any]) -> None:
    """yai drs register: registration is a yai-specific command (DRS-RO-01)."""
    store = DrsStore(Path(config.get("data_dir", "var/drs")))
    oid = store.register(
        Path(args.file),
        metadata_policy=args.metadata_policy or args.policy,
        access_policy=args.access_policy or args.policy,
        dataset=args.dataset,
        now=int(time.time()),
        name=args.name,
        mime_type=args.mime_type,
        created_time=parse_rfc3339(args.created_time) if args.created_time else None,
        description=args.description,
    )
    print(oid)


def set_dataset_main(args: Any, config: dict[str, Any]) -> None:
    """yai drs set-dataset: takes effect on the next /access (DRS reads the DB every time)."""
    DrsStore(Path(config.get("data_dir", "var/drs"))).set_dataset(args.object_id, args.dataset)
