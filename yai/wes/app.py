"""yai-wes: WES 1.1.0 (design/wes.md). API and engine share one process (wes.md section 4)."""

from __future__ import annotations

import fcntl
import json
import logging
import shutil
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from starlette.datastructures import UploadFile

from yai.aai.jwtutil import TokenError, unverified_parts
from yai.aai.service_auth import AtPrincipalResolver, JwtAuthenticator, bearer_token
from yai.common import startup
from yai.common.app import base_app
from yai.common.body import limited_request, too_deep
from yai.common.config import auth_mode, load_secret
from yai.common.errors import ApiError
from yai.common.fakes import AnonymousAuthenticator, FixedPrincipalResolver
from yai.common.fetch import Fetcher, FetchError
from yai.common.ids import new_id
from yai.common.paging import PageState, PageTokens
from yai.common.resolver import DrsResolver
from yai.common.roles import Principal
from yai.common.serviceinfo import service_info
from yai.common.timeutil import Clock, wes_time
from yai.wes import cwl
from yai.wes.db import TERMINAL, RunLimit, RunRow, StepRow, WesDb
from yai.wes.engine import Engine, EngineConfig
from yai.wes.tes_client import TesClient, TesError

log = logging.getLogger("yai.wes")
PSAT_SCOPES = startup.PSAT_SCOPES
BASE = "/ga4gh/wes/v1"
STATES = ["UNKNOWN", "QUEUED", "INITIALIZING", "RUNNING", "PAUSED", "COMPLETE", "EXECUTOR_ERROR",
          "SYSTEM_ERROR", "CANCELED", "CANCELING", "PREEMPTED"]  # fmt: skip
ENGINE, ENGINE_VERSION = "yai-mini", "0.1.0"
PAGE_DEFAULT, PAGE_MAX = 100, 1000  # paging.yaml (provisional)
PASSPORT_TYP = "vnd.ga4gh.passport+jwt"
MAX_DOC = 1024 * 1024
MAX_ATTACHMENT_INPUT = 1024 * 1024  # limit for passing an attachment as TES content
# Submit limits (provisional). Attachments are only used as the document or
# content inputs, both 1 MiB
MAX_ATTACHMENT = 1024 * 1024
MAX_ATTACHMENTS = 32
MAX_BODY = 40 * 1024 * 1024
LOCATION_SCHEMES = ("attachment:", "drs://", "https://", "http://")  # what the engine can resolve


def safe_name(name: str) -> str:
    """Attachment file name (WES-RUN-02): `..`, absolute paths, empty and NUL give 400."""
    if not name or "\x00" in name or name.startswith("/") or "\\" in name:
        raise ApiError(400, "bad attachment file name")
    parts = PurePosixPath(name).parts
    if not parts or any(p in ("..", ".") for p in parts):
        raise ApiError(400, "bad attachment file name")
    return "/".join(parts)


def check_passports(raw: str | None, now: int, skew: int = 60) -> list[str]:
    """yai_passports (D-009): check shape and expiry only. DRS decides (wes.md section 3)."""
    if raw is None:
        return []
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ApiError(400, "yai_passports must be a JSON array of strings") from exc
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        raise ApiError(400, "yai_passports must be a JSON array of strings")
    if len(value) > 10 or any(len(p) > 64 * 1024 for p in value):  # same limit as DRS
        raise ApiError(400, "yai_passports: at most 10 passports of 64 KiB each")
    for p in value:
        try:
            header, claims = unverified_parts(p)
        except TokenError as exc:
            raise ApiError(400, "yai_passports: not a JWT") from exc
        is_passport = header.get("typ") == PASSPORT_TYP
        if not is_passport or not isinstance(claims.get("ga4gh_passport_v1"), list):
            raise ApiError(400, "yai_passports: not a passport")
        exp = claims.get("exp")
        if not isinstance(exp, int) or exp + skew <= now:
            raise ApiError(400, "yai_passports: expired")
    return value


def _scheme(url: str) -> str:
    try:
        return urlsplit(url).scheme
    except ValueError as exc:  # e.g. a broken IPv6
        raise ApiError(400, "not a valid URL") from exc


