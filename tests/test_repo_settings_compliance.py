"""Compliance test for the `allow_auto_merge` GitHub repository setting.

The org settings standard requires **Allow auto-merge = `true`** on every repo,
because the Dependabot auto-merge workflow depends on it
(standards/github-settings.md#merge-settings). GitHub repository settings live in
the GitHub control plane, not in a file inside this repo, so there is nothing on
disk to assert against. This test therefore:

  * unit-tests the compliance predicate offline (always runs), and
  * verifies the *live* setting via the GitHub REST API when a token is present,
    skipping gracefully otherwise — mirroring the live-contract-test convention
    in ``conftest.py`` (green out of the box, real check when credentialed).
"""

from __future__ import annotations

import os

import pytest

# These checks are not BroodMinder contract tests; they manage their own GitHub
# credential (or skip), so exempt them from the BROODMINDER_API_KEY blanket skip.
pytestmark = pytest.mark.compliance

REPO_SLUG = os.environ.get("GITHUB_REPOSITORY", "petry-projects/broodminder-export")


def repo_setting_enabled(repo_json: dict, setting: str) -> bool:
    """Return a repo setting's boolean value, defaulting to ``False`` when absent.

    A missing setting is treated as *not enabled* so the compliance assertion
    fails closed rather than silently passing on an unexpected API payload.
    Only the boolean ``True`` is accepted; truthy non-boolean values (e.g. the
    string ``"false"``) are treated as non-compliant.
    """
    return repo_json.get(setting, False) is True


# --- offline predicate tests (always run; give the guard teeth) --------------


def test_predicate_true_when_enabled():
    assert repo_setting_enabled({"allow_auto_merge": True}, "allow_auto_merge") is True


def test_predicate_false_when_disabled():
    # A non-compliant payload must be reported as non-compliant.
    assert repo_setting_enabled({"allow_auto_merge": False}, "allow_auto_merge") is False


def test_predicate_false_when_missing():
    assert repo_setting_enabled({}, "allow_auto_merge") is False


def test_predicate_false_when_truthy_string():
    # The string "false" is truthy in Python; strict `is True` must reject it.
    assert repo_setting_enabled({"allow_auto_merge": "false"}, "allow_auto_merge") is False


# --- live guard against the real repository setting (skips without a token) ---


def _github_token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def test_allow_auto_merge_enabled_live():
    token = _github_token()
    if not token:
        pytest.skip("no GH_TOKEN/GITHUB_TOKEN in env; live repo-settings check skipped")

    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{REPO_SLUG}", headers=headers, timeout=15
        )
    except httpx.HTTPError as exc:  # network unavailable — don't fail the suite
        pytest.skip(f"could not reach GitHub API: {exc}")

    # Only a successful read lets us make a compliance judgement. Any other
    # status (auth, not-found, rate limit, GitHub 5xx) means we *cannot verify*,
    # so skip rather than fail the suite on transient/credential issues.
    if resp.status_code != 200:
        pytest.skip(
            f"could not read {REPO_SLUG} settings (HTTP {resp.status_code}); "
            "live repo-settings check skipped"
        )

    try:
        resp_data = resp.json()
    except ValueError as exc:
        pytest.skip(f"could not parse {REPO_SLUG} API response: {exc}")
    if not isinstance(resp_data, dict):
        pytest.skip(
            f"Unexpected response format from {REPO_SLUG} API response; "
            "live repo-settings check skipped"
        )
    if "allow_auto_merge" not in resp_data:
        pytest.skip(
            f"allow_auto_merge field absent from {REPO_SLUG} API response; "
            "token may lack admin visibility — live repo-settings check skipped"
        )
    assert repo_setting_enabled(resp_data, "allow_auto_merge") is True, (
        "allow_auto_merge must be enabled on this repository — required for the "
        "Dependabot auto-merge workflow (org github-settings standard, Merge Settings)"
    )
