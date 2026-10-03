# 🐝 broodminder-data

[![CI](https://github.com/petry-projects/broodminder-data/actions/workflows/ci.yml/badge.svg)](https://github.com/petry-projects/broodminder-data/actions/workflows/ci.yml)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=petry-projects_broodminder-data&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=petry-projects_broodminder-data)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![PyPI version](https://img.shields.io/pypi/v/broodminder-data.svg)](https://pypi.org/project/broodminder-data/)
[![OpenAPI 3.1](https://img.shields.io/badge/OpenAPI-3.1-brightgreen.svg)](openapi.yaml)
[![MCP Ready](https://img.shields.io/badge/MCP-Ready-purple.svg)](https://modelcontextprotocol.io/)

**Make [BroodMinder](https://broodminder.com) beehive data easily accessible across diverse consumption use cases — bulk export, periodic delta sync, OpenAPI 3.1, and AI agents via MCP.**

`broodminder-data` provides a unified developer platform for BroodMinder beehive sensor telemetry (internal/ambient temperature, relative humidity, scale weight, swarm indicators, acoustics, radar, and inspection notes). This project provides a **published OpenAPI 3.1 specification**, a **Python CLI and client SDK**, and a **Model Context Protocol (MCP) server** for AI agents.

> [!IMPORTANT]
> **Unofficial.** Not affiliated with or endorsed by BroodMinder. It uses the
> public External User API with *your own* API key. The bundled
> [OpenAPI spec](openapi/broodminder-openapi.yaml) (symlinked to [`openapi.yaml`](openapi.yaml))
> is reverse-engineered from observed live behavior — corrections via PR are welcome.

---

## 5 Core Consumption Modalities

```
                     ┌──────────────────────────────────────────────┐
                     │         BroodMinder Cloud API               │
                     └──────────────────────┬───────────────────────┘
                                            │
                                ┌───────────▼───────────┐
                                │   broodminder-data    │
                                └───────────┬───────────┘
                                            │
         ┌──────────────────┬───────────────┼───────────────┬──────────────────┐
         ▼                  ▼               ▼               ▼                  ▼
  📦 Bulk Export      🔄 Delta Sync   📜 OpenAPI 3.1   🤖 MCP Server    🐍 Python SDK / CLI
  Full history       Incremental     Published spec   Claude, Cursor,   Typed models &
  JSON / CSV / NDJSON catch-up cron   & Redocly docs   Antigravity CLI   scriptable client
```

1. **📦 Bulk Historical Export**: Walks your entire apiary → hive → device hierarchy with rate-limit-aware, resumable 180-day windowing to extract complete multi-year histories into compressed JSON, NDJSON, and CSV.
2. **🔄 Periodic Delta Sync**: Lightweight, incremental polling engine designed for regular cron or background services, fetching only new readings since the last checkpoint while buffering for late-arriving BLE uploads.
3. **📜 Published OpenAPI 3.1 Specification**: Formal, validated OpenAPI 3.1 contract covering all observed endpoints, query parameters, error responses (including HTTP 412 auth responses), and canonical telemetry schemas.
4. **🤖 Model Context Protocol (MCP) Server**: Native MCP integration (`broodminder-mcp`) connecting AI agents (**Claude Desktop, Antigravity CLI, Cursor, Windsurf, Claude Code**) directly to hive metrics, temperature trends, weight deltas, and notes.
5. **🐍 Unified Python Client Library & CLI**: Strongly-typed domain models, offline sandbox mode, and a standalone `broodminder` CLI.

---

## Architecture Discussions & Roadmap

We are tracking each expanded capability in GitHub Discussions. Join the conversation:

- 💬 [**Discussion #148: Periodic Delta Sync Engine for Incremental Telemetry & Continuous Ingestion**](https://github.com/petry-projects/broodminder-data/discussions/148)
- 💬 [**Discussion #149: Published OpenAPI 3.1 Specification & Interactive Documentation (Redocly/Swagger)**](https://github.com/petry-projects/broodminder-data/discussions/149)
- 💬 [**Discussion #150: Model Context Protocol (MCP) Server for Hive Monitoring & AI Agent Integration**](https://github.com/petry-projects/broodminder-data/discussions/150)
- 💬 [**Discussion #151: Unified Python Client SDK and Standalone CLI Library**](https://github.com/petry-projects/broodminder-data/discussions/151)

---

## Table of Contents

- [Features](#features)
- [What You Get](#what-you-get)
- [Get an API Key](#get-an-api-key)
- [Installation](#installation)
- [Configuration](#configuration)
- [Quickstart Usage](#quickstart-usage)
  - [1. Discover Account Topology](#1-discover-account-topology)
  - [2. Bulk History Export](#2-bulk-history-export)
  - [3. Incremental Catch-up Sync](#3-incremental-catch-up-sync)
  - [4. Build Analysis-Ready Datasets](#4-build-analysis-ready-datasets)
  - [5. Inspect Battery Health & Offline Sensors](#5-inspect-battery-health--offline-sensors)
- [🔋 Battery Health & Offline Sensor Monitoring](#-battery-health--offline-sensor-monitoring)
- [OpenAPI 3.1 Specification & Interactive Docs](#openapi-31-specification--interactive-docs)
- [Model Context Protocol (MCP) Server](#model-context-protocol-mcp-server)
- [Python SDK Usage](#python-sdk-usage)
- [PyPI Packaging & Automated Publishing](#pypi-packaging--automated-publishing)
- [Output Files & Schema](#output-files--schema)
- [API Behavior & Rate Limits](#api-behavior--rate-limits)
- [Testing & Quality Gates](#testing--quality-gates)
- [Project Structure](#project-structure)
- [Privacy & Security](#privacy--security)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- 📦 **Complete export** — walks every apiary → hive → device and pulls all readings and notes across your entire history.
- 🔁 **Resumable** — checkpoints each time window; stop and re-run anytime and it skips what's already fetched.
- 🚦 **Rate-limit-aware** — respects the ~1000 calls/day cap, self-throttles, and resumes cleanly after a `429`.
- 🧹 **Idempotent outputs** — de-duplicates overlapping windows, so re-runs never double-count.
- 🗜️ **Compact** — raw and flattened outputs are gzipped (a multi-year, 90-hive account is tens of MB).
- 📜 **OpenAPI 3.1 spec** — formal machine-readable API definition with Redocly validation.
- 🤖 **Agent-ready** — MCP server architecture for conversational hive analysis and automated inspections.
- 🧪 **Contract-tested** — live contract test suite pins the API's real behavior and acts as a canary when upstream changes.
- 🔌 **Reusable client** — `bm/client.py` is transport-clean and easy to lift into a notebook, script, or MCP server.

---

## What You Get

A flattened, analysis-ready row per reading:

| field | description |
|---|---|
| `apiaryId`, `apiaryName` | apiary the hive belongs to |
| `hiveId`, `hiveName` | hive identity |
| `positionID`, `deviceId` | sensor position + device (`deviceId` is the unique series key) |
| `timestamp`, `datetime` | Unix epoch seconds (UTC) + ISO-8601 string |
| `batteryLevel`, `chargeRemaining` | device power (nullable; the two alternate) |
| `m_temperature` | temperature (all devices) |
| `m_humidity` | relative humidity (humidity-capable devices) |
| `m_weight` | scale weight (hives with a scale) |
| `m_swarmState` | BroodMinder swarm indicator |
| `m_audio` | acoustic reading (audio-capable devices; unit/scale unconfirmed) |
| `m_radar` | movement/activity indicator (radar-equipped devices) |

> Metric presence varies by device type — temperature is near-universal; weight
> appears only on hives with a scale; audio and radar appear on specialized monitors.

---

## Get an API Key

The External User API is in alpha. Request a key from BroodMinder
([support@broodminder.com](mailto:support@broodminder.com)). The key is tied to your account and only authorizes
access to your own data.

---

## Installation

### From PyPI
```bash
# Core package
pip install broodminder-data

# With Model Context Protocol (MCP) agent support:
pip install "broodminder-data[mcp]"
```

### From Source (Local Development)
```bash
git clone https://github.com/petry-projects/broodminder-data.git
cd broodminder-data

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e ".[mcp,dev]"
```

Requires **Python 3.10+**.

---

## Configuration

```bash
cp .env.example .env
```

Edit `.env` and paste your key:

```dotenv
BROODMINDER_API_KEY=your-api-key-here
BROODMINDER_BASE_URL=https://external-api.mybroodminder.com
```

`.env` is git-ignored and never leaves your machine.

---

## Quickstart Usage

### 1. Discover Account Topology
Confirm auth and see your apiaries, hives, and a sensor data sample:
```bash
.venv/bin/python scripts/discover.py
```

### 2. Bulk History Export
Pull historical telemetry (resumable; respects the daily quota):
```bash
.venv/bin/python scripts/extract_all.py --start 2025-01-01
```

Options:
| flag | default | purpose |
|---|---|---|
| `--start YYYY-MM-DD` | `2021-01-01` | history start |
| `--end YYYY-MM-DD` | today (UTC) | history end |
| `--catchup` | off | resume forward from each hive's latest completed window in `manifest.json` |
| `--window-days N` | `180` | request window size (API caps at ~6 months) |
| `--apiary NAME\|ID` | all | limit to one apiary (repeatable) |
| `--max-calls N` | `900` | stop before this many API calls (daily-cap guard) |
| `--reverse` | off | walk newest→oldest (for backfilling) |
| `--stop-after-empty N` | `0` | with `--reverse`, stop a hive after N empty windows |
| `--no-notes` | off | skip the notes endpoint |

### 3. Incremental Catch-up Sync
Resume forward from each hive's latest extracted window:
```bash
.venv/bin/python scripts/extract_all.py --catchup
```

### 4. Build Analysis-Ready Datasets
Convert raw windows into clean, de-duplicated NDJSON and CSV:
```bash
.venv/bin/python scripts/flatten.py --merge
```

### 5. Inspect Battery Health & Offline Sensors
Identify sensors that have low batteries or stopped reporting:
```bash
.venv/bin/python scripts/battery_health.py
# or via entrypoint: broodminder-battery
```

---

## 🔋 Battery Health & Offline Sensor Monitoring

Answers the critical question: **"What batteries are low and need to be changed?"**

In beehive deployments, two distinct conditions indicate battery replacement or inspection:
1. **Low Battery (<80%):** Cold cluster and winter ambient temperatures accelerate coin-cell and alkaline voltage dropoff. Sensors dipping below 80% should be checked or replaced before winter cluster closure.
2. **"Not Reporting" (Silent Dropouts):** When a battery fully dies in the field, the sensor simply goes dark. Sensors that have not reported data for more than 7 days are flagged as stale.

```bash
# Scan local dataset and show devices needing attention (<80% or >7d stale)
.venv/bin/python scripts/battery_health.py

# Show all devices including healthy ones
.venv/bin/python scripts/battery_health.py --all

# Custom warning thresholds
.venv/bin/python scripts/battery_health.py --threshold 75 --stale-days 5

# Filter by apiary
.venv/bin/python scripts/battery_health.py --apiary "Home"

# Export as JSON or CSV
.venv/bin/python scripts/battery_health.py --format json
.venv/bin/python scripts/battery_health.py --format csv

# Automation/Alerting mode (exits with code 1 if devices need attention)
.venv/bin/python scripts/battery_health.py --check
```

---

## OpenAPI 3.1 Specification & Interactive Docs

`broodminder-data` maintains a formal [OpenAPI 3.1 specification](openapi/broodminder-openapi.yaml) (also accessible via the root symlink [`openapi.yaml`](openapi.yaml)).

### Local Linting & Preview
Using [Redocly CLI](https://redocly.com/docs/cli/):

```bash
# Lint specification against OpenAPI 3.1 rules
npx @redocly/cli lint openapi.yaml

# Launch interactive documentation preview server
npx @redocly/cli preview-docs openapi.yaml
```

---

## Model Context Protocol (MCP) Server

Connect your hive data directly to AI agents (**Claude Desktop, Antigravity CLI, Cursor, Windsurf, Claude Code**):

### Agent Configuration (`claude_desktop_config.json` or `mcp.json`)

```json
{
  "mcpServers": {
    "broodminder": {
      "command": "python3",
      "args": ["-m", "bm.mcp_server"],
      "env": {
        "BROODMINDER_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

### Core MCP Tools
- `get_apiary_summary`: High-level inventory of apiaries, hives, and device counts.
- `get_hive_status`: Latest sensor telemetry (brood temperature, ambient temperature, humidity, weight).
- `get_telemetry_trends`: Time-series rollups (min, max, mean, delta) over specified lookback windows.
- `get_hive_notes`: Recent inspection notes, treatments, and queen observations.
- `get_device_health`: Battery levels and sync freshness across sensors.

---

## Python SDK Usage

```python
from bm.client import BroodMinderClient

# Automatically reads BROODMINDER_API_KEY from environment or .env
client = BroodMinderClient()

# List apiaries and hives
apiaries = client.get_apiaries()
for apiary in apiaries:
    print(f"Apiary: {apiary['name']} (ID: {apiary['apiary_id']})")
    hives = client.get_hives(apiary_id=apiary['apiary_id'])
    for hive in hives:
        print(f"  - Hive: {hive['name']}")

# Fetch time-series readings for a device (epoch seconds)
readings = client.get_device_readings(
    device_id="42:11:22:33:44:55",
    start=1704067200,  # 2024-01-01T00:00:00Z
    end=1706745600,    # 2024-02-01T00:00:00Z
)
print(f"Fetched {len(readings)} readings")
```

---

## PyPI Packaging & Automated Publishing

`broodminder-data` uses automated, tokenless **PyPI Trusted Publishing (OIDC)**.

### 1. Tokenless Trusted Publishing Architecture

Releases publish directly from GitHub Actions without storing long-lived, sensitive API tokens:
- GitHub Actions exchanges its cryptographic OIDC ID token with PyPI for a short-lived upload token.
- PyPI validates the repository (`petry-projects/broodminder-data`), workflow (`publish.yml`), and environment (`pypi`).

### 2. Onboarding Steps (First Release Setup)

Before publishing the first release, the account owner registers a **Pending Publisher** on PyPI:
1. Log in to [pypi.org/manage/account/publishing/](https://pypi.org/manage/account/publishing/).
2. Under **"Add a pending publisher"**, enter:
   - **PyPI Project Name:** `broodminder-data`
   - **Owner:** `petry-projects`
   - **Repository name:** `broodminder-data`
   - **Workflow name:** `publish.yml`
   - **Environment name:** `pypi`
3. Click **"Add publisher"**.

### 3. Local Onboarding & Verification

Run the onboarding tool to inspect registry availability, build the sdist and wheel, and verify package metadata:

```bash
# Probe PyPI status, build sdist/wheel, and run twine verification
python scripts/pypi_onboard.py
```

### 4. Automated Publishing Workflow

- **Automated on Merge to `main`:** When a PR bumping the package `version` in `pyproject.toml` is merged to `main`, `.github/workflows/publish.yml` detects that git tag `v<version>` does not exist yet, builds and verifies the distribution packages with `twine check --strict`, publishes to PyPI tokenlessly via Trusted Publishing OIDC, and automatically creates the git tag and GitHub Release with generated release notes.
- **GitHub Release Trigger:** Publishing a release manually or via the GitHub UI also triggers `.github/workflows/publish.yml`.
- **Manual Trigger (with Dry Run):** You can run the workflow manually via `workflow_dispatch` with `dry_run: true` (default) to test artifact generation without releasing.

---

## Output Files & Schema

Extracted data is saved under `data/extract/` (git-ignored):

| file | contents |
|---|---|
| `raw/<hiveId>/<start>-<end>.readings.json.gz` | lossless raw responses (replay source) |
| `raw/<hiveId>/<start>-<end>.notes.json.gz` | lossless raw notes |
| `manifest.json` | per-window progress + row counts (drives resume) |
| `readings.ndjson.gz` | one JSON object per reading (analysis-ready) |
| `readings.csv.gz` | same, columnar |
| `notes.ndjson` | one object per note |
| `coverage.json` | per-hive earliest/latest reading + counts |

---

## API Behavior & Rate Limits

The machine-readable description is in [`openapi.yaml`](openapi.yaml). Notable quirks handled automatically:

- **Authentication:** `X-Api-Key` header. Missing or invalid keys return **HTTP 412** (not 401/403).
- **Time Windows:** Maximum ~6 months per request — auto-chunked.
- **No Pagination:** Each window is a single JSON array payload.
- **Rate Limit:** ~1,000 calls per UTC day with no `Retry-After` header — the client tracks calls, self-throttles, and catches `429` responses cleanly.

---

## Testing & Quality Gates

```bash
# Run unit & offline tests
.venv/bin/python -m pytest

# Byte-compile verification
python3 -m compileall bm scripts tests
```

- **Offline tests (`tests/test_offline.py`, `tests/test_scripts_refactor.py`):** Run fast and hermetically without network access.
- **Live contract tests (`tests/test_contract.py`):** Automatically run when `BROODMINDER_API_KEY` is present to verify live API compatibility; skip gracefully otherwise.
- **OpenAPI validation:** `npx @redocly/cli lint openapi.yaml`.

---

## Project Structure

```
broodminder-data/
├── bm/
│   ├── __init__.py
│   └── client.py            # Reusable BroodMinderClient (auth, retry, windowing)
├── scripts/
│   ├── discover.py          # Auth check + account topology/schema sample
│   ├── extract_all.py       # Resumable, budget-aware extraction (--catchup)
│   ├── flatten.py           # Raw → NDJSON/CSV/coverage (--merge)
│   ├── cron_sync.sh         # Routine unattended forward catch-up sync
│   └── cron_backfill.sh     # Initial unattended multi-day backfill
├── tests/
│   ├── conftest.py
│   ├── test_offline.py      # Fast deterministic unit tests
│   ├── test_scripts_refactor.py # Script unit test coverage
│   └── test_contract.py     # Live contract tests (skip without key)
├── openapi/
│   └── broodminder-openapi.yaml # OpenAPI 3.1 specification
├── openapi.yaml -> openapi/broodminder-openapi.yaml # Root symlink
├── redocly.yaml             # Redocly linting & preview configuration
├── requirements.txt         # Runtime dependencies
└── pyproject.toml           # Build configuration & metadata
```

---

## Privacy & Security

`.env` (your API key) and `data/` (your extracted hive data) are **git-ignored** and never leave your machine.
All test fixtures use synthetic or anonymized values. Please **never** paste an API key or raw hive telemetry into an issue, PR, or discussion.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the [Code of Conduct](CODE_OF_CONDUCT.md).
Check out open [Discussions](https://github.com/petry-projects/broodminder-data/discussions) to weigh in on upcoming features and architectural decisions.

---

## License

[MIT](LICENSE) © Petry Projects.
