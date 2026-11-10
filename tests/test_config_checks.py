"""Startup checks (D-055): refusals exit with a one-line SystemExit, WARNINGs log one line
to yai.startup."""

from __future__ import annotations

import logging
import os
import subprocess
import sys

import pytest

from yai.common import startup

AAI_AUTH = {"mode": "aai", "issuer": "http://127.0.0.1:1/", "audience": "yai-tes",
            "jwks_uri": "http://127.0.0.1:1/jwks.json"}  # fmt: skip


def refuses(service: str, config: dict, match: str) -> None:
    with pytest.raises(SystemExit, match=match) as exc:
        startup.check(service, config)
    assert "\n" not in str(exc.value.code)


@pytest.mark.parametrize("service", ["drs", "tes", "wes"])
def test_NEG_CONFIG_REFUSE_auth(service):
    """Startup fails without auth.mode (anonymous must be explicit). In aai mode, a missing issuer
    etc. fails with one line."""
    refuses(service, {}, "auth.mode is required")
    refuses(service, {"auth": {"issuer": "http://127.0.0.1:1/"}}, "auth.mode is required")
    for key in ("issuer", "jwks_uri", "audience"):
        auth = {k: v for k, v in AAI_AUTH.items() if k != key}
        refuses(service, {"auth": auth}, f"auth.{key} is required when auth.mode is aai")
    startup.check(service, {"auth": AAI_AUTH})


def run_yai(*args: str, env_config: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "YAI_CONFIG"}
    if env_config is not None:
        env["YAI_CONFIG"] = env_config
    return subprocess.run([sys.executable, "-m", "yai", *args], env=env, capture_output=True,
                          text=True, timeout=60)  # fmt: skip


def test_NEG_CONFIG_REFUSE_no_config_file(tmp_path):
    """With neither --config nor YAI_CONFIG, no command starts (checked with real startup)."""
    for args in (["serve", "tes", "--port", "9"], ["tes", "worker"], ["drs", "register", "x"]):
        r = run_yai(*args)
        assert r.returncode == 1 and r.stderr.strip() == "--config or YAI_CONFIG is required", r
    cfg = tmp_path / "tes.yaml"
    cfg.write_text("data_dir: d\n")
    r = run_yai("serve", "tes", "--config", str(cfg), "--port", "9")
    assert r.returncode == 1 and r.stderr.strip().startswith("auth.mode is required"), r


@pytest.mark.parametrize("service", ["drs", "tes", "wes"])
def test_NEG_CONFIG_WARN_anonymous(service, caplog):
    with caplog.at_level(logging.WARNING, logger="yai.startup"):
        startup.check(service, {"auth": {"mode": "anonymous"}})
    lines = [r.getMessage() for r in caplog.records if r.name == "yai.startup"]
    assert any("auth.mode is anonymous" in m for m in lines), lines


def test_startup_log_line(tmp_path, caplog):
    """The startup line: only the chosen fields. Paths are absolute, newlines are escaped to keep
    one line, and secrets never appear."""
    data_dir = tmp_path / "a\nb"
    cfg = {"auth": {"mode": "anonymous"}, "data_dir": str(data_dir),
           "clients": {"c": {"secret": "s" * 40}},
           "url_secret_file": str(tmp_path / "sec")}  # fmt: skip
    (tmp_path / "sec").write_text("t" * 40)
    with caplog.at_level(logging.INFO, logger="yai.startup"):
        startup.log_start("drs", cfg, "rel.yaml")
    (line,) = [r.getMessage() for r in caplog.records if r.name == "yai.startup"]
    assert "\n" not in line and "auth.mode=anonymous" in line
    assert repr(os.path.abspath("rel.yaml")) in line and "a\\nb" in line
    assert "s" * 40 not in line and "t" * 40 not in line


