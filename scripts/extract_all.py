"""Resumable, budget-aware full-history extraction of all BroodMinder data.

Walks every apiary -> hive, windows the full time range past the API's 6-month
per-request cap, and pulls readings (+ notes) for each window. Writes the raw
responses to disk losslessly and records progress in a manifest so the run can
be stopped and resumed across days (the key is capped at 1000 calls/day).

Layout (under --out, default data/extract/):
    manifest.json                         progress + per-window row counts
    raw/<hiveId>/<start>-<end>.readings.json
    raw/<hiveId>/<start>-<end>.notes.json

Re-run with the same args to resume; completed windows are skipped. Use
scripts/flatten.py afterwards to build analysis-ready NDJSON/CSV (no API calls).

Examples:
    python scripts/extract_all.py --start 2022-01-01 --max-calls 800
    python scripts/extract_all.py --apiary "My Apiary" --start 2024-06-01
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bm.client import (  # noqa: E402
    BroodMinderClient,
    BroodMinderError,
    RateLimited,
    iter_windows,
    now_epoch,
)


class UnsafePathError(ValueError):
    pass


def resolve_within(base: Path, *parts: str) -> Path:
    """Join untrusted *parts onto base and prove the result stays inside base.

    Both the ``--out`` directory (a CLI argument) and the hive id (external API
    data) flow into the raw-write paths. A component containing ``..`` or an
    absolute path would otherwise let a write escape the extract directory and
    clobber arbitrary files (pythonsecurity:S8707). Resolve the candidate and
    fail closed if it lands outside ``base`` — real ids and window filenames
    never trip this, so behavior is unchanged for legitimate input.
    """
    base_resolved = base.resolve()
    target = base_resolved.joinpath(*parts).resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:
        raise UnsafePathError(f"path escapes {base_resolved}: {parts!r}") from exc
    return target


def write_gz(path: Path, obj) -> None:
    """Write a JSON object gzip-compressed (raw hive data is highly repetitive
    and the spike disk is small — gzip shrinks it ~19x)."""
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(obj, fh)


def parse_date(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def count_reading_rows(payload) -> int:
    if not isinstance(payload, list):
        return 0
    return sum(len(pos.get("readings", []) or []) for pos in payload)


def count_notes(payload) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        return len(payload.get("notes", []) or [])
    return 0


def load_manifest(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"completed": {}, "meta": {}}


def get_manifest_max_end(manifest: dict) -> int | None:
    """Find the latest completed window end timestamp across all hives."""
    completed = manifest.get("completed", {})
    if not completed:
        return None
    ends = []
    for k in completed.keys():
        parts = k.rsplit("|", 2)
        if len(parts) == 3:
            try:
                ends.append(int(parts[2]))
            except ValueError:
                pass
    return max(ends) if ends else None


def get_hive_resume_start(manifest: dict, hive_id: str | int, default_start: int) -> int:
    """Find the resume start timestamp for a specific hive based on completed windows.

    Resumes from the latest completed window end timestamp for this hive, or falls back
    to default_start if no prior completed windows exist for the hive.
    """
    completed = manifest.get("completed", {})
    hid_str = str(hive_id)
    ends = []
    for k in completed.keys():
        parts = k.rsplit("|", 2)
        if len(parts) == 3 and parts[0] == hid_str:
            try:
                ends.append(int(parts[2]))
            except ValueError:
                pass
    return max(ends) if ends else default_start


class _BudgetExhausted(Exception):
    """Raised by process_hive when the call budget is reached; caught in main."""


def select_apiaries(apiaries, filters: list) -> list:
    """Filter apiaries by name (case-insensitive) or exact apiaryId. No filters
    means keep them all. Normalizes both bare-list and wrapped-dict payloads."""
    if isinstance(apiaries, dict):
        apiaries = apiaries.get("apiaries", [])
    if not filters:
        return apiaries
    wanted = {a.lower() for a in filters}
    return [a for a in apiaries
            if a.get("name", "").lower() in wanted or a.get("apiaryId") in filters]


def fetch_window(bm, a: dict, h: dict, hid: str, s: int, e: int, hdir: Path, args) -> dict:
    """Fetch + persist one (hive, window) and return its manifest record."""
    hdir.mkdir(parents=True, exist_ok=True)
    rec = {"apiaryId": a.get("apiaryId"), "apiaryName": a.get("name"),
           "hiveName": h.get("name")}
    readings = bm.hive_readings(hid, s, e)
    write_gz(hdir / f"{s}-{e}.readings.json.gz", readings)
    rec["reading_rows"] = count_reading_rows(readings)
    if not args.no_notes:
        notes = bm.hive_notes(hid, s, e)
        write_gz(hdir / f"{s}-{e}.notes.json.gz", notes)
        rec["notes"] = count_notes(notes)
    return rec


def _log_window(a: dict, h: dict, s: int, e: int, rec: dict) -> None:
    if rec["reading_rows"] or rec.get("notes"):
        ds = datetime.fromtimestamp(s, tz=timezone.utc)
        de = datetime.fromtimestamp(e, tz=timezone.utc)
        print(f"  {h['name']:>10} [{a['name'][:14]:<14}] "
              f"{ds:%Y-%m-%d}..{de:%Y-%m-%d}  "
              f"rows={rec['reading_rows']:<5} notes={rec.get('notes', '-')}")


def _empty_run(count: int, reading_rows: int, limit: int) -> tuple[int, bool]:
    """Track consecutive all-empty windows for --stop-after-empty backfill.

    Returns (new_count, should_stop). With the feature off (limit 0) the count
    is left untouched and it never stops.
    """
    if not limit:
        return count, False
    count = count + 1 if reading_rows == 0 else 0
    return count, count >= limit


def process_hive(bm, a: dict, h: dict, wins: list, args, raw: Path,
                 completed: dict, save_manifest) -> None:
    """Walk one hive's windows, fetching+recording the not-yet-completed ones.

    Raises _BudgetExhausted when the call budget is reached (the caller stops
    the whole run and exits resumably).
    """
    hid = h["hiveId"]
    # hid comes from the API — treat as untrusted; confine to extract root (pythonsecurity:S8707).
    hdir = resolve_within(raw, hid)
    empties = 0
    for s, e in wins:
        key = f"{hid}|{s}|{e}"
        if key in completed:
            # Honor early-exit using cached row counts too, so a resumed
            # backfill doesn't walk past the known data edge.
            empties, stop = _empty_run(empties, completed[key].get("reading_rows", 0),
                                       args.stop_after_empty)
            if stop:
                break
            continue
        if bm.call_count >= args.max_calls:
            print(f"\n⏸  budget reached ({bm.call_count} calls). Resume later.")
            raise _BudgetExhausted

        rec = fetch_window(bm, a, h, hid, s, e, hdir, args)
        completed[key] = rec
        _log_window(a, h, s, e, rec)
        if len(completed) % 25 == 0:
            save_manifest()
        # Early-exit bookkeeping for backfill: stop walking a hive backwards
        # once we hit a run of empty windows (data is effectively contiguous;
        # nothing older to find).
        empties, stop = _empty_run(empties, rec["reading_rows"], args.stop_after_empty)
        if stop:
            break  # next hive


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", default="2021-01-01", help="history start (YYYY-MM-DD)")
    p.add_argument("--end", default=None, help="history end (YYYY-MM-DD); default now")
    p.add_argument("--window-days", type=int, default=180, help="<= ~6 months")
    p.add_argument("--apiary", action="append", default=[],
                   help="filter by apiary name or id (repeatable); default all")
    p.add_argument("--max-calls", type=int, default=900,
                   help="stop before this many API calls (1000/day cap)")
    p.add_argument("--out", default=str(ROOT / "data" / "extract"))
    p.add_argument("--no-notes", action="store_true", help="skip notes endpoint")
    p.add_argument("--reverse", action="store_true",
                   help="walk windows newest->oldest (for backfilling history)")
    p.add_argument("--stop-after-empty", type=int, default=0,
                   help="with --reverse: stop a hive after N consecutive empty "
                        "windows (saves calls on hives with no old data; 0=off)")
    p.add_argument("--catchup", action="store_true",
                   help="resume forward from each hive's latest completed window in manifest.json")
    args = p.parse_args()

    # Normalize the CLI-supplied output root once; every write below is proven
    # (via resolve_within) to stay inside it, so a hostile --out or hive id
    # can't traverse out of the extract tree (pythonsecurity:S8707).
    out = Path(args.out).resolve()
    raw = out / "raw"
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = load_manifest(manifest_path)
    completed: dict = manifest["completed"]

    start = parse_date(args.start)

    # Snap the open end to midnight UTC so re-runs within a day reuse the same
    # window key (stable resume; the live now-epoch would otherwise mint a new
    # key each run and re-fetch the final window). Today's partial data is
    # picked up on the next day's run.
    if args.end:
        end = parse_date(args.end)
    else:
        end = now_epoch() // 86400 * 86400

    if not args.catchup and start >= end:
        end_dt = datetime.fromtimestamp(end, tz=timezone.utc)
        print(f"already up to date through {end_dt:%Y-%m-%d} (start={start} >= end={end}). Nothing to extract.")
        return 0

    window = args.window_days * 24 * 60 * 60

    def save_manifest():
        manifest["meta"] = {
            "start": start, "end": end, "window_days": args.window_days,
            "windows_completed": len(completed),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2))

    try:
        bm_client = BroodMinderClient()
    except BroodMinderError as ex:
        print(f"✗ Client initialization error: {ex}", file=sys.stderr)
        return 1

    stopped_early = False
    wins = list(iter_windows(start, end, window))
    with bm_client as bm:
        try:
            # The very first call can itself be rate-limited; keep it inside the
            # handler so a 429 here exits cleanly (resumable) rather than crashing.
            apiaries = select_apiaries(bm.apiaries(), args.apiary)
            hives = [(a, h) for a in apiaries for h in a.get("hives", [])]
            if args.catchup:
                all_caught_up = True
                for a, h in hives:
                    h_start = get_hive_resume_start(manifest, h["hiveId"], start)
                    if h_start < end:
                        all_caught_up = False
                        break
                if hives and all_caught_up:
                    end_dt = datetime.fromtimestamp(end, tz=timezone.utc)
                    print(f"already up to date through {end_dt:%Y-%m-%d} across all {len(hives)} hives. Nothing to extract.")
                    return 0

            print(f"scope: {len(apiaries)} apiaries, {len(hives)} hives")
            print(f"range: {args.start} .. {args.end or 'now'}  "
                  f"({len(wins)} windows/hive)")
            print(f"budget: stop at {args.max_calls} calls (already used {bm.call_count})\n")

            for a, h in hives:
                hid = h["hiveId"]
                hive_start = get_hive_resume_start(manifest, hid, start) if args.catchup else start
                if hive_start >= end:
                    continue
                h_wins = list(iter_windows(hive_start, end, window))
                hive_wins = list(reversed(h_wins)) if args.reverse else h_wins
                process_hive(bm, a, h, hive_wins, args, raw, completed, save_manifest)
        except _BudgetExhausted:
            stopped_early = True
        except RateLimited as ex:
            print(f"\n⏸  rate limited by server ({ex.status}). Saving and exiting; resume later.")
            stopped_early = True
        except UnsafePathError as ex:
            save_manifest()
            print(f"\n✗ unsafe path: {ex}", file=sys.stderr)
            return 2
        except BroodMinderError as ex:
            save_manifest()
            print(f"\n✗ API error: {ex}", file=sys.stderr)
            return 2

        save_manifest()
        total_rows = sum(v.get("reading_rows", 0) for v in completed.values())
        total_notes = sum(v.get("notes", 0) for v in completed.values())
        print(f"\n{'paused' if stopped_early else 'done'}: "
              f"{len(completed)} windows, {total_rows} reading rows, {total_notes} notes")
        print(f"API calls this run: {bm.call_count}")
        print(f"raw -> {raw}\nmanifest -> {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
