"""Shape of the compose setup (D-035, D-037). Uses no Docker daemon.

- compose rules (no --privileged, host network or pid, no rootful docker.sock; the published
  port is >= 1024 and a variable; every bind mount source is a variable under YAI_LOCAL_DIR)
- setup.sh creates the CA, certificate, secrets, configs and copies of what is bind-mounted;
  the configs parse as YAML and the certificate verifies against the CA
- `docker compose config` if the docker CLI is there (no daemon needed)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
# the stage 3 partners (interop) follow the same rules
SOCK_DIR = "/run/user/1001/docker"  # a rootless daemon's socket directory (example)
FILES = ("compose.yaml", "compose.e2e.yaml", "interop/compose.interop.yaml")


def raw(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", FILES)
def test_compose_avoids_host_privileges(name):
    """Never mount the rootful daemon's socket (docker.sock under /var/run or /run). The rootless
    daemon's socket is mounted as its directory, from a variable (YAI_DOCKER_SOCK_DIR), read-only
    (D-037)."""
    doc = yaml.safe_load(raw(name))
    for svc_name, svc in doc["services"].items():
        assert not svc.get("privileged"), svc_name
        assert "network_mode" not in svc and "pid" not in svc and "ipc" not in svc, svc_name
        for v in svc.get("volumes") or []:
            src = str(v).split(":")[0]
            assert not re.match(r"/(var/)?run/docker\.sock", src), svc_name
            if "YAI_DOCKER_SOCK_DIR" in src:
                assert str(v).endswith(":ro"), svc_name
    text = re.sub(r"#.*", "", raw(name))
    assert "/var/run/docker.sock" not in text and "/run/docker.sock" not in text


def test_only_the_proxy_is_published_on_a_variable_port():
    doc = yaml.safe_load(raw("compose.yaml"))
    published = {k: v["ports"] for k, v in doc["services"].items() if v.get("ports")}
    assert list(published) == ["proxy"]
    (port,) = published["proxy"]
    assert port == "${YAI_BIND:-127.0.0.1}:${YAI_HTTPS_PORT:-8443}:443"


def test_bind_mount_paths_and_docker_host_are_variables():
    doc = yaml.safe_load(raw("compose.yaml"))
    worker = doc["services"]["tes-worker"]
    assert worker["environment"]["DOCKER_HOST"] == "${YAI_DOCKER_HOST:-}"
    assert worker["environment"]["YAI_TES_HOST_WORK_DIR"].startswith(
        "${YAI_TES_WORK_DIR_FOR_DOCKER"
    )


@pytest.mark.parametrize("name", FILES)
def test_every_bind_mount_source_is_a_variable(name):
    """Bind mount sources must be where the Docker daemon sees them (a checkout inside a
    development container may be invisible to the daemon). Pass them all as variables such as
    YAI_LOCAL_DIR, never as paths relative to the compose file (D-037)."""
    doc = yaml.safe_load(raw(name))
    for svc_name, svc in doc["services"].items():
        for v in svc.get("volumes") or []:
            src = str(v).split(":")[0]
            if src.startswith(("/", ".")):
                raise AssertionError(f"{svc_name}: bind mount without a variable: {v}")


def test_dockerfile_works_with_uid_0_and_sets_a_writable_docker_config():
    """With rootless Docker the user is 0:0 (D-034): 0 exists, so no useradd. USER is numeric"""
    dockerfile = raw("Dockerfile")
    assert 'if [ "${YAI_UID}" != 0 ]' in dockerfile
    assert re.search(r"^USER \$\{YAI_UID\}:\$\{YAI_GID\}$", dockerfile, re.M)
    assert "USER yai" not in dockerfile
    assert (
        "DOCKER_CONFIG=/tmp/docker" in dockerfile
    )  # where the docker CLI can write in a read_only image


@pytest.fixture(scope="module")
def local(tmp_path_factory):
    if not shutil.which("openssl"):
        pytest.skip("no openssl")
    out = tmp_path_factory.mktemp("deploy") / "local"
    env = dict(os.environ, YAI_LOCAL_DIR=str(out), YAI_UID="0", YAI_GID="0")
    subprocess.run(["sh", str(DEPLOY / "setup.sh")], env=env, check=True, capture_output=True)
    return out


def test_setup_makes_certs_secrets_and_configs(local):
    certs = local / "certs"
    subprocess.run(["openssl", "verify", "-CAfile", certs / "ca.pem", certs / "server.pem"],
                   check=True, capture_output=True)  # fmt: skip
    san = subprocess.run(
        ["openssl", "x509", "-in", certs / "server.pem", "-noout", "-ext", "subjectAltName"],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    for host in ("aai", "drs", "tes", "wes"):
        assert f"DNS:{host}.yai.test" in san
    assert not (certs / "ca-key.pem").exists()  # the CA key is not where containers see it
    secret = (local / "secrets" / "wes-client-secret").read_text().strip()
    configs = {s: yaml.safe_load((local / "config" / f"{s}.yaml").read_text()) for s in
               ("aai", "drs", "tes", "wes")}  # fmt: skip
    assert configs["aai"]["clients"]["yai-wes"]["secret"] == secret
    tes_secret = (local / "secrets" / "tes-client-secret").read_text().strip()
    assert configs["aai"]["clients"]["yai-tes"]["secret"] == tes_secret  # D-069
    assert configs["tes"]["drs"]["client_secret_file"].endswith("/tes-client-secret")
    assert configs["tes"]["runner"] == {"kind": "docker", "docker": {"user": "0:0"}}
    assert configs["drs"]["public_base_url"] == "https://drs.yai.test"  # 443(D-017)
    texts = "".join((local / "config" / f"{s}.yaml").read_text() for s in configs)
    assert not re.search(r"@[A-Z_]+@", texts)  # nothing left unreplaced
    env = (local / "compose.env").read_text()
    assert f"YAI_TES_WORK_DIR={local}/tes-work" in env
    assert f"YAI_DOCKER_SOCK_DIR={local}/docker-sock" in env  # an empty directory unless given
    assert "YAI_DOCKER_HOST=unix:///run/yai-docker/docker.sock" in env
    # copies of what compose bind-mounts (where the daemon sees them, D-037)
    caddyfile = (DEPLOY / "caddy" / "Caddyfile").read_text()
    assert (local / "caddy" / "Caddyfile").read_text() == caddyfile
    assert (local / "seed" / "seed.py").read_text() == (DEPLOY / "seed.py").read_text()
    for f in (DEPLOY / "testdata").iterdir():
        assert (local / "seed" / "testdata" / f.name).read_bytes() == f.read_bytes()
    # the two that containers write are 775 (keeps a default ACL's mask)
    for d in ("tes-work", "results"):
        assert (local / d).stat().st_mode & 0o777 == 0o775, d


@pytest.mark.parametrize("name", FILES)
def test_docker_compose_config_is_valid(local, name):
    if not shutil.which("docker"):
        pytest.skip("no docker CLI (skipping the shape check too)")
    proc = subprocess.run(
        ["docker", "compose", "-f", str(DEPLOY / name), "--env-file", str(local / "compose.env"),
         "config", "-q"], capture_output=True, text=True,
    )  # fmt: skip
    if "unknown" in proc.stderr and "compose" in proc.stderr:
        pytest.skip("no docker compose")
    assert proc.returncode == 0, proc.stderr


def test_image_makes_every_volume_target_for_the_service_user():
    """A named volume target missing from the image is created owned by root, and the service
    cannot write to it."""
    dockerfile = raw("Dockerfile")
    made = set(re.findall(r"/yai/[\w/]+", dockerfile.split("mkdir -p", 1)[1].split("chown")[0]))
    doc = yaml.safe_load(raw("compose.yaml"))
    for name, svc in doc["services"].items():
        if svc.get("image", "").startswith("${YAI_IMAGE") or "<<" in svc:
            for v in svc.get("volumes") or []:
                src, target = str(v).split(":")[:2]
                if not src.startswith(("$", ".", "/")) and target.startswith("/yai/var"):
                    assert target in made, f"{name}: {target} is not created in the Dockerfile"


def test_build_context_excludes_secrets_and_env():
    root = DEPLOY.parent
    ignore = (root / ".dockerignore").read_text().split()
    assert "deploy/local/" in ignore and ".git/" in ignore and ".venv/" in ignore
    assert "deploy/.env" in (root / ".gitignore").read_text().split()


@pytest.fixture(scope="module")
def interop_local(local):
    """deploy/interop/setup.sh (stage 3), run after deploy/setup.sh."""
    env = dict(os.environ, YAI_LOCAL_DIR=str(local), YAI_UID="0", YAI_GID="0",
               YAI_DOCKER_SOCK_DIR=SOCK_DIR)  # fmt: skip
    subprocess.run(["sh", str(DEPLOY / "interop" / "setup.sh")], env=env, check=True,
                   capture_output=True)  # fmt: skip
    return local / "interop"


def test_interop_setup_adds_partners_without_touching_the_rootful_socket(interop_local):
    """The interop configs (R-09), and Sapporo's run.sh rewritten for the rootless socket."""
    cfg = {s: yaml.safe_load((interop_local / "config" / f"{s}.yaml").read_text())
           for s in ("aai", "drs", "tes", "wes", "wes-funnel")}  # fmt: skip
    for s in ("tes", "wes", "wes-funnel"):
        assert "pgdrs.yai.test:443" in cfg[s]["fetch"]["allow"]
        assert cfg[s]["fetch"]["insecure_http_hosts"] == [
            "pgdrs.yai.test:8645"
        ]  # the partner always uses http
    assert cfg["wes"]["tes"]["url"] == "https://tes.yai.test/ga4gh/tes/v1"
    assert cfg["wes-funnel"]["tes"]["url"] == "https://funnel.yai.test/ga4gh/tes/v1"
    # no Passport is sent to partners
    assert cfg["wes"]["resolver"]["passport_hosts"] == ["drs.yai.test"]
    assert any(b["iss"] == "https://broker.yai.test/" for b in cfg["drs"]["trust"]["brokers"])
    run_sh = (interop_local / "sapporo" / "run.sh").read_text()
    assert "/var/run/docker.sock" not in run_sh
    assert f"-v {SOCK_DIR}:/run/yai-docker:ro" in run_sh
    assert "DOCKER_API_VERSION=1.41" in run_sh  # daemon 20.10.13
    funnel = yaml.safe_load((interop_local / "funnel" / "funnel.yaml").read_text())
    assert funnel["Worker"]["WorkDir"] == str(interop_local / "funnel-work")
    assert "/yai/var/tes/files" in funnel["LocalStorage"]["AllowedDirs"]
    for d in ("funnel-work", "sapporo-runs", "out"):
        assert (interop_local / d).stat().st_mode & 0o777 == 0o775, d


SERVICE_OF = {"aai": "aai", "drs": "drs", "tes": "tes", "wes": "wes", "wes-funnel": "wes"}


def test_NEG_CONFIG_SHIPPED(local, interop_local):
    """The deployed (setup.sh output), interop and example configs pass the startup checks.
    Only the example aai is refused, as its secret is "" (it does not start as copied, D-055)."""
    from yai.common import startup

    for base, names in ((local / "config", ("aai", "drs", "tes", "wes")),
                        (interop_local / "config", tuple(SERVICE_OF))):  # fmt: skip
        for name in names:
            cfg = yaml.safe_load((base / f"{name}.yaml").read_text())
            startup.check(SERVICE_OF[name], cfg)
            if name == "tes":
                startup.check("tes-worker", cfg)
    examples = DEPLOY.parent / "config"
    for name in ("drs", "tes", "wes"):
        cfg = yaml.safe_load((examples / f"{name}.example.yaml").read_text())
        startup.check(name, cfg)
        if name == "tes":
            startup.check("tes-worker", cfg)
    aai = yaml.safe_load((examples / "aai.example.yaml").read_text())
    with pytest.raises(SystemExit, match="secret must be"):
        startup.check("aai", aai)
