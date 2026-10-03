"""Flatten raw extracted windows into analysis-ready outputs. No API calls.

Reads data/extract/raw/<hiveId>/*.readings.json[.gz] (+ notes) and produces:
    data/extract/readings.ndjson.gz   one JSON object per reading row (gzipped)
    data/extract/readings.csv.gz      same, columnar (gzipped; --no-csv to skip)
    data/extract/notes.ndjson         one object per note
    data/extract/coverage.json        per-hive earliest/latest ts + row counts

Streams rows straight to disk (no 1.4M-row list in memory) and compresses
output (the spike disk is small). Idempotent: rebuilds outputs each run, so
it's safe to re-run after every incremental extract.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "extract"
RAW = OUT / "raw"


def load_json(path: Path):
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(path.read_text())


def hive_meta(manifest: dict) -> dict:
    meta = {}
    for key, rec in manifest.get("completed", {}).items():
        hid = key.split("|", 1)[0]
        meta.setdefault(hid, {"apiaryName": rec.get("apiaryName"),
                              "apiaryId": rec.get("apiaryId"),
                              "hiveName": rec.get("hiveName")})
    return meta


def iter_reading_files(hdir: Path):
    """All readings windows for a hive, gz or plain."""
    yield from sorted(list(hdir.glob("*.readings.json")) + list(hdir.glob("*.readings.json.gz")))


def iter_note_files(hdir: Path):
    yield from sorted(list(hdir.glob("*.notes.json")) + list(hdir.glob("*.notes.json.gz")))


def hive_dirs(raw_root: Path):
    """Every per-hive subdirectory under the raw root, sorted."""
    return [d for d in sorted(raw_root.iterdir()) if d.is_dir()]


def iter_readings(hdir: Path):
    """Yield (positionID, reading) for every reading row across a hive's windows."""
    for f in iter_reading_files(hdir):
        for pos in load_json(f) or []:
            pid = pos.get("positionID")
            for r in pos.get("readings", []) or []:
                yield pid, r


def discover_metric_keys(raw_root: Path) -> set:
    """Pass 1: the (small, stable) set of metric names across all windows, so
    the CSV can have a fixed header."""
    keys: set[str] = set()
    for hdir in hive_dirs(raw_root):
        for _pid, r in iter_readings(hdir):
            keys.update((r.get("readings") or {}).keys())
    return keys


def build_row(hid: str, m: dict, pid: str, r: dict) -> dict:
    """Assemble one flat reading row (base columns + ``m_``-prefixed metrics)."""
    ts = r.get("timestamp")
    metrics = r.get("readings") or {}
    return {
        "apiaryId": m.get("apiaryId"), "apiaryName": m.get("apiaryName"),
        "hiveId": hid, "hiveName": m.get("hiveName"),
        "positionID": pid, "deviceId": r.get("deviceId"),
        "timestamp": ts,
        "datetime": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts is not None else None,
        "batteryLevel": r.get("batteryLevel"),
        "chargeRemaining": r.get("chargeRemaining"),
        **{f"m_{k}": v for k, v in metrics.items()},
    }


def accumulate_coverage(coverage: dict, row: dict) -> None:
    """Fold one flat reading row into the per-hive running coverage stats."""
    hid = row["hiveId"]
    ts = row.get("timestamp")
    c = coverage[hid]
    c["rows"] += 1
    c["devices"].add(row.get("deviceId"))
    c["positions"].add(row.get("positionID"))
    if ts is not None:
        c["min_ts"] = ts if c["min_ts"] is None else min(c["min_ts"], ts)
        c["max_ts"] = ts if c["max_ts"] is None else max(c["max_ts"], ts)


def iter_hive_rows(hdir: Path, meta: dict):
    """Yield deduplicated flat row dicts for one hive directory."""
    hid = hdir.name
    m = meta.get(hid, {})
    seen: set = set()
    for pid, r in iter_readings(hdir):
        ts = r.get("timestamp")
        dk = (pid, r.get("deviceId"), ts)
        if dk in seen:
            continue
        seen.add(dk)
        yield build_row(hid, m, pid, r)


