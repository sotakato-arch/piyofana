"""Append-only archive of untouched feed responses.

One gzip file per JST day, one gzip member per poll, so `zcat` yields one
response per line. Bodies are re-serialized compactly: semantically identical
to what the feed sent, though not byte-identical, because a pretty-printed
body would otherwise span many lines. This is rebuild insurance, not a query
path.
"""

import gzip
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def write_raw(body: bytes, fetched_at: datetime, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{fetched_at.astimezone(JST):%Y-%m-%d}.json.gz"
    # Create with 0600 before gzip opens it, so the file is never world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "ab") as raw, gzip.GzipFile(fileobj=raw, mode="ab") as gz:
        line = json.dumps(json.loads(body), ensure_ascii=False, separators=(",", ":"))
        gz.write(line.encode() + b"\n")
    return path
