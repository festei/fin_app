"""Fixture loader for the provider tests.

Deliberately not a ``conftest.py``: pytest registers every ``conftest.py`` as the top-level module
``conftest``, and a core test imports ``bar`` from the core one by that name.
"""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_json():
    def load(name: str):
        return json.loads((FIXTURES / name).read_text())

    return load
