"""Battery health and offline sensor monitoring engine.

Evaluates device battery levels and telemetry freshness across BroodMinder hardware.
In hive sensor deployments, both:
  1. Low battery (<80% voltage drop threshold, <25% critical exhaustion)
  2. "Not reporting" (stale telemetry exceeding expected sync intervals, e.g. >7 days)
indicate that a sensor's battery needs to be checked or replaced.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


@dataclass
class DeviceHealth:
    """Health summary for an individual sensor device."""
    device_id: str
    hive_id: str | None = None
    hive_name: str | None = None
    apiary_id: str | None = None
    apiary_name: str | None = None
    battery_percent: int | None = None
    last_seen_epoch: int | None = None
    last_seen_datetime: str | None = None
    days_offline: float | None = None
    status: str = "OK"  # "CRITICAL", "LOW", "STALE", "OK", "UNKNOWN"
    needs_attention: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return asdict(self)


def extract_battery(reading: dict[str, Any]) -> int | None:
    """Extract and normalize battery percentage from a reading dictionary.

    Handles `batteryLevel` and `chargeRemaining`, which alternate depending
    on device hardware and firmware version.
    """
    raw = reading.get("batteryLevel")
    if raw is None or raw == "":
        raw = reading.get("chargeRemaining")

    if raw is None or raw == "":
        return None

    try:
        val = int(round(float(raw)))
        # Clamp to realistic 0-100% bounds
        return max(0, min(100, val))
    except (ValueError, TypeError, OverflowError):
        return None


def _matches_apiary(reading: dict[str, Any], apiary_filter: str | None) -> bool:
    """Return True when the reading belongs to the requested apiary (or no filter set)."""
    if not apiary_filter:
        return True
    ap_name = reading.get("apiaryName")
    ap_id = reading.get("apiaryId")
    matches_name = ap_name and apiary_filter in ap_name.lower()
    matches_id = ap_id and apiary_filter == str(ap_id).lower()
    return bool(matches_name or matches_id)


def _parse_last_epoch(reading: dict[str, Any]) -> int | None:
    """Parse the reading's timestamp into an integer epoch, or None if absent/invalid."""
    ts = reading.get("timestamp")
    return int(ts) if ts is not None and str(ts).isdigit() else None


def _classify_battery(
    battery: int | None, is_stale: bool, threshold: int, critical: int
) -> tuple[str, list[str], bool]:
    """Derive (status, battery-related reasons, battery-needs-attention) for a reading."""
    if battery is None:
        if is_stale:
            return "STALE", [], False
        return "UNKNOWN", ["No battery telemetry reported"], False

    if battery < critical:
        return "CRITICAL", [f"Critical battery {battery}% (<{critical}% critical threshold)"], True
    if battery < threshold:
        return "LOW", [f"Low battery {battery}% (<{threshold}% warning threshold)"], True
    if is_stale:
        return "STALE", [], False
    return "OK", [], False


def _evaluate_reading(
    reading: dict[str, Any],
    ref_ts: int,
    threshold: int,
    critical: int,
    stale_days: int,
) -> DeviceHealth | None:
    """Evaluate a single reading into a DeviceHealth, or None if it has no device id."""
    dev_id = str(reading.get("deviceId") or "").strip()
    if not dev_id:
        return None

    battery = extract_battery(reading)
    last_epoch = _parse_last_epoch(reading)

    dt = reading.get("datetime")
    if not dt and last_epoch is not None:
        dt = datetime.fromtimestamp(last_epoch, tz=timezone.utc).isoformat()

    days_offline = None
    if last_epoch is not None:
        days_offline = round(max(0, ref_ts - last_epoch) / 86400.0, 1)

    is_stale = days_offline is not None and days_offline > stale_days
    reasons: list[str] = []
    if is_stale:
        reasons.append(f"Not reporting ({days_offline:.1f} days offline > {stale_days}d threshold)")

    status, battery_reasons, battery_attention = _classify_battery(
        battery, is_stale, threshold, critical
    )
    reasons.extend(battery_reasons)

    return DeviceHealth(
        device_id=dev_id,
        hive_id=reading.get("hiveId"),
        hive_name=reading.get("hiveName"),
        apiary_id=reading.get("apiaryId"),
        apiary_name=reading.get("apiaryName"),
        battery_percent=battery,
        last_seen_epoch=last_epoch,
        last_seen_datetime=dt,
        days_offline=days_offline,
        status=status,
        needs_attention=is_stale or battery_attention,
        reasons=reasons,
    )