ANON = {"mode": "anonymous"}
SECRET = "x" * 32
AAI_OK = {"clients": {"c": {"secret": SECRET}}, "users": {"alice": {}}}
WES_OK = {"auth": ANON, "fetch": {"allow": ["drs.yai.test:443"],
                                  "insecure_http_hosts": ["127.0.0.1:8402"]}}  # fmt: skip
DOCKER = {"auth": ANON, "runner": {"kind": "docker", "docker": {"user": "1:1"}}}

REFUSE = [
    # AAI secrets: at least 32 characters and not the sample value (D-055 (4))
    ("aai", {"clients": {"c": {"secret": "x" * 31}}}, "clients.c.secret must be"),
    ("aai", {"clients": {"c": {"secret": "change-me" + "x" * 0}}}, "clients.c.secret must be"),
    ("aai", {"clients": {"c": {"secret": ""}}}, "clients.c.secret must be"),
    # Effective sub (or client_id) collides with a user name (P3-11)
    ("aai", {"clients": {"alice": {"secret": SECRET}}, "users": {"alice": {}}}, "sub must not"),
    (
        "aai",
        {"clients": {"c": {"secret": SECRET, "sub": "alice"}}, "users": {"alice": {}}},
        "sub must not",
    ),
    # ttl (P3-9)
    ("aai", {"ttl": {"access_token": 0}}, r"ttl.access_token must be between 1 and 86400"),
    ("aai", {"ttl": {"visa": 86401}}, "ttl.visa must be between"),
    ("aai", {"ttl": {"psat": "abc"}}, "ttl.psat must be an integer"),
    (
        "aai",
        {"users": {"alice": {"visas": [{"value": "v", "ttl": 10**9}]}}},
        r"users.alice.visas\[0\].ttl must be between",
    ),
    # DRS ranges (P3-9)
    ("drs", {"auth": ANON, "signed_url_ttl_seconds": 0}, "signed_url_ttl_seconds must be"),
    ("drs", {"auth": ANON, "signed_url_ttl_seconds": 31 * 10**8}, "signed_url_ttl_seconds"),
    ("drs", {"auth": ANON, "trust": {"clock_skew_seconds": 301}}, "clock_skew_seconds must"),
    ("drs", {"auth": ANON, "trust": {"clock_skew_seconds": -1}}, "clock_skew_seconds must"),
    ("drs", {"auth": ANON, "trust": {"max_visas_per_passport": 0}}, "max_visas_per_passport must"),
    # Key refetch (P3-10, N-7)
    (
        "drs",
        {"auth": ANON, "trust": {"key_refetch_min_interval_seconds": 0}},
        "trust.key_refetch_min_interval_seconds must be at least 1",
    ),
    ("drs", {"auth": ANON, "trust": {"key_cache_seconds": 30}}, "trust.key_cache_seconds must"),
    (
        "tes",
        {"auth": {**AAI_AUTH, "key_refetch_min_interval_seconds": 0}},
        "auth.key_refetch_min_interval_seconds",
    ),
    ("wes", {"auth": {**AAI_AUTH, "key_cache_seconds": 10}}, "auth.key_cache_seconds must"),
    # Negative values must not fall back to "no limit" (P3-9, D-053)
    ("tes", {"auth": ANON, "max_active_tasks_per_user": -1}, "max_active_tasks_per_user must"),
    # Do not coerce floats or infinity to int before checking (-0.5 passed as 0 = no limit)
    ("tes", {"auth": ANON, "max_active_tasks_per_user": -0.5}, "must be an integer"),
    ("wes", {**WES_OK, "max_active_runs_per_user": -0.01}, "must be an integer"),
    ("tes", {"auth": ANON, "max_active_tasks_per_user": float("inf")}, "must be an integer"),
    ("drs", {"auth": ANON, "signed_url_ttl_seconds": float("nan")}, "must be an integer"),
    ("aai", {"ttl": {"visa": "1e3"}}, "must be an integer"),
    ("tes", {"auth": ANON, "limits": {"max_inputs": -1}}, "limits.max_inputs must"),
    ("tes", {"auth": ANON, "max_input_bytes": 0}, "max_input_bytes must be at least 1"),
    ("tes-worker", {"max_output_bytes": 0}, "max_output_bytes must be at least 1"),
    ("wes", {**WES_OK, "max_active_runs_per_user": -1}, "max_active_runs_per_user must"),
    # Docker network (P3-6 (2))
    (
        "tes-worker",
        {"runner": {"kind": "docker", "docker": {"network": "host"}}},
        "runner.docker.network must be none",
    ),
    # The WES resolver uses the same Fetcher checks as at run time. Refused even
    # if only listed in the auth table
    (
        "wes",
        {
            **WES_OK,
            "auth": {**ANON, "insecure_http_hosts": ["127.0.0.1:9"]},
            "resolver": {"host_override": {"drs.yai.test": "http://127.0.0.1:9"}},
        },
        "resolver.host_override.drs.yai.test: only https is allowed",
    ),
    (
        "wes",
        {**WES_OK, "resolver": {"compact": {"p": "https://evil.example/objects/{id}"}}},
        "resolver.compact.p: host not allowed",
    ),
    ("aai", {"clients": {"c": {"secret": SECRET, "sub": "svc"}}}, "clients.c.sub must equal"),
    ("drs", {"auth": {**ANON, "accept_passport_scoped": True}}, "not a configuration key"),
    # the TES resolving drs:// itself (D-069) needs a client secret and safe targets
    (
        "tes",
        {"auth": ANON, "drs": {"enabled": True}},
        "drs.client_secret_file is required",
    ),
    (
        "tes",
        {
            "auth": ANON,
            "drs": {
                "enabled": True,
                "client_secret_file": "s",
                "token_url": "http://aai.yai.test/token",
            },
        },
        "drs.token_url: plain http",
    ),
    (
        "tes",
        {
            "auth": ANON,
            "drs": {
                "enabled": True,
                "client_secret_file": "s",
                "token_url": "https://aai.yai.test/token",
                "compact": {"p": "https://evil.example/objects/{id}"},
            },
        },
        "drs.compact.p: host not allowed",
    ),
    (
        "tes",
        {
            "auth": {**ANON, "audience": "yai-tes"},
            "drs": {
                "enabled": True,
                "client_id": "other",
                "client_secret_file": "s",
                "token_url": "https://aai.yai.test/token",
            },
        },
        "drs.service_name .default drs.client_id. must equal auth.audience",
    ),
    (
        "tes",
        {
            "auth": {**ANON, "audience": "yai-tes"},
            "drs": {
                "enabled": True,
                "client_id": "tes-client",
                "service_name": "other",
                "client_secret_file": "s",
                "token_url": "https://aai.yai.test/token",
            },
        },
        "drs.service_name .default drs.client_id. must equal auth.audience",
    ),
    # an empty tes.scopes would ask for every scope of the broker's client (D-069)
    ("wes", {**WES_OK, "tes": {"scopes": []}}, "tes.scopes must name at least one TES scope"),
    # the Passport scopes never come from tes.scopes, however they are written (D-069)
    (
        "wes",
        {**WES_OK, "tes": {"scopes": ["openid", "ga4gh_passport_v1"]}},
        "tes.scopes must name at least one TES scope",
    ),
    (
        "wes",
        {**WES_OK, "tes": {"scopes": ["openid ga4gh_passport_v1"]}},
        "tes.scopes must name at least one TES scope",
    ),
    # http for tes.url and tes.token_url is loopback only
    (
        "wes",
        {**WES_OK, "tes": {"url": "http://tes.example/ga4gh/tes/v1"}},
        "tes.url: plain http is allowed only for loopback",
    ),
    (
        "wes",
        {**WES_OK, "tes": {"token_url": "http://aai.yai.test/token"}},
        "tes.token_url: plain http",
    ),
]


