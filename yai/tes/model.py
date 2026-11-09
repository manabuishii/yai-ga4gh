"""CreateTask checks and per-view responses (tes.md section 2, TES-CRT-*, TES-VIEW-01)."""

from __future__ import annotations

import copy
import posixpath
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from yai.common.errors import ApiError
from yai.common.fetch import Fetcher, FetchError
from yai.common.imageref import BadImageReference, check_image_reference
from yai.common.roles import Principal
from yai.common.timeutil import rfc3339

READ_ONLY = ("id", "state", "logs", "creation_time")  # TES-CRT-08
FORBIDDEN_ROOTS = ("/proc", "/sys", "/dev", "/etc")
YAI_TAGS = ("yai.on_behalf_of", "yai.run_id", "yai.submit_nonce")
MAX_CONTENT = 1 << 20  # content of at least 128 KiB is accepted (TES-CRT-05). The limit is 1 MiB


# ram_gb floor of 6 MiB (Docker's --memory minimum). Below it, it was truncated to
# --memory 0m = no limit (D-060 (1), the finding that a tiny ram_gb meant no limit)
MIN_RAM_GB = 6 / 1024


@dataclass(frozen=True)
class Limits:
    max_cpu: int = 2
    max_ram_gb: float = 4.0
    default_cpu: int = 1
    default_ram_gb: float = 2.0
    max_inputs: int = 100  # number of inputs and outputs (D-044)
    max_outputs: int = 100


def bad(msg: str) -> ApiError:
    return ApiError(400, msg)


def optional(
    obj: dict[str, Any], key: str, typ: type | tuple[type, ...], what: str, default: Any
) -> Any:
    """Omitted or null means default. Wrong type is 400 (falsy values checked by type too)."""
    value = obj.get(key)
    if value is None:
        return default
    if not isinstance(value, typ) or (isinstance(value, bool) and bool not in _types(typ)):
        raise bad(f"{what} has the wrong type")
    return value


def _types(typ: type | tuple[type, ...]) -> tuple[type, ...]:
    return typ if isinstance(typ, tuple) else (typ,)


def check_container_path(value: Any, what: str) -> str:
    """Absolute, no `..`, and not under /proc etc. (TES-CRT-03, NEG-PATH)."""
    if not isinstance(value, str) or not value.startswith("/"):
        raise bad(f"{what} must be an absolute path")
    parts = value.split("/")
    if ".." in parts or "\x00" in value:
        raise bad(f"{what} must not contain '..'")
    if ":" in value:
        raise bad(f"{what} must not contain ':'")  # separator of `-v host:container:mode` (D-041)
    norm = posixpath.normpath(value)
    if norm == "/" or any(norm == r or norm.startswith(r + "/") for r in FORBIDDEN_ROOTS):
        raise bad(f"{what} is not allowed")
    return norm


def file_url_path(url: str, files_root: str) -> str:
    """Turn a file:// URL into a path relative to the configured root. Outside root or `..`: 400."""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise bad("file url is not a valid URL") from exc
    if parts.scheme != "file" or parts.netloc not in ("", "localhost") or parts.query:
        raise bad("file url must be file:///<path>")
    path = unquote(parts.path)
    if ".." in path.split("/") or "\x00" in path:
        raise bad("file url must not contain '..'")
    root = files_root.rstrip("/")
    norm = posixpath.normpath(path)
    if not norm.startswith(root + "/"):
        raise bad("file url must be under the storage root")
    return norm[len(root) + 1 :]


def overlaps(a: str, b: str) -> bool:
    return a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")


