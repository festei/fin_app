"""Keep the Prefect client (locked in uv.lock) and server (compose image tags) on one version."""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def locked_prefect_version() -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    versions = [p["version"] for p in lock["package"] if p["name"] == "prefect"]
    assert len(versions) == 1, versions
    return versions[0]


@pytest.mark.parametrize("compose", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_compose_prefect_server_matches_locked_client(compose):
    text = (ROOT / "deploy" / compose).read_text()
    tags = re.findall(r"image:\s*prefecthq/prefect:(\S+)", text)
    assert tags, f"no prefect server image in {compose}"
    for tag in tags:
        assert tag.startswith(f"{locked_prefect_version()}-"), (
            f"{compose}: server image tag {tag!r} != locked prefect {locked_prefect_version()}"
        )