@pytest.mark.parametrize(("service", "config", "match"), REFUSE)
def test_NEG_CONFIG_REFUSE(service, config, match):
    """Each "refuse" row of implementation plan 2.3 blocks startup (D-055 (3))."""
    if service == "aai":
        config = {**AAI_OK, **config}
    refuses(service, config, match)


WARN = [
    ("tes", {"auth": ANON}, "anonymous with the local-process runner"),
    ("tes-worker", {}, "no isolation between submitters"),
    ("tes-worker", {"runner": {"kind": "docker", "docker": {"user": ""}}}, "user is empty"),
    ("drs", {"auth": ANON, "trust": {}}, "passport_aud_required is not set"),
    (
        "drs",
        {
            "auth": {**ANON, "insecure_http_hosts": ["example.org:80"]},
            "trust": {"passport_aud_required": False},
        },
        "auth.insecure_http_hosts has",
    ),
    (
        "tes",
        {"auth": ANON, "fetch": {"insecure_http_hosts": ["yai.test.evil:80"]}},
        "fetch.insecure_http_hosts has yai.test.evil:80",
    ),
]


@pytest.mark.parametrize(("service", "config", "match"), WARN)
def test_NEG_CONFIG_WARN(service, config, match, caplog):
    """Each "WARNING" row logs one line and startup continues (D-055 (5))."""
    with caplog.at_level(logging.WARNING, logger="yai.startup"):
        startup.check(service, config)
    lines = [r.getMessage() for r in caplog.records if r.name == "yai.startup"]
    assert any(match in m for m in lines), lines


