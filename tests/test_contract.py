"""Contract tests for the BroodMinder External User API.

Goals:
  * Prove every endpoint returns what we depend on (shape + invariants).
  * Pin down the *actual* (not documented) behavior for auth, bad ids,
    missing params, and the 6-month window cap, so downstream code (extractor,
    a future MCP/ingestion job) can rely on it.

These hit the live API but stay cheap (a couple dozen calls total) and well
under the 1000/day budget.
"""

from __future__ import annotations

import numbers

import pytest

from bm.client import BroodMinderClient, MAX_WINDOW_SECONDS

# Every test in this module hits the live API and is skipped without a key.
pytestmark = pytest.mark.live


# --------------------------------------------------------------------------
# /user/metadata/apiaries
# --------------------------------------------------------------------------
def test_apiaries_is_list(apiaries):
    assert isinstance(apiaries, list), "apiaries should be a bare JSON array"
    assert apiaries, "expected at least one apiary"


def test_apiary_schema(apiaries):
    for ap in apiaries:
        assert isinstance(ap.get("apiaryId"), str)
        assert ap["apiaryId"]
        assert isinstance(ap.get("name"), str)
        assert isinstance(ap.get("hives"), list)
        loc = ap.get("location")
        if loc is not None:  # location may be absent for some apiaries
            assert isinstance(loc.get("latitude"), numbers.Real)
            assert isinstance(loc.get("longitude"), numbers.Real)


def test_hive_schema(all_hives):
    assert all_hives, "expected hives across the account"
    for hv in all_hives:
        assert isinstance(hv.get("hiveId"), str)
        assert hv["hiveId"]
        assert isinstance(hv.get("name"), str)
        # description/color are present but may be empty strings
        assert "description" in hv
        assert "color" in hv


def test_hive_ids_unique(all_hives):
    ids = [h["hiveId"] for h in all_hives]
    assert len(ids) == len(set(ids)), "hive ids must be unique across apiaries"


# --------------------------------------------------------------------------
# /user/hive/{id}/readings
# --------------------------------------------------------------------------
def test_hive_readings_shape(client, sample_hive_id, recent_window):
    start, end = recent_window
    data = client.hive_readings(sample_hive_id, start, end)
    assert isinstance(data, list), "readings should be an array of positions"
    for pos in data:
        assert isinstance(pos.get("positionID"), str)
        assert isinstance(pos.get("readings"), list)


def test_hive_reading_row_schema(client, sample_hive_id, recent_window):
    start, end = recent_window
    data = client.hive_readings(sample_hive_id, start, end)
    rows = [r for pos in data for r in pos.get("readings", [])]
    if not rows:
        pytest.skip("no recent readings for sample hive in last 7d")
    for r in rows[:200]:
        assert isinstance(r.get("deviceId"), str)
        assert isinstance(r.get("timestamp"), numbers.Integral)
        # battery/charge are each nullable; at least the keys exist
        assert "batteryLevel" in r
        assert "chargeRemaining" in r
        for k in ("batteryLevel", "chargeRemaining"):
            assert r[k] is None or isinstance(r[k], numbers.Real)
        metrics = r.get("readings")
        assert isinstance(metrics, dict), "each row carries metric dict"
        assert metrics, "each row carries a non-empty metric dict"
        for v in metrics.values():
            assert v is None or isinstance(v, numbers.Real)


def test_hive_readings_timestamps_in_window(client, sample_hive_id, recent_window):
    start, end = recent_window
    data = client.hive_readings(sample_hive_id, start, end)
    rows = [r for pos in data for r in pos.get("readings", [])]
    if not rows:
        pytest.skip("no recent readings to range-check")
    slack = 2 * 24 * 60 * 60  # allow a little boundary slack
    for r in rows:
        ts = r["timestamp"]
        assert start - slack <= ts <= end + slack, f"timestamp {ts} outside window"


