import asyncio

import pytest


@pytest.mark.parametrize("path", ["/api/v1/analyze", "/api/v1/analyze/"])
@pytest.mark.parametrize("key", [None, "", "wrong"])
def test_unauthorized_never_calls_receive(app, path, key):
    messages = []

    async def receive():
        pytest.fail("unauthorized body was read")

    async def send(message):
        messages.append(message)

    headers = [(b"content-type", b"multipart/form-data; boundary=test")]
    if key is not None:
        headers.append((b"x-api-key", key.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 123),
        "root_path": "",
        "http_version": "1.1",
    }
    asyncio.run(app(scope, receive, send))
    assert messages[0]["status"] == 401
