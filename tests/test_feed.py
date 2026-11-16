import json
import logging

import httpx
import pytest

from collector.feed import FeedError, fetch_feed, redact

URL = "https://feed.piyolog.com/v1/feed/24h/fakeid/fakesecret"


def client_for(*responses):
    """Return (client, calls) where each request pops the next response.

    A response is either an httpx.Response or an exception to raise.
    """
    calls = []
    queue = list(responses)

    def handler(request):
        calls.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def err(status, code):
    return httpx.Response(status, json={"error": {"code": code}})


def test_ok_parses_envelope(feed_body):
    client, calls = client_for(httpx.Response(200, content=feed_body))
    r = fetch_feed(URL, client=client)
    assert len(r.records) == 12
    assert r.generated_at.tzinfo is not None
    assert r.range_from < r.range_to
    assert r.body == feed_body
    assert len(calls) == 1


def test_404_unavailable():
    client, calls = client_for(err(404, "feed_unavailable"))
    with pytest.raises(FeedError) as ei:
        fetch_feed(URL, client=client, sleep=lambda s: None)
    e = ei.value
    assert (e.kind, e.http_status, e.code) == ("unavailable", 404, "feed_unavailable")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,code,kind",
    [(413, "feed_response_too_large", "too_large"), (429, "rate_limit_exceeded", "rate_limited")],
)
def test_client_errors_not_retried(status, code, kind):
    client, calls = client_for(err(status, code))
    with pytest.raises(FeedError) as ei:
        fetch_feed(URL, client=client, sleep=lambda s: None)
    assert ei.value.kind == kind
    assert len(calls) == 1


def test_5xx_retries_twice_then_raises():
    slept = []
    client, calls = client_for(*(err(500, "internal_error") for _ in range(3)))
    with pytest.raises(FeedError) as ei:
        fetch_feed(URL, client=client, sleep=slept.append)
    assert ei.value.kind == "server"
    assert len(calls) == 3
    assert slept == [2, 4]


def test_5xx_then_ok(feed_body):
    client, calls = client_for(err(503, None), httpx.Response(200, content=feed_body))
    r = fetch_feed(URL, client=client, sleep=lambda s: None)
    assert len(r.records) == 12
    assert len(calls) == 2


def test_network_error_hides_url():
    client, _ = client_for(*(httpx.ConnectError(f"boom {URL}") for _ in range(3)))
    with pytest.raises(FeedError) as ei:
        fetch_feed(URL, client=client, sleep=lambda s: None)
    e = ei.value
    assert e.kind == "network"
    assert "fakesecret" not in str(e) and "fakeid" not in str(e)
    assert e.__cause__ is None
    assert e.__suppress_context__ is True


@pytest.mark.parametrize("body", [b"not json", json.dumps({"schema_version": 1}).encode()])
def test_bad_payload(body):
    client, _ = client_for(httpx.Response(200, content=body))
    with pytest.raises(FeedError) as ei:
        fetch_feed(URL, client=client)
    assert ei.value.kind == "bad_payload"


def test_no_url_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    cases = [err(404, "feed_unavailable"), err(429, "rate_limit_exceeded"), httpx.Response(200, content=b"x")]
    for case in cases:
        client, _ = client_for(case)
        with pytest.raises(FeedError):
            fetch_feed(URL, client=client, sleep=lambda s: None)
    client, _ = client_for(*(httpx.ConnectError(f"boom {URL}") for _ in range(3)))
    with pytest.raises(FeedError):
        fetch_feed(URL, client=client, sleep=lambda s: None)
    assert "fakesecret" not in caplog.text
    assert "fakeid" not in caplog.text


def test_redact():
    assert redact(f"GET {URL} failed", URL) == "GET [REDACTED] failed"
    assert "fakesecret" not in redact("token=fakesecret", URL)
    assert "fakeid" not in redact("/24h/fakeid/", URL)
