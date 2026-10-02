"""Unit tests for battery health and offline sensor monitoring."""
from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import pytest

from bm.battery import (
    DeviceHealth,
    evaluate_device_health,
    extract_battery,
    format_csv,
    format_json,
    format_table,
    scan_device_health_from_file,
    scan_device_health_from_stream,
)


# --- 1. extract_battery tests ------------------------------------------------


def test_extract_battery_level_field():
    assert extract_battery({"batteryLevel": 85, "chargeRemaining": None}) == 85


def test_extract_charge_remaining_field():
    assert extract_battery({"batteryLevel": None, "chargeRemaining": 72}) == 72


def test_extract_battery_string_conversion():
    assert extract_battery({"batteryLevel": "64", "chargeRemaining": None}) == 64


def test_extract_battery_both_none():
    assert extract_battery({"batteryLevel": None, "chargeRemaining": None}) is None
    assert extract_battery({}) is None


def test_extract_battery_invalid_value():
    assert extract_battery({"batteryLevel": "invalid"}) is None


def test_extract_battery_clamping():
    assert extract_battery({"batteryLevel": 110}) == 100
    assert extract_battery({"batteryLevel": -5}) == 0


# --- 2. evaluate_device_health tests -----------------------------------------


def test_evaluate_device_health_healthy():
    now_ts = 1700000000
    readings = [
        {
            "deviceId": "dev-1",
            "hiveId": "hive-1",
            "hiveName": "Hive Alpha",
            "apiaryId": "ap-1",
            "apiaryName": "North Yard",
            "timestamp": now_ts - 3600,  # 1 hour ago
            "datetime": "2023-11-14T21:13:20Z",
            "batteryLevel": 92,
        }
    ]
    results = evaluate_device_health(readings, now_ts=now_ts, threshold=80, stale_days=7)
    assert len(results) == 1
    d = results[0]
    assert d.device_id == "dev-1"
    assert d.battery_percent == 92
    assert d.status == "OK"
    assert not d.needs_attention
    assert len(d.reasons) == 0


def test_evaluate_device_health_low_battery():
    now_ts = 1700000000
    readings = [
        {
            "deviceId": "dev-2",
            "hiveId": "hive-1",
            "hiveName": "Hive Alpha",
            "apiaryName": "North Yard",
            "timestamp": now_ts - 7200,  # 2 hours ago
            "batteryLevel": 65,  # <80%, but >=25%
        }
    ]
    results = evaluate_device_health(readings, now_ts=now_ts, threshold=80, critical=25, stale_days=7)
    d = results[0]
    assert d.status == "LOW"
    assert d.needs_attention
    assert any("battery" in r.lower() and "65%" in r for r in d.reasons)


def test_evaluate_device_health_critical_battery():
    now_ts = 1700000000
    readings = [
        {
            "deviceId": "dev-3",
            "hiveId": "hive-2",
            "hiveName": "Hive Beta",
            "timestamp": now_ts - 3600,
            "chargeRemaining": 18,  # <25%
        }
    ]
    results = evaluate_device_health(readings, now_ts=now_ts, threshold=80, critical=25, stale_days=7)
    d = results[0]
    assert d.status == "CRITICAL"
    assert d.needs_attention
    assert any("critical" in r.lower() or "18%" in r for r in d.reasons)


def test_evaluate_device_health_stale_not_reporting():
    now_ts = 1700000000
    eight_days_ago = now_ts - (8 * 86400)
    readings = [
        {
            "deviceId": "dev-4",
            "hiveId": "hive-3",
            "hiveName": "Hive Gamma",
            "timestamp": eight_days_ago,
            "batteryLevel": 88,
        }
    ]
    results = evaluate_device_health(readings, now_ts=now_ts, threshold=80, stale_days=7)
    d = results[0]
    assert d.status == "STALE"
    assert d.needs_attention
    assert d.days_offline is not None and d.days_offline >= 8.0
    assert any("not reporting" in r.lower() or "stale" in r.lower() for r in d.reasons)


