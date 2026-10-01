"""Compliance test for the `pr-quality` ruleset's `dismiss_stale_reviews_on_push`.

The org rulesets standard codifies the `pr-quality` ruleset in
`standards/rulesets/pr-quality.json` (source of truth), which requires the
pull-request rule's **`dismiss_stale_reviews_on_push` = `true`** so that a push
to a PR branch dismisses previously-approving reviews and forces re-review of
the new code (standards/github-settings.md#pr-quality--standard-ruleset). The
org's `scripts/apply-rulesets.sh` converges the live ruleset; this repo cannot
mutate the GitHub control plane from its working tree, so — mirroring
``test_repo_settings_compliance.py`` — this guard:

  * unit-tests the compliance predicate offline (always runs, fails closed), and
  * verifies the *live* ruleset via the GitHub Rulesets REST API when a token is
    present, skipping gracefully otherwise (green out of the box, real check when
    credentialed).

A ruleset lives in the GitHub control plane, not on disk, so there is nothing
static to assert against; the live half is the real drift guard and the offline
predicate gives it teeth. This is a regression guard for issue #91.

Ref: petry-projects/.github/standards/github-settings.md#pr-quality--standard-ruleset-all-repositories
"""

from __future__ import annotations

import os

import pytest

# Not a BroodMinder contract test; manages its own GitHub credential (or skips),
# so exempt from the BROODMINDER_API_KEY blanket skip.
pytestmark = pytest.mark.compliance

REPO_SLUG = os.environ.get("GITHUB_REPOSITORY", "petry-projects/broodminder-export")

# The codified `pr-quality` ruleset name and the required pull-request parameter.
RULESET_NAME = "pr-quality"
PARAM = "dismiss_stale_reviews_on_push"


def pr_rule_param_enabled(ruleset_json: dict, param: str) -> bool:
    """Return a ``pull_request`` rule parameter's boolean value from a ruleset.

    Given the JSON of a single ruleset (as returned by
    ``GET /repos/{repo}/rulesets/{id}``), locate the ``pull_request`` rule in its
    ``rules`` array and return ``param``'s value, defaulting to ``False`` when the
    ruleset, the rule, or the parameter is absent. Fails closed: only the boolean
    ``True`` counts as enabled, so a missing rule or a truthy non-boolean (e.g. the
    string ``"true"``) is reported as non-compliant rather than silently passing.
    """
    if not isinstance(ruleset_json, dict):
        return False
    rules = ruleset_json.get("rules")
    if not isinstance(rules, list):
        return False
    for rule in rules:
        if isinstance(rule, dict) and rule.get("type") == "pull_request":
            params = rule.get("parameters")
            if isinstance(params, dict):
                return params.get(param, False) is True
    return False


# --- offline predicate tests (always run; give the guard teeth) --------------


def _ruleset_with_param(value):
    """Build a minimal ruleset payload whose pull_request rule sets PARAM=value."""
    return {
        "name": RULESET_NAME,
        "rules": [
            {"type": "required_linear_history", "parameters": {}},
            {"type": "pull_request", "parameters": {PARAM: value}},
        ],
    }


def test_predicate_true_when_enabled():
    assert pr_rule_param_enabled(_ruleset_with_param(True), PARAM) is True


def test_predicate_false_when_disabled():
    # A non-compliant payload must be reported as non-compliant.
    assert pr_rule_param_enabled(_ruleset_with_param(False), PARAM) is False


def test_predicate_false_when_param_missing():
    ruleset = {"name": RULESET_NAME, "rules": [{"type": "pull_request", "parameters": {}}]}
    assert pr_rule_param_enabled(ruleset, PARAM) is False


def test_predicate_false_when_pull_request_rule_missing():
    ruleset = {"name": RULESET_NAME, "rules": [{"type": "deletion", "parameters": {}}]}
    assert pr_rule_param_enabled(ruleset, PARAM) is False


def test_predicate_false_when_rules_absent():
    assert pr_rule_param_enabled({"name": RULESET_NAME}, PARAM) is False


def test_predicate_false_when_rules_scalar():
    assert pr_rule_param_enabled({"rules": 1}, PARAM) is False


def test_predicate_false_when_truthy_string():
    # The string "true" is truthy in Python; strict `is True` must reject it.
    assert pr_rule_param_enabled(_ruleset_with_param("true"), PARAM) is False


def test_predicate_false_when_ruleset_not_dict():
    assert pr_rule_param_enabled([], PARAM) is False
    assert pr_rule_param_enabled(None, PARAM) is False


