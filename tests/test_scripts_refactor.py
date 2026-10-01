"""Unit tests for the pure helpers extracted from the script `main()` functions.

These lock the behavior of the logic pulled out of `discover.main`,
`extract_all.main`, and `flatten.main` when their cognitive complexity was
reduced (SonarCloud python:S3776). They exercise the extracted helpers directly
— no API calls — so the refactor is provably behavior-preserving.
"""

from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import discover  # noqa: E402
import extract_all  # noqa: E402
import flatten  # noqa: E402
from bm.client import BroodMinderError  # noqa: E402


# ---------------------------------------------------------------------------
# discover.find_sample_ids
# ---------------------------------------------------------------------------
def test_find_sample_ids_from_list():
    apiaries = [
        {"hives": [
            {"hiveId": "H1", "devices": [{"deviceId": "D1"}, {"deviceId": "D2"}]},
            {"hiveId": "H2", "devices": [{"deviceId": "D3"}]},
        ]},
    ]
    assert discover.find_sample_ids(apiaries) == ("H1", "D1")


def test_find_sample_ids_dict_wrapper():
    apiaries = {"apiaries": [{"hives": [{"hiveId": "H1", "devices": [{"deviceId": "D1"}]}]}]}
    assert discover.find_sample_ids(apiaries) == ("H1", "D1")


def test_find_sample_ids_alternate_keys():
    # schemas vary: id / deviceID / positions instead of hiveId / deviceId / devices
    apiaries = [{"hives": [{"id": "H9", "positions": [{"deviceID": "D9"}]}]}]
    assert discover.find_sample_ids(apiaries) == ("H9", "D9")


def test_find_sample_ids_hive_without_devices():
    apiaries = [{"hives": [{"hiveId": "H1"}]}]
    assert discover.find_sample_ids(apiaries) == ("H1", None)


def test_find_sample_ids_empty():
    assert discover.find_sample_ids([]) == (None, None)
    assert discover.find_sample_ids({"apiaries": []}) == (None, None)
    assert discover.find_sample_ids(None) == (None, None)


# ---------------------------------------------------------------------------
# extract_all.select_apiaries
# ---------------------------------------------------------------------------
_APIARIES = [
    {"apiaryId": "A1", "name": "North Yard", "hives": []},
    {"apiaryId": "A2", "name": "South Yard", "hives": []},
]


def test_select_apiaries_no_filter_returns_all():
    assert extract_all.select_apiaries(_APIARIES, []) == _APIARIES


def test_select_apiaries_by_name_case_insensitive():
    got = extract_all.select_apiaries(_APIARIES, ["north yard"])
    assert [a["apiaryId"] for a in got] == ["A1"]


def test_select_apiaries_by_id_exact():
    got = extract_all.select_apiaries(_APIARIES, ["A2"])
    assert [a["apiaryId"] for a in got] == ["A2"]


def test_select_apiaries_no_match_is_empty():
    assert extract_all.select_apiaries(_APIARIES, ["nope"]) == []


def test_select_apiaries_normalizes_wrapped_dict():
    wrapped = {"apiaries": _APIARIES}
    assert extract_all.select_apiaries(wrapped, []) == _APIARIES
    got = extract_all.select_apiaries(wrapped, ["north yard"])
    assert [a["apiaryId"] for a in got] == ["A1"]


# ---------------------------------------------------------------------------
# flatten.build_row
# ---------------------------------------------------------------------------
def test_build_row_full():
    meta = {"apiaryId": "A1", "apiaryName": "North", "hiveName": "Hive-1"}
    reading = {
        "deviceId": "D1",
        "timestamp": 1_700_000_000,
        "batteryLevel": 90,
        "chargeRemaining": 12,
        "readings": {"temperature": 34.5, "humidity": 55},
    }
    row = flatten.build_row("H1", meta, "P1", reading)
    assert row["apiaryId"] == "A1"
    assert row["apiaryName"] == "North"
    assert row["hiveId"] == "H1"
    assert row["hiveName"] == "Hive-1"
    assert row["positionID"] == "P1"
    assert row["deviceId"] == "D1"
    assert row["timestamp"] == 1_700_000_000
    assert row["datetime"] == "2023-11-14T22:13:20+00:00"
    assert row["batteryLevel"] == 90
    assert row["chargeRemaining"] == 12
    assert row["m_temperature"] == 34.5
    assert row["m_humidity"] == 55


