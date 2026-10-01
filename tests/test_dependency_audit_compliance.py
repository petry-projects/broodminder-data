"""Compliance test for the pip-audit requirements file the dependency-audit
workflow depends on.

``.github/workflows/dependency-audit.yml`` is a thin caller stub adopted verbatim
from the org standards; all logic lives in the reusable workflow
(``petry-projects/.github`` → ``dependency-audit-reusable.yml``). That reusable's
``pip-audit`` step installs its tooling with::

    pip install --require-hashes --only-binary :all: -r scripts/pip-audit-requirements.txt

so the audit job is only green when ``scripts/pip-audit-requirements.txt`` exists
and is valid for ``--require-hashes`` mode (every requirement pinned to an exact
version and carrying at least one SHA-256 hash, with ``pip-audit`` itself pinned).

Issue #68 (Fleet Monitor) flagged a dependency-audit failure whose sole failing
run errored with ``Could not open requirements file: ...
'scripts/pip-audit-requirements.txt'`` — the file was transiently absent. This
guard is a local regression check so that file cannot silently disappear or lose
its hashes between org audits. The stub workflow must not be edited (its trigger
events, ``uses:`` line, and job name are a required status check), so the invariant
is enforced here instead.

It is text-based so it runs with only ``requirements.txt`` installed (no YAML or
packaging parser needed), mirroring ``test_ci_compliance.py`` and
``test_gitignore_secrets_compliance.py``.

Ref: petry-projects/.github/standards/ci-standards.md#7-dependency-audit-dependency-audityml
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "scripts" / "pip-audit-requirements.txt"
DEPENDENCY_AUDIT_WORKFLOW = ROOT / ".github" / "workflows" / "dependency-audit.yml"

# The dependency-audit caller stub is a thin caller adopted verbatim from the org
# standard; its `on:` trigger surface is owned centrally and must not drift. The
# canonical surface is pull_request + push (both filtered to `main`) + merge_group,
# where `merge_group` is required so the `dependency-audit / Detect ecosystems`
# status check reports on a merge queue's `gh-readonly-queue/*` ref. The mapping is
# compared *exactly* (not as a subset): a removed/retargeted `branches:` filter, an
# added nested option (e.g. a narrowed `types:`/`paths-ignore:`), or an added event
# is caught as drift rather than silently accepted. Each trigger maps to a dict of
# *all* its nested options (option → flow-list values); `None` marks a trigger with
# no nested options at all.
#
# Ref: petry-projects/.github/standards/ci-standards.md#centralization-tiers
_CANONICAL_ON: dict[str, dict[str, list[str]] | None] = {
    "pull_request": {"branches": ["main"]},
    "push": {"branches": ["main"]},
    "merge_group": None,
}

# The reusable dependency-audit workflow installs pip-audit from this file with
# `--require-hashes`, so it must be pinned by exact version like the rest.
REQUIRED_PIN = "pip-audit=="

# A requirement line opens a pinned spec: `name==version [\]`. Hash lines and
# blank/comment lines are continuations or noise, not new requirements.
_PIN_LINE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>[^\s\\]+)")
_HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}", re.IGNORECASE)


def _requirements_text() -> str:
    """Read the requirements file, returning ``""`` when it is absent.

    Fail closed: a missing file yields empty text so parsing tests report a clean
    compliance violation (``requirements_violations`` flags it) instead of a raw
    ``FileNotFoundError`` traceback. ``test_pip_audit_requirements_file_exists``
    remains the primary, explicit guard for existence.
    """
    return REQUIREMENTS.read_text(encoding="utf-8") if REQUIREMENTS.exists() else ""


def requirements_violations(text: str) -> list[str]:
    """Return reasons ``text`` is not a valid ``--require-hashes`` file.

    Fails closed: empty text, a missing ``pip-audit`` pin, any requirement that
    is not pinned with ``==``, or any pinned requirement lacking a SHA-256 hash
    each produce a violation string. An empty list means the file is compliant.
    """
    violations: list[str] = []
    if not text.strip():
        return ["requirements file is empty"]

    # Split into logical requirement blocks: each starts at a `name==` line and
    # runs until the next one, gathering its trailing `--hash=` continuation lines.
    blocks: list[tuple[str, list[str]]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _PIN_LINE.match(stripped)
        if m:
            blocks.append((m.group("name"), [stripped]))
        elif stripped.startswith("--hash"):
            if blocks:
                blocks[-1][1].append(stripped)
            else:
                violations.append(f"hash line without requirement: {stripped!r}")
        else:
            violations.append(f"unpinned requirement or invalid line: {stripped!r}")

    if not blocks:
        violations.append("no pinned requirements found")

    # Validate pip-audit is present as an actual pinned requirement (not just in a
    # comment), by checking parsed block names rather than raw text.
    _norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()
    if not any(_norm(name) == "pip-audit" for name, _ in blocks):
        violations.append(f"missing exact pin for pip-audit ('{REQUIRED_PIN}')")

    for name, lines in blocks:
        if not any(_HASH.search(line) for line in lines):
            violations.append(f"requirement {name!r} is missing a --hash=sha256 entry")

    return violations


# --- guards against the real requirements file -------------------------------


def test_pip_audit_requirements_file_exists():
    assert REQUIREMENTS.exists(), (
        f"{REQUIREMENTS} is missing — the reusable dependency-audit workflow "
        "installs pip-audit from it with `pip install --require-hashes -r "
        "scripts/pip-audit-requirements.txt`; without it the audit job fails "
        "(issue #68)"
    )


def test_pip_audit_requirements_is_hash_pinned():
    violations = requirements_violations(_requirements_text())
    assert not violations, (
        "scripts/pip-audit-requirements.txt is not valid for `--require-hashes` "
        f"install: {violations} — regenerate it with pip-compile "
        "--generate-hashes (see the file header)"
    )


# --- predicate teeth: prove the guard fails on non-compliant content ----------


def test_predicate_flags_missing_file_content():
    assert requirements_violations("") == ["requirements file is empty"]


def test_predicate_flags_missing_pip_audit_pin():
    # A validly-hashed but wrong package (no pip-audit) must be rejected.
    text = (
        "requests==2.34.2 \\\n"
        "    --hash=sha256:" + "a" * 64 + "\n"
    )
    violations = requirements_violations(text)
    assert any("pip-audit" in v for v in violations), violations


def test_predicate_flags_pip_audit_pin_in_comment_only():
    # A comment containing 'pip-audit==' must not satisfy the pip-audit pin guard.
    text = (
        "# pip-audit==2.9.0 must be pinned\n"
        "requests==2.34.2 \\\n"
        "    --hash=sha256:" + "a" * 64 + "\n"
    )
    violations = requirements_violations(text)
    assert any("pip-audit" in v for v in violations), violations


def test_predicate_flags_requirement_without_hash():
    text = "pip-audit==2.9.0\nrequests==2.34.2\n"
    violations = requirements_violations(text)
    assert any("requests" in v and "hash" in v for v in violations), violations


def test_predicate_flags_unpinned_line_after_pinned_block():
    # Regression: an unpinned name after a valid hash-pinned block must be flagged,
    # not silently merged into the preceding block's hash list.
    text = (
        "pip-audit==2.9.0 \\\n"
        "    --hash=sha256:" + "b" * 64 + "\n"
        "requests\n"
    )
    violations = requirements_violations(text)
    assert any("requests" in v for v in violations), violations


def test_predicate_flags_hash_line_without_requirement():
    # A bare --hash line before any pinned block is not valid.
    text = "--hash=sha256:" + "c" * 64 + "\npip-audit==2.9.0 \\\n    --hash=sha256:" + "d" * 64 + "\n"
    violations = requirements_violations(text)
    assert any("hash line without requirement" in v for v in violations), violations


def test_predicate_accepts_the_real_file():
    # Sanity anchor: the shipped file must be accepted, so the teeth above are
    # exercising genuinely-bad input rather than a perpetually-broken guard.
    assert requirements_violations(_requirements_text()) == []


# --- dependency-audit stub `on:` surface guard --------------------------------


def _split_top_level(body: str) -> list[str]:
    """Split ``body`` on top-level commas — those outside any ``[..]``/``{..}``
    nesting — so an inline flow list embedded in a value (``types: [a, b]``) is
    not torn apart at its internal commas."""
    parts: list[str] = []
    depth = 0
    current = ""
    for ch in body:
        if ch in "[{":
            depth += 1
            current += ch
        elif ch in "]}":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current)
    return parts


def _strip_inline_comment(text: str) -> str:
    """Strip a YAML inline comment from ``text``.

    A comment begins at a ``#`` that is at the start of the value or is preceded
    by whitespace, and that lies outside any ``[..]``/``{..}`` nesting and outside
    single/double quotes — matching YAML's rule that an inline comment must be
    separated from the value by whitespace (so ``branches: [main] # default`` and
    ``- main # default`` compare equal to their comment-free forms). A ``#`` glued
    to a value (``feat#123``) or inside quotes/brackets is left untouched."""
    depth = 0
    quote: str | None = None
    prev_ws = True  # start-of-string counts as preceding whitespace
    for i, ch in enumerate(text):
        if quote is not None:
            if ch == quote:
                quote = None
            prev_ws = False
            continue
        if ch in "'\"":
            quote = ch
            prev_ws = False
            continue
        if ch == "#" and depth == 0 and prev_ws:
            return text[:i].rstrip()
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        prev_ws = ch.isspace()
    return text


def _parse_value(rest: str) -> list[str]:
    """Parse a nested option's value into a list: an inline ``[a, b]`` flow list,
    or a bare scalar (returned as a single-element list). Surrounding quotes are
    stripped so ``[main]`` and ``['main']`` compare equal, and a trailing inline
    comment is ignored so ``[main] # default`` compares equal to ``[main]``."""
    rest = _strip_inline_comment(rest).strip()
    if not rest:
        return []
    lm = re.match(r"^\[([^\]]*)\]$", rest)
    if lm:
        return [v.strip().strip("'\"") for v in lm.group(1).split(",") if v.strip()]
    return [rest.strip("'\"")]


def _parse_inline_options(raw: str) -> dict[str, list[str]] | None:
    """Parse an inline flow-mapping of trigger options written on the trigger's
    own line, e.g. ``merge_group: {types: [checks_requested]}``. Returns the
    option→values dict, or ``None`` **only** when ``raw`` is genuinely empty (a
    bare trigger) or an empty ``{}`` (semantically the same as no options).

    A non-empty value that is *not* a mapping — a scalar such as ``false`` or a
    flow list such as ``[types]`` — is not a valid bare trigger; it is captured
    under the reserved ``<non-mapping>`` key (which the option regex can never
    produce) so it compares distinct from ``None`` and surfaces as drift rather
    than being silently collapsed to the bare-trigger representation."""
    raw = _strip_inline_comment(raw).strip()
    if not raw:
        return None
    m = re.match(r"^\{(.*)\}$", raw)
    if not m:
        return {"<non-mapping>": _parse_value(raw)}
    body = m.group(1).strip()
    if not body:
        return None
    options: dict[str, list[str]] = {}
    for entry in _split_top_level(body):
        em = re.match(r"^\s*['\"]?([A-Za-z_][\w-]*)['\"]?:\s*(.*)$", entry)
        if em:
            options[em.group(1)] = _parse_value(em.group(2))
    return options


def _on_triggers(text: str) -> dict[str, dict[str, list[str]] | None]:
    """Return the ``on:`` trigger mapping: each top-level trigger key mapped to a
    dict of *all* its nested options (option name → its value list), or ``None``
    when the trigger carries no nested options at all.

    Text-based (no YAML parser, matching this module's other guards): find the
    ``on:`` mapping, then

    * tolerate optional single/double quotes around ``on`` and its child keys;
    * detect the top-level trigger indent dynamically (the first indented line's
      width) rather than assuming a fixed width, so a re-indent of the stub does
      not fool the guard;
    * skip blank and comment lines — a column-0 ``# comment`` no longer
      prematurely ends the block and falsely reports later triggers missing;
    * capture *every* nested option (``branches:``, ``types:``,
      ``paths-ignore:`` …), not just ``branches:``, so drift such as a narrowed
      ``types: [opened]`` or an added ``paths-ignore:`` filter — which would stop
      audits running for synchronized PRs or selected files — is visible to the
      exact-match comparison rather than silently discarded;
    * parse an inline option map written on the trigger's own line
      (``merge_group: {types: [checks_requested]}``) so such drift is captured
      rather than recorded as ``None`` and silently accepted;
    * collect block-style sequences (``branches:`` followed by ``- main``) into
      the same list an inline ``[main]`` produces, so a representation-only
      rewrite compares equal instead of falsely reporting drift.

    A non-blank, non-comment, non-indented (column-0) line ends the ``on:`` block.
    """
    lines = text.splitlines()
    triggers: dict[str, dict[str, list[str]] | None] = {}
    in_on = False
    trigger_indent: int | None = None
    current: str | None = None
    current_option: str | None = None
    for line in lines:
        if not in_on:
            if re.match(r"^['\"]?on['\"]?:\s*$", line):
                in_on = True
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # A non-indented (column-0) line ends the `on:` block.
        if not line.startswith((" ", "\t")):
            break
        cur_indent = len(line) - len(line.lstrip())
        if trigger_indent is None:
            trigger_indent = cur_indent
        km = re.match(r"^\s*['\"]?([A-Za-z_][\w-]*)['\"]?:\s*(.*)$", line)
        if cur_indent <= trigger_indent:
            # A top-level trigger key (e.g. `pull_request:`, `merge_group:`).
            # Capture any inline option map on the same line; a bare trigger with
            # no inline mapping records `None` until a nested option appears.
            if km:
                current = km.group(1)
                current_option = None
                triggers[current] = _parse_inline_options(km.group(2))
            continue
        # A more-deeply-indented line under `current`.
        if current is None:
            continue
        # A block-sequence item (`- main`) continues the current option's list,
        # so block style parses identically to an inline `[main]` flow list.
        sm = re.match(r"^\s*-\s*(.*)$", line)
        if sm and current_option is not None:
            item = _strip_inline_comment(sm.group(1)).strip().strip("'\"")
            if item:
                if triggers[current] is None:
                    triggers[current] = {}
                triggers[current].setdefault(current_option, []).append(item)
            continue
        # A nested option (`branches: [main]`, `types: [opened]`, or a bare
        # `branches:` whose items follow as a block sequence).
        if km:
            option = km.group(1)
            rest = km.group(2).strip()
            current_option = option
            if triggers[current] is None:
                triggers[current] = {}
            # An empty value starts a list the following `- item` lines fill;
            # a present value (flow list or scalar) is captured immediately.
            triggers[current][option] = _parse_value(rest) if rest else []
    return triggers


def test_dependency_audit_stub_on_surface_matches_standard():
    assert DEPENDENCY_AUDIT_WORKFLOW.exists(), (
        f"{DEPENDENCY_AUDIT_WORKFLOW} is missing — it is the thin caller stub "
        "adopted verbatim from petry-projects/.github standards/workflows/"
    )
    triggers = _on_triggers(DEPENDENCY_AUDIT_WORKFLOW.read_text(encoding="utf-8"))
    assert triggers == _CANONICAL_ON, (
        f"dependency-audit.yml `on:` surface has drifted from the standard: "
        f"expected {_CANONICAL_ON} but found {triggers}. The stub's trigger "
        "surface is owned centrally — re-sync from "
        "standards/workflows/dependency-audit.yml (merge_group is required for "
        "merge-queue status reporting, and both pull_request/push must stay "
        "filtered to `main`)."
    )


def test_on_trigger_parser_flags_missing_merge_group():
    # Teeth: a stub missing `merge_group` must be detected as drift.
    drifted = "on:\n  pull_request:\n    branches: [main]\n  push:\n    branches: [main]\n"
    assert _on_triggers(drifted) == {
        "pull_request": {"branches": ["main"]},
        "push": {"branches": ["main"]},
    }


def test_on_trigger_parser_flags_dropped_branch_filter():
    # Teeth: dropping `branches: [main]` from push must be visible as drift.
    drifted = "on:\n  pull_request:\n    branches: [main]\n  push:\n  merge_group:\n"
    assert _on_triggers(drifted) != _CANONICAL_ON
    assert _on_triggers(drifted)["push"] is None


def test_on_trigger_parser_flags_retargeted_branch_filter():
    # Teeth: retargeting a filter to another branch must be visible as drift.
    drifted = (
        "on:\n  pull_request:\n    branches: [develop]\n"
        "  push:\n    branches: [main]\n  merge_group:\n"
    )
    assert _on_triggers(drifted) != _CANONICAL_ON


def test_on_trigger_parser_flags_added_event():
    # Teeth: an extra unexpected trigger must be visible as drift.
    drifted = (
        "on:\n  pull_request:\n    branches: [main]\n  push:\n    branches: [main]\n"
        "  merge_group:\n  schedule:\n"
    )
    assert _on_triggers(drifted) != _CANONICAL_ON
    assert "schedule" in _on_triggers(drifted)


def test_on_trigger_parser_ignores_column0_comment():
    # Regression (codeant/gemini): a column-0 comment inside/after the `on:` block
    # must not end parsing and drop later triggers.
    text = (
        "on:\n  pull_request:\n    branches: [main]\n"
        "# a stray column-0 comment\n  push:\n    branches: [main]\n  merge_group:\n"
    )
    assert _on_triggers(text) == _CANONICAL_ON


def test_on_trigger_parser_tolerates_alternate_indent_and_quotes():
    # Robustness (gemini): a 4-space indent and quoted keys parse identically.
    text = (
        "'on':\n    'pull_request':\n        branches: [main]\n"
        "    push:\n        branches: [main]\n    merge_group:\n"
    )
    assert _on_triggers(text) == _CANONICAL_ON


def test_on_trigger_parser_flags_added_nested_type_filter():
    # Teeth (codex): an added nested option such as `types: [opened]` under a
    # trigger narrows when the audit runs (synchronized PRs would stop firing);
    # it must be captured and surface as drift, not silently discarded.
    drifted = (
        "on:\n  pull_request:\n    branches: [main]\n    types: [opened]\n"
        "  push:\n    branches: [main]\n  merge_group:\n"
    )
    assert _on_triggers(drifted) != _CANONICAL_ON
    assert _on_triggers(drifted)["pull_request"] == {
        "branches": ["main"],
        "types": ["opened"],
    }


def test_on_trigger_parser_flags_added_paths_ignore_filter():
    # Teeth (codex): a `paths-ignore:` filter would stop audits running for
    # selected files; the added nested option must surface as drift.
    drifted = (
        "on:\n  pull_request:\n    branches: [main]\n"
        "  push:\n    branches: [main]\n    paths-ignore: [docs/**]\n  merge_group:\n"
    )
    assert _on_triggers(drifted) != _CANONICAL_ON
    assert _on_triggers(drifted)["push"] == {
        "branches": ["main"],
        "paths-ignore": ["docs/**"],
    }


def test_on_trigger_parser_flags_inline_option_map():
    # Teeth (codex): an option map written on the trigger's own line, e.g.
    # `merge_group: {types: [checks_requested]}`, must be parsed — not discarded
    # and recorded as `None` — so the added filter surfaces as drift rather than
    # being silently accepted against the canonical `merge_group: None`.
    drifted = (
        "on:\n  pull_request:\n    branches: [main]\n"
        "  push:\n    branches: [main]\n"
        "  merge_group: {types: [checks_requested]}\n"
    )
    assert _on_triggers(drifted) != _CANONICAL_ON
    assert _on_triggers(drifted)["merge_group"] == {"types": ["checks_requested"]}


def test_on_trigger_parser_accepts_block_style_branch_list():
    # Representation-only tolerance (cubic): a valid YAML block sequence
    # (`branches:` followed by `- main`) means the same as the canonical inline
    # `branches: [main]`, so it must parse to the same mapping and NOT be reported
    # as drift.
    block_style = (
        "on:\n  pull_request:\n    branches:\n      - main\n"
        "  push:\n    branches:\n      - main\n  merge_group:\n"
    )
    assert _on_triggers(block_style) == _CANONICAL_ON


def test_on_trigger_parser_flags_block_style_retargeted_branch():
    # Teeth: a block-style sequence with a drifted target must still surface as
    # drift, proving the block-sequence collection does not blindly accept.
    drifted = (
        "on:\n  pull_request:\n    branches:\n      - develop\n"
        "  push:\n    branches:\n      - main\n  merge_group:\n"
    )
    assert _on_triggers(drifted) != _CANONICAL_ON
    assert _on_triggers(drifted)["pull_request"] == {"branches": ["develop"]}


def test_on_trigger_parser_ignores_inline_value_comments():
    # Representation-only tolerance (codex): a YAML inline comment on a trigger
    # value — inline `branches: [main] # default` or block-style `- main # default`
    # — is not part of the value, so the mapping must compare equal to the
    # canonical comment-free form and NOT be reported as drift.
    inline = (
        "on:\n  pull_request:\n    branches: [main] # default branch\n"
        "  push:\n    branches: [main]  # default branch\n  merge_group:\n"
    )
    assert _on_triggers(inline) == _CANONICAL_ON
    block = (
        "on:\n  pull_request:\n    branches:\n      - main # default branch\n"
        "  push:\n    branches:\n      - main\n  merge_group:\n"
    )
    assert _on_triggers(block) == _CANONICAL_ON


def test_on_trigger_parser_flags_non_mapping_bare_trigger_value():
    # Teeth (codex): a bare canonical trigger changed to a non-mapping value —
    # a scalar (`merge_group: false`) or a flow list (`merge_group: [types]`) —
    # must NOT collapse to `None` (the valid bare-trigger representation); it has
    # to surface as drift against the canonical `merge_group: None`.
    scalar = (
        "on:\n  pull_request:\n    branches: [main]\n"
        "  push:\n    branches: [main]\n  merge_group: false\n"
    )
    assert _on_triggers(scalar) != _CANONICAL_ON
    assert _on_triggers(scalar)["merge_group"] is not None

    flow_list = (
        "on:\n  pull_request:\n    branches: [main]\n"
        "  push:\n    branches: [main]\n  merge_group: [types]\n"
    )
    assert _on_triggers(flow_list) != _CANONICAL_ON
    assert _on_triggers(flow_list)["merge_group"] is not None
