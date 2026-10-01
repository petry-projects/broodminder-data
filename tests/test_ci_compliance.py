"""Compliance tests for the CI secret-scan job.

Enforces the org push-protection standard's `secret_scan_ci_job_present`
requirement: the primary CI workflow must run gitleaks, and the repo must
ship a root .gitleaks.toml so `--config .gitleaks.toml` resolves.

Ref: petry-projects/.github/standards/push-protection.md#required-ci-job

Also enforces the dev-lead caller-stub channel pin
(`dev-lead-stub-agent-ref`): the stub must pass `with: agent_ref:
dev-lead/<channel>` and pin the reusable's `uses:` ref to the same channel.

Ref: petry-projects/.github/standards/ci-standards.md#dev-lead-agent

These are text-based checks so they run without extra dependencies (CI only
installs requirements.txt, which has no YAML parser).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
GITLEAKS_CONFIG = ROOT / ".gitleaks.toml"
DEV_LEAD_WORKFLOW = ROOT / ".github" / "workflows" / "dev-lead.yml"

# A dev-lead channel is `stable`, `next`, `ring<N>`, or the versioned
# `v<N>-stable` / `v<N>-next` / `v<N>-ring<M>` form (see ci-standards.md).
DEV_LEAD_CHANNEL = re.compile(r"^dev-lead/(v\d+-)?(stable|next|ring\d+)$")


def _ci_text() -> str:
    assert CI_WORKFLOW.exists(), f"{CI_WORKFLOW} is missing"
    return CI_WORKFLOW.read_text()


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
        "DEV_LEAD_CHANNEL to drop a supported form reintroduces issue #67"
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
    return DEV_LEAD_WORKFLOW.read_text()


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
        "(dev-lead/stable, dev-lead/next, dev-lead/ring<N>, or versioned dev-lead/v<N>-<channel>)"
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
        "the same channel"
    )