def test_build_row_null_timestamp_gives_null_datetime():
    row = flatten.build_row("H1", {}, "P1", {"deviceId": "D1", "timestamp": None})
    assert row["timestamp"] is None
    assert row["datetime"] is None


def test_build_row_zero_timestamp_gives_epoch_datetime():
    row = flatten.build_row("H1", {}, "P1", {"deviceId": "D1", "timestamp": 0})
    assert row["timestamp"] == 0
    assert row["datetime"] == "1970-01-01T00:00:00+00:00"


def test_build_row_missing_metrics_has_no_metric_columns():
    row = flatten.build_row("H1", {}, "P1", {"deviceId": "D1", "timestamp": 1})
    assert not any(k.startswith("m_") for k in row)


# ---------------------------------------------------------------------------
# flatten.accumulate_coverage
# ---------------------------------------------------------------------------
def test_accumulate_coverage_zero_timestamp():
    from collections import defaultdict
    coverage = defaultdict(lambda: {"rows": 0, "min_ts": None, "max_ts": None,
                                    "devices": set(), "positions": set()})
    row = {"hiveId": "H1", "deviceId": "D1", "positionID": "P1", "timestamp": 0}
    flatten.accumulate_coverage(coverage, row)
    assert coverage["H1"]["min_ts"] == 0
    assert coverage["H1"]["max_ts"] == 0
    assert coverage["H1"]["rows"] == 1


# ---------------------------------------------------------------------------
# flatten.build_coverage
# ---------------------------------------------------------------------------
def test_build_coverage_zero_timestamp():
    coverage = {
        "H1": {"rows": 1, "min_ts": 0, "max_ts": 0, "devices": {"D1"}, "positions": {"P1"}}
    }
    meta = {"H1": {"apiaryName": "Apiary1", "hiveName": "Hive1"}}
    out = flatten.build_coverage(coverage, meta)
    assert out["H1"]["earliest"] == "1970-01-01T00:00:00+00:00"
    assert out["H1"]["latest"] == "1970-01-01T00:00:00+00:00"


def test_build_coverage_null_timestamps():
    coverage = {
        "H1": {"rows": 0, "min_ts": None, "max_ts": None, "devices": set(), "positions": set()}
    }
    out = flatten.build_coverage(coverage, {})
    assert out["H1"]["earliest"] is None
    assert out["H1"]["latest"] is None


# ---------------------------------------------------------------------------
# flatten.load_json
# ---------------------------------------------------------------------------
def test_load_json_plain(tmp_path):
    f = tmp_path / "data.json"
    f.write_text('{"key": "val"}', encoding="utf-8")
    assert flatten.load_json(f) == {"key": "val"}


def test_load_json_gz(tmp_path):
    f = tmp_path / "data.json.gz"
    with gzip.open(f, "wt", encoding="utf-8") as fh:
        fh.write('{"key": "val"}')
    assert flatten.load_json(f) == {"key": "val"}


# ---------------------------------------------------------------------------
# flatten.hive_meta
# ---------------------------------------------------------------------------
def test_hive_meta_extracts_fields():
    manifest = {"completed": {
        "H1|0|1": {"apiaryName": "North", "apiaryId": "A1", "hiveName": "Hive-1"},
        "H1|1|2": {"apiaryName": "North", "apiaryId": "A1", "hiveName": "Hive-1"},
        "H2|0|1": {"apiaryName": "South", "apiaryId": "A2", "hiveName": "Hive-2"},
    }}
    meta = flatten.hive_meta(manifest)
    assert meta["H1"]["apiaryName"] == "North"
    assert meta["H2"]["hiveName"] == "Hive-2"
    assert len(meta) == 2


def test_hive_meta_empty():
    assert flatten.hive_meta({}) == {}


