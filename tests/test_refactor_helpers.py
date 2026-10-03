"""Edge-case tests for the helpers extracted while reducing the cognitive
complexity of the three script `main()` functions (issue #56).

The broad, per-helper behavior-locking coverage lives in
``tests/test_scripts_refactor.py``; this suite is deliberately kept focused on
cases that file does *not* already cover (preview clipping, RateLimited
propagation, the mid-window budget guard, forward-mode early-exit, the
streaming metric-key harvest, and the across-any-hive device search) so the two
files do not maintain duplicate copies of the same assertions.

These are pure/offline tests (no live API, no `live` marker) so they run in CI.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    mod_name = f"_scripts_{name}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


discover = _load_script("discover")
extract_all = _load_script("extract_all")
flatten = _load_script("flatten")


# ==========================================================================
# discover.py
# ==========================================================================
def test_walk_sample_ids_device_in_later_hive():
    # The hive that supplies hive_id has no devices; the device must still be
    # discovered in a later hive. Regression guard for the across-any-hive
    # search (the earlier draft scoped the device search to the hive that
    # provided hive_id, missing devices when the first hive had none).
    apiaries = [{"hives": [
        {"hiveId": "H1"},
        {"hiveId": "H2", "devices": [{"deviceId": "D2"}]},
    ]}]
    hive_id, device_id = discover.walk_sample_ids(apiaries)
    assert hive_id == "H1"
    assert device_id == "D2"


def test_sample_endpoint_success_stores_and_clips(capsys):
    # The full sample is stored under `<key>_sample`, but the printed preview
    # is clipped to `clip` characters. Use an oversized sample so clipping is
    # actually exercised, then assert the printed preview is truncated.
    out = {}
    big = {"blob": "x" * 500}
    discover.sample_endpoint(out, "hive_readings", "→ header", "hive readings",
                             lambda: big, clip=40)
    assert out["hive_readings_sample"] == big   # full sample stored, not clipped
    assert "hive_readings_error" not in out
    preview = capsys.readouterr().out.split("→ header\n", 1)[1].rstrip("\n")
    assert len(preview) == 40                    # printed preview clipped to `clip`


def test_sample_endpoint_propagates_rate_limited():
    # RateLimited must escape sample_endpoint (re-raised before the
    # BroodMinderError handler) so the caller can abort the whole run rather
    # than swallow a throttle as a per-endpoint error.
    out = {}

    def boom() -> NoReturn:
        raise discover.RateLimited(429, "GET", "/x", "slow down")

    with pytest.raises(discover.RateLimited):
        discover.sample_endpoint(out, "hive_readings", "→ header", "hive readings",
                                 boom, clip=100)
    assert out == {}  # neither a sample nor an endpoint error was recorded


# ==========================================================================
# extract_all.py
# ==========================================================================
def _args(**over):
    base = dict(no_notes=False, stop_after_empty=0, max_calls=900, reverse=False)
    base.update(over)
    return SimpleNamespace(**base)


class _FakeBM:
    def __init__(self, readings, notes=None):
        self._readings = readings
        self._notes = notes if notes is not None else []
        self.call_count = 0

    def hive_readings(self, hid, s, e):
        self.call_count += 1
        return self._readings

    def hive_notes(self, hid, s, e):
        self.call_count += 1
        return self._notes


def test_process_hive_budget_raises(tmp_path):
    # One call remaining (899/900) but a notes-enabled window needs two, so the
    # pre-window guard must refuse *before* fetching. This catches a guard that
    # checks only whether the count has already reached the budget.
    bm = _FakeBM([{"positionID": "p", "readings": [{"timestamp": 1}]}])
    bm.call_count = 899  # one call left; a window needs two (readings + notes)
    completed = {}
    with pytest.raises(extract_all.BudgetExhausted):
        extract_all.process_hive(bm, {"apiaryId": "A", "name": "Api"},
                                  {"hiveId": "H1", "name": "Hive"},
                                  [(0, 100)], _args(max_calls=900), tmp_path, completed,
                                  lambda: None)
    assert bm.call_count == 899  # no call was made
    assert completed == {}  # nothing fetched


def test_process_hive_commits_partial_on_midwindow_budget(tmp_path):
    # When retries inflate call_count so the budget is hit *after* readings are
    # written but *before* notes, fetch_window raises carrying the readings-only
    # record. process_hive must commit it to `completed` and flush the manifest
    # before re-raising, so a resume skips this window instead of re-spending a
    # call re-fetching its readings.
    class _RetryingBM(_FakeBM):
        def hive_readings(self, hid, s, e):
            self.call_count += 3  # simulate internal retries consuming budget
            return self._readings

    bm = _RetryingBM([{"positionID": "p", "readings": [{"timestamp": 1}]}], [{"d": "n"}])
    bm.call_count = 897  # pre-window guard (897+2<=900) passes; readings -> 900
    completed = {}
    saved = []
    with pytest.raises(extract_all.BudgetExhausted):
        extract_all.process_hive(bm, {"apiaryId": "A", "name": "Api"},
                                  {"hiveId": "H1", "name": "Hive"},
                                  [(0, 100)], _args(max_calls=900), tmp_path, completed,
                                  lambda: saved.append(1))
    assert completed["H1|0|100"]["reading_rows"] == 1  # readings record persisted
    assert "notes" not in completed["H1|0|100"]        # notes never fetched
    assert saved                                       # manifest flushed before raise


def test_process_hive_stop_after_empty_forward_no_stop(tmp_path):
    # In forward mode (reverse=False), stop_after_empty has no effect, so an
    # empty window must not truncate a chronological extraction.
    bm = _FakeBM([{"positionID": "p", "readings": []}])
    completed = {}
    extract_all.process_hive(bm, {"apiaryId": "A", "name": "Api"},
                             {"hiveId": "H1", "name": "Hive"},
                             [(0, 100), (100, 200)],
                             _args(stop_after_empty=1, no_notes=True, reverse=False),
                             tmp_path, completed, lambda: None)
    assert len(completed) == 2  # both windows fetched; no early stop in forward mode


# ==========================================================================
# flatten.py
# ==========================================================================
def _write_gz(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _make_raw(tmp_path):
    raw = tmp_path / "raw"
    # Hive H1: two windows, second overlaps the first (dup row) to test dedup.
    _write_gz(raw / "H1" / "0-100.readings.json.gz",
              [{"positionID": "p1", "readings": [
                  {"deviceId": "d1", "timestamp": 10, "batteryLevel": 90,
                   "chargeRemaining": 80, "readings": {"temp": 20.0, "hum": 50.0}},
                  {"deviceId": "d1", "timestamp": 20, "batteryLevel": None,
                   "chargeRemaining": None, "readings": {"temp": 21.0}},
              ]}])
    _write_gz(raw / "H1" / "100-200.readings.json.gz",
              [{"positionID": "p1", "readings": [
                  {"deviceId": "d1", "timestamp": 20, "batteryLevel": None,
                   "chargeRemaining": None, "readings": {"temp": 21.0}},  # dup
                  {"deviceId": "d1", "timestamp": 30, "batteryLevel": 88,
                   "chargeRemaining": 70, "readings": {"weight": 5.0}},
              ]}])
    return raw


def test_stream_readings_dedups_covers_and_collects_keys(tmp_path):
    raw = _make_raw(tmp_path)
    meta = {"H1": {"apiaryId": "A", "apiaryName": "Api", "hiveName": "Hive"}}
    coverage = defaultdict(lambda: {"rows": 0, "min_ts": None, "max_ts": None,
                                    "devices": set(), "positions": set()})
    ndjson = tmp_path / "out.ndjson"
    with ndjson.open("w") as fh:
        n, keys = flatten.stream_readings(raw, meta, [], [], fh, None, coverage)
    assert n == 3  # 4 rows, 1 duplicate removed
    # Metric keys are harvested during this single pass, so an NDJSON-only
    # export reports metrics without a second full read via discover_metric_keys.
    assert keys == {"temp", "hum", "weight"}
    lines = [json.loads(x) for x in ndjson.read_text().splitlines()]
    assert {ln["timestamp"] for ln in lines} == {10, 20, 30}
    assert lines[0]["m_temp"] == 20.0
    c = coverage["H1"]
    assert c["rows"] == 3
    assert c["min_ts"] == 10 and c["max_ts"] == 30
    assert c["devices"] == {"d1"} and c["positions"] == {"p1"}
