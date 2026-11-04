"""The repository's own bookkeeping: nothing that has tests may be left out of the default run.

A test directory missing from ``testpaths`` is silent: its tests simply never run, and every green
check afterwards means less than it seems to. This once hid the engine-parity tests.
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_every_directory_with_tests_is_in_the_default_test_run():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    testpaths = set(config["tool"]["pytest"]["ini_options"]["testpaths"])
    with_tests = {
        str(p.parent.relative_to(ROOT))
        for pattern in (
            "packages/*/tests/test_*.py",
            "apps/*/tests/test_*.py",
            "flows/tests/test_*.py",
        )
        for p in ROOT.glob(pattern)
    }
    assert with_tests, "found no test directories: is the layout assumption still right?"
    assert with_tests - testpaths == set(), "tests that never run by default"
    assert {p for p in testpaths if not (ROOT / p).is_dir()} == set(), "testpaths that do not exist"