def test_evaluate_device_health_both_low_and_stale():
    now_ts = 1700000000
    ten_days_ago = now_ts - (10 * 86400)
    readings = [
        {
            "deviceId": "dev-5",
            "hiveId": "hive-4",
            "hiveName": "Hive Delta",
            "timestamp": ten_days_ago,
            "batteryLevel": 12,  # critical battery AND stale
        }
    ]
    results = evaluate_device_health(readings, now_ts=now_ts, threshold=80, critical=25, stale_days=7)
    d = results[0]
    assert d.status == "CRITICAL"
    assert d.needs_attention
    assert len(d.reasons) >= 2  # battery reason and stale reason


def test_evaluate_device_health_apiary_filter():
    now_ts = 1700000000
    readings = [
        {"deviceId": "d1", "apiaryName": "Yard A", "batteryLevel": 50, "timestamp": now_ts},
        {"deviceId": "d2", "apiaryName": "Yard B", "batteryLevel": 50, "timestamp": now_ts},
    ]
    results = evaluate_device_health(readings, now_ts=now_ts, apiary="yard a")
    assert len(results) == 1
    assert results[0].device_id == "d1"


# --- 3. scan_device_health_from_stream tests ---------------------------------


def test_scan_device_health_from_stream_selects_latest_timestamp():
    now_ts = 1700000000
    # Stream with out-of-order readings for the same device
    stream = [
        {"deviceId": "dev-x", "timestamp": 1000, "batteryLevel": 99},
        {"deviceId": "dev-x", "timestamp": 3000, "batteryLevel": 60},  # Latest
        {"deviceId": "dev-x", "timestamp": 2000, "batteryLevel": 80},
    ]
    results = scan_device_health_from_stream(stream, now_ts=now_ts, threshold=80)
    assert len(results) == 1
    assert results[0].last_seen_epoch == 3000
    assert results[0].battery_percent == 60
    assert results[0].needs_attention


# --- 4. scan_device_health_from_file tests -----------------------------------


def test_scan_device_health_from_ndjson_gz(tmp_path: Path):
    now_ts = 1700000000
    file_path = tmp_path / "readings.ndjson.gz"
    records = [
        {"deviceId": "dev-1", "hiveName": "H1", "apiaryName": "A1", "timestamp": now_ts - 100, "batteryLevel": 90},
        {"deviceId": "dev-2", "hiveName": "H2", "apiaryName": "A1", "timestamp": now_ts - 200, "chargeRemaining": 15},
    ]
    with gzip.open(file_path, "wt", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    results = scan_device_health_from_file(file_path, now_ts=now_ts, threshold=80, critical=25)
    assert len(results) == 2
    by_id = {d.device_id: d for d in results}
    assert by_id["dev-1"].status == "OK"
    assert by_id["dev-2"].status == "CRITICAL"


def test_scan_device_health_from_csv(tmp_path: Path):
    now_ts = 1700000000
    file_path = tmp_path / "readings.csv"
    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["deviceId", "hiveName", "apiaryName", "timestamp", "datetime", "batteryLevel", "chargeRemaining"])
        writer.writeheader()
        writer.writerow({
            "deviceId": "dev-csv-1",
            "hiveName": "Hive CSV",
            "apiaryName": "Apiary CSV",
            "timestamp": str(now_ts - 500),
            "datetime": "2023-11-14T21:05:00Z",
            "batteryLevel": "75",
            "chargeRemaining": "",
        })

    results = scan_device_health_from_file(file_path, now_ts=now_ts, threshold=80)
    assert len(results) == 1
    assert results[0].device_id == "dev-csv-1"
    assert results[0].battery_percent == 75
    assert results[0].status == "LOW"


# --- 5. Formatting tests -----------------------------------------------------


