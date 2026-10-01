"""Compliance guard for the `permissions:` scopes declared in `ci.yml`.

Fleet Monitor flagged `ci.yml` as DEGRADED (issue #96) on a 1/2 failure rate. The
sole failing run ([`31516657667`]) was a **startup failure**, not a test flake:
`created_at == updated_at`, zero jobs, and GitHub's "this run likely failed because
of a workflow file issue" banner. At that commit the `build-and-test` job declared::

    permissions:
      contents: read
      administration: read   # <- not a valid GITHUB_TOKEN permission scope

`administration` is **not** one of GitHub's accepted `GITHUB_TOKEN` permission keys,
so GitHub rejected the workflow at parse time and failed the run before any job
started. Because a startup failure is surfaced *before* branch filters are applied,
it produced a failed `ci.yml` run even though `on.push.branches` is `[main]`.

This guard pins that every key in every `permissions:` block of `ci.yml` is a
documented `GITHUB_TOKEN` scope, so an invalid scope is caught in-suite (at PR time)
instead of as a red startup-failure run.

Ref: https://docs.github.com/actions/using-jobs/assigning-permissions-to-jobs

Like ``test_ci_compliance.py`` / ``test_ci_resilience.py`` this is a text-based check
so it runs with only ``requirements.txt`` installed (no YAML parser in CI).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

# The complete set of permission scopes GitHub accepts under a workflow- or
# job-level `permissions:` mapping for the GITHUB_TOKEN. Any other key is rejected
# at parse time and fails the run at startup.
# Ref: https://docs.github.com/actions/using-jobs/assigning-permissions-to-jobs
VALID_PERMISSION_SCOPES = frozenset(
    {
        "actions",
        "attestations",
        "checks",
        "contents",
        "deployments",
        "discussions",
        "id-token",
        "issues",
        "models",
        "packages",
        "pages",
        "pull-requests",
        "repository-projects",
        "security-events",
        "statuses",
    }
)

# A block-form `permissions:` header (top-level or job-level): nothing follows the
# colon. Inline forms (`permissions: {}`, `read-all`, `write-all`) carry no scope
# keys and are always valid, so they intentionally do not match.
_PERMISSIONS_HEADER = re.compile(r"^(?P<indent>\s*)permissions:\s*$")
# A scope entry inside a block: `<key>: read|write|none`, with optional quotes around the value.
_SCOPE_ENTRY = re.compile(r"^(?P<indent>\s+)(?P<key>[A-Za-z][\w-]*):\s*['\"]?(read|write|none)['\"]?\s*$")


def _ci_text() -> str:
    assert CI_WORKFLOW.exists(), f"{CI_WORKFLOW} is missing"
    return CI_WORKFLOW.read_text(encoding="utf-8")


def _permission_keys(text: str) -> list[str]:
    """Return every permission key declared in a block-form `permissions:` mapping.

    Inline forms that carry no keys — `permissions: {}`, `permissions: read-all`,
    `permissions: write-all` — contribute nothing (they are always valid). Comments
    and blank lines inside a block are skipped; a block ends at the first line
    indented no deeper than its `permissions:` header.
    """
    keys: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        header = _PERMISSIONS_HEADER.match(lines[i])
        if not header:
            i += 1
            continue
        header_indent = len(header.group("indent"))
        i += 1
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                i += 1
                continue
            indent = len(line) - len(line.lstrip())
            if indent <= header_indent:
                break  # dedent to the header level or shallower → block is over
            entry = _SCOPE_ENTRY.match(line)
            if entry:
                keys.append(entry.group("key"))
            i += 1
    return keys


def _invalid_permission_keys(text: str) -> list[str]:
    """Permission keys present in ``text`` that are not documented GITHUB_TOKEN scopes."""
    return sorted({k for k in _permission_keys(text) if k not in VALID_PERMISSION_SCOPES})


def _job_with_scope(scope: str) -> str:
    """A minimal workflow whose one job requests ``scope`` under `permissions:`."""
    return (
        "name: CI\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "permissions: {}\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    permissions:\n"
        "      contents: read\n"
        f"      {scope}: read\n"
        "    steps:\n"
        "      - run: true\n"
    )


# --- the real ci.yml must only request valid scopes --------------------------


def test_ci_yml_declares_permission_scopes():
    """Sanity: the parser actually finds the scopes `ci.yml` declares, so the
    validity check below can't pass vacuously by parsing nothing."""
    keys = _permission_keys(_ci_text())
    assert "contents" in keys, (
        "expected `ci.yml` to declare `contents:` under a job `permissions:` block; "
        "if the parser finds no scopes the validity guard would pass vacuously"
    )


def test_ci_yml_uses_only_valid_permission_scopes():
    invalid = _invalid_permission_keys(_ci_text())
    assert invalid == [], (
        f"ci.yml requests invalid GITHUB_TOKEN permission scope(s) {invalid!r}; an "
        "unrecognized key fails the workflow at startup before any job runs "
        "(Fleet Monitor issue #96 remediation)"
    )


# --- the guard must reject invalid scopes ------------------------------------


def test_guard_rejects_administration_scope():
    """The exact scope that caused issue #96's startup failure must be rejected."""
    assert "administration" in _invalid_permission_keys(_job_with_scope("administration")), (
        "`administration` is not a valid GITHUB_TOKEN scope and must be flagged — "
        "it is the invalid key that failed ci.yml at startup (issue #96)"
    )


@pytest.mark.parametrize(
    "scope",
    ["administration", "admin", "repo", "workflows", "secrets", "environments", "members"],
)
def test_guard_rejects_unknown_scopes(scope):
    assert scope in _invalid_permission_keys(_job_with_scope(scope)), (
        f"'{scope}' is not a documented GITHUB_TOKEN permission scope and must be flagged"
    )


@pytest.mark.parametrize("scope", sorted(VALID_PERMISSION_SCOPES))
def test_guard_accepts_documented_scopes(scope):
    assert _invalid_permission_keys(_job_with_scope(scope)) == [], (
        f"'{scope}' is a documented GITHUB_TOKEN scope and must not be flagged"
    )


@pytest.mark.parametrize("form", ["permissions: {}", "permissions: read-all", "permissions: write-all"])
def test_guard_tolerates_valid_inline_permission_forms(form):
    """`{}` / `read-all` / `write-all` carry no scope keys and must not be flagged."""
    text = f"name: CI\n{form}\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: true\n"
    assert _permission_keys(text) == []
    assert _invalid_permission_keys(text) == []


@pytest.mark.parametrize("value_form", ['read', '"read"', "'read'", 'write', '"write"', "'write'", 'none', '"none"', "'none'"])
def test_guard_accepts_quoted_permission_values(value_form):
    """The parser must accept permission values with optional quotes (single or double)."""
    text = (
        "name: CI\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "permissions: {}\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    permissions:\n"
        f"      contents: {value_form}\n"
        "    steps:\n"
        "      - run: true\n"
    )
    keys = _permission_keys(text)
    assert "contents" in keys, f"parser must recognize permission with value form: {value_form}"
    assert _invalid_permission_keys(text) == [], f"valid permissions must not be flagged for value form: {value_form}"
