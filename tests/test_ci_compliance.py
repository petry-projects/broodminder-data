"""Compliance tests for the CI secret-scan job.

Enforces the org push-protection standard's `secret_scan_ci_job_present`
requirement: the primary CI workflow must run gitleaks, and the repo must
ship a root .gitleaks.toml so `--config .gitleaks.toml` resolves.

Ref: petry-projects/.github/standards/push-protection.md#required-ci-job

Also enforces the dev-lead caller-stub channel pin
(`dev-lead-stub-agent-ref`): the stub must pass `with: agent_ref:
dev-lead/<channel>` and pin the reusable's `uses:` ref to the same channel.

Ref: petry-projects/.github/standards/ci-standards.md#dev-lead-agent
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


class _UniqueKeyLoader(yaml.SafeLoader):
    """A SafeLoader that rejects duplicate mapping keys.

    PyYAML's default constructor silently keeps the *last* value when a mapping
    has duplicate keys (its constructor does not check for duplicates), even
    though the YAML spec requires mapping keys to be unique. A workflow that
    declared two `concurrency:` blocks would therefore parse cleanly and the
    compliance checks would inspect only the last one — masking an invalid
    duplicate-key configuration. This loader fails closed on any duplicate.
    """


def _reject_duplicate_keys(loader: _UniqueKeyLoader, node, deep: bool = False):
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _reject_duplicate_keys
)


def _strict_yaml_load(text: str):
    """Parse YAML with duplicate mapping keys rejected (see _UniqueKeyLoader)."""
    return yaml.load(text, Loader=_UniqueKeyLoader)


ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
GITLEAKS_CONFIG = ROOT / ".gitleaks.toml"
DEV_LEAD_WORKFLOW = ROOT / ".github" / "workflows" / "dev-lead.yml"
PR_AUTO_REVIEW_WORKFLOW = ROOT / ".github" / "workflows" / "pr-auto-review.yml"

# A dev-lead channel is `stable`, `next`, `ring<N>`, or the versioned
# `v<N>-stable` / `v<N>-next` / `v<N>-ring<M>` form (see ci-standards.md).
DEV_LEAD_CHANNEL = re.compile(r"^dev-lead/(v\d+-)?(stable|next|ring\d+)$")


def _ci_text() -> str:
    assert CI_WORKFLOW.exists(), f"{CI_WORKFLOW} is missing"
    return CI_WORKFLOW.read_text(encoding="utf-8")


def test_ci_has_gitleaks_secret_scan_job():
    text = _ci_text()
    assert "secret-scan:" in text, "ci.yml must declare a `secret-scan` job"
    assert "gitleaks detect" in text, "secret-scan job must run `gitleaks detect`"
    assert "--config .gitleaks.toml" in text, "gitleaks must use the repo .gitleaks.toml"
    assert "--exit-code 1" in text, "gitleaks must fail the build on detection"
    assert "--redact" in text, "gitleaks must redact leaked values from logs"


def test_secret_scan_job_uses_checksum_verified_install():
    text = _ci_text()
    assert "GITLEAKS_VERSION" in text, "install step must pin a gitleaks version"
    assert "GITLEAKS_CHECKSUM" in text, "install step must verify a checksum (GITLEAKS_CHECKSUM)"
    assert "sha256sum -c" in text, "install step must verify the download with sha256sum -c"
    assert "fetch-depth: 0" in text, "checkout must fetch full history for a complete scan"


def test_gitleaks_config_present():
    assert GITLEAKS_CONFIG.exists(), ".gitleaks.toml must exist at the repo root"
    text = GITLEAKS_CONFIG.read_text()
    assert "[allowlist]" in text, ".gitleaks.toml must define an [allowlist] section"


# --- dev-lead channel-form regression guard (issue #67) -----------------------
#
# The flaky `.github/workflows/sonarcloud.yml` failures flagged by Fleet Monitor
# (issue #67) were pytest failures, not SonarCloud endpoint flakiness: an org
# standards-sync updated `dev-lead.yml` to the versioned channel
# `dev-lead/v1-stable` before DEV_LEAD_CHANNEL was broadened to accept the
# `v<N>-` form. Because sonarcloud.yml (and ci.yml) run the full suite to
# generate coverage, that mismatch surfaced as a "SonarCloud" workflow failure.
#
# These guards pin the accepted/rejected channel forms directly against the
# regex so a future narrowing of DEV_LEAD_CHANNEL cannot silently reintroduce
# that failure class — independent of whatever channel `dev-lead.yml` happens to
# pin at any given moment.


@pytest.mark.parametrize(
    "ref",
    [
        "dev-lead/stable",
        "dev-lead/next",
        "dev-lead/ring0",
        "dev-lead/ring12",
        "dev-lead/v1-stable",
        "dev-lead/v2-next",
        "dev-lead/v10-ring3",
    ],
)
def test_dev_lead_channel_regex_accepts_supported_forms(ref):
    assert DEV_LEAD_CHANNEL.match(ref), (
        f"'{ref}' must be accepted as a valid dev-lead channel; narrowing "
        f"DEV_LEAD_CHANNEL to drop a supported form reintroduces issue #67"
    )


@pytest.mark.parametrize(
    "ref",
    [
        "dev-lead/",
        "dev-lead/prod",
        "dev-lead/v-stable",  # missing version number
        "dev-lead/vstable",
        "dev-lead/1-stable",  # missing 'v' prefix
        "dev-lead/stable-v1",
        "dev-lead/ring",  # ring without an ordinal
        "release/stable",
    ],
)
def test_dev_lead_channel_regex_rejects_malformed_forms(ref):
    assert not DEV_LEAD_CHANNEL.match(ref), (
        f"'{ref}' must not be accepted as a dev-lead channel; the gate must stay strict"
    )


# --- dev-lead caller-stub channel pin (dev-lead-stub-agent-ref) ---------------


def _dev_lead_text() -> str:
    assert DEV_LEAD_WORKFLOW.exists(), f"{DEV_LEAD_WORKFLOW} is missing"
    return DEV_LEAD_WORKFLOW.read_text(encoding="utf-8")


def test_dev_lead_stub_passes_valid_agent_ref():
    """The stub must pass `with: agent_ref: dev-lead/<channel>` where the
    channel is `stable`, `next`, `ring<N>`, or the versioned `v<N>-stable` /
    `v<N>-next` / `v<N>-ring<M>` form (ci-standards.md#dev-lead-agent)."""
    text = _dev_lead_text()
    m = re.search(r"^\s*agent_ref:\s*['\"]?([^'\"\s]+)['\"]?\s*$", text, re.MULTILINE)
    assert m, "dev-lead.yml must pass `with: agent_ref: dev-lead/<channel>`"
    ref = m.group(1)
    assert DEV_LEAD_CHANNEL.match(ref), (
        f"agent_ref '{ref}' must be a valid dev-lead channel "
        f"(dev-lead/stable, dev-lead/next, dev-lead/ring<N>, or versioned dev-lead/v<N>-<channel>)"
    )


def test_dev_lead_uses_ref_matches_agent_ref():
    """The reusable `uses:` ref and `agent_ref` must pin the same channel so the
    reusable checks out its own scripts/prompts from the channel it runs."""
    text = _dev_lead_text()
    uses = re.search(
        r"^\s*uses:\s*['\"]?petry-projects/\.github-private/\.github/workflows/"
        r"dev-lead-reusable\.yml@([^'\"\s]+)['\"]?",
        text,
        re.MULTILINE,
    )
    agent = re.search(r"^\s*agent_ref:\s*['\"]?([^'\"\s]+)['\"]?\s*$", text, re.MULTILINE)
    assert uses, "dev-lead.yml must pin the reusable via `uses: ...@<channel>`"
    assert agent, "dev-lead.yml must pass `with: agent_ref: dev-lead/<channel>`"
    assert DEV_LEAD_CHANNEL.match(uses.group(1)), (
        f"uses ref '{uses.group(1)}' must pin a valid dev-lead channel"
    )
    assert uses.group(1) == agent.group(1), (
        f"uses ref '{uses.group(1)}' and agent_ref '{agent.group(1)}' must pin "
        f"the same channel"
    )


# --- pr-auto-review caller-stub concurrency surface --------------------------
#
# The pr-auto-review.yml caller stub is a thin caller whose `on:`, `permissions:`,
# and `concurrency:` surfaces are owned centrally by
# petry-projects/.github/standards/workflows/pr-auto-review.yml — only the
# documented `with:` inputs and the tier channel pin may differ per repo. The
# `concurrency:` block deduplicates the default-branch-context triggers
# (check_suite / workflow_run) per PR while leaving PR-head triggers on a
# unique-per-run group (issue #1126). A stub that drops or alters this block has
# drifted from canonical and must be re-synced (stub-surface-drift check).
#
# IMPORTANT: This test validates the LOCAL stub against the EXPECTED canonical
# pattern. When the canonical workflow in petry-projects/.github is updated
# (e.g., adding head SHA to the concurrency group), the stub MUST be updated
# first, then this test updated to validate the new pattern, and ONLY THEN
# should the central canonical be updated to match. The test's required_expressions
# must be kept in sync with the actual canonical workflow to maintain drift
# detection.
#
# Ref: petry-projects/.github/standards/ci-standards.md#centralization-tiers


def _pr_auto_review_workflow() -> dict:
    assert PR_AUTO_REVIEW_WORKFLOW.exists(), f"{PR_AUTO_REVIEW_WORKFLOW} is missing"
    text = PR_AUTO_REVIEW_WORKFLOW.read_text(encoding="utf-8")
    return _strict_yaml_load(text)


@pytest.mark.compliance
def test_pr_auto_review_declares_concurrency_block():
    """The stub must declare a top-level `concurrency:` block (not nested under a
    job) so default-branch-context triggers dedupe per PR."""
    workflow = _pr_auto_review_workflow()
    assert "concurrency" in workflow, (
        "pr-auto-review.yml must declare a top-level `concurrency:` block "
        "re-synced from standards/workflows/pr-auto-review.yml"
    )
    assert isinstance(workflow["concurrency"], dict), (
        "concurrency must be a mapping (not nested under a job)"
    )


def _normalize_expr(expr: str) -> str:
    """Collapse every run of whitespace (spaces and the newlines a folded YAML
    block scalar preserves for its more-indented lines) to a single space and
    strip. This yields one canonical token stream so a complete expression can
    be compared verbatim regardless of how the source happens to wrap."""
    return re.sub(r"\s+", " ", expr).strip()


# The complete canonical concurrency expressions, token-for-token. Comparing the
# normalized *whole* expression — not a set of substrings — is what makes an
# operator swap detectable: flipping a branch `||` to `&&` (which would make both
# event branches fall through to the run-ID group) or flipping the
# `cancel-in-progress` `||` to `&&` (which would disable cancellation for both
# events) leaves every substring present but changes the overall string, so the
# equality check below fails where a substring check would silently pass.
_CANONICAL_GROUP = _normalize_expr(
    """
    ${{
    ((github.event_name == 'check_suite'
      && github.event.check_suite.pull_requests[0]
      && !github.event.check_suite.pull_requests[1])
      && format('pr-auto-review-ready-check-pr-{0}-{1}', github.event.check_suite.pull_requests[0].number, github.event.check_suite.pull_requests[0].head.sha))
    || ((github.event_name == 'workflow_run'
      && github.event.workflow_run.pull_requests[0]
      && !github.event.workflow_run.pull_requests[1])
      && format('pr-auto-review-ready-check-pr-{0}-{1}', github.event.workflow_run.pull_requests[0].number, github.event.workflow_run.pull_requests[0].head.sha))
    || format('pr-auto-review-ready-check-unique-{0}', github.run_id)
    }}
    """
)

_CANONICAL_CANCEL = _normalize_expr(
    "${{ github.event_name == 'check_suite' || github.event_name == 'workflow_run' }}"
)


@pytest.mark.compliance
def test_pr_auto_review_concurrency_group_matches_canonical():
    """The `concurrency:` surface must match the canonical group/cancel
    expressions: check_suite and workflow_run collapse onto a per-PR group and
    cancel in progress; every other context falls back to a unique-per-run
    group that never cancels.

    The assertions compare the *complete* normalized expressions, not a set of
    substrings: a substring check passes even when a logical operator is swapped
    (e.g. `||`→`&&` between the two event branches, which would make both
    branches fall through to the run-ID fallback, or in cancel-in-progress,
    which would disable cancellation for both events). Full-expression equality
    makes any such operator change fail."""
    workflow = _pr_auto_review_workflow()
    assert "concurrency" in workflow
    concurrency = workflow["concurrency"]

    # Validate group is a string expression
    group = concurrency.get("group")
    assert group, "concurrency.group must be defined"
    assert isinstance(group, str), "concurrency.group must be a string expression"

    assert _normalize_expr(group) == _CANONICAL_GROUP, (
        "pr-auto-review.yml concurrency.group has drifted from canonical; the "
        "complete expression (operators included) must match:\n"
        f"  expected: {_CANONICAL_GROUP}\n"
        f"  actual:   {_normalize_expr(group)}"
    )

    # Validate cancel-in-progress is the complete canonical expression. Checking
    # the whole expression (not the two event-name substrings) ensures swapping
    # its `||` to `&&` — which silently disables cancellation for both events —
    # is detected.
    assert "cancel-in-progress" in concurrency, (
        "concurrency.cancel-in-progress must be defined"
    )
    cancel_in_progress = concurrency["cancel-in-progress"]
    assert isinstance(cancel_in_progress, str), (
        "concurrency.cancel-in-progress must be a string expression"
    )
    assert _normalize_expr(cancel_in_progress) == _CANONICAL_CANCEL, (
        "pr-auto-review.yml concurrency.cancel-in-progress has drifted from "
        "canonical; the complete expression (operators included) must match:\n"
        f"  expected: {_CANONICAL_CANCEL}\n"
        f"  actual:   {_normalize_expr(cancel_in_progress)}"
    )
