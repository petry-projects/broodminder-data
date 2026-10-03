# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.4] - 2026-10-03

### Added
- Added `CHANGELOG.md` documenting historical and current releases.
- Registered `Changelog` URL under `[project.urls]` in `pyproject.toml` so PyPI displays a direct release notes link in the project sidebar.
- Added packaging unit tests in `tests/test_packaging.py` validating `CHANGELOG.md` presence, structure, and `Changelog` project URL registration.

## [0.1.3] - 2026-10-03

### Added
- **Sensor Battery Health & Offline Reporting Monitor:**
  - Evaluates battery percentages against low-battery threshold (<80%) and flags devices needing battery replacement.
  - Detects silent dropouts ("not reporting" / stale sensors) with default 7-day cutoff.
  - Implemented core evaluation engine in `bm/battery.py` (`evaluate_battery_health`, `scan_device_health`, `print_health_summary`).
  - Added standalone CLI script `scripts/battery_health.py` and console script entrypoint `broodminder-battery`.
  - Added deterministic unit test suite in `tests/test_battery.py` with 98.2% test coverage.
- **Automated On-Merge Release Workflow:**
  - Added `push: branches: [main]` trigger in `.github/workflows/publish.yml` to automatically publish releases to PyPI and generate GitHub Releases when version is bumped.
  - Added version extraction and release gate helpers in `scripts/pypi_onboard.py` (`get_package_version`, `should_release_version`).
  - Implemented least-privilege isolated CI jobs (`prepare`, `build`, `publish`, `release`) with concurrency serialization to eliminate check-then-act race conditions.

## [0.1.2] - 2026-10-02

### Changed
- Cleaned up organizational metadata and documentation references.

## [0.1.1] - 2026-10-02

### Changed
- Cleaned up lingering repository references and aligned scope across project documents.

## [0.1.0] - 2026-10-02

### Added
- Initial PyPI release of `broodminder-data`.
- Reusable, typed Python client SDK (`bm.client.BroodMinderClient`) with automatic pagination, rate-limit awareness, and exponential backoff retry.
- Standalone CLI utilities:
  - Account topology discovery (`scripts/discover.py`).
  - Resumable, budget-aware bulk historical export (`scripts/extract_all.py`).
  - Incremental data flattener with deduplication into NDJSON and CSV (`scripts/flatten.py`).
- Authoritative OpenAPI 3.1 specification for the External User API (`openapi/broodminder-openapi.yaml`) with Redocly linting and interactive docs preview.
- Native Model Context Protocol (MCP) server (`bm/mcp_server.py`) built on FastMCP for LLM agent integration.
- PyPI onboarding probe, build verification, and Trusted Publishing OIDC workflow.

[0.1.4]: https://github.com/petry-projects/broodminder-data/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/petry-projects/broodminder-data/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/petry-projects/broodminder-data/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/petry-projects/broodminder-data/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/petry-projects/broodminder-data/releases/tag/v0.1.0