def evaluate_device_health(
    readings: Iterable[dict[str, Any]],
    now_ts: int | None = None,
    threshold: int = 80,
    critical: int = 25,
    stale_days: int = 7,
    apiary: str | None = None,
) -> list[DeviceHealth]:
    """Evaluate health status across latest device readings.

    Args:
        readings: Iterable of reading dictionaries containing device metadata and timestamps.
        now_ts: Reference epoch timestamp (defaults to current time).
        threshold: Battery percentage warning threshold (< threshold requires check). Default: 80.
        critical: Battery percentage critical threshold (< critical requires immediate change). Default: 25.
        stale_days: Number of days without reporting before device is flagged STALE. Default: 7.
        apiary: Optional filter for apiary name or ID (case-insensitive).

    Returns:
        List of DeviceHealth objects sorted with devices needing attention first.
    """
    ref_ts = now_ts if now_ts is not None else int(time.time())
    apiary_filter = apiary.strip().lower() if apiary else None

    devices: list[DeviceHealth] = []
    for r in readings:
        if not _matches_apiary(r, apiary_filter):
            continue
        health = _evaluate_reading(r, ref_ts, threshold, critical, stale_days)
        if health is not None:
            devices.append(health)

    # Sort: needs_attention first, then lowest battery, then longest offline
    devices.sort(
        key=lambda d: (
            not d.needs_attention,
            d.battery_percent if d.battery_percent is not None else 999,
            -(d.days_offline or 0),
        )
    )

    return devices


def scan_device_health_from_stream(
    records: Iterable[dict[str, Any]],
    now_ts: int | None = None,
    threshold: int = 80,
    critical: int = 25,
    stale_days: int = 7,
    apiary: str | None = None,
) -> list[DeviceHealth]:
    """Scan a stream of readings, keep the latest reading per device, and evaluate health."""
    latest_by_device: dict[str, dict[str, Any]] = {}
    latest_ts_by_device: dict[str, int] = {}

    for r in records:
        dev_id = r.get("deviceId")
        if not dev_id:
            continue

        try:
            ts = int(r.get("timestamp") or 0)
        except (ValueError, TypeError):
            ts = 0

        if dev_id not in latest_by_device or ts >= latest_ts_by_device[dev_id]:
            latest_by_device[dev_id] = r
            latest_ts_by_device[dev_id] = ts

    return evaluate_device_health(
        latest_by_device.values(),
        now_ts=now_ts,
        threshold=threshold,
        critical=critical,
        stale_days=stale_days,
        apiary=apiary,
    )


