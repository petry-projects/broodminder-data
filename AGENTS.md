# AGENTS.md — Agent Guidelines & Project Standards

This file defines project-specific development standards for **broodminder-data** (formerly `broodminder-export`).
It follows the [AGENTS.md convention](https://agents.md/) and extends the organization-wide engineering standards.

> **Organization standards:** This repository inherits shared development and security standards from
> [`petry-projects/.github/AGENTS.md`](https://github.com/petry-projects/.github/blob/main/AGENTS.md).
> Read that file before making changes that touch CI, agent configuration, repo settings, or labels.

---

## 1. Project Overview & Architecture

`broodminder-data` makes BroodMinder beehive sensor telemetry easily accessible across diverse consumption use cases:
1. **Bulk Historical Export:** Resumable, rate-limit-aware extraction into compressed JSON, NDJSON, and CSV.
2. **Periodic Delta Sync:** Incremental catch-up sync for downstream time-series databases, home automation, and cron jobs.
3. **OpenAPI 3.1 Specification:** Published, validated OpenAPI 3.1 specification for the External User API with Redocly interactive docs.
4. **AI Agents via Model Context Protocol (MCP):** Native MCP server (`broodminder-mcp`) enabling LLM agents to query hive health, temperature trends, scale weights, and inspection notes.
5. **Python CLI & Client SDK:** Reusable, typed Python library and standalone `broodminder` CLI.

### Architectural Layout

```
broodminder-data/
├── bm/
│   ├── __init__.py           # Public exports
│   ├── client.py             # Reusable BroodMinderClient (auth, retry, windowing)
│   └── mcp_server.py         # FastMCP server for AI agents
├── scripts/
│   ├── discover.py           # Account topology and schema inspection
│   ├── extract_all.py        # Resumable, budget-aware bulk extraction
│   ├── flatten.py            # Raw windows -> NDJSON/CSV/coverage transformation
│   ├── cron_sync.sh          # Periodic forward catch-up sync
│   └── cron_backfill.sh      # Unattended multi-day historical backfill
├── tests/
│   ├── conftest.py           # Shared fixtures & skip markers
│   ├── test_offline.py       # Fast, deterministic unit tests
│   ├── test_scripts_refactor.py # Script logic and data formatting tests
│   └── test_contract.py      # Live contract tests against BroodMinder API
├── openapi/
│   └── broodminder-openapi.yaml # Authoritative OpenAPI 3.1 specification
├── openapi.yaml              # Root symlink to openapi/broodminder-openapi.yaml
├── redocly.yaml              # Redocly linting and documentation preview rules
├── pyproject.toml            # PEP 621 build configuration with optional extras
└── requirements.txt          # Runtime dependencies
```

---

## 2. Local Development Commands

- Install: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Install with MCP: `.venv/bin/pip install -e ".[mcp]"`
- Dev run (Discover): `.venv/bin/python scripts/discover.py`
- Dev run (Extract): `.venv/bin/python scripts/extract_all.py --start 2025-01-01`
- Dev run (Flatten): `.venv/bin/python scripts/flatten.py --merge`
- Test: `.venv/bin/python -m pytest`
- Lint: `.venv/bin/python -m compileall bm scripts tests`
- OpenAPI Lint: `npx @redocly/cli lint openapi.yaml`
- OpenAPI Preview: `npx @redocly/cli preview-docs openapi.yaml`

---

## 3. Required Environment Variables

- `BROODMINDER_API_KEY`: External User API key tied to your account (request from support@broodminder.com). Example: `a1b2c3d4e5f6...`. Live contract tests skip automatically when this is unset.
- `BROODMINDER_BASE_URL`: API base URL. Optional; defaults to `https://external-api.mybroodminder.com`.

---

## 4. Privacy & Zero-Credential Leakage Mandate

- **NEVER commit secrets or live hive data.** `BROODMINDER_API_KEY` lives in `.env` and extracted data lives under `data/` — both are git-ignored.
- **Synthetic Test Data:** Unit tests and offline fixtures must use synthetic or mock payloads. Never commit real API keys or identifiable hive coordinates into test fixtures, issues, or PRs.

---

## 5. Test-Driven Development (TDD) & Quality Standards

- **TDD is Mandatory:** Write unit tests before implementing new client features, CLI commands, or MCP tools.
- **Fast & Deterministic Unit Tests:** Offline tests (`test_offline.py`, `test_scripts_refactor.py`) must run without network dependencies and finish in seconds.
- **Live Contract Tests:** `test_contract.py` pins live API contracts and auto-skips when `BROODMINDER_API_KEY` is not present, keeping CI runs green on PRs from contributors.