def attach_locations(
    inputs: list[cwl.InputDef], params: dict[str, Any], names: dict[str, bytes]
) -> dict[str, Any]:
    """If a File location is a URL relative to an attachment, mark it attachment: (WES-RUN-03).

    Also applies to CWL default Files (submit and run decide alike).
    Locations the engine cannot resolve and non-UTF-8 attachments (TES content is a string) get 400.
    """
    out = dict(params)
    for inp in inputs:
        v = params.get(inp.name, inp.default)
        if inp.type != "File" or not isinstance(v, dict) or v.get("class") != "File":
            continue
        loc = v.get("location") or v.get("path")
        if not isinstance(loc, str):
            continue  # cwl.bind returns 400
        if not _scheme(loc):
            rel = safe_name(loc)
            if rel not in names:
                raise cwl.CWLError(f"input {inp.name}: no such attachment")
            if len(names[rel]) > MAX_ATTACHMENT_INPUT:
                raise cwl.CWLError(f"input {inp.name}: attachment larger than 1 MiB")
            try:
                names[rel].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise cwl.CWLError(f"input {inp.name}: attachment must be UTF-8 text") from exc
            loc = f"attachment:{rel}"
            out[inp.name] = {**v, "location": loc}
        if not loc.startswith(LOCATION_SCHEMES):
            raise cwl.CWLError(f"input {inp.name}: unsupported location")
    return out


async def _limited_form(request: Request):
    """Limit the whole body and attachment count while reading (D-042 shared)."""
    bounded = limited_request(request, MAX_BODY)
    return await bounded.form(max_files=MAX_ATTACHMENTS, max_fields=50)


