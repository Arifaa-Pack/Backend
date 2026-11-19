"""Fixtures for kx.cms tests.

The root tests/conftest.py is not an ancestor of this directory, so (like kx.agents) we load it by
path and re-export its `databases` fixture and the "skip @pytest.mark.db without KX_TEST_PG_DSN" hook.
Run from the repo root:  KX_TEST_PG_DSN=... python -m pytest packages/kx-cms/tests
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import ModuleType


def _load_root_conftest() -> ModuleType:
    path = pathlib.Path(__file__).resolve().parents[3] / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location("kx_root_conftest_cms", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_root = _load_root_conftest()
pg_databases = _root.pg_databases
databases = _root.databases
pytest_collection_modifyitems = _root.pytest_collection_modifyitems
