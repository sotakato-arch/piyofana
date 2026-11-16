"""Fetch the PiyoLog feed and classify every way it can fail.

The feed URL is the credential, so nothing raised or logged here may carry
it: exceptions are rebuilt from scratch with `from None`, and the original
httpx error (whose message includes the URL) is dropped.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

# httpx logs every request URL at INFO ("HTTP Request: GET <url>"), which would
# write the credential to the container log. Silence it wherever this module
# is imported, rather than trusting every caller to remember.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

Kind = Literal["unavailable", "too_large", "rate_limited", "server", "network", "bad_payload"]

_STATUS_KIND: dict[int, Kind] = {404: "unavailable", 413: "too_large", 429: "rate_limited"}


@dataclass(frozen=True)
class FeedResponse:
    generated_at: datetime
    range_from: datetime
    range_to: datetime
    records: list[dict]
    body: bytes


class FeedError(Exception):
    def __init__(self, kind: Kind, http_status: int | None = None, code: str | None = None):
        self.kind = kind
        self.http_status = http_status
        self.code = code
        super().__init__(f"feed {kind} (http_status={http_status}, code={code})")


def redact(text: str, url: str) -> str:
    """Remove the feed URL and its identifying path segments from text."""
    text = text.replace(url, "[REDACTED]")
    for segment in urlsplit(url).path.rstrip("/").split("/")[-2:]:
        if segment:
            text = text.replace(segment, "[REDACTED]")
    return text


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _error_code(response: httpx.Response) -> str | None:
    try:
        code = response.json().get("error", {}).get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) else None


def _parse(body: bytes) -> FeedResponse:
    try:
        doc = json.loads(body)
        records = doc["records"]
        if not isinstance(records, list):
            raise TypeError
        return FeedResponse(
            generated_at=_ts(doc["generated_at"]),
            range_from=_ts(doc["range"]["from"]),
            range_to=_ts(doc["range"]["to"]),
            records=records,
            body=body,
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        raise FeedError("bad_payload") from None


def fetch_feed(
    url: str,
    *,
    client: httpx.Client,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = 2,
) -> FeedResponse:
    """GET the feed. Server and network errors are retried with 2s, 4s backoff."""
    for attempt in range(retries + 1):
        try:
            response = client.get(url)
        except httpx.HTTPError:
            error = FeedError("network")
        else:
            if response.status_code == 200:
                return _parse(response.content)
            if response.status_code in _STATUS_KIND:
                raise FeedError(_STATUS_KIND[response.status_code], response.status_code, _error_code(response))
            if response.status_code >= 500:
                error = FeedError("server", response.status_code, _error_code(response))
            else:
                raise FeedError("bad_payload", response.status_code, _error_code(response))
        if attempt < retries:
            log.info("feed attempt %d failed: %s; retrying", attempt + 1, error)
            sleep(2 ** (attempt + 1))
    raise error from None
