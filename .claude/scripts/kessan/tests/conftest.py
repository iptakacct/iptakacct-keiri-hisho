import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import load_accounts  # noqa: E402
from common import init_year_dir  # noqa: E402


@pytest.fixture
def accounts():
    return load_accounts()


@pytest.fixture
def year_dir(tmp_path):
    d = tmp_path / "2026-03期"
    init_year_dir(d)
    return d
