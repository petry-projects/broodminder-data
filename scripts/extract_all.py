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
    """hiveId resolved outside the extraction root."""


class BudgetExhausted(RuntimeError):
    """The API-call budget was reached; the run stops and can be resumed.

    May carry a ``partial`` per-window record: readings that were already
    fetched and written to disk before a *mid-window* budget guard fired
    (notes skipped). The caller commits it to the manifest before stopping so
    a resume skips this window instead of re-spending a call re-fetching it.
    """

    def __init__(self, partial: dict | None = None):
        super().__init__()
        self.partial = partial


_BudgetExhausted = BudgetExhausted


def resolve_within(base: Path, *parts: str) -> Path:
    base_resolved = base.resolve()
    target = base_resolved.joinpath(*parts).resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:
        raise UnsafePathError(f"path escapes {base_resolved}: {parts!r}") from exc
    return target


def select_apiaries(apiaries, filters):
    """Return the subset of apiaries matching the filter list (name or id, case-insensitive).

    If filters is empty, returns all. Handles both list and ``{"apiaries": [...]}`` shapes.
    """
    if isinstance(apiaries, dict):
        apiaries = apiaries.get("apiaries", [])
    if not filters:
        return apiaries
    wanted = {a.lower() for a in filters}
    wanted_ids = {a for a in filters}  # also check exact ID matches
    return [a for a in apiaries
            if (a.get("name") or "").lower() in wanted
            or a.get("apiaryId") in wanted_ids]


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


def _bump_empty(args, count: int, reading_rows: int) -> int:
    """Return the updated consecutive-empty-window count (backfill early-exit).

    No-op unless ``--stop-after-empty`` is set with ``--reverse``; increments on
    an empty window, resets to 0 on a window that has data.
    """
    if not args.stop_after_empty or not args.reverse:
        return count
    return count + 1 if reading_rows == 0 else 0


def _stop(args, count: int) -> bool:
    """Whether the consecutive-empty streak has reached the backfill cutoff."""
    return bool(args.stop_after_empty) and bool(args.reverse) and count >= args.stop_after_empty


def _log_window(a, h, s, e, rec) -> None:
    """Print a one-line progress row for a window that produced data."""
    if not (rec["reading_rows"] or rec.get("notes")):
        return
    ds = datetime.fromtimestamp(s, tz=timezone.utc)
    de = datetime.fromtimestamp(e, tz=timezone.utc)
    print(f"  {h['name']:>10} [{a['name'][:14]:<14}] "
          f"{ds:%Y-%m-%d}..{de:%Y-%m-%d}  "
          f"rows={rec['reading_rows']:<5} notes={rec.get('notes', '-')}")


def fetch_window(bm, a, h, hid: str, s: int, e: int, hdir: Path, args) -> dict:
    """Fetch one hive window (readings + optional notes), write the raw gzip
    files, and return the per-window manifest record with row counts.

    Note: Internal retries may increment call_count beyond the expected
    calls_per_window, so the overall budget check is approximate.
    """
    hdir.mkdir(parents=True, exist_ok=True)
    rec = {"apiaryId": a.get("apiaryId"), "apiaryName": a.get("name"),
           "hiveName": h.get("name")}
    readings = bm.hive_readings(hid, s, e)
    write_gz(hdir / f"{s}-{e}.readings.json.gz", readings)
    rec["reading_rows"] = count_reading_rows(readings)

    if not args.no_notes:
        # Check budget before making notes call, since retries may have
        # incremented call_count beyond what the pre-window check anticipated.
        # Readings are already written to disk, so hand the readings-only
        # record back to the caller to persist before stopping.
        if bm.call_count >= args.max_calls:
            rec["notes_pending"] = True
            raise _BudgetExhausted(rec)
        notes = bm.hive_notes(hid, s, e)
        write_gz(hdir / f"{s}-{e}.notes.json.gz", notes)
        rec["notes"] = count_notes(notes)
    return rec


