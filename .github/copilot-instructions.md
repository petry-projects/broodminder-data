# Copilot Instructions — broodminder-export

## About

`broodminder-export` is a small, dependency-light Python CLI that pulls a user's
complete history out of the BroodMinder External User API into portable gzipped
JSON, NDJSON, and CSV files.

## Tech Stack

- **Runtime:** Python 3.10+ (CI runs 3.12)
- **Framework:** none — plain CLI scripts plus a reusable HTTP client
- **Testing:** pytest (live contract tests that auto-skip without an API key)
- **Linting:** `python -m compileall` in CI; SonarCloud for quality gate
- **Key libraries:** `httpx` (HTTP transport), `python-dotenv` (`.env` loading)

## Project Structure

```text
bm/
  __init__.py
  client.py          # reusable BroodMinderClient: auth, 429-aware retry, time windowing
scripts/
  discover.py        # auth check + account topology/schema sample
  extract_all.py     # resumable, budget-aware extraction (writes raw windows + manifest)
  flatten.py         # raw windows -> NDJSON/CSV/coverage (no API calls)
  cron_backfill.sh   # unattended multi-day backfill
tests/
  conftest.py        # shared fixtures; skips live tests when no key is set
  test_contract.py   # live contract tests pinning the API's real shape
openapi/
  broodminder-openapi.yaml  # reverse-engineered spec of the observed API
```

Conventions: keep `bm/client.py` transport-clean and dependency-light so it stays
easy to reuse. Scripts are entry points, not importable packages. Extracted data
lives under git-ignored `data/` and must never be committed.

## Local Dev Commands

- Install:    `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Discover:   `.venv/bin/python scripts/discover.py` (auth check + topology sample)
- Extract:    `.venv/bin/python scripts/extract_all.py --start 2025-01-01`
- Flatten:    `.venv/bin/python scripts/flatten.py` (raw -> NDJSON/CSV, no API calls)
- Compile:    `.venv/bin/python -m compileall bm scripts`
- Test:       `.venv/bin/python -m pytest -q`

## Required Environment Variables

Set these in a git-ignored `.env` (loaded automatically via `python-dotenv`):

- `BROODMINDER_API_KEY`: External User API key, tied to your account (request from
  support@broodminder.com). Live contract tests skip when this is unset.
- `BROODMINDER_BASE_URL`: API base URL. Optional; defaults to
  `https://external-api.mybroodminder.com`.

Never paste an API key or raw hive data into an issue, PR, commit, or test fixture.

## Testing Framework

- Runner: pytest (`testpaths = ["tests"]`, `addopts = "-v -ra"` in `pyproject.toml`)
- The suite hits the live API to pin its real behavior; it **auto-skips** without
  `BROODMINDER_API_KEY`, so CI and fresh clones stay green.
- No coverage threshold is enforced locally; SonarCloud tracks the quality gate.

## Repo-Specific Overrides

None. This repo follows the org-level standards; deviations, if any, are recorded
in `AGENTS.md`.

## Org Standards

See [petry-projects/.github — AGENTS.md](https://github.com/petry-projects/.github/blob/main/AGENTS.md)
for org-wide development standards, and this repo's [`AGENTS.md`](../AGENTS.md) for
any repo-specific notes.
