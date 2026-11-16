import gzip
import stat
from datetime import UTC, datetime

from collector.archive import write_raw


def test_appends_members(tmp_path):
    t = datetime(2026, 11, 15, 3, 0, tzinfo=UTC)
    p1 = write_raw(b'{"a": 1}', t, tmp_path)
    p2 = write_raw(b'{"b": 2}', t, tmp_path)
    assert p1 == p2
    assert gzip.open(p1).read().splitlines() == [b'{"a":1}', b'{"b":2}']


def test_jst_date_boundary(tmp_path):
    before = write_raw(b"{}", datetime(2026, 11, 15, 14, 59, 59, tzinfo=UTC), tmp_path)
    after = write_raw(b"{}", datetime(2026, 11, 15, 15, 0, 0, tzinfo=UTC), tmp_path)
    assert before.name == "2026-11-15.json.gz"
    assert after.name == "2026-11-16.json.gz"


def test_creates_root(tmp_path):
    root = tmp_path / "nested" / "raw"
    assert write_raw(b"{}", datetime(2026, 11, 15, tzinfo=UTC), root).parent == root


def test_file_mode_600(tmp_path):
    p = write_raw(b"{}", datetime(2026, 11, 15, tzinfo=UTC), tmp_path)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_multiline_body_becomes_one_line(tmp_path):
    import json

    pretty = json.dumps({"records": [{"a": 1, "memo": "日本語"}]}, indent=2).encode()
    p = write_raw(pretty, datetime(2026, 11, 15, tzinfo=UTC), tmp_path)
    lines = gzip.open(p).read().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == json.loads(pretty)
    assert "日本語".encode() in lines[0]
