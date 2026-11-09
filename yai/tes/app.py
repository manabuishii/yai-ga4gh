"""yai-tes: the TES 1.1 API (design/tes.md). The worker is a separate process (yai tes worker)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request

from yai.aai.service_auth import AtPrincipalResolver, JwtAuthenticator
from yai.common import startup
from yai.common.app import base_app
from yai.common.body import json_body
from yai.common.config import auth_mode, load_secret
from yai.common.errors import ApiError
from yai.common.fakes import (
    AnonymousAuthenticator,
    FixedPrincipalResolver,
    StaticBearerAuthenticator,
)
from yai.common.fetch import Fetcher
from yai.common.ids import new_id
from yai.common.paging import PageState, PageTokens
from yai.common.roles import Principal
from yai.common.serviceinfo import service_info
from yai.common.timeutil import Clock
from yai.tes.db import TERMINAL, TaskLimit, TaskRow, TesDb
from yai.tes.model import Limits, render, validate_task
from yai.tes.worker import runner_kind

log = logging.getLogger("yai.tes")
BASE = "/ga4gh/tes/v1"
VIEWS = ("MINIMAL", "BASIC", "FULL")
# Body limit (D-042). WES sends attachments (up to 1 MiB each, up to 32) as inputs[].content,
# so 64 MiB allows for JSON escaping of quotes and \ (up to 2x)
MAX_BODY = 64 * 1024 * 1024
RUNNER_TAGS = {
    "local-process": "local-process (image is not used, D-024)",
    "docker": "docker (D-034)",
}
STATES = ("UNKNOWN", "QUEUED", "INITIALIZING", "RUNNING", "PAUSED", "COMPLETE", "EXECUTOR_ERROR",
          "SYSTEM_ERROR", "CANCELED", "CANCELING", "PREEMPTED")  # fmt: skip
PAGE_DEFAULT, PAGE_MAX_EXCLUSIVE = 256, 2048  # TES-LST-01


def create_app(config: dict[str, Any]) -> FastAPI:
    startup.check("tes", config)  # reject before building the gate (D-055)
    app = base_app("yai-tes")
    app.state.config = config
    data_dir = Path(config.get("data_dir", "var/tes"))
    db = TesDb(data_dir / "tes.sqlite")
    files_root = str(Path(config.get("files_root", "var/tes/files")).resolve())
    Path(files_root).mkdir(parents=True, exist_ok=True)
    fetch = config.get("fetch", {})
    fetcher = Fetcher(
        allow=list(fetch.get("allow", ["drs.yai.test:443"])),
        insecure_http_hosts=list(fetch.get("insecure_http_hosts", [])),
    )
    lim = config.get("limits", {})
    limits = Limits(
        max_cpu=int(lim.get("max_cpu", 2)),
        max_ram_gb=float(lim.get("max_ram_gb", 4)),
        max_inputs=int(lim.get("max_inputs", 100)),
        max_outputs=int(lim.get("max_outputs", 100)),
    )
    # Per-user limit of unfinished tasks (N-1, D-053; same shape as WES max_active_runs_per_user).
    # 0 means no limit
    max_active = int(config.get("max_active_tasks_per_user", 16))
    clock = Clock()
    tokens = PageTokens(load_secret(data_dir / "page-secret"))
    auth = config.get("auth") or {}
    mode = auth_mode(config, ("aai", "anonymous", "static-bearer"))
    authenticator: Any
    resolver: Any
    if mode == "aai":
        authenticator, resolver = JwtAuthenticator(auth, clock), AtPrincipalResolver()
    elif mode == "static-bearer":
        authenticator = StaticBearerAuthenticator(auth.get("tokens", {}))
        resolver = FixedPrincipalResolver()
    else:
        authenticator, resolver = AnonymousAuthenticator(), FixedPrincipalResolver()
    everyone_sees_all = mode == "anonymous"
    router = APIRouter(prefix=BASE)

    def principal_of(request: Request, scope: str) -> Principal:
        """Check R1, R2 and scope (tes in permissions.yaml)."""
        creds = authenticator.authenticate(request)
        principal = resolver.resolve(creds)
        if principal is None:
            raise ApiError(401, "authentication required")
        if "*" not in principal.scopes and scope not in principal.scopes:
            raise ApiError(403, "forbidden")
        return principal

    def obo(principal: Principal) -> str:
        return f"{principal.iss}|{principal.sub}"

    def visible(row: TaskRow | None, principal: Principal) -> TaskRow:
        """Visible only to the submitter or the yai.on_behalf_of user. Others get 404 (D-013)."""
        if row is None:
            raise ApiError(404, "task not found")
        if everyone_sees_all or row.owner == principal.owner or row.on_behalf_of == obo(principal):
            return row
        raise ApiError(404, "task not found")

    def view_of(value: str | None) -> str:
        view = value or "MINIMAL"
        if view not in VIEWS:
            raise ApiError(400, "view must be MINIMAL, BASIC or FULL")
        return view

    # Handlers are def (sync). FastAPI runs them in threads, so key fetch and SQLite do not block
    # other requests
    @router.get("/service-info")
    def get_service_info() -> dict[str, Any]:
        return service_info(
            service_id=config.get("service_id", "test.yai.tes"),
            name="yai-tes",
            artifact="tes",
            spec_version="1.1.0",
            storage=[f"file://{files_root}", "https://"],
            tesResources_backend_parameters=[],
            # no tags (D-040): absent from TES 1.1 tesServiceInfo and the service-info 1.0.0
            # Service, and py-tes 1.1.4's strict types reject them (INTEROP-TES-1). The runner kind
            # goes in description
            description="yai tes, a learning implementation. Runner: "
            + RUNNER_TAGS[runner_kind(config)],
        )

    def submit_principal(request: Request) -> Principal:
        """Auth and scope for CreateTask. A dependency resolved before the body (D-056). As a def
        it runs in the threadpool and key fetch does not block the loop. No Body/Form arguments
        (they would make the body be read first)."""
        return principal_of(request, "tes.submit")

    # FastAPI resolves dependencies in argument order: auth -> count while reading (64 MiB) -> JSON.
    # Without auth, 401 before reading the body. JSON parsing stays on the loop (moving it to a
    # thread would not help while holding the GIL; the remaining limit is D-056)
    submitter = Depends(submit_principal)
    task_body = Depends(json_body(MAX_BODY))

    @router.post("/tasks")
    def create_task(principal: Principal = submitter, body: Any = task_body) -> dict[str, str]:
        task, warnings = validate_task(
            body, principal, files_root=files_root, fetcher=fetcher, limits=limits,
            output_owner=db.output_owner,
        )  # fmt: skip
        tags = task["tags"]
        try:
            task_id = db.create(
                new_id("t"), principal.owner, task, clock.now(),
                on_behalf_of=tags.get("yai.on_behalf_of"), run_id=tags.get("yai.run_id"),
                nonce=tags.get("yai.submit_nonce"), max_active=max_active,
            )  # fmt: skip
        except TaskLimit:
            # 429, not 400: waiting helps, not fixing (same as WES in D-044)
            raise ApiError(429, f"too many unfinished tasks (at most {max_active})") from None
        for w in warnings:
            db.add_system_log(task_id, 0, w)
            log.warning("task %s: %s", task_id, w)
        return {"id": task_id}

    @router.get("/tasks")
    def list_tasks(request: Request) -> dict[str, Any]:
        principal = principal_of(request, "tes.read")
        q = request.query_params
        view = view_of(q.get("view"))
        try:
            size = int(q.get("page_size", PAGE_DEFAULT))
        except ValueError as exc:
            raise ApiError(400, "page_size must be an integer") from exc
        if not 0 < size < PAGE_MAX_EXCLUSIVE:
            raise ApiError(400, "page_size must be 1..2047")
        state = q.get("state")
        if state is not None and state not in STATES:
            raise ApiError(400, "unknown state")
        keys, values = q.getlist("tag_key"), q.getlist("tag_value")
        if len(values) > len(keys):
            raise ApiError(400, "tag_value without tag_key")
        tag_filter = {k: (values[i] if i < len(values) else "") for i, k in enumerate(keys)}
        filters = {"name_prefix": q.get("name_prefix"), "state": state, "tags": tag_filter,
                   "view": view}  # fmt: skip
        who = principal.owner
        if q.get("page_token"):
            page = tokens.read(q["page_token"], principal=who, scope="tes.tasks", filters=filters)
        else:
            page = PageState(db.max_seq(), None)
        rows = db.list_visible(
            None if everyone_sees_all else who, snapshot_seq=page.snapshot_seq,
            after_seq=page.after_seq, name_prefix=q.get("name_prefix"), state=state,
        )  # fmt: skip
        rows = [r for r in rows if tags_match(tag_filter, r.task.get("tags") or {})]
        chosen = rows[:size]
        out: dict[str, Any] = {
            "tasks": [render(r, db.logs(r.id) if view != "MINIMAL" else [], view) for r in chosen]
        }
        if len(rows) > size:  # no next_page_token at the end (G-X-04)
            nxt = PageState(page.snapshot_seq, chosen[-1].seq)
            out["next_page_token"] = tokens.make(
                nxt, principal=who, scope="tes.tasks", filters=filters
            )
        return out

    @router.get("/tasks/{task_id}")
    def get_task(task_id: str, request: Request) -> dict[str, Any]:
        principal = principal_of(request, "tes.read")
        view = view_of(request.query_params.get("view"))
        row = visible(db.get(task_id), principal)
        return render(row, db.logs(row.id) if view != "MINIMAL" else [], view)

    @router.post("/tasks/{task_id}:cancel")
    def cancel_task(task_id: str, request: Request) -> dict[str, Any]:
        principal = principal_of(request, "tes.cancel")
        row = visible(db.get(task_id), principal)
        if not everyone_sees_all and row.owner != principal.owner:
            raise ApiError(403, "only the submitter can cancel")  # delegated tasks: submitter only
        for _ in range(5):
            if row.state in TERMINAL or row.state == "CANCELING":
                return {}  # finished ones: 200 and do nothing (D-020)
            if db.transition(row.id, row.state, "CANCELING"):
                return {}
            row = db.get(task_id) or row  # re-read after losing a race
        raise ApiError(500, "could not cancel")

    app.include_router(router)
    return app


def tags_match(filt: dict[str, str], tags: dict[str, str]) -> bool:
    """Table of TES-LST-02: an empty value matches if the key exists, else key and value match."""
    for k, v in filt.items():
        if k not in tags:
            return False
        if v and tags[k] != v:
            return False
    return True