def validate_task(
    body: Any,
    principal: Principal,
    *,
    files_root: str,
    fetcher: Fetcher,
    limits: Limits,
    output_owner,
) -> tuple[dict[str, Any], list[str]]:
    """Check a received tesTask and return the form to store plus warnings. 400 if invalid."""
    if not isinstance(body, dict):
        raise bad("task must be an object")
    task = {k: copy.deepcopy(v) for k, v in body.items() if k not in READ_ONLY}
    warnings: list[str] = []
    for key in ("name", "description"):
        optional(task, key, str, key, None)

    # executors(TES-CRT-01)
    executors = task.get("executors")
    if not isinstance(executors, list) or not executors:
        raise bad("executors is required")
    for i, ex in enumerate(executors):
        if not isinstance(ex, dict):
            raise bad(f"executors[{i}] must be an object")
        try:
            check_image_reference(ex.get("image"))  # `-...` never becomes an option (D-041)
        except BadImageReference as exc:
            raise bad(f"executors[{i}].{exc}") from exc
        cmd = ex.get("command")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
            raise bad(f"executors[{i}].command must be a non-empty array of strings")
        for key in ("workdir", "stdin", "stdout", "stderr"):
            if ex.get(key) is not None:
                ex[key] = check_container_path(ex[key], f"executors[{i}].{key}")
        env = ex.get("env")
        if env is not None and (
            not isinstance(env, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
        ):
            raise bad(f"executors[{i}].env must map strings to strings")
        if "ignore_error" in ex and not isinstance(ex["ignore_error"], bool):
            raise bad(f"executors[{i}].ignore_error must be a boolean")

    # inputs (TES-CRT-03/04/05, TES-RUN-03)
    inputs = optional(task, "inputs", list, "inputs", [])
    if len(inputs) > limits.max_inputs:
        raise bad(f"at most {limits.max_inputs} inputs")
    for i, inp in enumerate(inputs):
        if not isinstance(inp, dict):
            raise bad(f"inputs[{i}] must be an object")
        for key in ("name", "description"):
            optional(inp, key, str, f"inputs[{i}].{key}", None)
        inp["path"] = check_container_path(inp.get("path"), f"inputs[{i}].path")
        inp.setdefault("type", "FILE")
        if inp["type"] != "FILE":
            raise bad(f"inputs[{i}].type: only FILE is supported")
        content = inp.get("content")
        if content is not None:
            if not isinstance(content, str) or len(content.encode()) > MAX_CONTENT:
                raise bad(f"inputs[{i}].content must be a string up to 1 MiB")
            inp.pop("url", None)  # with content, url is ignored (TES-CRT-04)
            continue
        url = inp.get("url")
        if not isinstance(url, str) or not url:
            raise bad(f"inputs[{i}].url is required when content is absent")
        try:
            scheme = urlsplit(url).scheme
        except ValueError as exc:
            raise bad(f"inputs[{i}].url is not a valid URL") from exc
        if scheme == "file":
            rel = file_url_path(url, files_root)
            owner = output_owner(url)
            if owner is None or not file_input_allowed(owner, principal, task):
                raise bad(f"inputs[{i}].url is not an output you may read")  # C-25
            inp["_rel"] = rel
        elif scheme in ("https", "http"):
            try:
                fetcher.check(url)
            except FetchError as exc:  # broken ports, IPv6 etc. too (host_port)
                raise bad(f"inputs[{i}].url: {exc.reason}") from exc  # NEG-SSRF
        else:
            raise bad(f"inputs[{i}].url: unsupported scheme")

    # outputs (under the file:// root, no wildcards, not overlapping our own outputs)
    outputs = optional(task, "outputs", list, "outputs", [])
    if len(outputs) > limits.max_outputs:
        raise bad(f"at most {limits.max_outputs} outputs")
    seen: set[str] = set()
    for i, out in enumerate(outputs):
        if not isinstance(out, dict):
            raise bad(f"outputs[{i}] must be an object")
        for key in ("name", "description"):
            optional(out, key, str, f"outputs[{i}].{key}", None)
        out["path"] = check_container_path(out.get("path"), f"outputs[{i}].path")
        out.setdefault("type", "FILE")
        if out["type"] != "FILE" or out.get("path_prefix") or any(c in out["path"] for c in "*?["):
            raise bad(f"outputs[{i}]: only single files are supported (no wildcards)")
        url = out.get("url")
        if not isinstance(url, str):
            raise bad(f"outputs[{i}].url is required")
        rel = file_url_path(url, files_root)
        # Only our own outputs are known on submit. Storage is not checked (replies must not reveal
        # others' files, D-044). If one exists, publish does not overwrite:
        # SYSTEM_ERROR
        mine = output_owner(url)
        if rel in seen or (mine is not None and tuple(mine[:2]) == principal.owner):
            raise bad(f"outputs[{i}].url already exists")
        seen.add(rel)

    # volumes (TES-RUN-02/04). Must not overlap input/output paths
    volumes = optional(task, "volumes", list, "volumes", [])
    task["volumes"] = [check_container_path(v, f"volumes[{i}]") for i, v in enumerate(volumes)]
    for v in task["volumes"]:
        for io in [*inputs, *outputs]:
            if overlaps(v, io["path"]):
                raise bad(f"volume {v} overlaps {io['path']}")

    # resources (limits and defaults), backend_parameters (TES-CRT-07)
    res = optional(task, "resources", dict, "resources", {})
    optional(res, "preemptible", bool, "resources.preemptible", None)
    optional(res, "disk_gb", (int, float), "resources.disk_gb", None)
    zones = optional(res, "zones", list, "resources.zones", [])
    if not all(isinstance(z, str) for z in zones):
        raise bad("resources.zones must be an array of strings")
    cpu = res.get("cpu_cores", limits.default_cpu)
    ram = res.get("ram_gb", limits.default_ram_gb)
    if not isinstance(cpu, int) or isinstance(cpu, bool) or not 0 < cpu <= limits.max_cpu:
        raise bad(f"cpu_cores must be 1..{limits.max_cpu}")
    if (
        not isinstance(ram, (int, float))
        or isinstance(ram, bool)
        or not MIN_RAM_GB <= ram <= limits.max_ram_gb
    ):
        raise bad(f"ram_gb must be >= {MIN_RAM_GB} and <= {limits.max_ram_gb}")
    res["cpu_cores"], res["ram_gb"] = cpu, ram
    params = optional(res, "backend_parameters", dict, "resources.backend_parameters", None)
    strict = optional(res, "backend_parameters_strict", bool, "backend_parameters_strict", False)
    res.pop("backend_parameters", None)
    res.pop("backend_parameters_strict", None)
    if params:
        if strict:
            raise bad("backend_parameters are not supported (strict)")
        warnings.append(f"backend_parameters ignored: {sorted(params)}")
    task["resources"] = res

    # tags (yai. only for services plus tes.delegate, NEG-TAG-SPOOF)
    tags = optional(task, "tags", dict, "tags", {})
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in tags.items()):
        raise bad("tags must map strings to strings")
    if any(k.startswith("yai.") for k in tags):
        if principal.kind != "service" or "tes.delegate" not in principal.scopes:
            raise bad("tags starting with 'yai.' are reserved")
        if any(k.startswith("yai.") and k not in YAI_TAGS for k in tags):
            raise bad("unknown yai. tag")
        obo = tags.get("yai.on_behalf_of", "")
        if obo and obo.count("|") != 1:
            raise bad("yai.on_behalf_of must be <iss>|<sub>")
    task["tags"] = tags
    return task, warnings


