"""Test-wide guard: no test may reach the real network (OpenRouter). Tests mock the model call."""
import httpx
import pytest


@pytest.fixture(autouse=True)
def _no_real_http(monkeypatch):
    def blocked(self, request):
        raise AssertionError(f"Test tried a real HTTP call to {request.url}. Mock ModelRouter._post instead.")

    # Real network goes through HTTPTransport. Starlette's TestClient and tests that patch
    # httpx.Client.post use other paths, so they are unaffected.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
