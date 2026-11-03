"""The protected core (fin_domain, fin_application) must stay free of infrastructure.

Three independent checks, so the protection does not rest on a single tool:
the declared dependencies (what can be installed), the imports in the source (what is used), and
a self-test of the checker. import-linter (`uv run lint-imports`) and ruff (TID251) enforce the
same rule from the outside. See docs/specs/009-hexagonal-redesign.md.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2]
DOMAIN = PACKAGES / "domain"
APPLICATION = PACKAGES / "application"

# Third-party and standard-library modules that mean I/O, a framework or configuration.
INFRASTRUCTURE = {
    "sqlalchemy",
    "alembic",
    "psycopg",
    "httpx",
    "requests",
    "pandas",
    "numpy",
    "yfinance",
    "yaml",
    "langgraph",
    "fastapi",
    "starlette",
    "prefect",
    "pydantic",
    "pydantic_settings",
    "os",
    "pathlib",
    "sqlite3",
    "socket",
    "urllib",
}
# Everything else in this repository sits outside the core.
OUTSIDE_THE_CORE = {
    "fin_core",
    "fin_persistence",
    "fin_providers",
    "fin_telegram",
    "fin_langgraph",
    "fin_config",
    "fin_bootstrap",
    "fin_testkit",
    "fin_api",
    "fin_worker",
    "fin_flows",
}


def forbidden_imports(source: str, forbidden: set[str]) -> list[str]:
    """Top-level modules ``source`` imports that are in ``forbidden`` (absolute imports)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        found += [n for n in names if n.split(".")[0] in forbidden]
    return found


def violations(package_dir: Path, forbidden: set[str]) -> dict[str, list[str]]:
    result = {}
    for path in sorted((package_dir / "src").rglob("*.py")):
        bad = forbidden_imports(path.read_text(), forbidden)
        if bad:
            result[str(path.relative_to(package_dir))] = bad
    return result


def declared_dependencies(package_dir: Path) -> list[str]:
    """Distribution names from ``[project].dependencies``, version specifiers dropped."""
    data = tomllib.loads((package_dir / "pyproject.toml").read_text())
    return [
        re.split(r"[\s<>=!~\[;]", dep, maxsplit=1)[0].lower()
        for dep in data["project"]["dependencies"]
    ]


def test_domain_declares_no_dependencies():
    assert declared_dependencies(DOMAIN) == []


def test_application_depends_on_the_domain_only():
    if not APPLICATION.exists():
        return
    assert declared_dependencies(APPLICATION) == ["fin-domain"]


def test_domain_source_imports_no_infrastructure_and_nothing_outside_the_core():
    assert violations(DOMAIN, INFRASTRUCTURE | OUTSIDE_THE_CORE | {"fin_application"}) == {}


def test_application_source_imports_no_infrastructure_and_nothing_outside_the_core():
    if not APPLICATION.exists():
        return
    assert violations(APPLICATION, INFRASTRUCTURE | OUTSIDE_THE_CORE) == {}


def test_checker_flags_what_it_should_and_allows_the_rest():
    bad = "import sqlalchemy.orm\nfrom os import path\nimport fin_persistence.tables\n"
    assert forbidden_imports(bad, INFRASTRUCTURE | OUTSIDE_THE_CORE) == [
        "sqlalchemy.orm",
        "os",
        "fin_persistence.tables",
    ]
    ok = (
        "from datetime import datetime\n"
        "from fin_domain.shared import Ticker\n"
        "from . import sibling\n"
    )
    assert forbidden_imports(ok, INFRASTRUCTURE | OUTSIDE_THE_CORE) == []
