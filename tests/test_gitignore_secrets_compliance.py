"""Compliance test for the `.gitignore` secrets baseline (`gitignore_secrets_block`).

The org push-protection standard requires every repo to carry the managed
"petry-projects secrets baseline" block in its `.gitignore` as the first layer of
defense against committing credentials. Conformance is defined by the org
gitignore-standard: the baseline lives between canonical BEGIN/END markers and the
weekly compliance audit hash-matches the content between them.

This test is a local regression guard so the baseline (notably `*.pem`, the pattern
flagged in issue #17) cannot silently disappear between audits. It is text-based so
it runs with only ``requirements.txt`` installed (no YAML parser needed), mirroring
``test_ci_compliance.py``.

Ref: petry-projects/.github/standards/push-protection.md#required-gitignore-entries
Ref: petry-projects/.github/standards/gitignore-standard.md
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITIGNORE = ROOT / ".gitignore"

BEGIN_MARKER = "# >>> BEGIN petry-projects secrets baseline (managed by .github — do not edit) >>>"
END_MARKER = "# <<< END petry-projects secrets baseline <<<"

# A representative core of the org baseline — one anchor per secret category —
# always including `*.pem`, the pattern this issue was raised for. These are the
# lines that must survive verbatim inside the managed block; a full byte-for-byte
# baseline is what the org audit hash-matches, so this guard deliberately checks a
# stable, high-signal subset rather than duplicating the whole file.
REQUIRED_PATTERNS = (
    ".env",           # dotenv family
    "aws.credentials",  # cloud provider credentials
    "kubeconfig",     # kubernetes secrets
    "*.pem",          # SSH/TLS/GPG key material — the pattern flagged in issue #17
    "*.key",
    "*.tfstate",      # terraform state
    "*.tfvars",
    ".vault-token",   # secret-manager caches
    ".npmrc",         # package-registry credentials
    "id_rsa",
    "secrets.json",   # generic secret filename conventions
)


def _gitignore_text() -> str:
    assert GITIGNORE.exists(), f"{GITIGNORE} is missing"
    return GITIGNORE.read_text(encoding='utf-8')


def _baseline_block(text: str) -> str:
    """Return the content between the managed BEGIN/END markers.

    Returns an empty string when either marker is absent so callers fail closed
    (a missing block is treated as "no patterns present").
    """
    start = text.find(BEGIN_MARKER)
    end = text.find(END_MARKER)
    if start == -1 or end == -1 or end <= start + len(BEGIN_MARKER):
        return ""
    return text[start + len(BEGIN_MARKER):end]


def missing_baseline_patterns(text: str, required=REQUIRED_PATTERNS) -> list[str]:
    """Return the required patterns absent from the managed baseline block.

    A pattern only counts as present if it appears as its own line inside the
    marked block, so a substring match elsewhere in the file cannot mask a
    removed baseline entry.
    """
    block = _baseline_block(text)
    if not block:
        return list(required)
    lines = {line.strip() for line in block.splitlines()}
    return [pat for pat in required if pat not in lines]


# --- guards against the real .gitignore --------------------------------------


def test_secrets_baseline_markers_present():
    text = _gitignore_text()
    assert BEGIN_MARKER in text, (
        ".gitignore must contain the managed secrets-baseline BEGIN marker "
        "(org gitignore-standard)"
    )
    assert END_MARKER in text, (
        ".gitignore must contain the managed secrets-baseline END marker "
        "(org gitignore-standard)"
    )


def test_required_secret_patterns_present():
    missing = missing_baseline_patterns(_gitignore_text())
    assert not missing, (
        "these required secret patterns are missing from the .gitignore baseline "
        f"block: {missing} — copy the org baseline at /.gitignore "
        "(push-protection.md#required-gitignore-entries)"
    )


# --- predicate teeth: prove the guard fails when the baseline is non-compliant --


def test_missing_pattern_predicate_reports_stripped_pattern():
    """If `*.pem` is stripped from the block, the predicate must report it missing —
    otherwise the guard above would pass trivially and give false assurance."""
    text = _gitignore_text()
    assert "*.pem" not in missing_baseline_patterns(text), "*.pem must be present in the baseline to test stripping it"
    stripped = "\n".join(
        line for line in text.splitlines() if line.strip() != "*.pem"
    )
    assert "*.pem" in missing_baseline_patterns(stripped)


def test_missing_pattern_predicate_reports_all_without_markers():
    """With no baseline block at all, every required pattern is reported missing."""
    assert missing_baseline_patterns("# just a comment\nnode_modules/\n") == list(
        REQUIRED_PATTERNS
    )
