#!/usr/bin/env python3
"""Onboard, package, and verify broodminder-data for PyPI publishing.

PyPI publishing and distribution verification:

1. Probes the PyPI registry API to determine package name availability.
2. Validates package metadata and builds PEP 517 sdist (.tar.gz) and wheel (.whl).
3. Verifies generated distributions using `twine check`.
4. Explains the PyPI Trusted Publishing (OIDC) configuration required for
   tokenless, automated GitHub Actions publishing.

Usage:
    python scripts/pypi_onboard.py
    python scripts/pypi_onboard.py --build
    python scripts/pypi_onboard.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PACKAGE_NAME = "broodminder-data"
PYPI_API_BASE = "https://pypi.org/pypi"

PENDING_PUBLISHER_HINT = (
    "PyPI Trusted Publishing requires a registered Pending Publisher before the\n"
    "first release can be published tokenlessly from GitHub Actions.\n\n"
    "Setup Steps (Account Owner):\n"
    "1. Go to: https://pypi.org/manage/account/publishing/\n"
    "2. Fill in the 'Add a pending publisher' form:\n"
    f"   - PyPI Project Name: {PACKAGE_NAME}\n"
    "   - Owner: petry-projects\n"
    f"   - Repository name: {PACKAGE_NAME}\n"
    "   - Workflow name: publish.yml\n"
    "   - Environment name: pypi\n"
    "3. Click 'Add publisher'.\n"
    "4. Once added, subsequent GitHub Actions release runs publish automatically\n"
    "   over OIDC with zero permanent tokens needed."
)


def _project_version_from_text(text: str) -> str:
    """Regex fallback for reading ``[project].version`` without a TOML parser.

    Used only when ``tomllib`` is unavailable (Python < 3.11). The search is
    scoped to the ``[project]`` table so a ``version`` key in a ``[tool.*]`` or
    dependency table cannot be mistaken for the package version (which would make
    the release workflow publish under a bogus tag).
    """
    section = re.search(r'^\[project\]\s*$(.*?)(?=^\[|\Z)', text, re.MULTILINE | re.DOTALL)
    body = section.group(1) if section else ""
    match = re.search(r'^\s*version\s*=\s*["\']([^"\']+)["\']', body, re.MULTILINE)
    return match.group(1) if match else ""


def get_package_version(pyproject_path: Path | None = None) -> str:
    """Read the package version string from pyproject.toml."""
    path = pyproject_path or Path(__file__).resolve().parent.parent / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib

        data = tomllib.loads(text)
        return str(data.get("project", {}).get("version", ""))
    except ImportError:
        return _project_version_from_text(text)


def should_release_version(version: str, existing_tags: list[str]) -> bool:
    """Return True if the version is non-empty and does not exist in the list of existing tags."""
    if not version:
        return False
    tag = f"v{version}"
    return tag not in existing_tags and version not in existing_tags


def check_pypi_status(package_name: str = PACKAGE_NAME) -> tuple[str, str]:
    """Query PyPI API to see if the package is available or already published."""
    url = f"{PYPI_API_BASE}/{package_name}/json"
    req = urllib.request.Request(url, headers={"User-Agent": f"pypi-onboard/{package_name}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            version = data.get("info", {}).get("version", "unknown")
            return "TAKEN", f"already registered on PyPI (latest version: {version})"
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return "AVAILABLE", "available for registration"
        return "UNKNOWN", f"HTTP {err.code}: {err.reason}"
    except Exception as exc:  # noqa: BLE001
        return "ERROR", str(exc)


def check_build_tools() -> list[str]:
    """Check required build and packaging CLI tools."""
    missing = []
    for tool in ("python3", "pip"):
        if not shutil.which(tool):
            missing.append(tool)
    return missing


def build_package(root_dir: Path) -> tuple[bool, str]:
    """Build sdist and wheel using `python -m build`."""
    cmd = [sys.executable, "-m", "build", str(root_dir)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if res.returncode != 0:
            return False, f"build failed:\n{res.stderr or res.stdout}"
        return True, res.stdout
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def verify_package(dist_dir: Path) -> tuple[bool, str]:
    """Verify built artifacts using `twine check`."""
    artifacts = list(dist_dir.glob("*.tar.gz")) + list(dist_dir.glob("*.whl"))
    if not artifacts:
        return False, "no artifacts found in dist/"
    cmd = [sys.executable, "-m", "twine", "check"] + [str(a) for a in artifacts]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            return False, f"twine check failed:\n{res.stderr or res.stdout}"
        return True, res.stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Onboard and verify PyPI package publishing.")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Validate without publishing (default)")
    parser.add_argument("--build", action="store_true", help="Build and verify distribution packages")
    parser.add_argument(
        "--package-version",
        "--version",
        action="store_true",
        dest="show_version",
        help="Print package version from pyproject.toml and exit",
    )
    args = parser.parse_args(argv)

    root_dir = Path(__file__).resolve().parent.parent
    dist_dir = root_dir / "dist"

    if args.show_version:
        version = get_package_version(root_dir / "pyproject.toml")
        print(version)
        return 0

    print(f"=== PyPI Onboarding: {PACKAGE_NAME} ===\n")

    # 1. Check registry availability
    print(f"1. Probing PyPI registry for `{PACKAGE_NAME}`...")
    status, detail = check_pypi_status(PACKAGE_NAME)
    if status == "AVAILABLE":
        print(f"   \u2705 Status: {status} ({detail})")
    elif status == "TAKEN":
        print(f"   \u2139\ufe0f Status: {status} ({detail})")
    else:
        print(f"   \u26a0\ufe0f Status: {status} ({detail})")

    # 2. Check local tools
    print("\n2. Checking build tools...")
    missing = check_build_tools()
    if missing:
        print(f"   \u274c Missing build tools: {', '.join(missing)}")
        return 1
    print("   \u2705 python and pip are available")

    # 3. Build & verify if requested or during dry run
    if args.build or args.dry_run:
        print("\n3. Building distribution packages (sdist + wheel)...")
        if dist_dir.exists():
            shutil.rmtree(dist_dir)
        ok, build_out = build_package(root_dir)
        if not ok:
            print(f"   \u274c Build error:\n{build_out}")
            return 1
        print("   \u2705 Build completed successfully")

        print("\n4. Verifying packages with twine check...")
        valid, twine_out = verify_package(dist_dir)
        if not valid:
            print(f"   \u274c Twine verification error:\n{twine_out}")
            return 1
        print(f"   \u2705 Twine check passed:\n     {twine_out}")

        artifacts = list(dist_dir.glob("*"))
        print("\n   Generated artifacts:")
        for a in artifacts:
            size_kb = a.stat().st_size / 1024
            print(f"     - {a.name} ({size_kb:.1f} KB)")

    # 4. Instructions for Trusted Publishing
    print("\n5. Trusted Publishing (OIDC) Setup:")
    print("=" * 60)
    print(PENDING_PUBLISHER_HINT)
    print("=" * 60)
    print("\nWorkflow File: .github/workflows/publish.yml")
    print("Action: pypa/gh-action-pypi-publish (standard PyPA Trusted Publisher)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
