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
