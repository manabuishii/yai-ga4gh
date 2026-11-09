"""Startup checks and the startup line (D-055).

Checks key combinations and ranges at startup. Rejections are SystemExit (one line, no traceback);
WARNINGs log one line to `yai.startup` and startup continues (table: impl. plan 2.3, D-055 (3)).
Called first in each service's create_app and from the TES worker, so TestClient tests hit it too.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import re
from pathlib import Path
from typing import Any

from yai.common.config import auth_mode
from yai.common.fetch import Fetcher, FetchError, host_port

log = logging.getLogger("yai.startup")
# The root level is WARNING (cli). With only the child logger at INFO, only the startup line
# reaches the root handler (propagation does not check the parent logger's level)
log.setLevel(logging.INFO)

AUTH_MODES = {"drs": ("aai", "anonymous"), "tes": ("aai", "anonymous", "static-bearer"),
              "wes": ("aai", "anonymous")}  # fmt: skip
DAY = 86400  # upper bound for ttl and URL expiry (1 day; over 12x the D-019 default)
MIN_CLIENT_SECRET = 32  # same as MIN_SECRET of load_secret (D-055 (4))


def is_loopback(host: str) -> bool:
    """Whether host (without port) is 127.0.0.0/8, ::1 or localhost."""
    host = host.strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _host(hostport: str) -> str:
    """host from `host:port` (host_port form)."""
    return hostport.rsplit(":", 1)[0] if ":" in hostport else hostport


def _test_domain(host: str) -> bool:
    """`*.yai.test` (strict from the last label; not `yai.test.example` or `xyai.test`)."""
    labels = host.lower().rstrip(".").split(".")
    return len(labels) >= 3 and labels[-2:] == ["yai", "test"] and all(labels)


def _int(where: str, value: Any) -> int:
    """Accept integer forms only (int, finite float without fraction, digit-only strings). Checking
    the range after int() let -0.5 pass as 0 (no limit) and inf raised OverflowError."""
    if isinstance(value, bool):
        raise SystemExit(f"{where} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value.strip()):
        return int(value)
    raise SystemExit(f"{where} must be an integer")


def _range(where: str, value: Any, low: int, high: int | None = None) -> None:
    n = _int(where, value)
    if n < low or (high is not None and n > high):
        bound = f"between {low} and {high}" if high is not None else f"at least {low}"
        raise SystemExit(f"{where} must be {bound} (got {n})")


def _keys(where: str, table: dict[str, Any]) -> None:
    """Key refetch interval >= 1, cache >= interval (N-7, P3-10). Defaults 60 and 3600."""
    interval = table.get("key_refetch_min_interval_seconds", 60)
    _range(f"{where}.key_refetch_min_interval_seconds", interval, 1)
    cache = _int(f"{where}.key_cache_seconds", table.get("key_cache_seconds", 3600))
    if cache < _int(where, interval):
        raise SystemExit(f"{where}.key_cache_seconds must not be less than "
                         f"{where}.key_refetch_min_interval_seconds")  # fmt: skip


def _insecure(where: str, table: dict[str, Any]) -> None:
    for hp in table.get("insecure_http_hosts") or []:
        host = _host(str(hp))
        if not (is_loopback(host) or _test_domain(host)):
            log.warning("%s.insecure_http_hosts has %s: plain http to a host that is neither "
                        "loopback nor *.yai.test", where, hp)  # fmt: skip


def _runner(config: dict[str, Any]) -> str:
    return str((config.get("runner") or {}).get("kind", "local-process"))


def check(service: str, config: dict[str, Any]) -> None:
    """service is aai, drs, tes, tes-worker or wes."""
    if service in AUTH_MODES:
        mode = auth_mode(config, AUTH_MODES[service])
        if mode == "anonymous":
            log.warning("yai-%s: auth.mode is anonymous: every request is allowed without "
                        "credentials", service)  # fmt: skip
    if service in ("aai", "drs") and "clock_offset_seconds" in config:
        # not silently ignored (it used to shift the clock used by the gate, D-054 (3))
        raise SystemExit("clock_offset_seconds is no longer supported (D-054)")
    if service in AUTH_MODES:
        auth = config.get("auth") or {}
        if auth.get("mode") == "aai":
            _keys("auth", auth)
        _insecure("auth", auth)
    {"aai": _check_aai, "drs": _check_drs, "tes": _check_tes, "tes-worker": _check_tes_worker,
     "wes": _check_wes}[service](config)  # fmt: skip


def _check_aai(config: dict[str, Any]) -> None:
    users = config.get("users") or {}
    for cid, client in (config.get("clients") or {}).items():
        secret = str(client.get("secret") or "").strip()
        if len(secret) < MIN_CLIENT_SECRET or secret.upper() == "CHANGE-ME":
            raise SystemExit(f"clients.{cid}.secret must be a random value of at least "
                             f"{MIN_CLIENT_SECRET} characters")  # fmt: skip
        # the effective sub uses the same formula as issuance (client_credentials in yai/aai/app.py)
        if str(client.get("sub", cid)) in users:
            raise SystemExit(f"clients.{cid}: its sub must not be a user name")
    for key, value in (config.get("ttl") or {}).items():
        _range(f"ttl.{key}", value, 1, DAY)
    for name, user in users.items():
        for i, visa in enumerate((user or {}).get("visas") or []):
            if "ttl" in visa:
                _range(f"users.{name}.visas[{i}].ttl", visa["ttl"], 1, DAY)


def _check_drs(config: dict[str, Any]) -> None:
    _range("signed_url_ttl_seconds", config.get("signed_url_ttl_seconds", 3600), 1, DAY)
    trust = config.get("trust") or {}
    _range("trust.clock_skew_seconds", trust.get("clock_skew_seconds", 60), 0, 300)
    _range("trust.max_visas_per_passport", trust.get("max_visas_per_passport", 100), 1)
    _keys("trust", trust)
    _insecure("trust", trust)
    if "passport_aud_required" not in trust:
        log.warning("yai-drs: trust.passport_aud_required is not set: a passport without aud "
                    "is accepted (G-AAI-03)")  # fmt: skip


def _check_tes(config: dict[str, Any]) -> None:
    _range("max_active_tasks_per_user", config.get("max_active_tasks_per_user", 16), 0)
    limits = config.get("limits") or {}
    for key in ("max_inputs", "max_outputs"):
        _range(f"limits.{key}", limits.get(key, 100), 0)
    _bytes(config)
    _insecure("fetch", config.get("fetch") or {})
    mode = (config.get("auth") or {}).get("mode")
    if mode == "anonymous" and _runner(config) == "local-process":
        log.warning("yai-tes: auth.mode anonymous with the local-process runner: anyone who "
                    "reaches the API runs commands as this user")  # fmt: skip


def _check_tes_worker(config: dict[str, Any]) -> None:
    _bytes(config)
    _insecure("fetch", config.get("fetch") or {})
    runner = config.get("runner") or {}
    if _runner(config) == "local-process":
        log.warning("tes worker: the local-process runner has no isolation between "
                    "submitters (D-024)")  # fmt: skip
        return
    docker = runner.get("docker") or {}
    if str(docker.get("network", "none")) != "none":
        raise SystemExit("runner.docker.network must be none")
    if "user" in docker and not docker["user"]:
        log.warning("tes worker: runner.docker.user is empty: containers run as the image's "
                    "user")  # fmt: skip


def _bytes(config: dict[str, Any]) -> None:
    for key in ("max_input_bytes", "max_output_bytes"):
        _range(key, config.get(key, 10 * 1024**3), 1)


def _check_wes(config: dict[str, Any]) -> None:
    _range("max_active_runs_per_user", config.get("max_active_runs_per_user", 16), 0)
    fetch = config.get("fetch") or {}
    _insecure("fetch", fetch)
    # resolver targets: the same Fetcher check resolver.py uses at run time
    fetcher = Fetcher(allow=list(fetch.get("allow", [])),
                      insecure_http_hosts=list(fetch.get("insecure_http_hosts", [])))  # fmt: skip
    res = config.get("resolver") or {}
    urls = [
        (f"resolver.host_override.{k}", f"{v}/ga4gh/drs/v1")
        for k, v in (res.get("host_override") or {}).items()
    ]
    urls += [
        (f"resolver.compact.{k}", str(v).replace("{id}", "x"))
        for k, v in (res.get("compact") or {}).items()
    ]
    for where, url in urls:
        try:
            fetcher.check(url)
        except FetchError as exc:
            raise SystemExit(f"{where}: {exc.reason} (see fetch.allow)") from None
    # tes.url and tes.token_url have no run-time check, so http only for loopback
    tes = config.get("tes") or {}
    for key in ("url", "token_url"):
        if key not in tes:
            continue
        try:
            scheme, hp = host_port(str(tes[key]))
        except FetchError as exc:
            raise SystemExit(f"tes.{key}: {exc.reason}") from None
        if scheme == "http" and not is_loopback(_host(hp)):
            raise SystemExit(f"tes.{key}: plain http is allowed only for loopback")
        if scheme not in ("http", "https"):
            raise SystemExit(f"tes.{key} must be https")


def log_start(service: str, config: dict[str, Any], config_path: str | None) -> None:
    """The startup line (D-055 (7)). Only decided items. Paths use repr to escape newlines and
    control characters."""
    mode = (config.get("auth") or {}).get("mode", "-") if service in AUTH_MODES else "-"
    path = repr(str(Path(config_path).resolve())) if config_path else "-"
    data_dir = repr(str(Path(config.get("data_dir", f"var/{service.split('-')[0]}")).resolve()))
    log.info("yai-%s starting: auth.mode=%s config=%s data_dir=%s test_endpoints=%s",
             service, mode, path, data_dir, bool(config.get("test_endpoints", False)))  # fmt: skip