# ---------------------------------------------------------------------------
# flatten.iter_reading_files / iter_note_files
# ---------------------------------------------------------------------------
def test_iter_reading_files(tmp_path):
    (tmp_path / "a.readings.json").write_text("[]", encoding="utf-8")
    (tmp_path / "b.readings.json.gz").write_bytes(b"")
    (tmp_path / "other.json").write_text("", encoding="utf-8")
    names = [f.name for f in flatten.iter_reading_files(tmp_path)]
    assert "a.readings.json" in names
    assert "b.readings.json.gz" in names
    assert "other.json" not in names


def test_iter_note_files(tmp_path):
    (tmp_path / "a.notes.json").write_text("[]", encoding="utf-8")
    (tmp_path / "b.notes.json.gz").write_bytes(b"")
    (tmp_path / "other.json").write_text("", encoding="utf-8")
    names = [f.name for f in flatten.iter_note_files(tmp_path)]
    assert "a.notes.json" in names
    assert "b.notes.json.gz" in names
    assert "other.json" not in names


# ---------------------------------------------------------------------------
# flatten.iter_readings
# ---------------------------------------------------------------------------
def test_iter_readings(tmp_path):
    data = [{"positionID": "P1", "readings": [{"deviceId": "D1", "timestamp": 1}]}]
    f = tmp_path / "w.readings.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    pairs = list(flatten.iter_readings(tmp_path))
    assert pairs == [("P1", {"deviceId": "D1", "timestamp": 1})]


# ---------------------------------------------------------------------------
# flatten.discover_metric_keys
# ---------------------------------------------------------------------------
def test_discover_metric_keys(tmp_path):
    (tmp_path / "skip.txt").write_text("not a dir", encoding="utf-8")  # exercises continue branch
    hdir = tmp_path / "H1"
    hdir.mkdir()
    data = [{"positionID": "P1", "readings": [{"readings": {"temp": 35, "hum": 60}}]}]
    (hdir / "w.readings.json").write_text(json.dumps(data), encoding="utf-8")
    keys = flatten.discover_metric_keys(tmp_path)
    assert keys == {"temp", "hum"}


# ---------------------------------------------------------------------------
# flatten.iter_hive_rows
# ---------------------------------------------------------------------------
def test_iter_hive_rows_deduplicates(tmp_path):
    hdir = tmp_path / "H1"
    hdir.mkdir()
    row = {"positionID": "P1", "readings": [
        {"deviceId": "D1", "timestamp": 1},
        {"deviceId": "D1", "timestamp": 1},  # duplicate
    ]}
    (hdir / "w.readings.json").write_text(json.dumps([row]), encoding="utf-8")
    rows = list(flatten.iter_hive_rows(hdir, {}))
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# flatten.write_notes
# ---------------------------------------------------------------------------
def test_write_notes(tmp_path):
    hdir = tmp_path / "H1"
    hdir.mkdir()
    notes_data = [{"text": "a note", "date": "2024-01-01"}]
    (hdir / "w.notes.json").write_text(json.dumps(notes_data), encoding="utf-8")
    out_file = tmp_path / "notes.ndjson"
    n = flatten.write_notes(out_file, {"H1": {"hiveName": "Hive-1"}}, raw=tmp_path)
    assert n == 1
    line = json.loads(out_file.read_text(encoding="utf-8").strip())
    assert line["hiveId"] == "H1"
    assert line["text"] == "a note"


# ---------------------------------------------------------------------------
# extract_all.count_reading_rows / count_notes
# ---------------------------------------------------------------------------
def test_count_reading_rows_list():
    payload = [{"readings": [1, 2, 3]}, {"readings": [4]}]
    assert extract_all.count_reading_rows(payload) == 4


def test_count_reading_rows_not_list():
    assert extract_all.count_reading_rows(None) == 0
    assert extract_all.count_reading_rows({}) == 0


def test_count_notes_list():
    assert extract_all.count_notes([1, 2, 3]) == 3


def test_count_notes_dict():
    assert extract_all.count_notes({"notes": [1, 2]}) == 2


def test_count_notes_other():
    assert extract_all.count_notes(None) == 0


# ---------------------------------------------------------------------------
# extract_all.parse_date
# ---------------------------------------------------------------------------
def test_parse_date():
    assert extract_all.parse_date("2022-01-01") == 1640995200


