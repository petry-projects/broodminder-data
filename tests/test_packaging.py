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

# Derive the expected version from project metadata so a version bump does not
# require editing hard-coded golden values across the packaging tests.
EXPECTED_VERSION = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["project"]["version"]


def test_pyproject_toml_structure():
    assert PYPROJECT_PATH.exists()
    content = PYPROJECT_PATH.read_text(encoding="utf-8")
    data = tomllib.loads(content)

    project = data.get("project", {})
    assert project.get("name") == "broodminder-data"
    assert isinstance(project.get("version"), str) and project.get("version")
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
    assert urls.get("Changelog") == "https://github.com/petry-projects/broodminder-data/blob/main/CHANGELOG.md"

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


def test_get_package_version(tmp_path: Path):
    from scripts.pypi_onboard import get_package_version

    # Current repo version, derived from metadata (no hard-coded golden value)
    assert get_package_version() == EXPECTED_VERSION

    # Custom pyproject
    custom_toml = tmp_path / "pyproject.toml"
    custom_toml.write_text('[project]\nname = "test"\nversion = "1.2.3"\n')
    assert get_package_version(custom_toml) == "1.2.3"


def test_project_version_from_text_scoped_to_project_section():
    """The regex fallback (Python < 3.11) must read [project].version only.

    A `version` key in an earlier [tool.*] table must not shadow the package
    version, otherwise the release workflow could publish under a bogus tag.
    """
    from scripts.pypi_onboard import _project_version_from_text

    toml_text = (
        '[tool.some_tool]\n'
        'version = "9.9.9"\n'
        '\n'
        '[project]\n'
        'name = "demo"\n'
        'version = "1.2.3"\n'
        '\n'
        '[tool.other]\n'
        'version = "0.0.0"\n'
    )
    assert _project_version_from_text(toml_text) == "1.2.3"
    assert _project_version_from_text('[tool.x]\nversion = "7.7.7"\n') == ""


def test_should_release_version():
    from scripts.pypi_onboard import should_release_version

    existing = ["v0.1.0", "v0.1.1", "v0.1.2"]
    assert should_release_version("0.1.2", existing) is False
    assert should_release_version("0.1.3", existing) is True
    assert should_release_version("", existing) is False
    assert should_release_version("0.1.0", ["0.1.0"]) is False


def test_pypi_onboard_version_cli(capsys: pytest.CaptureFixture):
    from scripts.pypi_onboard import main

    code = main(["--version"])
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == EXPECTED_VERSION


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
    assert "push:" in text
    assert "branches: [main]" in text

    # Permissions
    assert "id-token: write" in text
    assert "contents: read" in text
    assert "contents: write" in text

    # Environment
    assert "environment:" in text
    assert "name: pypi" in text

    # Checkout credentials must not be persisted (write token would otherwise be
    # exposed to build/dependency hooks).
    assert "persist-credentials: false" in text

    # Publication runs are serialized to avoid tag/release-creation races.
    assert "concurrency:" in text
    assert "group: pypi-publish" in text

    # Release events must match the packaged version before publishing.
    assert "RELEASE_TAG" in text

    # Build happens in a dedicated job that passes artifacts to the OIDC job.
    assert "upload-artifact" in text
    assert "download-artifact" in text

    # Action pinning to commit SHAs (no naked @v1 or @v4)
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("uses:"):
            # Format must be uses: owner/action@<40-char-sha> # <tag>
            parts = line.split("@", 1)
            assert len(parts) == 2, f"Action reference must be pinned with @: {line}"
            ref_part = parts[1].split()[0]
            assert len(ref_part) == 40, f"Action must be pinned to 40-character SHA: {line}"


def test_changelog_structure():
    changelog_path = ROOT / "CHANGELOG.md"
    assert changelog_path.exists()
    content = changelog_path.read_text(encoding="utf-8")
    assert "# Changelog" in content
    assert "## [Unreleased]" in content
    assert "## [0.1.4]" in content
    assert "## [0.1.3]" in content
    assert "## [0.1.2]" in content
    assert "## [0.1.1]" in content
    assert "## [0.1.0]" in content
    assert "[Unreleased]:" in content
    assert "[0.1.4]:" in content


