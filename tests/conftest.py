"""Shared fixtures for the BroodMinder contract tests.

IDs are pulled from data/discovery.json when present (cheap, no live call),
falling back to one live /apiaries call. Tests are written to be lenient about
the alpha API's schema quirks (bare arrays, nullable battery fields, notes that
may be a list *or* an object) while still asserting the invariants we depend on.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bm.client import BroodMinderClient, now_epoch  # noqa: E402

DAY = 24 * 60 * 60
DISCOVERY = ROOT / "data" / "discovery.json"


def pytest_collection_modifyitems(config, items):
    """These are LIVE contract tests against the real API. With no key set
    (e.g. CI, or a fresh clone), skip live contract tests rather than erroring
    — so the suite is green out of the box and offline unit tests still run.
    """
    if os.environ.get("BROODMINDER_API_KEY"):
        return
    skip = pytest.mark.skip(reason="BROODMINDER_API_KEY not set; live contract tests skipped")
    for item in items:
        if "test_contract" in item.nodeid:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def client():
    with BroodMinderClient() as c:
        yield c


@pytest.fixture(scope="session")
def apiaries(client):
    """Apiaries from discovery cache if available, else one live call."""
    if DISCOVERY.exists():
        data = json.loads(DISCOVERY.read_text())
        if data.get("apiaries"):
            return data["apiaries"]
    return client.apiaries()


@pytest.fixture(scope="session")
def all_hives(apiaries):
    hives = []
    for ap in apiaries:
        for hv in ap.get("hives", []):
            hives.append({**hv, "apiaryId": ap.get("apiaryId"), "apiaryName": ap.get("name")})
    return hives


@pytest.fixture(scope="session")
def sample_hive_id(all_hives):
    assert all_hives, "no hives found for this account"
    return all_hives[0]["hiveId"]


@pytest.fixture(scope="session")
def sample_device_id():
    """A real device id, harvested from the discovery readings sample."""
    if DISCOVERY.exists():
        data = json.loads(DISCOVERY.read_text())
        for pos in data.get("hive_readings_sample", []) or []:
            for r in pos.get("readings", []) or []:
                if r.get("deviceId"):
                    return r["deviceId"]
    return None


@pytest.fixture(scope="session")
def recent_window():
    """A small, cheap, recent time window (7 days)."""
    end = now_epoch()
    return end - 7 * DAY, end
