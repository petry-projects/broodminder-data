#!/usr/bin/env python3
"""CLI utility to inspect sensor battery levels and detect offline sensors.

Answers the question: "What batteries are low and need to be changed?"
Both battery level <80% and stale reporting (>7 days without data)
indicate that a sensor needs inspection or battery replacement.

Usage:
    python scripts/battery_health.py
    python scripts/battery_health.py --all
    python scripts/battery_health.py --threshold 75 --stale-days 5
    python scripts/battery_health.py --apiary "Home" --format json
    python scripts/battery_health.py --check
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bm.battery import find_default_data_file, main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
