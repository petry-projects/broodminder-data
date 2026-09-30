"""Discovery: confirm auth, dump the real account topology + sample schemas.

Run this FIRST. It:
  1. Calls /user/metadata/apiaries and prints the apiary/hive tree.
  2. Pulls a small recent readings/notes sample for the first hive + device so
     we can see the *actual* field names (the docs are approximate).
  3. Writes the raw JSON to data/discovery.json for the tests to key off.

Stays well under the rate limit (a handful of calls).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bm.client import BroodMinderClient, BroodMinderError, now_epoch  # noqa: E402

DAY = 24 * 60 * 60
OUT = Path(__file__).resolve().parent.parent / "data" / "discovery.json"


def first(obj, *keys):
    """Return the first present key from a dict (schemas vary)."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj:
                return obj[k]
    return None


def main() -> int:
    out: dict = {}
    try:
        bm_client = BroodMinderClient()
    except BroodMinderError as e:
        print(f"AUTH/CONFIG FAILED: {e}", file=sys.stderr)
        return 1

    with bm_client as bm:
        print(f"base_url = {bm.base_url}")
        print("→ GET /user/metadata/apiaries")
        try:
            apiaries = bm.apiaries()
        except BroodMinderError as e:
            print(f"AUTH/METADATA FAILED: {e}", file=sys.stderr)
            return 1
        out["apiaries"] = apiaries
        print(json.dumps(apiaries, indent=2)[:4000])

        # Walk the tree to find a hive id and a device id to sample.
        hive_id = device_id = None
        containers = apiaries if isinstance(apiaries, list) else apiaries.get("apiaries", [])
        for ap in containers or []:
            for hv in first(ap, "hives") or []:
                hive_id = hive_id or first(hv, "hiveId", "id", "hiveID")
                for dv in (first(hv, "devices", "positions") or []):
                    device_id = device_id or first(dv, "deviceId", "id", "deviceID")
        print(f"\nsample hive_id={hive_id}  device_id={device_id}")

        end = now_epoch()
        start = end - 30 * DAY  # last 30 days as a probe
        if hive_id is not None:
            print(f"→ GET /user/hive/{hive_id}/readings (last 30d)")
            try:
                hr = bm.hive_readings(hive_id, start, end)
                out["hive_readings_sample"] = hr
                print(json.dumps(hr, indent=2)[:2500])
            except BroodMinderError as e:
                out["hive_readings_error"] = str(e)
                print(f"  hive readings error: {e}")

            print(f"→ GET /user/hive/{hive_id}/notes (last 30d)")
            try:
                hn = bm.hive_notes(hive_id, start, end)
                out["hive_notes_sample"] = hn
                print(json.dumps(hn, indent=2)[:1500])
            except BroodMinderError as e:
                out["hive_notes_error"] = str(e)
                print(f"  hive notes error: {e}")

        if device_id is not None:
            print(f"→ GET /user/device/{device_id}/readings (last 30d)")
            try:
                dr = bm.device_readings(device_id, start, end)
                out["device_readings_sample"] = dr
                print(json.dumps(dr, indent=2)[:2500])
            except BroodMinderError as e:
                out["device_readings_error"] = str(e)
                print(f"  device readings error: {e}")

        out["_call_count"] = bm.call_count
        print(f"\ntotal API calls this run: {bm.call_count}")

    OUT.write_text(json.dumps(out, indent=2))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
