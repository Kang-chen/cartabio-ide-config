"""Shared fixtures. Makes ``scripts/`` importable and hands out fixture runs."""
from __future__ import annotations

import pathlib
import sys

import pytest

# This file now lives at ``assets/eval/conftest.py`` (moved from ``tests/``), so
# the skill root is two levels up and ``fixture_run`` lives beside this file.
HERE = pathlib.Path(__file__).resolve().parent          # assets/eval
ROOT = HERE.parent.parent                                # skill root
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(HERE))

import fixture_run  # noqa: E402


@pytest.fixture
def run_root(tmp_path):
    """A complete synthetic run with every shipped defect represented."""
    return fixture_run.build(tmp_path / "run")


@pytest.fixture
def make_run(tmp_path):
    """Factory for runs with non-default shapes (no narratives, quick mode…)."""
    counter = {"n": 0}

    def _make(**kwargs):
        counter["n"] += 1
        return fixture_run.build(tmp_path / f"run{counter['n']}", **kwargs)

    return _make


@pytest.fixture
def model(run_root):
    from report_model import build_model, load_contract
    return build_model(run_root, load_contract())
