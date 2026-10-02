"""Unit tests for packaging metadata, PyPI onboarding script, and publish workflow."""
from __future__ import annotations

import tomllib
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.pypi_onboard import (
    PACKAGE_NAME,
    PENDING_PUBLISHER_HINT,
    check_build_tools,
    check_pypi_status,
)

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = ROOT / "pyproject.toml"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"


def test_pyproject_toml_structure():
    assert PYPROJECT_PATH.exists()
    content = PYPROJECT_PATH.read_text(encoding="utf-8")
    data = tomllib.loads(content)

    project = data.get("project", {})
    assert project.get("name") == "broodminder-data"
    assert project.get("version") == "0.1.0"
    assert project.get("license") == "MIT"
    assert "broodminder" in project.get("keywords", [])
    assert "mcp" in project.get("keywords", [])

    authors = project.get("authors", [])
    assert len(authors) > 0
    assert authors[0].get("name") == "Don Petry"

    classifiers = project.get("classifiers", [])
    assert "License :: OSI Approved :: MIT License" not in classifiers  # PEP 639
    assert "Programming Language :: Python :: 3" in classifiers

    urls = project.get("urls", {})
    assert "https://github.com/petry-projects/broodminder-data" in urls.get("Homepage", "")
    assert "https://github.com/petry-projects/broodminder-data" in urls.get("Repository", "")

    opt_deps = project.get("optional-dependencies", {})
    assert "mcp" in opt_deps
    assert "build" in opt_deps
    assert "dev" in opt_deps


def test_check_pypi_status_available():
    mock_err = urllib.error.HTTPError(
        url="https://pypi.org/pypi/broodminder-data/json",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=mock_err):
        status, detail = check_pypi_status(PACKAGE_NAME)
        assert status == "AVAILABLE"
        assert "available" in detail.lower()


def test_check_pypi_status_taken():
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"info": {"version": "0.1.0"}}'
    mock_resp.__enter__.return_value = mock_resp
    with patch("urllib.request.urlopen", return_value=mock_resp):
        status, detail = check_pypi_status(PACKAGE_NAME)
        assert status == "TAKEN"
        assert "0.1.0" in detail


def test_check_build_tools():
    missing = check_build_tools()
    assert isinstance(missing, list)


def test_pending_publisher_hint_content():
    assert PACKAGE_NAME in PENDING_PUBLISHER_HINT
    assert "petry-projects" in PENDING_PUBLISHER_HINT
    assert "publish.yml" in PENDING_PUBLISHER_HINT
    assert "pypi" in PENDING_PUBLISHER_HINT


def test_publish_workflow_structure():
    assert PUBLISH_WORKFLOW.exists(), "publish.yml workflow must exist"
    text = PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    # Workflow triggers
    assert "release:" in text
    assert "workflow_dispatch:" in text
    assert "dry_run:" in text

    # Permissions
    assert "id-token: write" in text
    assert "contents: read" in text

    # Environment
    assert "environment:" in text
    assert "name: pypi" in text

    # Action pinning to commit SHAs (no naked @v1 or @v4)
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("uses:"):
            # Format must be uses: owner/action@<40-char-sha> # <tag>
            parts = line.split("@", 1)
            assert len(parts) == 2, f"Action reference must be pinned with @: {line}"
            ref_part = parts[1].split()[0]
            assert len(ref_part) == 40, f"Action must be pinned to 40-character SHA: {line}"
