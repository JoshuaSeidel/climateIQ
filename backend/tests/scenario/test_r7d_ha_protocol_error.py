"""HA restart mid-request surfaces as raw httpx errors, not HAClientError."""
import httpx
import pytest

from backend.integrations.ha_client import HAClient, HAClientError


@pytest.mark.parametrize("exc", [
    httpx.RemoteProtocolError("Server disconnected without sending a response"),
    httpx.ReadError("connection reset"),
    httpx.WriteError("broken pipe"),
])
async def test_transport_errors_are_wrapped(exc):
    def handler(request):
        raise exc

    c = HAClient(url="http://ha.invalid:8123", token="t")  # noqa: S106
    c._client = httpx.AsyncClient(base_url="http://ha.invalid:8123",
                                  transport=httpx.MockTransport(handler))
    c._connected = True
    with pytest.raises(HAClientError):
        await c.get_state("climate.x")