def test_NEG_CONFIG_WARN_quiet_for_loopback_and_test_names(caplog):
    cfg = {"auth": {**ANON, "insecure_http_hosts": ["127.0.0.1:8401", "[::1]:80", "a.yai.test:80"]},
           "trust": {"passport_aud_required": True}}  # fmt: skip
    with caplog.at_level(logging.WARNING, logger="yai.startup"):
        startup.check("drs", cfg)
        startup.check("tes-worker", DOCKER)
    lines = [r.getMessage() for r in caplog.records if r.name == "yai.startup"]
    assert lines == [
        "yai-drs: auth.mode is anonymous: every request is allowed without credentials"
    ]


def test_wes_tes_scopes_drop_passport_scopes_however_written():
    """D-069: the scopes the WES asks for never include the Passport scopes from configuration,
    whether listed separately or packed into one space-separated entry."""
    from yai.common.startup import wes_tes_scopes

    assert wes_tes_scopes({}) == ["tes.submit", "tes.read", "tes.cancel"]
    assert wes_tes_scopes({"scopes": ["tes.read openid", "ga4gh_passport_v1", "tes.cancel"]}) == [
        "tes.read", "tes.cancel"]  # fmt: skip
    assert wes_tes_scopes({"scopes": ["openid ga4gh_passport_v1"]}) == []


def test_NEG_CONFIG_RANGE_EDGES():
    """Boundaries: 0 keeps its current per-key meaning (no limit / accept none). Range ends pass."""
    startup.check("tes", {"auth": ANON, "max_active_tasks_per_user": 0,
                          "limits": {"max_inputs": 0, "max_outputs": 0}})  # fmt: skip
    startup.check("wes", {**WES_OK, "max_active_runs_per_user": 0})
    for ttl in (1, 86400):
        startup.check("aai", {**AAI_OK, "ttl": {"access_token": ttl, "visa": ttl}})
        startup.check("drs", {"auth": ANON, "signed_url_ttl_seconds": ttl})
    startup.check("drs", {"auth": ANON, "trust": {"clock_skew_seconds": 0,
                                                    "key_refetch_min_interval_seconds": 1,
                                                    "key_cache_seconds": 1}})  # fmt: skip
    startup.check("drs", {"auth": ANON, "trust": {"clock_skew_seconds": 300}})
    # Integer-valued floats and strings still pass as before (YAML 3600.0, "3600")
    startup.check("drs", {"auth": ANON, "signed_url_ttl_seconds": 3600.0})
    startup.check("aai", {**AAI_OK, "ttl": {"visa": "3600"}})