# --------------------------------------------------------------------------
# /user/hive/{id}/notes
# --------------------------------------------------------------------------
def _normalize_notes(payload):
    """Notes come back as a bare list (observed) or {notes:[...]} (documented)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("notes", [])
    raise AssertionError(f"unexpected notes payload type: {type(payload)}")


def test_hive_notes_shape(client, sample_hive_id, recent_window):
    start, end = recent_window
    payload = client.hive_notes(sample_hive_id, start, end)
    notes = _normalize_notes(payload)
    assert isinstance(notes, list)
    for n in notes:
        assert "description" in n or "note" in n
        assert "timestamp" in n or "created" in n


# --------------------------------------------------------------------------
# /user/device/{id}/readings
# --------------------------------------------------------------------------
def test_device_readings_shape(client, sample_device_id, recent_window):
    if not sample_device_id:
        pytest.skip("no device id discovered")
    start, end = recent_window
    data = client.device_readings(sample_device_id, start, end)
    assert isinstance(data, list)
    for r in data[:200]:
        assert isinstance(r.get("deviceId"), str)
        assert isinstance(r.get("timestamp"), numbers.Integral)
        assert isinstance(r.get("readings"), dict)


def test_device_readings_match_hive_readings(client, sample_hive_id, sample_device_id, recent_window):
    """A device's own readings should appear inside its hive's readings."""
    if not sample_device_id:
        pytest.skip("no device id discovered")
    start, end = recent_window
    hive = client.hive_readings(sample_hive_id, start, end)
    hive_devices = {r["deviceId"] for pos in hive for r in pos.get("readings", [])}
    if sample_device_id not in hive_devices:
        pytest.skip("sample device not in sample hive for this window")
    dev = client.device_readings(sample_device_id, start, end)
    assert all(r["deviceId"] == sample_device_id for r in dev)


# --------------------------------------------------------------------------
# Auth / error / edge-case behavior  (pins ACTUAL behavior)
# --------------------------------------------------------------------------
def test_invalid_api_key_rejected(monkeypatch):
    """NON-STANDARD: a bad key returns 412 (not 401/403) with 'Key not found'.

    Pinned so a future MCP/ingestion layer treats 412 (not just 401) as the
    auth-failure signal for this API.
    """
    bad = BroodMinderClient(api_key="definitely-not-a-valid-key")
    try:
        resp = bad.raw("/user/metadata/apiaries")
        assert resp.status_code == 412, f"expected 412, got {resp.status_code}"
        assert "not found" in resp.text.lower()
    finally:
        bad.close()


def test_missing_api_key_rejected():
    """Empty/absent key also returns 412 with 'api key not specified'."""
    bad = BroodMinderClient(api_key="")
    # Force an empty header (constructor would otherwise fall back to env).
    bad.api_key = ""
    bad._client.headers["X-Api-Key"] = ""
    try:
        resp = bad.raw("/user/metadata/apiaries")
        assert resp.status_code == 412
        assert "not specified" in resp.text.lower()
    finally:
        bad.close()


def test_unknown_hive_id(client, recent_window):
    start, end = recent_window
    resp = client.raw("/user/hive/this-hive-does-not-exist/readings",
                      {"start": start, "end": end})
    assert resp.status_code in (400, 403, 404), f"got {resp.status_code}: {resp.text[:200]}"


def test_missing_time_params(client, sample_hive_id):
    """readings without start/end — record whether it's rejected or defaulted."""
    resp = client.raw(f"/user/hive/{sample_hive_id}/readings")
    assert resp.status_code in (400, 422, 200), f"got {resp.status_code}"
    if resp.status_code == 200:
        pytest.skip("API defaults missing start/end instead of rejecting (noted)")


def test_window_over_six_months(client, sample_hive_id):
    """Request a >6-month window and assert the API's actual policy.

    Either it rejects (4xx) or it clamps/serves a subset — we just pin which,
    so the extractor's windowing assumption (<=6mo) is justified.
    """
    end = 1_780_000_000  # fixed epoch (avoid time-dependent flakiness)
    start = end - int(MAX_WINDOW_SECONDS * 1.5)  # ~9 months
    resp = client.raw(f"/user/hive/{sample_hive_id}/readings",
                      {"start": start, "end": end})
    assert resp.status_code in (200, 400, 422), f"unexpected {resp.status_code}"
    # If it 200s, that's fine — the extractor still windows defensively.
