# Contributing to broodminder-data

Thanks for helping make BroodMinder beehive data easily accessible across bulk
export, periodic delta sync, OpenAPI, and AI agents via MCP! Contributions that
improve extraction reliability, expand CLI/SDK features, add agent tooling, fix
API-behavior drift, or sharpen the docs/OpenAPI spec are very welcome.

## Ground rules

- **Never commit secrets or data.** Your API key lives in `.env` and your hive
  data lives in `data/` — both are git-ignored. Do not paste keys or raw
  readings into issues, PRs, or test fixtures.
- Keep the core client (`bm/client.py`) dependency-light and transport-clean so
  it stays easy to reuse and port.

## Getting started

1. Fork the repository and create a branch off `main`.
2. Set up a dev environment:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. Make your change. If it touches API behavior, update the OpenAPI spec in
   `openapi/` to match what you observed.
4. Run the checks:
   ```bash
   python -m compileall bm scripts
   .venv/bin/python -m pytest      # live tests skip without BROODMINDER_API_KEY
   ```
5. Open a pull request describing what you changed and how you verified it. CI
   must pass before review.

## Reporting API quirks

If BroodMinder's API changes or you spot behavior the OpenAPI spec gets wrong,
please open an issue (or PR the spec) with the endpoint, the request, and a
**redacted** sample response. The `x-observed` / `x-question` annotations in the
spec are the right place to capture open questions.

## Code of conduct

This project follows our [Code of Conduct](CODE_OF_CONDUCT.md). By participating
you agree to uphold it.