def _iter_ndjson(fh: Iterable[str]) -> Iterable[dict[str, Any]]:
    """Yield parsed objects from NDJSON / JSON-lines text, skipping blank/invalid lines."""
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def scan_device_health_from_file(
    file_path: Path | str,
    now_ts: int | None = None,
    threshold: int = 80,
    critical: int = 25,
    stale_days: int = 7,
    apiary: str | None = None,
) -> list[DeviceHealth]:
    """Read an NDJSON, CSV, or gzip-compressed file and return evaluated device health."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    name = path.name.lower()
    opener = gzip.open if name.endswith(".gz") else open
    is_csv = ".csv" in name

    with opener(path, "rt", encoding="utf-8") as fh:
        records = csv.DictReader(fh) if is_csv else _iter_ndjson(fh)
        # The stream is consumed eagerly here, before the file handle closes.
        return scan_device_health_from_stream(
            records,
            now_ts=now_ts,
            threshold=threshold,
            critical=critical,
            stale_days=stale_days,
            apiary=apiary,
        )


def _health_row(d: DeviceHealth) -> list[str]:
    """Build a single formatted table row for a device."""
    batt_str = f"{d.battery_percent}%" if d.battery_percent is not None else "N/A"
    offline_str = f"{d.days_offline:.1f}d" if d.days_offline is not None else "-"
    apiary_str = (d.apiary_name or "-")[:16]
    hive_str = (d.hive_name or "-")[:12]
    dev_str = d.device_id[:14] + "..." if len(d.device_id) > 17 else d.device_id
    reasons_str = "; ".join(d.reasons) if d.reasons else "Normal"
    return [d.status, batt_str, offline_str, apiary_str, hive_str, dev_str, reasons_str]


def format_table(devices: list[DeviceHealth], show_all: bool = False) -> str:
    """Format device health items as a clean, aligned terminal/markdown table."""
    items = devices if show_all else [d for d in devices if d.needs_attention]

    if not items:
        return "All devices healthy (no batteries < threshold and no stale reporting)."

    headers = ["Status", "Battery", "Days Offline", "Apiary", "Hive", "Device ID", "Diagnosis"]
    rows = [_health_row(d) for d in items]

    # Compute column widths from headers and every row cell.
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(val))

    def _line(cols: list[str]) -> str:
        return " | ".join(f"{col:<{col_widths[i]}}" for i, col in enumerate(cols))

    sep = "-+-".join("-" * w for w in col_widths)
    output = [_line(headers), sep]
    output.extend(_line(row) for row in rows)

    return "\n".join(output)


def format_json(devices: list[DeviceHealth], show_all: bool = False, indent: int = 2) -> str:
    """Format device health records as JSON string."""
    items = devices if show_all else [d for d in devices if d.needs_attention]
    return json.dumps([d.to_dict() for d in items], indent=indent)


def format_csv(devices: list[DeviceHealth], show_all: bool = False) -> str:
    """Format device health records as CSV string."""
    items = devices if show_all else [d for d in devices if d.needs_attention]
    output = io.StringIO()
    fields = [
        "device_id",
        "hive_name",
        "apiary_name",
        "battery_percent",
        "days_offline",
        "status",
        "needs_attention",
        "last_seen_datetime",
        "reasons",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for d in items:
        row = d.to_dict()
        row["reasons"] = "; ".join(d.reasons)
        writer.writerow({k: row.get(k) for k in fields})
    return output.getvalue()


def find_default_data_file(base_dir: Path | None = None) -> Path | None:
    """Find the best available local flattened readings dataset."""
    root = base_dir or Path.cwd()
    candidates = [
        root / "data" / "extract" / "readings.ndjson.gz",
        root / "data" / "extract" / "readings.csv.gz",
        root / "data" / "extract" / "readings.ndjson",
        root / "data" / "extract" / "readings.csv",
    ]
    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            return c
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for battery and device health inspection."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Inspect BroodMinder sensor battery health and stale reporting status."
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="Path to flattened readings file (.ndjson.gz, .csv.gz, etc.)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=80,
        help="Battery warning threshold percentage (default: 80)",
    )
    parser.add_argument(
        "--critical",
        type=int,
        default=25,
        help="Critical battery threshold percentage (default: 25)",
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=7,
        help="Days without reporting before flagging sensor as stale (default: 7)",
    )
    parser.add_argument(
        "--apiary",
        type=str,
        default=None,
        help="Filter by apiary name or ID",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show all devices including healthy ones",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json", "csv"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit with code 1 if any device needs attention (alerting mode)",
    )

    args = parser.parse_args(argv)

    data_file = args.data or find_default_data_file()
    if not data_file or not data_file.exists():
        print(
            "Error: No readings data found. Run `scripts/extract_all.py` and `scripts/flatten.py` "
            "first, or specify `--data path/to/readings.ndjson.gz`.",
            file=sys.stderr,
        )
        return 2

    devices = scan_device_health_from_file(
        data_file,
        threshold=args.threshold,
        critical=args.critical,
        stale_days=args.stale_days,
        apiary=args.apiary,
    )

    needs_attention = [d for d in devices if d.needs_attention]
    critical_count = sum(1 for d in devices if d.status == "CRITICAL")
    low_count = sum(1 for d in devices if d.status == "LOW")
    stale_count = sum(1 for d in devices if d.status == "STALE")

    # Output according to format
    if args.format == "table":
        table = format_table(devices, show_all=args.all)
        print(table)
        print(
            f"\nSummary: {len(devices)} device(s) evaluated. "
            f"{len(needs_attention)} need attention "
            f"({critical_count} critical <{args.critical}%, {low_count} low <{args.threshold}%, {stale_count} stale >{args.stale_days}d)."
        )
    elif args.format == "json":
        print(format_json(devices, show_all=args.all))
    elif args.format == "csv":
        print(format_csv(devices, show_all=args.all).strip())

    if args.check and needs_attention:
        return 1
    return 0