def process_hive(bm, a, h, wins, args, raw: Path, completed: dict, save_manifest) -> None:
    """Fetch every outstanding window for one hive, recording results in
    ``completed``. Raises ``BudgetExhausted`` when the call budget is reached so
    the caller can stop the whole run cleanly (resumable)."""
    hid = h["hiveId"]
    hdir = resolve_within(raw, hid)
    consecutive_empty = 0
    calls_per_window = 1 + (0 if args.no_notes else 1)
    for s, e in wins:
        key = f"{hid}|{s}|{e}"
        if key in completed:
            rec = completed[key]
            # Fetch pending notes if budget allows (mid-window resume: readings done, notes skipped).
            if rec.get("notes_pending") and not args.no_notes and bm.call_count < args.max_calls:
                try:
                    notes = bm.hive_notes(hid, s, e)
                    write_gz(hdir / f"{s}-{e}.notes.json.gz", notes)
                    rec["notes"] = count_notes(notes)
                    del rec["notes_pending"]
                    save_manifest()
                except BudgetExhausted:
                    raise
            # Honor early-exit using cached row counts too, so a resumed
            # backfill doesn't walk past the known data edge.
            consecutive_empty = _bump_empty(args, consecutive_empty, rec.get("reading_rows", 0))
            if _stop(args, consecutive_empty):
                break
            continue
        if bm.call_count + calls_per_window > args.max_calls:
            print(f"\n⏸  budget reached ({bm.call_count} calls). Resume later.")
            raise BudgetExhausted

        try:
            rec = fetch_window(bm, a, h, hid, s, e, hdir, args)
        except BudgetExhausted as ex:
            if ex.partial is not None:
                # Readings were written before the mid-window budget guard
                # fired; persist the readings-only record and flush the
                # manifest so a resume skips this window rather than re-paying
                # for its readings call.
                completed[key] = ex.partial
                save_manifest()
                print(f"\n⏸  budget reached mid-window ({bm.call_count} calls). Resume later.")
            raise
        completed[key] = rec
        consecutive_empty = _bump_empty(args, consecutive_empty, rec["reading_rows"])
        _log_window(a, h, s, e, rec)
        if len(completed) % 25 == 0:
            save_manifest()
        if _stop(args, consecutive_empty):
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
    args = p.parse_args()

    start = parse_date(args.start)
    # Snap the open end to midnight UTC so re-runs within a day reuse the same
    # window key (stable resume; the live now-epoch would otherwise mint a new
    # key each run and re-fetch the final window). Today's partial data is
    # picked up on the next day's run.
    if args.end:
        end = parse_date(args.end)
    else:
        end = now_epoch() // 86400 * 86400
    window = args.window_days * 24 * 60 * 60
    out = Path(args.out).resolve()
    raw = out / "raw"
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = load_manifest(manifest_path)
    completed: dict = manifest["completed"]

    def save_manifest():
        manifest["meta"] = {
            "start": start, "end": end, "window_days": args.window_days,
            "windows_completed": len(completed),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2))

    stopped_early = False
    with BroodMinderClient() as bm:
        try:
            # The very first call can itself be rate-limited; keep it inside the
            # handler so a 429 here exits cleanly (resumable) rather than crashing.
            apiaries = bm.apiaries()
            apiaries = select_apiaries(apiaries, args.apiary)
            hives = [(a, h) for a in apiaries for h in a.get("hives", [])]
            print(f"scope: {len(apiaries)} apiaries, {len(hives)} hives")
            print(f"range: {args.start} .. {args.end or 'now'}  "
                  f"({len(list(iter_windows(start, end, window)))} windows/hive)")
            print(f"budget: stop at {args.max_calls} calls (already used {bm.call_count})\n")

            wins = list(iter_windows(start, end, window))
            if args.reverse:
                wins.reverse()
            for a, h in hives:
                process_hive(bm, a, h, wins, args, raw, completed, save_manifest)
        except BudgetExhausted:
            stopped_early = True
        except RateLimited as ex:
            print(f"\n⏸  rate limited by server ({ex.status}). Saving and exiting; resume later.")
            stopped_early = True
        except UnsafePathError as ex:
            save_manifest()
            print(f"\n✗ unsafe hiveId rejected: {ex}", file=sys.stderr)
            return 2
        except BroodMinderError as ex:
            save_manifest()
            print(f"\n✗ API error: {ex}", file=sys.stderr)
            return 3

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
