"""yai-tes: the TES 1.1 API (design/tes.md). The worker is a separate process (yai tes worker)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request

from yai.aai.service_auth import AtPrincipalResolver, JwtAuthenticator, bearer_token
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
from yai.common.fetch import Fetcher, FetchError, redact
from yai.common.ids import new_id
from yai.common.paging import PageState, PageTokens
from yai.common.resolver import DrsResolver
from yai.common.roles import Principal
from yai.common.serviceinfo import service_info
from yai.common.timeutil import Clock
from yai.tes.db import TERMINAL, TaskLimit, TaskRow, TesDb
from yai.tes.drs import DrsInputs
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
    # D-069: drs:// inputs resolved here, for the user the caller's token names (off by default)
    drs_cfg = config.get("drs") or {}
    drs_inputs: DrsInputs | None = None
    if drs_cfg.get("enabled"):
        secret_file = Path(str(drs_cfg.get("client_secret_file")))
        drs_inputs = DrsInputs(
            token_url=str(drs_cfg.get("token_url", "https://aai.yai.test/token")),
            client_id=str(drs_cfg.get("client_id", "yai-tes")),
            client_secret=secret_file.read_text().strip(),
            audience=str(drs_cfg.get("audience", "yai-drs")),
            resolver=DrsResolver(
                passport_hosts=list(drs_cfg.get("passport_hosts", ["drs.yai.test"])),
                host_override=dict(drs_cfg.get("host_override", {})),
                compact=dict(drs_cfg.get("compact", {})),
                fetcher=fetcher,
            ),
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
        # with drs.enabled the caller's token may be a PSAT addressed to this TES (D-069)
        authenticator = JwtAuthenticator(auth, clock, passport_scoped=drs_inputs is not None)
        resolver = AtPrincipalResolver()
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

    def acting_service(principal: Principal) -> str | None:
        """`<iss>|<sub>` when the caller is a service (its own token): it sees the tasks it
        submitted for users (D-067). A user, even one a service acts for, is not a service."""
        return principal.self_ref if principal.kind == "service" else None

    def visible(row: TaskRow | None, principal: Principal) -> TaskRow:
        """Visible to the owner with their own token (every task of theirs), to the same
        (owner, actor) pair with an exchanged token (only what that service submitted for them),
        and to the service that submitted it with its own token. Others get 404 (D-013, D-067)."""
        if row is None:
            raise ApiError(404, "task not found")
        if everyone_sees_all:
            return row
        if row.owner == principal.owner and principal.actor_ref in (None, row.actor):
            return row
        if row.actor is not None and row.actor == acting_service(principal):
            return row
        raise ApiError(404, "task not found")

    def may_cancel(row: TaskRow, principal: Principal) -> bool:
        """Only the submitter: the same owner + actor pair, or the acting service itself (also
        after a restart, with its own token). The owner alone cannot cancel what a service
        submitted for them (that is the service's job, e.g. WES CancelRun)."""
        if row.owner == principal.owner and row.actor == principal.actor_ref:
            return True
        return row.actor is not None and row.actor == acting_service(principal)

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

    def caller_token(request: Request) -> str | None:
        """The raw Bearer, needed only to exchange it for the caller's Passport (D-069). Not
        stored anywhere."""
        return bearer_token(request) if drs_inputs is not None else None

    def resolve_drs_inputs(task: dict[str, Any], token: str | None) -> list[str]:
        """Replace drs:// inputs (marked by validate_task) with signed URLs. Returns the notes
        for the system log (the drs:// URIs, never the URLs' query)."""
        pending = [(i, inp.pop("_drs")) for i, inp in enumerate(task.get("inputs", []))
                   if "_drs" in inp]  # fmt: skip
        if not pending:
            return []
        if drs_inputs is None or token is None:  # validate_task only marks when enabled
            raise ApiError(400, "drs:// inputs are not enabled on this TES")
        notes = []
        for i, url in drs_inputs.resolve(pending, token).items():
            try:
                fetcher.check(url)  # the signed URL must be one the runner may fetch (NEG-SSRF)
            except FetchError as exc:
                raise ApiError(400, f"inputs[{i}].url: resolved to {exc.reason}") from exc
            task["inputs"][i]["url"] = url
            notes.append(f"inputs[{i}]: {redact(dict(pending)[i])} resolved at the DRS")
        return notes

    # FastAPI resolves dependencies in argument order: auth -> count while reading (64 MiB) -> JSON.
    # Without auth, 401 before reading the body. JSON parsing stays on the loop (moving it to a
    # thread would not help while holding the GIL; the remaining limit is D-056)
    submitter = Depends(submit_principal)
    token_of = Depends(caller_token)
    task_body = Depends(json_body(MAX_BODY))

    @router.post("/tasks")
    def create_task(
        principal: Principal = submitter, token: str | None = token_of, body: Any = task_body
    ) -> dict[str, str]:
        # A resend with the owner's earlier yai.submit_nonce (D-071) gets the earlier id before
        # anything else: no validation (its outputs may exist by now), no drs:// resolution
        sent_tags = body.get("tags") if isinstance(body, dict) else None
        nonce = sent_tags.get("yai.submit_nonce") if isinstance(sent_tags, dict) else None
        if isinstance(nonce, str) and nonce:
            earlier = db.task_by_nonce(principal.owner, nonce)
            if earlier is not None:
                return {"id": earlier}
        task, warnings = validate_task(
            body, principal, files_root=files_root, fetcher=fetcher, limits=limits,
            output_owner=db.output_owner, drs_enabled=drs_inputs is not None,
        )  # fmt: skip
        tags = task["tags"]
        notes = resolve_drs_inputs(task, token)
        try:
            task_id = db.create(
                new_id("t"), principal.owner, task, clock.now(),
                actor=principal.actor_ref, run_id=tags.get("yai.run_id"),
                nonce=tags.get("yai.submit_nonce"), max_active=max_active,
            )  # fmt: skip
        except TaskLimit:
            # 429, not 400: waiting helps, not fixing (same as WES in D-044)
            raise ApiError(429, f"too many unfinished tasks (at most {max_active})") from None
        for w in warnings:
            db.add_system_log(task_id, 0, w)
            log.warning("task %s: %s", task_id, w)
        for note in notes:
            db.add_system_log(task_id, 0, note)
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
            as_actor=acting_service(principal), actor=principal.actor_ref,
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
        if not everyone_sees_all and not may_cancel(row, principal):
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