def test_predicate_false_when_rule_not_dict():
    ruleset = {"name": RULESET_NAME, "rules": ["pull_request", None, 42]}
    assert pr_rule_param_enabled(ruleset, PARAM) is False


def test_predicate_false_when_parameters_not_dict():
    ruleset = {"name": RULESET_NAME, "rules": [{"type": "pull_request", "parameters": "bad"}]}
    assert pr_rule_param_enabled(ruleset, PARAM) is False


# --- live guard against the real ruleset (skips without a token) --------------


def _github_token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def test_pr_quality_dismiss_stale_reviews_on_push_live():
    token = _github_token()
    if not token:
        pytest.skip("no GH_TOKEN/GITHUB_TOKEN in env; live ruleset check skipped")

    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # Auth/permission failures mean the token is configured but unusable — that is a
    # setup error, not a transient issue, so we fail rather than silently skip.
    # Other non-200 responses (rate limit, GitHub 5xx, 404) are genuinely transient
    # or environmental, so we skip to avoid false positives on unrelated outages.
    _AUTH_ERRORS = {401, 403}

    # Follow all ruleset-list pages (per_page=100) until pr-quality is found.
    matching_ruleset = None
    next_url: str | None = f"https://api.github.com/repos/{REPO_SLUG}/rulesets"
    request_params: dict = {"per_page": 100}
    while next_url is not None and matching_ruleset is None:
        try:
            listing = httpx.get(next_url, headers=headers, params=request_params, timeout=15)
        except httpx.HTTPError as exc:  # network unavailable — don't fail the suite
            pytest.skip(f"could not reach GitHub API: {exc}")

        if listing.status_code != 200:
            if listing.status_code in _AUTH_ERRORS:
                pytest.fail(
                    f"GitHub API returned HTTP {listing.status_code} listing {REPO_SLUG} rulesets; "
                    "verify the token has 'Metadata' (read) repository permission"
                )
            pytest.skip(
                f"could not list {REPO_SLUG} rulesets (HTTP {listing.status_code}); "
                "live ruleset check skipped"
            )

        try:
            listing_data = listing.json()
        except Exception as exc:
            pytest.skip(f"could not parse GitHub API response: {exc}")
        if not isinstance(listing_data, list):
            pytest.skip(
                f"Unexpected response format from GitHub API (expected list, got {type(listing_data).__name__})"
            )

        matching_ruleset = next(
            (rs for rs in listing_data if isinstance(rs, dict) and rs.get("name") == RULESET_NAME),
            None,
        )

        # Advance to the next page (Link header) unless the ruleset was already found.
        link_header = listing.headers.get("Link", "")
        next_url = None
        request_params = {}
        for part in link_header.split(","):
            part = part.strip()
            if 'rel="next"' in part:
                url_part = part.split(";")[0].strip()
                if url_part.startswith("<") and url_part.endswith(">"):
                    next_url = url_part[1:-1]
                    break

    assert matching_ruleset is not None, (
        f"repository {REPO_SLUG} has no '{RULESET_NAME}' ruleset — the org standard "
        "requires it (github-settings.md#pr-quality); run "
        f"scripts/apply-rulesets.sh --repo {REPO_SLUG} to converge"
    )
    ruleset_id = matching_ruleset.get("id")
    assert ruleset_id is not None, (
        f"'{RULESET_NAME}' ruleset found but missing 'id' field (malformed API response)"
    )

    try:
        detail = httpx.get(
            f"https://api.github.com/repos/{REPO_SLUG}/rulesets/{ruleset_id}",
            headers=headers,
            timeout=15,
        )
    except httpx.HTTPError as exc:
        pytest.skip(f"could not reach GitHub API: {exc}")

    if detail.status_code != 200:
        if detail.status_code in _AUTH_ERRORS:
            pytest.fail(
                f"GitHub API returned HTTP {detail.status_code} reading '{RULESET_NAME}' ruleset; "
                "verify the token has 'Metadata' (read) repository permission"
            )
        pytest.skip(
            f"could not read '{RULESET_NAME}' ruleset detail (HTTP {detail.status_code}); "
            "live ruleset check skipped"
        )

    try:
        detail_data = detail.json()
    except Exception as exc:
        pytest.skip(f"could not parse GitHub API response: {exc}")
    assert pr_rule_param_enabled(detail_data, PARAM) is True, (
        f"ruleset '{RULESET_NAME}' parameter '{PARAM}' must be true on {REPO_SLUG} — "
        "the codified standard (standards/rulesets/pr-quality.json) is the source of "
        f"truth; run scripts/apply-rulesets.sh --repo {REPO_SLUG} to converge (issue #91)"
    )
