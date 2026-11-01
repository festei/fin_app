"""Every `python -m <module>` command in the compose files must name a module that exists."""

import importlib.util
import re
import shlex
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def python_modules(compose: str) -> list[tuple[str, str]]:
    services = yaml.safe_load((ROOT / "deploy" / compose).read_text())["services"]
    found = []
    for name, svc in services.items():
        command = svc.get("command")
        if not command:
            continue
        argv = shlex.split(command) if isinstance(command, str) else list(command)
        if len(argv) >= 3 and argv[0] == "python" and argv[1] == "-m":
            found.append((name, argv[2]))
    return found


@pytest.mark.parametrize("compose", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_python_m_commands_name_importable_modules(compose):
    modules = python_modules(compose)
    assert modules, "expected python -m commands in the compose file"
    for service, module in modules:
        assert importlib.util.find_spec(module) is not None, f"{service}: no module {module}"


@pytest.mark.parametrize("compose", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_flows_service_serves_the_ingestion_schedules(compose):
    assert ("flows", "fin_flows.serve") in python_modules(compose)
    assert re.search(r"__main__", (ROOT / "flows/fin_flows/serve.py").read_text())


def test_web_proxy_adds_the_api_token_and_requires_basic_auth_in_prod():
    """The browser must never hold the write token: nginx adds it, and prod puts the UI behind
    basic auth (real nginx behaviour is not exercised by these tests)."""
    template = (ROOT / "deploy/nginx.conf.template").read_text()
    assert 'proxy_set_header Authorization "Bearer ${API_TOKEN}";' in template
    assert 'auth_basic "${AUTH_BASIC}";' in template and "auth_basic_user_file" in template
    health = template.split("location = /healthz")[1].split("}")[0]
    assert "auth_basic off;" in health  # only the health check skips auth
    assert (
        "COPY deploy/nginx.conf.template /etc/nginx/templates/"
        in (ROOT / "deploy/web.Dockerfile").read_text()
    )
    dev = yaml.safe_load((ROOT / "deploy/docker-compose.yml").read_text())["services"]["web"]
    prod = yaml.safe_load((ROOT / "deploy/docker-compose.prod.yml").read_text())["services"]["web"]
    assert dev["environment"]["AUTH_BASIC"] == "off"
    assert prod["environment"]["AUTH_BASIC"] != "off"
    assert "${API_TOKEN:?" in prod["environment"]["API_TOKEN"]
    assert any(v.startswith("${HTPASSWD_FILE:?") for v in prod["volumes"])
    for compose in (dev, prod):  # web and api must be given the same token variable
        assert "API_TOKEN" in compose["environment"]