def file_input_allowed(owner: tuple, principal: Principal, task: dict[str, Any]) -> bool:
    """file:// inputs only from outputs of the same owner. For tasks a service submits on someone's
    behalf, only outputs with the same yai.on_behalf_of and the same yai.run_id (C-25)."""
    o_iss, o_sub, o_obo, o_run = owner
    if (o_iss, o_sub) != principal.owner:
        return False
    tags = task.get("tags") or {}
    if principal.kind == "service":
        return o_obo == tags.get("yai.on_behalf_of") and o_run == tags.get("yai.run_id")
    return True


def public_task(task: dict[str, Any]) -> dict[str, Any]:
    """Strip internal fields (starting with _) from the stored form."""
    out = copy.deepcopy(task)
    for inp in out.get("inputs") or []:
        for k in [k for k in inp if k.startswith("_")]:
            del inp[k]
    return out


def render(row, logs: list[dict[str, Any]], view: str) -> dict[str, Any]:
    """TES-VIEW-01: MINIMAL is id and state only.
    BASIC omits stdout, stderr, content and system_logs.
    Finished tasks have no content even in FULL (removed by the final write, D-043)."""
    if view == "MINIMAL":
        return {"id": row.id, "state": row.state}
    task = public_task(row.task)
    task["id"] = row.id
    task["state"] = row.state
    task["creation_time"] = rfc3339(row.created_at)
    task_logs = []
    for log in logs:
        entry: dict[str, Any] = {
            "logs": [
                {
                    k: v
                    for k, v in {
                        "start_time": rfc3339(e[3]) if e[3] else None,
                        "end_time": rfc3339(e[4]) if e[4] else None,
                        "stdout": e[5],
                        "stderr": e[6],
                        "exit_code": e[2] if e[2] is not None else -1,
                    }.items()
                    if v is not None
                }
                for e in log["executors"]
            ],
            "outputs": [{"url": u, "path": p, "size_bytes": str(s)} for u, p, s in log["outputs"]],
            "metadata": log["metadata"],
        }
        if log["start_time"]:
            entry["start_time"] = rfc3339(log["start_time"])
        if log["end_time"]:
            entry["end_time"] = rfc3339(log["end_time"])
        entry["system_logs"] = log["system_logs"]
        task_logs.append(entry)
    if task_logs:
        task["logs"] = task_logs
    if view == "BASIC":
        for inp in task.get("inputs") or []:
            inp.pop("content", None)
        for log in task.get("logs", []):
            log.pop("system_logs", None)
            for e in log["logs"]:
                e.pop("stdout", None)
                e.pop("stderr", None)
    return task