def test_format_table_output():
    devices = [
        DeviceHealth(
            device_id="dev-crit",
            hive_name="H1",
            apiary_name="Yard 1",
            battery_percent=12,
            last_seen_epoch=1000,
            last_seen_datetime="2023-01-01T00:00:00Z",
            days_offline=2.5,
            status="CRITICAL",
            needs_attention=True,
            reasons=["battery 12% (<25% critical)"],
        ),
        DeviceHealth(
            device_id="dev-ok",
            hive_name="H2",
            apiary_name="Yard 1",
            battery_percent=95,
            last_seen_epoch=1000,
            last_seen_datetime="2023-01-01T00:00:00Z",
            days_offline=0.1,
            status="OK",
            needs_attention=False,
            reasons=[],
        ),
    ]

    table_filtered = format_table(devices, show_all=False)
    assert "dev-crit" in table_filtered
    assert "dev-ok" not in table_filtered

    table_all = format_table(devices, show_all=True)
    assert "dev-crit" in table_all
    assert "dev-ok" in table_all


def test_format_json_output():
    devices = [
        DeviceHealth(
            device_id="dev-1",
            battery_percent=70,
            status="LOW",
            needs_attention=True,
            reasons=["battery 70% (<80%)"],
        )
    ]
    raw_json = format_json(devices, show_all=True)
    data = json.loads(raw_json)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["device_id"] == "dev-1"
    assert data[0]["status"] == "LOW"


def test_format_csv_output():
    devices = [
        DeviceHealth(
            device_id="dev-1",
            hive_name="H1",
            apiary_name="A1",
            battery_percent=70,
            status="LOW",
            needs_attention=True,
            reasons=["battery 70% (<80%)"],
        )
    ]
    raw_csv = format_csv(devices, show_all=True)
    lines = raw_csv.strip().splitlines()
    assert len(lines) == 2
    assert "device_id" in lines[0]
    assert "dev-1" in lines[1]


# --- 6. CLI script tests -----------------------------------------------------


def test_find_default_data_file(tmp_path: Path):
    from scripts.battery import find_default_data_file

    assert find_default_data_file(tmp_path) is None

    # Create dummy readings file
    extract_dir = tmp_path / "data" / "extract"
    extract_dir.mkdir(parents=True)
    data_file = extract_dir / "readings.ndjson.gz"
    with gzip.open(data_file, "wt") as f:
        f.write('{"deviceId": "dev1"}\n')

    found = find_default_data_file(tmp_path)
    assert found == data_file


def test_cli_battery_main(tmp_path: Path, capsys: pytest.CaptureFixture):
    import time
    from scripts.battery import main

    now = int(time.time())
    data_file = tmp_path / "readings.ndjson"
    data_file.write_text(
        json.dumps({"deviceId": "dev-low", "hiveName": "H1", "apiaryName": "A1", "timestamp": now - 3600, "batteryLevel": 45}) + "\n"
        + json.dumps({"deviceId": "dev-ok", "hiveName": "H2", "apiaryName": "A1", "timestamp": now - 3600, "batteryLevel": 95}) + "\n"
    )

    # 1. Default table output (only attention needed)
    code = main(["--data", str(data_file), "--threshold", "80"])
    assert code == 0
    captured = capsys.readouterr()
    assert "dev-low" in captured.out
    assert "dev-ok" not in captured.out
    assert "1 need attention" in captured.out

    # 2. JSON output with --all
    code = main(["--data", str(data_file), "--format", "json", "--all"])
    assert code == 0
    captured = capsys.readouterr()
    items = json.loads(captured.out)
    assert len(items) == 2

    # 3. CSV output
    code = main(["--data", str(data_file), "--format", "csv"])
    assert code == 0
    captured = capsys.readouterr()
    assert "device_id" in captured.out
    assert "dev-low" in captured.out

    # 4. Alerting --check mode exits 1 when devices need attention
    code = main(["--data", str(data_file), "--check"])
    assert code == 1

    # 5. Missing file error
    code = main(["--data", str(tmp_path / "nonexistent.json")])
    assert code == 2

