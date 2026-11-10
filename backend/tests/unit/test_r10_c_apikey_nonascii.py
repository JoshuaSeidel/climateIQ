"""APIKeyMiddleware: hmac.compare_digest(str, str) raises TypeError on non-ASCII -> 500 / ws crash instead of 401/4001."""
from backend.api.middleware import APIKeyMiddleware


async def _inner(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def _call(scope):
    sent = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(m):
        sent.append(m)
    await APIKeyMiddleware(_inner, api_key="secret")(scope, receive, send)
    return sent


def _scope(kind, headers=(), qs=b""):
    return {"type": kind, "path": "/api/v1/zones", "query_string": qs, "headers": list(headers),
            "client": ("1.2.3.4", 1), "method": "GET"}


async def test_http_non_ascii_authorization_is_401():
    sent = await _call(_scope("http", [(b"authorization", "Bearer sécret".encode("latin-1"))]))
    assert sent[0]["status"] == 401


async def test_http_utf8_authorization_is_401():
    sent = await _call(_scope("http", [(b"authorization", "Bearer sécret".encode())]))
    assert sent[0]["status"] == 401


async def test_ws_non_ascii_query_is_4001():
    sent = await _call(_scope("websocket", qs=b"api_key=s%C3%A9cret"))
    assert sent[0]["type"] == "websocket.close" and sent[0]["code"] == 4001
