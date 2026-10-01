"""Fast, deterministic offline unit tests (no network calls, run in CI without API key)."""

from __future__ import annotations

import csv
import gzip
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bm.client import BroodMinderClient, BroodMinderError, iter_windows, now_epoch, to_epoch
from scripts.extract_all import (
    count_notes,
    count_reading_rows,
    get_hive_resume_start,
    get_manifest_max_end,
    parse_date,
)
from scripts.flatten import KNOWN_METRIC_KEYS


def test_iter_windows_basic():
    # Exactly 2 windows of 100 seconds
    windows = list(iter_windows(1000, 1200, window=100))
    assert windows == [(1000, 1100), (1100, 1200)]


def test_iter_windows_partial_remainder():
    # 250 seconds -> 2 full windows of 100, one remainder of 50
    windows = list(iter_windows(1000, 1250, window=100))
    assert windows == [(1000, 1100), (1100, 1200), (1200, 1250)]


def test_iter_windows_empty():
    assert list(iter_windows(1000, 1000, window=100)) == []
    assert list(iter_windows(1200, 1000, window=100)) == []


def test_to_epoch_and_now_epoch():
    now = now_epoch()
    assert isinstance(now, int)
    assert now > 1_600_000_000

    dt_naive = datetime(2026, 7, 1, 12, 0, 0)
    dt_aware = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert to_epoch(dt_naive) == to_epoch(dt_aware)


def test_parse_date():
    ts = parse_date("2026-07-01")
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    assert dt.year == 2026
    assert dt.month == 7
    assert dt.day == 1
    assert dt.hour == 0
    assert dt.minute == 0
    assert dt.second == 0


def test_get_manifest_max_end():
    assert get_manifest_max_end({}) is None
    assert get_manifest_max_end({"completed": {}}) is None

    manifest = {
        "completed": {
            "hive1|1000|2000": {"reading_rows": 10},
            "hive1|2000|3500": {"reading_rows": 20},
            "hive2|1000|2500": {"reading_rows": 5},
            "malformed_key": {"reading_rows": 0},
        }
    }
    assert get_manifest_max_end(manifest) == 3500


def test_get_hive_resume_start():
    manifest = {
        "completed": {
            "hive1|1000|2000": {"reading_rows": 10},
            "hive1|2000|3500": {"reading_rows": 20},
            "hive2|1000|2500": {"reading_rows": 5},
            "hive|with|pipe|1000|4200": {"reading_rows": 7},
            "malformed_key": {"reading_rows": 0},
        }
    }
    # Hive with multiple windows resumes from its own latest window end
    assert get_hive_resume_start(manifest, "hive1", default_start=500) == 3500
    assert get_hive_resume_start(manifest, "hive2", default_start=500) == 2500
    # Hive with pipe characters in hiveId handled properly via rsplit
    assert get_hive_resume_start(manifest, "hive|with|pipe", default_start=500) == 4200
    # Hive with no prior windows falls back to default_start
    assert get_hive_resume_start(manifest, "hive3_unknown", default_start=500) == 500
    assert get_hive_resume_start({}, "hive1", default_start=500) == 500


def test_count_reading_rows():
    assert count_reading_rows(None) == 0
    assert count_reading_rows([]) == 0
    payload = [
        {"positionID": "p1", "readings": [{"deviceId": "d1"}, {"deviceId": "d1"}]},
        {"positionID": "p2", "readings": [{"deviceId": "d2"}]},
        {"positionID": "p3", "readings": []},
    ]
    assert count_reading_rows(payload) == 3


def test_count_notes():
    assert count_notes(None) == 0
    assert count_notes([]) == 0
    assert count_notes([{"id": "n1"}, {"id": "n2"}]) == 2
    assert count_notes({"notes": [{"id": "n1"}]}) == 1


def test_known_metric_keys_stability():
    expected = {"audio", "humidity", "radar", "swarmState", "temperature", "weight"}
    assert expected.issubset(KNOWN_METRIC_KEYS)


def test_client_init_requires_key(monkeypatch):
    monkeypatch.delenv("BROODMINDER_API_KEY", raising=False)
    with pytest.raises(BroodMinderError) as exc_info:
        BroodMinderClient(api_key=None)
    assert "BROODMINDER_API_KEY not set" in str(exc_info.value)


