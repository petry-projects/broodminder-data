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


def iter_readings(hdir: Path):
    """Yield (positionID, reading) for every reading row in a hive dir."""
    for f in iter_reading_files(hdir):
        for pos in load_json(f) or []:
            pid = pos.get("positionID")
            for r in pos.get("readings", []) or []:
                yield pid, r


def discover_metric_keys(raw: Path) -> set[str]:
    """Pass 1: scan every reading for its metric names so the CSV header is
    stable and fixed before any rows are streamed."""
    metric_keys: set[str] = set()
    for hdir in sorted(raw.iterdir()):
        if not hdir.is_dir():
            continue
        for _pid, r in iter_readings(hdir):
            metric_keys.update((r.get("readings") or {}).keys())
    return metric_keys


def build_row(hid: str, m: dict, pid, r: dict) -> dict:
    """Project one raw reading into a flat output row (metrics get an m_ prefix)."""
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


def iter_hive_rows(hdir: Path, m: dict, seen: set | None = None):
    """Yield deduped rows for one hive dir. Overlapping/re-fetched windows can
    repeat the same (position, device, timestamp); dedupe within the hive
    (reset per hive to bound memory)."""
    hid = hdir.name
    hive_seen = seen if seen is not None else set()
    for pid, r in iter_readings(hdir):
        dk = (pid, r.get("deviceId"), r.get("timestamp"))
        if dk in hive_seen:
            continue
        hive_seen.add(dk)
        yield build_row(hid, m, pid, r)


def accumulate_coverage(coverage: defaultdict, row: dict) -> None:
    """Fold one output row into the per-hive coverage tally."""
    c = coverage[row["hiveId"]]
    c["rows"] += 1
    c["devices"].add(row["deviceId"])
    c["positions"].add(row["positionID"])
    ts = row["timestamp"]
    if ts is not None:
        c["min_ts"] = ts if c["min_ts"] is None else min(c["min_ts"], ts)
        c["max_ts"] = ts if c["max_ts"] is None else max(c["max_ts"], ts)


def write_notes(path: Path, meta: dict, raw: Path = RAW, merge: bool = False, existing_target: Path | None = None) -> int:
    """Notes are small — flatten them to plain ndjson. Returns the row count."""
    n_notes = 0
    seen_notes: set = set()
    with path.open("w", encoding="utf-8") as fh:
        if merge and existing_target and existing_target.exists() and existing_target.stat().st_size > 0:
            with existing_target.open("r", encoding="utf-8") as in_notes:
                for line in in_notes:
                    if not line.strip():
                        continue
                    note = json.loads(line)
                    nk = (note.get("hiveId"), note.get("id") or (note.get("created"), note.get("description")))
                    seen_notes.add(nk)
                    fh.write(line if line.endswith("\n") else line + "\n")
                    n_notes += 1

        if raw.exists():
            for hdir in sorted(raw.iterdir()):
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
    return n_notes


def build_coverage(coverage: dict, meta: dict) -> dict:
    """Render the accumulated coverage tally into the serializable summary."""
    cov_out = {}
    for hid, c in sorted(coverage.items()):
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
    target_notes = OUT / "notes.ndjson"

    # Pass 1: discover metric keys, unioned with KNOWN_METRIC_KEYS for fixed header stability.
    metric_keys: set[str] = set(KNOWN_METRIC_KEYS)
    if RAW.exists() and not args.no_csv:
        metric_keys.update(discover_metric_keys(RAW))

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
    seen: dict[str, set] = defaultdict(set)
    n_rows = 0

    proc_id = os.getpid()
    tmp_ndjson = OUT / f"readings.ndjson.gz.tmp.{proc_id}"
    tmp_csv = OUT / f"readings.csv.gz.tmp.{proc_id}"
    tmp_notes = OUT / f"notes.ndjson.tmp.{proc_id}"

    ndjson_fh = gzip.open(tmp_ndjson, "wt", encoding="utf-8")
    csv_fh = csv_writer = None
    if not args.no_csv:
        csv_fh = io.TextIOWrapper(gzip.open(tmp_csv, "wb"), encoding="utf-8", newline="")
        csv_writer = csv.DictWriter(csv_fh, fieldnames=base_cols + metric_cols)
        csv_writer.writeheader()

    readings_success = False
    try:
        if args.merge and target_ndjson.exists() and target_ndjson.stat().st_size > 0:
            with gzip.open(target_ndjson, "rt", encoding="utf-8") as in_fh:
                for line in in_fh:
                    row = json.loads(line)
                    hid = row.get("hiveId")
                    pid = row.get("positionID")
                    did = row.get("deviceId")
                    ts = row.get("timestamp")
                    seen[hid].add((pid, did, ts))
                    ndjson_fh.write(line if line.endswith("\n") else line + "\n")
                    if csv_writer:
                        csv_writer.writerow({k: row.get(k) for k in base_cols + metric_cols})
                    n_rows += 1
                    accumulate_coverage(coverage, row)

        if RAW.exists():
            for hdir in sorted(RAW.iterdir()):
                if not hdir.is_dir():
                    continue
                m = meta.get(hdir.name, {})
                hive_seen = seen[hdir.name] if args.merge else None
                for row in iter_hive_rows(hdir, m, hive_seen):
                    ndjson_fh.write(json.dumps(row) + "\n")
                    if csv_writer:
                        csv_writer.writerow({k: row.get(k) for k in base_cols + metric_cols})
                    n_rows += 1
                    accumulate_coverage(coverage, row)
        readings_success = True
    finally:
        ndjson_fh.close()
        if csv_fh:
            csv_fh.close()
        if not readings_success:
            tmp_ndjson.unlink(missing_ok=True)
            tmp_csv.unlink(missing_ok=True)

    if tmp_ndjson.exists():
        tmp_ndjson.replace(target_ndjson)
    if not args.no_csv and tmp_csv.exists():
        tmp_csv.replace(target_csv)

    notes_success = False
    try:
        n_notes = write_notes(tmp_notes, meta, raw=RAW, merge=args.merge, existing_target=target_notes)
        notes_success = True
    finally:
        if not notes_success and tmp_notes.exists():
            tmp_notes.unlink(missing_ok=True)

    if tmp_notes.exists():
        tmp_notes.replace(target_notes)

    cov_out = build_coverage(coverage, meta)
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