# ---------------------------------------------------------------------------
# extract_all.load_manifest
# ---------------------------------------------------------------------------
def test_load_manifest_existing(tmp_path):
    data = {"completed": {"k": "v"}, "meta": {}}
    (tmp_path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    assert extract_all.load_manifest(tmp_path / "manifest.json") == data


def test_load_manifest_missing(tmp_path):
    result = extract_all.load_manifest(tmp_path / "no_manifest.json")
    assert result == {"completed": {}, "meta": {}}


# ---------------------------------------------------------------------------
# extract_all.write_gz
# ---------------------------------------------------------------------------
def test_write_gz_roundtrip(tmp_path):
    obj = {"a": [1, 2, 3]}
    path = tmp_path / "out.json.gz"
    extract_all.write_gz(path, obj)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        assert json.load(fh) == obj


# ---------------------------------------------------------------------------
# extract_all._empty_run
# ---------------------------------------------------------------------------
def test_empty_run_off():
    count, stop = extract_all._empty_run(5, 1, 0)
    assert count == 5
    assert stop is False


def test_empty_run_increment():
    count, stop = extract_all._empty_run(1, 0, 3)
    assert count == 2
    assert stop is False


def test_empty_run_reaches_limit():
    count, stop = extract_all._empty_run(2, 0, 3)
    assert count == 3
    assert stop is True


def test_empty_run_reset_on_data():
    count, stop = extract_all._empty_run(5, 1, 3)
    assert count == 0
    assert stop is False


# ---------------------------------------------------------------------------
# extract_all._log_window
# ---------------------------------------------------------------------------
def test_log_window_prints_when_data(capsys):
    a = {"name": "Yard"}
    h = {"name": "Hive-1"}
    rec = {"reading_rows": 5, "notes": 2}
    extract_all._log_window(a, h, 0, 86400, rec)
    out = capsys.readouterr().out
    assert "Hive-1" in out
    assert "rows=5" in out


def test_log_window_silent_when_empty(capsys):
    a = {"name": "Yard"}
    h = {"name": "Hive-1"}
    rec = {"reading_rows": 0}
    extract_all._log_window(a, h, 0, 86400, rec)
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# discover._sample
# ---------------------------------------------------------------------------
def test_sample_success(capsys):
    out = {}
    discover._sample(out, "label", lambda: {"x": 1}, "ok", "err", "msg", 100)
    assert out["ok"] == {"x": 1}
    assert "err" not in out


def test_sample_error(capsys):
    out = {}

    def bad_fetch():
        raise BroodMinderError(404, "GET", "/x", "not found")

    discover._sample(out, "label", bad_fetch, "ok", "err", "msg", 100)
    assert "ok" not in out
    assert "err" in out
    assert "not found" in out["err"]


# ---------------------------------------------------------------------------
# extract_all.fetch_window
# ---------------------------------------------------------------------------
def _make_args(no_notes=False):
    args = MagicMock()
    args.no_notes = no_notes
    return args


def test_fetch_window_with_notes(tmp_path):
    bm = MagicMock()
    bm.hive_readings.return_value = [{"readings": [1, 2]}]
    bm.hive_notes.return_value = [{"note": "hi"}]
    a = {"apiaryId": "A1", "name": "Yard"}
    h = {"name": "Hive-1"}
    rec = extract_all.fetch_window(bm, a, h, "H1", 0, 86400, tmp_path, _make_args())
    assert rec["reading_rows"] == 2
    assert rec["notes"] == 1
    assert (tmp_path / "0-86400.readings.json.gz").exists()
    assert (tmp_path / "0-86400.notes.json.gz").exists()


def test_fetch_window_no_notes(tmp_path):
    bm = MagicMock()
    bm.hive_readings.return_value = []
    a = {"apiaryId": "A1", "name": "Yard"}
    h = {"name": "Hive-1"}
    rec = extract_all.fetch_window(bm, a, h, "H1", 0, 86400, tmp_path, _make_args(no_notes=True))
    assert rec["reading_rows"] == 0
    assert "notes" not in rec


# ---------------------------------------------------------------------------
# extract_all.process_hive
# ---------------------------------------------------------------------------
def test_process_hive_skips_completed(tmp_path):
    bm = MagicMock()
    bm.call_count = 0
    a = {"apiaryId": "A1", "name": "Yard"}
    h = {"hiveId": "H1", "name": "Hive-1"}
    wins = [(0, 86400)]
    completed = {"H1|0|86400": {"reading_rows": 5}}
    args = MagicMock()
    args.stop_after_empty = 0
    args.max_calls = 900
    extract_all.process_hive(bm, a, h, wins, args, tmp_path, completed, lambda: None)
    bm.hive_readings.assert_not_called()


def test_process_hive_stops_at_budget(tmp_path):
    bm = MagicMock()
    bm.call_count = 900
    bm.hive_readings.return_value = []
    a = {"apiaryId": "A1", "name": "Yard"}
    h = {"hiveId": "H1", "name": "Hive-1"}
    wins = [(0, 86400)]
    completed = {}
    args = MagicMock()
    args.stop_after_empty = 0
    args.max_calls = 900
    args.no_notes = True
    with pytest.raises(extract_all._BudgetExhausted):
        extract_all.process_hive(bm, a, h, wins, args, tmp_path, completed, lambda: None)


def test_process_hive_early_exit_completed_stop(tmp_path):
    bm = MagicMock()
    bm.call_count = 0
    a = {"apiaryId": "A1", "name": "Yard"}
    h = {"hiveId": "H1", "name": "Hive-1"}
    # Two windows both completed with 0 rows; stop_after_empty=1 triggers break
    wins = [(0, 86400), (86400, 172800)]
    completed = {"H1|0|86400": {"reading_rows": 0}, "H1|86400|172800": {"reading_rows": 0}}
    args = MagicMock()
    args.stop_after_empty = 1
    args.max_calls = 900
    # Should NOT raise; just returns early after the break
    extract_all.process_hive(bm, a, h, wins, args, tmp_path, completed, lambda: None)
    bm.hive_readings.assert_not_called()


def test_process_hive_fetches_and_records(tmp_path):
    bm = MagicMock()
    bm.call_count = 0
    bm.hive_readings.return_value = [{"readings": [1, 2]}]
    bm.hive_notes.return_value = []
    a = {"apiaryId": "A1", "name": "Yard"}
    h = {"hiveId": "H1", "name": "Hive-1"}
    wins = [(0, 86400)]
    completed = {}
    args = MagicMock()
    args.stop_after_empty = 0
    args.max_calls = 900
    args.no_notes = False
    extract_all.process_hive(bm, a, h, wins, args, tmp_path, completed, lambda: None)
    assert "H1|0|86400" in completed
    assert completed["H1|0|86400"]["reading_rows"] == 2


def test_process_hive_calls_save_manifest_every_25(tmp_path):
    bm = MagicMock()
    bm.call_count = 0
    bm.hive_readings.return_value = []
    a = {"apiaryId": "A1", "name": "Yard"}
    h = {"hiveId": "H1", "name": "Hive-1"}
    wins = [(i, i + 1) for i in range(1)]
    # Pre-fill completed with 24 entries so adding one more triggers the % 25 == 0 branch
    completed = {f"OTHER|{i}|{i+1}": {"reading_rows": 1} for i in range(24)}
    args = MagicMock()
    args.stop_after_empty = 0
    args.max_calls = 900
    args.no_notes = True
    save_calls = []
    extract_all.process_hive(bm, a, h, wins, args, tmp_path, completed, lambda: save_calls.append(1))
    assert len(save_calls) == 1  # triggered at index 25


def test_process_hive_stop_after_empty_fetch(tmp_path):
    bm = MagicMock()
    bm.call_count = 0
    bm.hive_readings.return_value = []  # 0 rows => empty
    a = {"apiaryId": "A1", "name": "Yard"}
    h = {"hiveId": "H1", "name": "Hive-1"}
    wins = [(0, 86400), (86400, 172800)]
    completed = {}
    args = MagicMock()
    args.stop_after_empty = 1
    args.max_calls = 900
    args.no_notes = True
    extract_all.process_hive(bm, a, h, wins, args, tmp_path, completed, lambda: None)
    # After the first fetch with 0 rows, empties=1 >= limit=1 → break; second window never fetched
    assert bm.hive_readings.call_count == 1
