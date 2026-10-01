"""Resilience tests for the CI pipeline's external network downloads.

The primary CI workflow (`ci.yml`) is otherwise fully deterministic and offline:
`live` contract tests skip without ``BROODMINDER_API_KEY`` and the repo-settings
live check skips without a GitHub token, neither of which the test step injects.
The only nondeterministic steps are the two external downloads:

  * ``Install dependencies`` — ``pip install -r requirements.txt`` (PyPI), and
  * ``Install gitleaks``     — ``wget`` of the release tarball (GitHub Releases).

A single transient failure in either fails the whole run — the cause of the
Fleet Monitor's ~20% (1/5) failure-rate warning. These guards pin that both
steps retry-with-backoff so a flaky index/CDN doesn't fail CI, while still
running the original command and (for gitleaks) verifying the checksum.

The SonarCloud workflow (`sonarcloud.yml`) shares the same ``pip install``
exposure: it installs ``requirements.txt`` to generate the coverage report the
scan uploads. Its SonarCloud scan step is already resilient (continue-on-error
plus a retry step), so the remaining nondeterministic failure point is that pip
install. Fleet Monitor flagged the workflow at a 26.1% failure rate (issue #99);
the guard below pins the same retry-with-backoff there.

These are text-based checks so they run with only ``requirements.txt`` installed
(no YAML parser), mirroring ``test_ci_compliance.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SONARCLOUD_WORKFLOW = ROOT / ".github" / "workflows" / "sonarcloud.yml"

# A step block starts at an indented `- name:` and runs until the next one.
_STEP_SPLIT = re.compile(r"^\s+- name:", re.MULTILINE)


def _ci_text() -> str:
    assert CI_WORKFLOW.exists(), f"{CI_WORKFLOW} is missing"
    return CI_WORKFLOW.read_text(encoding="utf-8")


def _sonarcloud_text() -> str:
    assert SONARCLOUD_WORKFLOW.exists(), f"{SONARCLOUD_WORKFLOW} is missing"
    return SONARCLOUD_WORKFLOW.read_text(encoding="utf-8")


def _step_containing(text: str, needle: str) -> str:
    """Return the single workflow step block that contains ``needle``.

    Splitting on the step boundary keeps the assertion scoped to one step, so a
    retry loop in an unrelated step can't make the test pass by accident.
    """
    blocks = _STEP_SPLIT.split(text)
    matches = [b for b in blocks if needle in b]
    assert matches, f"no CI step contains {needle!r}"
    assert len(matches) == 1, f"expected exactly one step containing {needle!r}, found {len(matches)}"
    return matches[0]


def _has_retry_loop(block: str) -> bool:
    """True when the step's shell retries with a bounded loop and a backoff sleep."""
    looped = bool(re.search(r"\bfor\s+\w+\s+in\b", block) or re.search(r"\buntil\b", block))
    backed_off = "sleep" in block
    return looped and backed_off


def test_pip_install_step_retries():
    block = _step_containing(_ci_text(), "pip install -r requirements.txt")
    assert _has_retry_loop(block), (
        "the `Install dependencies` step must retry `pip install` with backoff so a "
        "transient PyPI/network failure doesn't fail CI (Fleet Monitor flake remediation)"
    )


def test_gitleaks_download_step_retries():
    block = _step_containing(_ci_text(), "gitleaks.tar.gz")
    assert "wget" in block, "gitleaks install step must download the release tarball with wget"
    assert _has_retry_loop(block), (
        "the `Install gitleaks` step must retry the release download with backoff so a "
        "transient GitHub-Releases/CDN failure doesn't fail CI"
    )
    # Verify the retry loop is bounded to exactly 3 attempts (not combined with wget's --tries).
    assert "for attempt in 1 2 3" in block, (
        "the `Install gitleaks` step must use `for attempt in 1 2 3` to bound retries to 3 total attempts"
    )
    # Verify wget uses --tries=1 so the loop controls all retries (no nested retry logic).
    assert "--tries=1" in block, (
        "wget invocation must use --tries=1 to limit each attempt to a single try, "
        "letting the outer loop manage all retries"
    )
    # Retry must not weaken supply-chain safety: the checksum is still verified.
    assert "sha256sum -c" in block, "gitleaks download must still verify the checksum after retrying"


def test_network_steps_still_run_underlying_commands():
    """Retries must *wrap* the real commands, not replace them."""
    text = _ci_text()
    pip_block = _step_containing(text, "pip install -r requirements.txt")
    pip_run = "\n".join(ln for ln in pip_block.splitlines() if not ln.lstrip().startswith("#"))
    assert "pip install -r requirements.txt" in pip_run, (
        "the `Install dependencies` step must execute `pip install -r requirements.txt` "
        "(not just reference it in a comment)"
    )
    gitleaks_block = _step_containing(text, "gitleaks.tar.gz")
    gitleaks_run = "\n".join(ln for ln in gitleaks_block.splitlines() if not ln.lstrip().startswith("#"))
    assert "wget" in gitleaks_run, (
        "the `Install gitleaks` step must execute `wget` to download `gitleaks.tar.gz` "
        "(not just reference them in comments)"
    )
    assert "gitleaks.tar.gz" in gitleaks_run, (
        "the `Install gitleaks` step must execute `wget` to download `gitleaks.tar.gz` "
        "(not just reference them in comments)"
    )


# --- SonarCloud workflow pip-install resilience (issue #99) -------------------
#
# sonarcloud.yml installs requirements.txt to generate the coverage report the
# scan uploads. Its scan step is already retry-guarded (continue-on-error + a
# retry step), so the last unguarded network failure point is that pip install.
# Fleet Monitor flagged the workflow at a 26.1% failure rate; these guards pin
# the same retry-with-backoff already applied to ci.yml.


def test_sonarcloud_pip_install_step_retries():
    block = _step_containing(_sonarcloud_text(), "pip install -r requirements.txt")
    assert _has_retry_loop(block), (
        "the sonarcloud.yml `Install dependencies` step must retry `pip install` with "
        "backoff so a transient PyPI/network failure doesn't fail the SonarCloud run "
        "(Fleet Monitor flake remediation, issue #99)"
    )
    assert "for attempt in 1 2 3" in block, (
        "the sonarcloud.yml `Install dependencies` step must use `for attempt in 1 2 3` "
        "to bound retries to exactly 3 total attempts"
    )
    assert re.search(r"sleep.*attempt", block), (
        "the sonarcloud.yml `Install dependencies` step must use increasing backoff delays "
        "that scale with the attempt number"
    )
    assert "exit 1" in block, (
        "the sonarcloud.yml `Install dependencies` step must explicitly `exit 1` after "
        "all retry attempts are exhausted"
    )


def test_sonarcloud_pip_install_still_runs_underlying_command():
    """The retry must *wrap* the real command, not replace it."""
    block = _step_containing(_sonarcloud_text(), "pip install -r requirements.txt")
    run = "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))
    assert "pip install -r requirements.txt" in run, (
        "the sonarcloud.yml `Install dependencies` step must execute "
        "`pip install -r requirements.txt` (not just reference it in a comment)"
    )
