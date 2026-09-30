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
import os
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


# Baseline of known metrics observed in production to guarantee a fixed CSV header.
KNOWN_METRIC_KEYS = {"audio", "humidity", "radar", "swarmState", "temperature", "weight"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-csv", action="store_true", help="skip the (large) CSV output")
    ap.add_argument("--merge", action="store_true",
                    help="merge incremental raw windows into existing readings and notes outputs")
    args = ap.parse_args()

    if not RAW.exists() and not args.merge:
        print("no raw data yet; run extract_all.py first", file=sys.stderr)
        return 1

    manifest = load_json(OUT / "manifest.json") if (OUT / "manifest.json").exists() else {}
    meta = hive_meta(manifest)

    base_cols = ["apiaryId", "apiaryName", "hiveId", "hiveName", "positionID",
                 "deviceId", "timestamp", "datetime", "batteryLevel", "chargeRemaining"]

    target_ndjson = OUT / "readings.ndjson.gz"
    target_csv = OUT / "readings.csv.gz"

    # Pass 1: discover metric keys, unioned with KNOWN_METRIC_KEYS for fixed header stability.
    metric_keys: set[str] = set(KNOWN_METRIC_KEYS)
    if RAW.exists():
        for hdir in sorted(RAW.iterdir()):
            if not hdir.is_dir():
                continue
            for f in iter_reading_files(hdir):
                for pos in load_json(f) or []:
                    for r in pos.get("readings", []) or []:
                        metric_keys.update((r.get("readings") or {}).keys())

    # In merge mode, include any existing m_* metrics already in the archive
    # so historical fields remain in the rebuilt CSV even if raw files were purged.
    if args.merge and target_ndjson.exists() and target_ndjson.stat().st_size > 0:
        with gzip.open(target_ndjson, "rt", encoding="utf-8") as in_fh:
            for line in in_fh:
                if not line.strip():
                    continue
                for key in json.loads(line):
                    if key.startswith("m_") and len(key) > 2:
                        metric_keys.add(key[2:])

    metric_cols = [f"m_{k}" for k in sorted(metric_keys)]

    coverage = defaultdict(lambda: {"rows": 0, "min_ts": None, "max_ts": None,
                                    "devices": set(), "positions": set()})
    # In non-merge mode, deduplication is scoped per-hive to keep memory minimal.
    # In --merge mode, seen holds (positionID, deviceId, timestamp) keys per hive
    # (~150MB footprint for ~2.7M rows) to stream and deduplicate incoming raw readings.
    seen: dict[str, set] = defaultdict(set)
    n_rows = 0

    proc_id = os.getpid()
    tmp_ndjson = OUT / f"readings.ndjson.gz.tmp.{proc_id}"
    tmp_csv = OUT / f"readings.csv.gz.tmp.{proc_id}"

    write_ndjson_path = tmp_ndjson
    ndjson_fh = gzip.open(write_ndjson_path, "wt", encoding="utf-8")
    csv_fh = csv_writer = None
    if not args.no_csv:
        write_csv_path = tmp_csv
        csv_fh = io.TextIOWrapper(gzip.open(write_csv_path, "wb"), encoding="utf-8", newline="")
        csv_writer = csv.DictWriter(csv_fh, fieldnames=base_cols + metric_cols)
        csv_writer.writeheader()

    readings_success = False
    try:
        # If merging, stream existing readings first into the temp file and populate seen keys.
        if args.merge and target_ndjson.exists() and target_ndjson.stat().st_size > 0:
            with gzip.open(target_ndjson, "rt", encoding="utf-8") as in_fh:
                for line in in_fh:
                    row = json.loads(line)
                    hid = row.get("hiveId")
                    pid = row.get("positionID")
                    did = row.get("deviceId")
                    ts = row.get("timestamp")
                    dk = (pid, did, ts)
                    seen[hid].add(dk)
                    ndjson_fh.write(line if line.endswith("\n") else line + "\n")
                    if csv_writer:
                        csv_writer.writerow({k: row.get(k) for k in base_cols + metric_cols})
                    n_rows += 1
                    c = coverage[hid]
                    c["rows"] += 1
                    if did:
                        c["devices"].add(did)
                    if pid:
                        c["positions"].add(pid)
                    if ts:
                        c["min_ts"] = ts if c["min_ts"] is None else min(c["min_ts"], ts)
                        c["max_ts"] = ts if c["max_ts"] is None else max(c["max_ts"], ts)

        # Process raw directories
        if RAW.exists():
            for hdir in sorted(RAW.iterdir()):
                if not hdir.is_dir():
                    continue
                hid = hdir.name
                m = meta.get(hid, {})
                if not args.merge:
                    hive_seen = set()
                else:
                    hive_seen = seen[hid]
                for f in iter_reading_files(hdir):
                    for pos in load_json(f) or []:
                        pid = pos.get("positionID")
                        for r in pos.get("readings", []) or []:
                            ts = r.get("timestamp")
                            did = r.get("deviceId")
                            dk = (pid, did, ts)
                            if dk in hive_seen:
                                continue
                            hive_seen.add(dk)
                            metrics = r.get("readings") or {}
                            row = {
                                "apiaryId": m.get("apiaryId"), "apiaryName": m.get("apiaryName"),
                                "hiveId": hid, "hiveName": m.get("hiveName"),
                                "positionID": pid, "deviceId": did,
                                "timestamp": ts,
                                "datetime": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
                                "batteryLevel": r.get("batteryLevel"),
                                "chargeRemaining": r.get("chargeRemaining"),
                                **{f"m_{k}": v for k, v in metrics.items()},
                            }
                            ndjson_fh.write(json.dumps(row) + "\n")
                            if csv_writer:
                                csv_writer.writerow({k: row.get(k) for k in base_cols + metric_cols})
                            n_rows += 1
                            c = coverage[hid]
                            c["rows"] += 1
                            if did:
                                c["devices"].add(did)
                            if pid:
                                c["positions"].add(pid)
                            if ts:
                                c["min_ts"] = ts if c["min_ts"] is None else min(c["min_ts"], ts)
                                c["max_ts"] = ts if c["max_ts"] is None else max(c["max_ts"], ts)
        readings_success = True
    finally:
        ndjson_fh.close()
        if csv_fh:
            csv_fh.close()
        if not readings_success:
            tmp_ndjson.unlink(missing_ok=True)
            tmp_csv.unlink(missing_ok=True)

    # Atomically replace target reading files with the newly written temporary outputs
    if tmp_ndjson.exists():
        tmp_ndjson.replace(target_ndjson)
    if not args.no_csv and tmp_csv.exists():
        tmp_csv.replace(target_csv)

    # Notes -> plain ndjson
    target_notes = OUT / "notes.ndjson"
    tmp_notes = OUT / f"notes.ndjson.tmp.{proc_id}"
    write_notes_path = tmp_notes
    seen_notes: set = set()
    n_notes = 0

    notes_success = False
    try:
        with write_notes_path.open("w", encoding="utf-8") as fh:
            if args.merge and target_notes.exists() and target_notes.stat().st_size > 0:
                with target_notes.open("r", encoding="utf-8") as in_notes:
                    for line in in_notes:
                        if not line.strip():
                            continue
                        note = json.loads(line)
                        nk = (note.get("hiveId"), note.get("id") or (note.get("created"), note.get("description")))
                        seen_notes.add(nk)
                        fh.write(line if line.endswith("\n") else line + "\n")
                        n_notes += 1

            if RAW.exists():
                for hdir in sorted(RAW.iterdir()):
                    if not hdir.is_dir():
                        continue
                    hid = hdir.name
                    m = meta.get(hid, {})
                    for f in iter_note_files(hdir):
                        payload = load_json(f)
                        items = payload if isinstance(payload, list) else (payload or {}).get("notes", [])
                        for n in items or []:
                            nk = (hid, n.get("id") or (n.get("created"), n.get("description")))
                            if nk in seen_notes:
                                continue
                            seen_notes.add(nk)
                            fh.write(json.dumps({"hiveId": hid, "hiveName": m.get("hiveName"), **n}) + "\n")
                            n_notes += 1
        notes_success = True
    finally:
        if not notes_success and tmp_notes.exists():
            tmp_notes.unlink(missing_ok=True)

    if tmp_notes.exists():
        tmp_notes.replace(target_notes)

    cov_out = {}
    for hid, c in sorted(coverage.items()):
        m = meta.get(hid, {})
        cov_out[hid] = {
            "apiaryName": m.get("apiaryName"), "hiveName": m.get("hiveName"),
            "rows": c["rows"],
            "devices": sorted(d for d in c["devices"] if d),
            "positions": sorted(p for p in c["positions"] if p),
            "earliest": datetime.fromtimestamp(c["min_ts"], tz=timezone.utc).isoformat() if c["min_ts"] else None,
            "latest": datetime.fromtimestamp(c["max_ts"], tz=timezone.utc).isoformat() if c["max_ts"] else None,
        }
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