def test_flatten_merge_deduplication(tmp_path, monkeypatch):
    import scripts.flatten as fl

    out_dir = tmp_path / "extract"
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True)

    monkeypatch.setattr(fl, "OUT", out_dir)
    monkeypatch.setattr(fl, "RAW", raw_dir)

    # 1. Create a prior baseline readings.ndjson.gz with a custom metric
    prior_rows = [
        {
            "apiaryId": "ap1", "apiaryName": "Apiary 1",
            "hiveId": "h1", "hiveName": "Hive 1",
            "positionID": "pos1", "deviceId": "dev1",
            "timestamp": 1000, "datetime": "2026-07-01T00:00:00+00:00",
            "batteryLevel": 90, "chargeRemaining": None,
            "m_temperature": 75.0, "m_humidity": 50.0, "m_custom_sensor": 42.0,
        },
        {
            "apiaryId": "ap1", "apiaryName": "Apiary 1",
            "hiveId": "h1", "hiveName": "Hive 1",
            "positionID": "pos1", "deviceId": "dev1",
            "timestamp": 1100, "datetime": "2026-07-01T01:00:00+00:00",
            "batteryLevel": 89, "chargeRemaining": None,
            "m_temperature": 76.0, "m_humidity": 51.0,
        },
    ]
    with gzip.open(out_dir / "readings.ndjson.gz", "wt", encoding="utf-8") as fh:
        for r in prior_rows:
            fh.write(json.dumps(r) + "\n")

    # 2. Create prior notes.ndjson
    with (out_dir / "notes.ndjson").open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"hiveId": "h1", "hiveName": "Hive 1", "id": "note1", "description": "old note"}) + "\n")

    # 3. Create raw files: 1 duplicate row (ts=1100), 1 new row (ts=1200)
    h1_dir = raw_dir / "h1"
    h1_dir.mkdir()
    raw_readings = [
        {
            "positionID": "pos1",
            "readings": [
                {"deviceId": "dev1", "timestamp": 1100, "batteryLevel": 89, "chargeRemaining": None,
                 "readings": {"temperature": 76.0, "humidity": 51.0}},
                {"deviceId": "dev1", "timestamp": 1200, "batteryLevel": 88, "chargeRemaining": None,
                 "readings": {"temperature": 77.0, "humidity": 52.0}},
            ],
        }
    ]
    with gzip.open(h1_dir / "1100-1300.readings.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(raw_readings, fh)

    manifest = {
        "completed": {
            "h1|1100|1300": {"apiaryName": "Apiary 1", "apiaryId": "ap1", "hiveName": "Hive 1"}
        }
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest))

    # Run flatten with --merge
    monkeypatch.setattr("sys.argv", ["flatten.py", "--merge"])
    exit_code = fl.main()
    assert exit_code == 0

    # Verify merged readings
    merged_rows = []
    with gzip.open(out_dir / "readings.ndjson.gz", "rt", encoding="utf-8") as fh:
        for line in fh:
            merged_rows.append(json.loads(line))

    # Total should be 3 (ts=1000, 1100, 1200; duplicate 1100 discarded)
    assert len(merged_rows) == 3
    timestamps = [r["timestamp"] for r in merged_rows]
    assert timestamps == [1000, 1100, 1200]

    # Verify CSV has full header with KNOWN_METRIC_KEYS and the discovered archive metric
    with gzip.open(out_dir / "readings.csv.gz", "rt", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        for expected_col in ["m_audio", "m_humidity", "m_radar", "m_swarmState", "m_temperature", "m_weight"]:
            assert expected_col in header
        # Discovered custom metric from existing archive is preserved in header
        assert "m_custom_sensor" in header

    # Verify coverage.json has min_ts=1000, max_ts=1200, rows=3
    cov = json.loads((out_dir / "coverage.json").read_text())
    assert "h1" in cov
    assert cov["h1"]["rows"] == 3
    assert cov["h1"]["earliest"] == "1970-01-01T00:16:40+00:00"
    assert cov["h1"]["latest"] == "1970-01-01T00:20:00+00:00"

    # Verify no temporary files remain
    tmp_files = list(out_dir.glob("*.tmp*"))
    assert tmp_files == []


def test_flatten_fresh_atomic_build(tmp_path, monkeypatch):
    import scripts.flatten as fl

    out_dir = tmp_path / "extract"
    raw_dir = out_dir / "raw"
    h1_dir = raw_dir / "h1"
    h1_dir.mkdir(parents=True)

    monkeypatch.setattr(fl, "OUT", out_dir)
    monkeypatch.setattr(fl, "RAW", raw_dir)

    raw_readings = [
        {
            "positionID": "pos1",
            "readings": [
                {"deviceId": "dev1", "timestamp": 2000, "readings": {"temperature": 70.0}},
                {"deviceId": "dev1", "timestamp": 2000, "readings": {"temperature": 70.0}},  # dup
                {"deviceId": "dev1", "timestamp": 2100, "readings": {"temperature": 71.0}},
            ],
        }
    ]
    with gzip.open(h1_dir / "2000-2200.readings.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(raw_readings, fh)

    raw_notes = [{"id": "n1", "description": "note 1"}]
    with gzip.open(h1_dir / "2000-2200.notes.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(raw_notes, fh)

    manifest = {"completed": {"h1|2000|2200": {"apiaryName": "Ap1", "hiveName": "H1"}}}
    (out_dir / "manifest.json").write_text(json.dumps(manifest))

    # Fresh run (no --merge)
    monkeypatch.setattr("sys.argv", ["flatten.py"])
    assert fl.main() == 0

    assert (out_dir / "readings.ndjson.gz").exists()
    assert (out_dir / "readings.csv.gz").exists()
    assert (out_dir / "notes.ndjson").exists()
    assert (out_dir / "coverage.json").exists()

    # Deduplicated 2 rows from 3
    with gzip.open(out_dir / "readings.ndjson.gz", "rt", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    assert len(rows) == 2

    # No leftover temporary files
    assert list(out_dir.glob("*.tmp*")) == []


def test_flatten_atomic_interruption_preserves_existing_targets(tmp_path, monkeypatch):
    import scripts.flatten as fl

    out_dir = tmp_path / "extract"
    raw_dir = out_dir / "raw"
    h1_dir = raw_dir / "h1"
    h1_dir.mkdir(parents=True)

    monkeypatch.setattr(fl, "OUT", out_dir)
    monkeypatch.setattr(fl, "RAW", raw_dir)

    # Pre-create baseline valid targets
    orig_readings = [{"hiveId": "h1", "timestamp": 100, "datetime": "1970-01-01T00:01:40+00:00"}]
    with gzip.open(out_dir / "readings.ndjson.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(orig_readings[0]) + "\n")
    orig_notes = [{"hiveId": "h1", "id": "orig_note"}]
    (out_dir / "notes.ndjson").write_text(json.dumps(orig_notes[0]) + "\n")

    raw_readings = [
        {
            "positionID": "pos1",
            "readings": [
                {"deviceId": "dev1", "timestamp": 2000, "readings": {"temperature": 70.0}},
            ],
        }
    ]
    with gzip.open(h1_dir / "2000-2200.readings.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(raw_readings, fh)

    manifest = {"completed": {"h1|2000|2200": {"apiaryName": "Ap1", "hiveName": "H1"}}}
    (out_dir / "manifest.json").write_text(json.dumps(manifest))

    # Simulate crash / exception during json.dumps in reading loop
    original_dumps = json.dumps
    def failing_dumps(obj, *args, **kwargs):
        if isinstance(obj, dict) and obj.get("timestamp") == 2000:
            raise RuntimeError("Simulated crash during write")
        return original_dumps(obj, *args, **kwargs)

    monkeypatch.setattr(json, "dumps", failing_dumps)
    monkeypatch.setattr("sys.argv", ["flatten.py"])

    with pytest.raises(RuntimeError, match="Simulated crash"):
        fl.main()

    # Pre-existing target files MUST survive uncorrupted
    with gzip.open(out_dir / "readings.ndjson.gz", "rt", encoding="utf-8") as fh:
        surviving = [json.loads(line) for line in fh]
    assert surviving == orig_readings
    assert (out_dir / "notes.ndjson").read_text().strip() == json.dumps(orig_notes[0])

    # No leftover temporary files
    assert list(out_dir.glob("*.tmp*")) == []


def test_extract_all_catchup_roster_check(tmp_path, monkeypatch, capsys):
    import scripts.extract_all as ex

    out_dir = tmp_path / "extract"
    out_dir.mkdir()
    end_ts = 1782864000  # 2026-07-01 00:00:00 UTC
    manifest = {
        "completed": {
            f"h1|1000|{end_ts}": {"reading_rows": 10},
            f"h2|1000|{end_ts}": {"reading_rows": 20},
        }
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest))

    calls = {"readings": 0, "apiaries": 0}

    class MockClient:
        def __init__(self):
            self.call_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def apiaries(self):
            calls["apiaries"] += 1
            return [{"apiaryId": "a1", "name": "Apiary 1",
                     "hives": [{"hiveId": "h1", "name": "H1"}, {"hiveId": "h2", "name": "H2"}]}]

        def hive_readings(self, hid, s, e):
            calls["readings"] += 1
            return []

        def hive_notes(self, hid, s, e):
            return []

    monkeypatch.setattr(ex, "BroodMinderClient", MockClient)
    monkeypatch.setattr("sys.argv", [
        "extract_all.py", "--catchup", "--end", "2026-07-01", "--out", str(out_dir)
    ])

    # Case 1: All discovered hives are caught up -> 1 discovery call, 0 extraction calls
    assert ex.main() == 0
    captured = capsys.readouterr()
    assert "already up to date through 2026-07-01 across all 2 hives" in captured.out
    assert calls["apiaries"] == 1
    assert calls["readings"] == 0

    # Case 2: New hive added to account -> not up to date, extracts the new hive
    class MockClientWithNewHive(MockClient):
        def apiaries(self):
            calls["apiaries"] += 1
            return [{"apiaryId": "a1", "name": "Apiary 1",
                     "hives": [{"hiveId": "h1", "name": "H1"},
                               {"hiveId": "h2", "name": "H2"},
                               {"hiveId": "h3_new", "name": "H3"}]}]

    monkeypatch.setattr(ex, "BroodMinderClient", MockClientWithNewHive)
    assert ex.main() == 0
    captured = capsys.readouterr()
    assert "already up to date across all" not in captured.out
    assert calls["readings"] > 0

