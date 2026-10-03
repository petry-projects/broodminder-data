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

from bm.client import BroodMinderClient, BroodMinderError, RateLimited, now_epoch  # noqa: E402

DAY = 24 * 60 * 60
OUT = Path(__file__).resolve().parent.parent / "data" / "discovery.json"


def first(obj, *keys):
    """Return the first present key from a dict (schemas vary)."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj:
                return obj[k]
    return None


def walk_sample_ids(apiaries):
    """Walk the apiary tree and return the first (hive_id, device_id) found.

    Handles both the observed bare-list shape and the documented
    ``{"apiaries": [...]}`` object shape, and the schema's key aliases.
    """
    containers = apiaries if isinstance(apiaries, list) else (apiaries or {}).get("apiaries", [])
    hive_id = device_id = None
    for ap in containers or []:
        for hv in first(ap, "hives") or []:
            hv_id = first(hv, "hiveId", "id", "hiveID")
            if hive_id is None and hv_id is not None:
                hive_id = hv_id
            # Only look for a device if we found hive_id and haven't found device_id yet
            # and the current hive matches the hive_id we found
            if hive_id == hv_id and device_id is None:
                for dv in first(hv, "devices", "positions") or []:
                    device_id = device_id or first(dv, "deviceId", "id", "deviceID")
    return hive_id, device_id


find_sample_ids = walk_sample_ids


def sample_endpoint(out, key, header, err_label, fn, clip):
    """Call ``fn()`` for a probe endpoint, recording the sample (or the error)
    under ``key`` in ``out`` and printing a clipped preview."""
    print(header)
    try:
        data = fn()
    except RateLimited:
        raise
    except BroodMinderError as e:
        out[f"{key}_error"] = str(e)
        print(f"  {err_label} error: {e}")
        return
    out[f"{key}_sample"] = data
    print(json.dumps(data, indent=2)[:clip])


def main() -> int:
    out: dict = {}
    with BroodMinderClient() as bm:
        print(f"base_url = {bm.base_url}")
        print("→ GET /user/metadata/apiaries")
        try:
            apiaries = bm.apiaries()
        except BroodMinderError as e:
            print(f"AUTH/METADATA FAILED: {e}", file=sys.stderr)
            return 1
        out["apiaries"] = apiaries
        print(json.dumps(apiaries, indent=2)[:4000])

        hive_id, device_id = walk_sample_ids(apiaries)
        print(f"\nsample hive_id={hive_id}  device_id={device_id}")

        end = now_epoch()
        start = end - 30 * DAY  # last 30 days as a probe
        try:
            if hive_id is not None:
                sample_endpoint(out, "hive_readings",
                                f"→ GET /user/hive/{hive_id}/readings (last 30d)",
                                "hive readings",
                                lambda: bm.hive_readings(hive_id, start, end), 2500)
                sample_endpoint(out, "hive_notes",
                                f"→ GET /user/hive/{hive_id}/notes (last 30d)",
                                "hive notes",
                                lambda: bm.hive_notes(hive_id, start, end), 1500)

            if device_id is not None:
                sample_endpoint(out, "device_readings",
                                f"→ GET /user/device/{device_id}/readings (last 30d)",
                                "device readings",
                                lambda: bm.device_readings(device_id, start, end), 2500)
        except RateLimited as e:
            print(f"\n⏸  rate limited by server ({e.status}). Saving partial result.")

        out["_call_count"] = bm.call_count
        print(f"\ntotal API calls this run: {bm.call_count}")

    OUT.write_text(json.dumps(out, indent=2))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
