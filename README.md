# 🐝 broodminder-export

[![CI](https://github.com/petry-projects/broodminder-export/actions/workflows/ci.yml/badge.svg)](https://github.com/petry-projects/broodminder-export/actions/workflows/ci.yml)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=petry-projects_broodminder-export&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=petry-projects_broodminder-export)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

**Get *all* of your data out of the [BroodMinder](https://broodminder.com) cloud — apiaries, hives, devices, every sensor reading, and notes — into plain, portable files you own.**

`broodminder-export` is a small, dependency-light Python tool that talks to the
BroodMinder External User API and pulls your complete history into gzipped JSON,
NDJSON, and CSV. It is **resumable**, **rate-limit-aware**, and **idempotent**,
so even large multi-apiary accounts with years of history extract reliably within
the API's daily quota.

> [!IMPORTANT]
> **Unofficial.** Not affiliated with or endorsed by BroodMinder. It uses the
> public External User API with *your own* API key. The bundled
> [OpenAPI spec](openapi/broodminder-openapi.yaml) is reverse-engineered from
> observed behavior — corrections via PR are welcome.

---

## Table of contents

- [Why](#why)
- [Features](#features)
- [What you get](#what-you-get)
- [Get an API key](#get-an-api-key)
- [Install](#install)
- [Configure](#configure)
- [Usage](#usage)
- [Full-history extraction](#full-history-extraction)
- [Output files](#output-files)
- [How it works](#how-it-works)
- [API behavior (observed)](#api-behavior-observed)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Privacy](#privacy)
- [Contributing](#contributing)
- [License](#license)

## Why

The BroodMinder app and web app are great, but your data lives in their cloud.
If you want to run your own analytics, build dashboards, train models, keep an
archive you control, or migrate elsewhere, you need a clean export. That's all
this does — reliably, and completely.

## Features

- 📦 **Complete export** — walks every apiary → hive → device and pulls all
  readings and notes across your entire history.
- 🔁 **Resumable** — checkpoints each time window; stop and re-run anytime and it
  skips what's already fetched.
- 🚦 **Rate-limit-aware** — respects the ~1000 calls/day cap, self-throttles, and
  resumes cleanly after a `429`.
- 🧹 **Idempotent outputs** — de-duplicates overlapping windows, so re-runs never
  double-count.
- 🗜️ **Compact** — raw and flattened outputs are gzipped (a multi-year, 90-hive
  account is tens of MB).
- 🧪 **Contract-tested** — a live test suite pins the API's real behavior and acts
  as a canary when it changes.
- 🔌 **Reusable client** — `bm/client.py` is transport-clean and easy to lift into
  a notebook, service, or MCP server.

## What you get

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
| `m_audio` | acoustic frequency/amplitude (audio-capable devices) |
| `m_radar` | movement/activity indicator (radar-equipped devices) |

> Metric presence varies by device type — temperature is near-universal; weight
> appears only on hives with a scale; audio and radar appear on specialized monitors.

## Get an API key

The External User API is in alpha. Request a key from BroodMinder
(support@broodminder.com). The key is tied to your account and only authorizes
access to your own data.

## Install

```bash
git clone https://github.com/petry-projects/broodminder-export.git
cd broodminder-export

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Requires **Python 3.10+**. Runtime dependencies: `httpx`, `python-dotenv`
(`pytest` for the test suite).

## Configure

```bash
cp .env.example .env
```

Edit `.env` and paste your key:

```dotenv
BROODMINDER_API_KEY=your-api-key-here
BROODMINDER_BASE_URL=https://external-api.mybroodminder.com
```

`.env` is git-ignored and never leaves your machine.

## Usage

```bash
# 1. Confirm auth + see your account topology (apiaries, hives, a data sample)
.venv/bin/python scripts/discover.py

# 2. Pull history (resumable; stops before the daily cap)
.venv/bin/python scripts/extract_all.py --start 2025-01-01

# 3. Catch up forward from your latest extracted window
.venv/bin/python scripts/extract_all.py --catchup

# 4. Build analysis-ready NDJSON + CSV (use --merge to preserve existing data)
.venv/bin/python scripts/flatten.py --merge
```

`extract_all.py` options:

| flag | default | purpose |
|---|---|---|
| `--start YYYY-MM-DD` | `2021-01-01` | history start |
| `--end YYYY-MM-DD` | today (UTC) | history end |
| `--catchup` | off | resume forward from latest window in `manifest.json` |
| `--window-days N` | `180` | request window size (API caps at ~6 months) |
| `--apiary NAME\|ID` | all | limit to one apiary (repeatable) |
| `--max-calls N` | `900` | stop before this many API calls (daily-cap guard) |
| `--reverse` | off | walk newest→oldest (for backfilling) |
| `--stop-after-empty N` | `0` | with `--reverse`, stop a hive after N empty windows |
| `--no-notes` | off | skip the notes endpoint |

`flatten.py` options:

| flag | default | purpose |
|---|---|---|
| `--merge` | off | merge incremental raw windows into existing outputs |
| `--no-csv` | off | skip generating `readings.csv.gz` |

## Full-history extraction

The API is capped at **~1000 calls/day**. A small account finishes in one run;
a large one (many hives, several years) spans a few days. The extractor is
**resumable** — just run it again and it skips windows already recorded in
`data/extract/manifest.json`.

To walk *backwards* to the beginning of your data while skipping hives that have
no old data, use **backfill mode**:

```bash
.venv/bin/python scripts/extract_all.py \
    --start 2016-01-01 --end 2025-01-01 \
    --reverse --stop-after-empty 3
```

For a fully unattended, multi-day backfill, [`scripts/cron_backfill.sh`](scripts/cron_backfill.sh)
runs the resumable backfill + flatten on a schedule (idempotent, safe to repeat):

```bash
( crontab -l 2>/dev/null; \
  echo "20 */6 * * * $(pwd)/scripts/cron_backfill.sh" ) | crontab -
```

For routine ongoing catch-up exports after the initial backfill is complete,
[`scripts/cron_sync.sh`](scripts/cron_sync.sh) pulls only new windows forward and
merges them:

```bash
( crontab -l 2>/dev/null; \
  echo "0 4 * * * $(pwd)/scripts/cron_sync.sh" ) | crontab -
```

## Output files

Under `data/extract/` (git-ignored):

| file | contents |
|---|---|
| `raw/<hiveId>/<start>-<end>.readings.json.gz` | lossless raw responses (replay source) |
| `raw/<hiveId>/<start>-<end>.notes.json.gz` | lossless raw notes |
| `manifest.json` | per-window progress + row counts (drives resume) |
| `readings.ndjson.gz` | one JSON object per reading (analysis-ready) |
| `readings.csv.gz` | same, columnar |
| `notes.ndjson` | one object per note |
| `coverage.json` | per-hive earliest/latest reading + counts |

## How it works

1. **`discover.py`** calls `/user/metadata/apiaries`, confirms auth, and dumps a
   small sample so you can see your real schema before a big pull.
2. **`extract_all.py`** iterates apiaries → hives, chunks the requested range into
   ≤6-month windows (`iter_windows`), and writes each window's raw response to
   disk, recording it in `manifest.json`. It counts API calls and stops before
   `--max-calls`; a server `429` is caught, saved, and resumable.
3. **`flatten.py`** reads the raw windows (no API calls), de-duplicates by
   `(positionID, deviceId, timestamp)`, and emits NDJSON + CSV + a coverage
   summary. Re-run it any time to rebuild outputs.

## API behavior (observed)

The machine-readable description is in
[`openapi/broodminder-openapi.yaml`](openapi/broodminder-openapi.yaml). Notable
quirks this tool handles for you:

- **Auth** via `X-Api-Key`. A missing/invalid key returns **HTTP 412** (not the
  usual 401/403).
- **6-month window cap** per readings/notes request — chunked automatically.
- **No pagination** — within a window the whole result is one JSON array (can be
  several MB); there's no cursor and no "changed-since" delta endpoint, so the
  tool windows by time and de-duplicates.
- **Daily rate limit** (~1000/day; body `"daily limit exceeded"`) with no
  `Retry-After` header — the tool self-throttles and resumes.

## Testing

```bash
.venv/bin/python -m pytest
```

The contract suite hits the live API to validate every endpoint's shape and pin
its real behavior. It **skips automatically** when `BROODMINDER_API_KEY` is
unset (so CI and fresh clones stay green), and runs for real when a key is
present.

## Project structure

```
broodminder-export/
├── bm/
│   ├── __init__.py
│   └── client.py            # reusable BroodMinderClient (auth, retry, windowing)
├── scripts/
│   ├── discover.py          # auth check + topology/schema sample
│   ├── extract_all.py       # resumable, budget-aware extraction (--catchup)
│   ├── flatten.py           # raw → NDJSON/CSV/coverage (--merge)
│   ├── cron_sync.sh         # routine unattended forward catch-up sync
│   └── cron_backfill.sh     # initial unattended multi-day backfill
├── tests/
│   ├── conftest.py
│   ├── test_offline.py      # fast deterministic unit tests (run in CI)
│   └── test_contract.py     # live contract tests (skip without a key)
├── openapi/
│   └── broodminder-openapi.yaml
├── requirements.txt
└── pyproject.toml
```

## Privacy

`.env` (your key) and `data/` (your extracted hive data) are **git-ignored** and
never leave your machine. This repository contains code only. Please **never**
paste an API key or raw hive data into an issue or PR.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the
[Code of Conduct](CODE_OF_CONDUCT.md). Bug reports about API drift — with a
**redacted** sample — are especially useful.

## License

[MIT](LICENSE) © Petry Projects.