def stream_readings(raw_root, meta, base_cols, metric_cols, ndjson_fh, csv_writer, coverage) -> int:
    """Pass 2: stream every deduped reading row to ndjson (+ optional csv),
    updating coverage. Returns the total row count written."""
    n_rows = 0
    for hdir in hive_dirs(raw_root):
        for row in iter_hive_rows(hdir, meta):
            ndjson_fh.write(json.dumps(row) + "\n")
            if csv_writer:
                csv_writer.writerow({k: row.get(k) for k in base_cols + metric_cols})
            n_rows += 1
            accumulate_coverage(coverage, row)
    return n_rows


def write_notes(out_path: Path, meta: dict, raw: Path) -> int:
    """Write all notes (small) to plain ndjson. Returns the note count."""
    n_notes = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for hdir in hive_dirs(raw):
            hid = hdir.name
            m = meta.get(hid, {})
            for f in iter_note_files(hdir):
                payload = load_json(f)
                items = payload if isinstance(payload, list) else (payload or {}).get("notes", [])
                for n in items or []:
                    fh.write(json.dumps({"hiveId": hid, "hiveName": m.get("hiveName"), **n}) + "\n")
                    n_notes += 1
    return n_notes


def build_coverage_out(coverage: dict, meta: dict) -> dict:
    """Turn the running per-hive coverage stats into the serializable summary."""
    cov_out = {}
    for hid, c in coverage.items():
        m = meta.get(hid, {})
        cov_out[hid] = {
            "apiaryName": m.get("apiaryName"), "hiveName": m.get("hiveName"),
            "rows": c["rows"],
            "devices": sorted(d for d in c["devices"] if d),
            "positions": sorted(p for p in c["positions"] if p),
            "earliest": datetime.fromtimestamp(c["min_ts"], tz=timezone.utc).isoformat() if c["min_ts"] is not None else None,
            "latest": datetime.fromtimestamp(c["max_ts"], tz=timezone.utc).isoformat() if c["max_ts"] is not None else None,
        }
    return cov_out


build_coverage = build_coverage_out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-csv", action="store_true", help="skip the (large) CSV output")
    args = ap.parse_args()

    if not RAW.exists():
        print("no raw data yet; run extract_all.py first", file=sys.stderr)
        return 1

    manifest = load_json(OUT / "manifest.json") if (OUT / "manifest.json").exists() else {}
    meta = hive_meta(manifest)

    base_cols = ["apiaryId", "apiaryName", "hiveId", "hiveName", "positionID",
                 "deviceId", "timestamp", "datetime", "batteryLevel", "chargeRemaining"]

    # Pass 1: discover metric keys (stable, tiny set) so CSV has a fixed header
    # AND for accurate output reporting of metrics actually present in NDJSON.
    metric_keys = discover_metric_keys(RAW)
    metric_cols = [f"m_{k}" for k in sorted(metric_keys)]

    # Pass 2: stream rows to gzipped ndjson (+ optional gzipped csv).
    coverage = defaultdict(lambda: {"rows": 0, "min_ts": None, "max_ts": None,
                                    "devices": set(), "positions": set()})
    ndjson_fh = gzip.open(OUT / "readings.ndjson.gz", "wt", encoding="utf-8")
    csv_fh = csv_writer = None
    if not args.no_csv:
        csv_fh = io.TextIOWrapper(gzip.open(OUT / "readings.csv.gz", "wb"), encoding="utf-8", newline="")
        csv_writer = csv.DictWriter(csv_fh, fieldnames=base_cols + metric_cols)
        csv_writer.writeheader()

    try:
        n_rows = stream_readings(RAW, meta, base_cols, metric_cols, ndjson_fh, csv_writer, coverage)
    finally:
        ndjson_fh.close()
        if csv_fh:
            csv_fh.close()

    n_notes = write_notes(OUT / "notes.ndjson", meta, RAW)

    cov_out = build_coverage_out(coverage, meta)
    (OUT / "coverage.json").write_text(json.dumps(cov_out, indent=2))

    print(f"readings rows : {n_rows}")
    print(f"notes         : {n_notes}")
    print(f"metrics seen  : {sorted(metric_keys)}")
    print(f"hives w/ data : {len(cov_out)}")
    outs = "readings.ndjson.gz, " + ("" if args.no_csv else "readings.csv.gz, ") + "notes.ndjson, coverage.json"
    print(f"wrote: {outs} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