def create_app(config: dict[str, Any]) -> FastAPI:
    startup.check("wes", config)  # reject before building the gate (D-055)
    app = base_app("yai-wes")
    app.state.config = config
    data_dir = Path(config.get("data_dir", "var/wes")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = open(data_dir / "wes.lock", "w")  # noqa: SIM115 held for the life of the process
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # a second one stops
    except BlockingIOError:
        raise SystemExit("another yai-wes is running") from None
    app.state.lock = lock
    runs_dir = data_dir / "runs"
    db = WesDb(data_dir / "wes.sqlite", runs_dir)
    clock = Clock()
    tokens = PageTokens(load_secret(data_dir / "page-secret"))
    public_base = str(config.get("public_base_url", "http://127.0.0.1:8404")).rstrip("/")
    fetch = config.get("fetch", {})
    fetcher = Fetcher(
        allow=list(fetch.get("allow", [])),
        insecure_http_hosts=list(fetch.get("insecure_http_hosts", [])),
    )
    tes_cfg = config.get("tes", {})
    secret_file = tes_cfg.get("client_secret_file")
    tes = TesClient(
        base_url=str(tes_cfg.get("url", "https://tes.yai.test/ga4gh/tes/v1")).rstrip("/"),
        token_url=str(tes_cfg.get("token_url", "https://aai.yai.test/token")),
        client_id=str(tes_cfg.get("client_id", "yai-wes")),
        client_secret=Path(secret_file).read_text().strip() if secret_file else "",
        audience=str(tes_cfg.get("audience", "yai-tes")),
    )
    # D-067: call TES with a token exchanged from the user's AT (`act` = this WES). false keeps
    # D-010 (the service identity, tasks owned by the WES), e.g. for a TES without the yai broker
    delegate = bool(tes_cfg.get("delegate", True))
    res = config.get("resolver", {})
    # D-069: false leaves drs:// inputs to the TES (a yai-tes with drs.enabled); the WES then
    # needs no Passport. Default true: the WES resolves them (the (a) of system.md section 3)
    resolve_in_wes = bool(res.get("resolve_in_wes", True))
    # scopes asked for in the exchange (D-067): the TES operations, plus the Passport scopes only
    # when the TES resolves drs:// itself and needs them (D-069). The broker narrows further to
    # the client's scopes
    tes_scopes = startup.wes_tes_scopes(tes_cfg)  # never the Passport scopes by configuration alone
    if not tes_scopes:  # startup.check refused this already; never ask for "all of the client's"
        raise SystemExit("tes.scopes must name at least one TES scope")
    if not resolve_in_wes:
        tes_scopes += sorted(PSAT_SCOPES)
    tes_scope = " ".join(tes_scopes)
    # the broker narrows to its client's scopes: if the Passport scopes this WES needs are not
    # granted, fail the exchange now rather than every run's first drs:// step (D-069)
    tes_required = PSAT_SCOPES if not resolve_in_wes else frozenset()
    resolver = DrsResolver(
        passport_hosts=list(res.get("passport_hosts", ["drs.yai.test"])),
        host_override=dict(res.get("host_override", {})),
        compact=dict(res.get("compact", {})),
        fetcher=fetcher,
    )
    eng = config.get("engine", {})
    max_active = int(config.get("max_active_runs_per_user", 16))  # 0 means no limit (D-044)
    engine_cfg = EngineConfig(
        poll_seconds=float(eng.get("poll_seconds", 2)),
        nonce_search_seconds=float(eng.get("nonce_search_seconds", 60)),
        nonce_search_interval=float(eng.get("nonce_search_interval", 10)),
        cancel_retry_seconds=float(eng.get("cancel_retry_seconds", 10)),
        unreachable_limit_seconds=float(eng.get("unreachable_limit_seconds", 1800)),
        # if relative, make absolute at startup (as a file:// URL)
        output_root=str(Path(eng.get("output_root", "/var/tes/files")).resolve()),
        faults=dict(config.get("faults", {})),
        resolve_drs=resolve_in_wes,
    )

    def workflow_of(run_id: str) -> Path:
        return next((runs_dir / run_id / "workflow").iterdir())

    def attachment_of(run_id: str, rel: str) -> str:
        return (runs_dir / run_id / "attachments" / safe_name(rel)).read_text(encoding="utf-8")

    engine = Engine(db, tes, resolver, engine_cfg, clock, workflow_of, attachment_of)
    app.state.engine = engine
    auth = config.get("auth") or {}
    authenticator: Any
    principal_resolver: Any
    if auth_mode(config, ("aai", "anonymous")) == "aai":
        authenticator, principal_resolver = JwtAuthenticator(auth, clock), AtPrincipalResolver()
    else:
        authenticator, principal_resolver = AnonymousAuthenticator(), FixedPrincipalResolver()
    router = APIRouter(prefix=BASE)

    def principal_of(request: Request, scope: str) -> Principal:
        principal = principal_resolver.resolve(authenticator.authenticate(request))
        if principal is None:
            raise ApiError(401, "authentication required")
        if "*" not in principal.scopes and scope not in principal.scopes:
            raise ApiError(403, "forbidden")
        return principal

    def own_run(run_id: str, principal: Principal) -> RunRow:
        run = db.get_run(run_id)
        if run is None or run.owner != principal.owner:
            raise ApiError(404, "run not found")  # another user's run is 404 (D-013)
        return run

    def page_args(request: Request) -> tuple[int, str | None]:
        q = request.query_params
        try:
            size = int(q.get("page_size", PAGE_DEFAULT))
        except ValueError as exc:
            raise ApiError(400, "page_size must be an integer") from exc
        if not 0 < size <= PAGE_MAX:
            raise ApiError(400, f"page_size must be 1..{PAGE_MAX}")
        return size, q.get("page_token") or None

    def task_url(run_id: str, n: int) -> str:
        return f"{public_base}{BASE}/runs/{run_id}/tasks/{run_id}.{n}"

    def task_log(run: RunRow, step: StepRow) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": f"{run.id}.{step.n}",
            "name": step.name,
            "cmd": step.cmd,
            "system_logs": [f"state={step.state or 'UNKNOWN'}"],
        }
        if step.start_time:
            out["start_time"] = wes_time(step.start_time)
        if step.end_time:
            out["end_time"] = wes_time(step.end_time)
        if step.exit_code is not None:
            out["exit_code"] = step.exit_code
        if step.tes_task_id:
            out["tes_uri"] = f"{tes.base_url}/tasks/{step.tes_task_id}"
            out["stdout"] = task_url(run.id, step.n) + "/stdout"  # C-08
            out["stderr"] = task_url(run.id, step.n) + "/stderr"
        return out

    # Handlers are def (sync) and run in threads. Only RunWorkflow is async to read multipart,
    # and offloads the heavy parts (key fetch, document fetch) to threads
    @router.get("/service-info")
    def get_service_info() -> dict[str, Any]:
        counts = db.counts()
        return service_info(
            service_id=config.get("service_id", "test.yai.wes"),
            name="yai-wes",
            artifact="wes",
            spec_version="1.1.0",
            workflow_type_versions={"CWL": {"workflow_type_version": ["v1.2"]}},
            supported_wes_versions=["1.1.0"],
            supported_filesystem_protocols=["drs", "https"],
            workflow_engine_versions={ENGINE: {"workflow_engine_version": [ENGINE_VERSION]}},
            default_workflow_engine_parameters=[],
            system_state_counts={s: counts.get(s, 0) for s in STATES},
            auth_instructions_url="https://yai.test/docs/design/aai.md",
            tags={
                "yai.cwl_subset": "only the subset in docs/design/wes.md section 1 "
                "(one CommandLineTool or a serial Workflow)"
            },
        )

    @router.post("/runs")
    async def run_workflow(request: Request) -> dict[str, str]:
        principal = await run_in_threadpool(principal_of, request, "wes.run")
        user_token = bearer_token(request) if principal.kind == "user" else None
        if not request.headers.get("content-type", "").startswith("multipart/form-data"):
            raise ApiError(400, "RunWorkflow needs multipart/form-data")  # WES-RUN-01
        form = await _limited_form(request)
        fields: dict[str, str | None] = {}
        for name in ("workflow_params", "tags", "workflow_engine_parameters", "workflow_type",
                     "workflow_type_version", "workflow_engine", "workflow_engine_version",
                     "workflow_url", "yai_passports"):  # fmt: skip
            v = form.get(name)
            if isinstance(v, UploadFile):
                raise ApiError(400, f"{name} must be a text field")
            fields[name] = v
        attachments: list[tuple[str, Any]] = []
        for part in form.getlist("workflow_attachment"):
            if not isinstance(part, UploadFile) or not part.filename:
                raise ApiError(400, "each workflow_attachment needs a filename")  # WES-RUN-01
            if part.size is not None and part.size > MAX_ATTACHMENT:
                raise ApiError(400, "each workflow_attachment must be at most 1 MiB")
            attachments.append((part.filename, part.file))
        # other checks, doc fetch, save and DB in a thread (frees the event loop)
        return await run_in_threadpool(accept_run, principal, fields, attachments, user_token)

    def accept_run(
        principal: Principal, fields: dict[str, str | None], attachments: list[tuple[str, Any]],
        user_token: str | None = None,
    ) -> dict[str, str]:  # fmt: skip
        params_raw = fields["workflow_params"]
        if params_raw is None:
            raise ApiError(400, "workflow_params is required")  # WES-RUN-07
        try:
            params = json.loads(params_raw)
            tags = json.loads(fields["tags"] or "{}")
            engine_params = json.loads(fields["workflow_engine_parameters"] or "{}")
        except (ValueError, RecursionError) as exc:
            msg = "workflow_params, tags and workflow_engine_parameters must be JSON"
            raise ApiError(400, msg) from exc
        # Nesting up to 64 levels like CWL YAML. Saving deeper input makes scrub_request hit
        # RecursionError on the final transition, so the run cannot end (D-060 (2))
        for name, value in (("workflow_params", params), ("tags", tags),
                            ("workflow_engine_parameters", engine_params)):  # fmt: skip
            if too_deep(value, cwl.MAX_DEPTH):
                raise ApiError(400, f"{name} is nested too deeply (at most {cwl.MAX_DEPTH})")
        if not isinstance(params, dict):
            raise ApiError(400, "workflow_params must be a JSON object")
        if not isinstance(tags, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in tags.items()
        ):
            raise ApiError(400, "tags must map strings to strings")
        if engine_params:
            raise ApiError(400, "workflow_engine_parameters are not supported")
        wtype, wver = fields["workflow_type"], fields["workflow_type_version"]
        engine_name, engine_ver = fields["workflow_engine"], fields["workflow_engine_version"]
        if wtype != "CWL" or wver != "v1.2":  # WES-RUN-05
            raise ApiError(400, "only workflow_type CWL v1.2 is supported")
        if engine_ver and not engine_name:  # WES-RUN-06
            raise ApiError(400, "workflow_engine is required with workflow_engine_version")
        if (engine_name and engine_name != ENGINE) or (engine_ver and engine_ver != ENGINE_VERSION):
            raise ApiError(400, "unsupported workflow engine")
        workflow_url = fields["workflow_url"]
        if not workflow_url:
            raise ApiError(400, "workflow_url is required")
        passports = check_passports(fields["yai_passports"], clock.now())
        # count check only, before fetching or writing attachments (D-057 (1)); final in create_run
        if max_active > 0 and db.active_runs(principal.owner) >= max_active:
            raise ApiError(429, f"too many unfinished runs (at most {max_active})")

        # read attachments (limit checked on receipt); save only after all checks pass
        names: dict[str, bytes] = {}
        for filename, file in attachments:
            file.seek(0)
            data = file.read(MAX_ATTACHMENT + 1)
            if len(data) > MAX_ATTACHMENT:
                raise ApiError(400, "each workflow_attachment must be at most 1 MiB")
            names[safe_name(filename)] = data

        # fetch and save the workflow document now, and check the subset
        if _scheme(workflow_url):
            # saved name = last path segment; `..` cannot be saved, so 400 (D-057 (2), review N-4),
            # checked before fetching
            doc_name = PurePosixPath(urlsplit(workflow_url).path).name or "workflow.cwl"
            if doc_name == "..":
                raise ApiError(400, "workflow_url: bad file name")
            try:
                doc = fetcher.get_small(workflow_url, max_bytes=MAX_DOC, total_timeout=10)
            except FetchError as exc:
                raise ApiError(400, f"workflow_url: {exc.reason}") from exc
        else:
            rel = safe_name(workflow_url)
            if rel not in names:
                raise ApiError(400, "workflow_url must be an absolute URL or an attachment")
            doc, doc_name = names[rel], PurePosixPath(rel).name
            if len(doc) > MAX_DOC:  # same limit as for URLs
                raise ApiError(400, "workflow document larger than 1 MiB")
        try:
            plan = cwl.parse_plan(doc)
            params = attach_locations(plan.inputs, params, names)
            bound = [cwl.bind(s.tool, cwl.step_params(plan, n, params))
                     for n, s in enumerate(plan.steps, 1)]  # fmt: skip
        except cwl.CWLError as exc:
            raise ApiError(400, f"workflow outside the supported CWL subset: {exc}") from exc

        record: dict[str, Any] = {
            "workflow_params": params,
            "workflow_type": wtype,
            "workflow_type_version": wver,
            "workflow_url": workflow_url,
            "tags": tags,
        }
        if engine_name:
            record["workflow_engine"] = engine_name
        if engine_ver:
            record["workflow_engine_version"] = engine_ver
        needs_passport = resolve_in_wes and any(
            loc.startswith("drs://") for _, files in bound for loc, _ in files.values()
        )
        if plan.is_workflow:
            steps = [(s.name, argv) for s, (argv, _) in zip(plan.steps, bound, strict=True)]
        else:
            steps = [(plan.label or PurePosixPath(doc_name).stem, bound[0][0])]

        # Exchange the user's AT for one the TES accepts, with `act` = this WES (D-067). Before
        # anything is saved: a broker that is down leaves no run behind. Kept in memory only
        tes_token = None
        if delegate and user_token is not None:
            try:
                tes_token = tes.exchange(user_token, tes_scope, require=tes_required)
            except TesError as exc:
                if exc.status == 400:  # the broker refused the user's token (revoked, expired,
                    # not issued to this WES): the caller's side, not an internal error
                    raise ApiError(403, "the access token could not be exchanged for TES") from exc
                raise ApiError(500, f"token exchange for TES failed: {exc.reason}") from exc

        run_id = new_id("r")
        run_dir = runs_dir / run_id
        try:
            for name, data in names.items():
                dest = run_dir / "attachments" / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            wf_dir = run_dir / "workflow"
            wf_dir.mkdir(parents=True, exist_ok=True)
            (wf_dir / doc_name).write_bytes(doc)
            db.create_run(run_id, principal.owner, record, clock.now(), needs_passport, steps,
                          max_active=max_active, delegated=tes_token is not None)  # fmt: skip
        except BaseException as exc:
            shutil.rmtree(run_dir, ignore_errors=True)  # leave no remains of a rejected run
            if isinstance(exc, RunLimit):
                raise ApiError(429, f"too many unfinished runs (at most {max_active})") from None
            raise
        engine.start(run_id, passports, tes_token)
        return {"run_id": run_id}

    @router.get("/runs")
    def list_runs(request: Request) -> dict[str, Any]:
        principal = principal_of(request, "wes.read")
        size, token = page_args(request)
        who = principal.owner
        if token:
            page = tokens.read(token, principal=who, scope="wes.runs", filters={})
        else:
            page = PageState(db.max_seq(), None)
        rows = db.list_runs(who, page.snapshot_seq, page.after_seq, size + 1)
        chosen = rows[:size]
        nxt = ""  # empty string means the end (WES-LST-04)
        if len(rows) > size:
            state = PageState(page.snapshot_seq, chosen[-1].seq)
            nxt = tokens.make(state, principal=who, scope="wes.runs", filters={})
        runs = []
        for r in chosen:  # RunSummary(WES-LST-05)
            item: dict[str, Any] = {
                "run_id": r.id,
                "state": r.state,
                "tags": r.request.get("tags") or {},
            }
            if r.start_time:
                item["start_time"] = wes_time(r.start_time)
            if r.end_time:
                item["end_time"] = wes_time(r.end_time)
            runs.append(item)
        return {"runs": runs, "next_page_token": nxt}

    @router.get("/runs/{run_id}")
    def get_run_log(run_id: str, request: Request) -> dict[str, Any]:
        principal = principal_of(request, "wes.read")
        run = own_run(run_id, principal)
        run_log: dict[str, Any] = {"name": run.id, "system_logs": run.system_logs}
        if run.start_time:
            run_log["start_time"] = wes_time(run.start_time)
        if run.end_time:
            run_log["end_time"] = wes_time(run.end_time)
        return {
            "run_id": run.id,
            "request": run.request,  # no Passports included (C-18)
            "state": run.state,
            "run_log": run_log,
            "task_logs_url": f"{public_base}{BASE}/runs/{run.id}/tasks",  # WES-LOG-03
            "outputs": run.outputs or {},
        }

    @router.get("/runs/{run_id}/status")
    def get_run_status(run_id: str, request: Request) -> dict[str, Any]:
        principal = principal_of(request, "wes.read")
        run = own_run(run_id, principal)
        return {"run_id": run.id, "state": run.state}

    @router.get("/runs/{run_id}/tasks")
    def list_tasks(run_id: str, request: Request) -> dict[str, Any]:
        principal = principal_of(request, "wes.read")
        run = own_run(run_id, principal)
        size, token = page_args(request)
        scope = f"wes.tasks:{run_id}"
        steps = db.steps(run_id)
        after = 0
        if token:
            state = tokens.read(token, principal=principal.owner, scope=scope, filters={})
            after = state.after_seq or 0
        chosen = [s for s in steps if s.n > after][:size]
        nxt = ""
        if chosen and any(s.n > chosen[-1].n for s in steps):
            state = PageState(len(steps), chosen[-1].n)
            nxt = tokens.make(state, principal=principal.owner, scope=scope, filters={})
        return {"task_logs": [task_log(run, s) for s in chosen], "next_page_token": nxt}

    def find_step(run: RunRow, task_id: str) -> StepRow:
        for s in db.steps(run.id):
            if f"{run.id}.{s.n}" == task_id:
                return s
        raise ApiError(404, "task not found")

    @router.get("/runs/{run_id}/tasks/{task_id}")
    def get_task(run_id: str, task_id: str, request: Request) -> dict[str, Any]:
        principal = principal_of(request, "wes.read")
        run = own_run(run_id, principal)
        return task_log(run, find_step(run, task_id))

    def task_output(run_id: str, task_id: str, request: Request, which: str):
        principal = principal_of(request, "wes.read")
        run = own_run(run_id, principal)
        step = find_step(run, task_id)
        if not step.tes_task_id:
            raise ApiError(404, "no log yet")
        try:
            task = tes.get(step.tes_task_id)
        except Exception as exc:
            raise ApiError(500, "could not read the task log from TES") from exc
        ex = ((task.get("logs") or [{}])[-1].get("logs") or [{}])[-1]
        value = ex.get(which) or ""
        if value.startswith(("http://", "https://", "s3://", "gs://", "file://")):
            # TES returned a URL instead of content: do not serve it as content (C-08)
            return PlainTextResponse("the TES returned a URL instead of the log content\n")
        return PlainTextResponse(value)

    @router.get("/runs/{run_id}/tasks/{task_id}/stdout")
    def task_stdout(run_id: str, task_id: str, request: Request):
        return task_output(run_id, task_id, request, "stdout")

    @router.get("/runs/{run_id}/tasks/{task_id}/stderr")
    def task_stderr(run_id: str, task_id: str, request: Request):
        return task_output(run_id, task_id, request, "stderr")

    @router.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: str, request: Request) -> dict[str, str]:
        principal = principal_of(request, "wes.cancel")
        run = own_run(run_id, principal)
        for _ in range(5):
            if run.state in TERMINAL or run.state == "CANCELING":
                return {"run_id": run_id}  # 200 and do nothing (D-020)
            to = "CANCELED" if run.state == "QUEUED" else "CANCELING"
            if db.transition(run_id, run.state, to, clock.now()):
                if to == "CANCELING":
                    engine.start(run_id)  # keep cancelling even if the watch has stopped
                return {"run_id": run_id}
            run = db.get_run(run_id) or run
        raise ApiError(500, "could not cancel")

    app.include_router(router)
    engine.recover()
    return app
