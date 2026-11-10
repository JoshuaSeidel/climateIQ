"""serve_spa joins the URL path onto frontend/dist with no containment check: `GET /../../..//etc/hosts`
(curl --path-as-is, or %2e%2e which uvicorn decodes) returns arbitrary files to anyone holding the
dial API key (e.g. /proc/self/environ -> SUPERVISOR_TOKEN)."""
import pytest


async def test_spa_path_traversal():
    from backend.api.main import _FRONTEND_DIR, app
    if not _FRONTEND_DIR.is_dir():
        pytest.skip("no frontend/dist")
    target = "/etc/hosts"
    depth = len(_FRONTEND_DIR.parts)
    path = "/" + "../" * depth + target.lstrip("/")
    body = b""
    status = None

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        nonlocal body, status
        if m["type"] == "http.response.start":
            status = m["status"]
        elif m["type"] == "http.response.body":
            body += m.get("body", b"")

    scope = {"type": "http", "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [(b"host", b"t")], "client": ("127.0.0.1", 1), "server": ("t", 80), "scheme": "http",
             "root_path": "", "http_version": "1.1", "asgi": {"version": "3.0"}}
    await app(scope, receive, send)
    assert b"localhost" not in body, f"read {target} via {path}: status {status}"
