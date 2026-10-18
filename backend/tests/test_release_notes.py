"""Tests for extracting release notes from CHANGELOG.md."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_notes.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_section_stops_at_next_version() -> None:
    module = _load_script()
    changelog = """# Changelog

## [Unreleased]

### Added

- pending

## [0.2.1] - 2026-10-18

### Fixed

- shipped

## [0.2.0] - 2026-10-13

- old
"""

    assert module.extract_section(changelog, "0.2.1") == "### Fixed\n\n- shipped\n"


def test_extract_section_rejects_missing_or_empty_version() -> None:
    module = _load_script()
    with pytest.raises(ValueError, match="缺少"):
        module.extract_section("## [0.2.1] - 2026-10-18\n\n- ok\n", "0.3.0")
    with pytest.raises(ValueError, match="为空"):
        module.extract_section("## [0.2.1] - 2026-10-18\n\n## [0.2.0]\n", "0.2.1")
