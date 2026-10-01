"""Path-traversal guard tests for scripts/extract_all.py (pythonsecurity:S8707).

`extract_all.py` writes raw data to `<out>/raw/<hiveId>/<start>-<end>...`. Both the
`--out` directory (a CLI argument) and the `hiveId` (external API data) are
untrusted: a component containing `..` or an absolute path could otherwise escape
the extract directory and clobber arbitrary files. `resolve_within` is the
containment validator that fails closed on any such escape; these tests pin that
behavior so the guard can't silently regress.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from extract_all import main, resolve_within  # noqa: E402


def _mock_client(hive_ids):
    """Build a MagicMock BroodMinderClient context manager with fake hives."""
    bm = MagicMock()
    bm.call_count = 0
    bm.__enter__.return_value = bm
    bm.__exit__.return_value = False
    bm.apiaries.return_value = [
        {
            "apiaryId": "test-apiary",
            "name": "Test Apiary",
            "hives": [{"hiveId": hid, "name": f"Hive {hid}"} for hid in hive_ids],
        }
    ]
    bm.hive_readings.return_value = []
    bm.hive_notes.return_value = []
    return bm


def test_normal_component_stays_within(tmp_path):
    base = tmp_path / "raw"
    got = resolve_within(base, "hive-123")
    assert got == (base / "hive-123").resolve()
    assert got.is_relative_to(base.resolve())


def test_nested_window_filename_allowed(tmp_path):
    """The real call shape: a per-hive dir plus a window-keyed filename."""
    hdir = resolve_within(tmp_path / "raw", "AA:BB:CC")
    got = resolve_within(hdir, "1609459200-1625097600.readings.json.gz")
    assert got.is_relative_to((tmp_path / "raw").resolve())


def test_no_parts_returns_base(tmp_path):
    assert resolve_within(tmp_path) == tmp_path.resolve()


@pytest.mark.parametrize(
    "evil",
    [
        "..",
        "../escape",
        "../../etc/passwd",
        "a/../../../escape",
    ],
)
def test_parent_traversal_rejected(tmp_path, evil):
    with pytest.raises(ValueError):
        resolve_within(tmp_path / "raw", evil)


def test_absolute_component_rejected(tmp_path):
    with pytest.raises(ValueError):
        resolve_within(tmp_path / "raw", "/etc/passwd")


def test_embedded_traversal_in_hive_id_rejected(tmp_path):
    """A hostile hiveId must not be able to redirect the write outside the root."""
    raw = tmp_path / "raw"
    with pytest.raises(ValueError):
        resolve_within(raw, "../../../../tmp/pwned")


# ── Integration tests for main() ────────────────────────────────────────────


def test_main_safe_hive_id_writes_manifest(tmp_path):
    """main() completes normally when the API returns a safe hive ID."""
    argv = [
        "extract_all", "--out", str(tmp_path),
        "--start", "2023-01-01", "--end", "2023-01-02",
        "--max-calls", "10",
    ]
    with patch.object(sys, "argv", argv):
        with patch("extract_all.BroodMinderClient", return_value=_mock_client(["hive-safe-123"])):
            result = main()

    assert result == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert "completed" in manifest


def test_main_path_traversal_hive_id_exits_2(tmp_path):
    """main() returns exit code 2 when the API returns a hive ID with path traversal."""
    argv = [
        "extract_all", "--out", str(tmp_path),
        "--start", "2023-01-01", "--end", "2023-01-02",
    ]
    with patch.object(sys, "argv", argv):
        with patch("extract_all.BroodMinderClient", return_value=_mock_client(["../escape"])):
            result = main()

    assert result == 2
    assert (tmp_path / "manifest.json").exists()
