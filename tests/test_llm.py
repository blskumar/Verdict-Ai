"""Tests for Package A: LLM router (ModelRouter, AllModelsExhausted, MockModel).

All tests use unittest.mock — no real network calls.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from verdict_ai.llm import AllModelsExhausted, MockModel, ModelRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _router(tmp_path, cap: int = 200) -> ModelRouter:
    """Create a router pointed at a temp cache dir with a minimal inline config."""
    cfg = tmp_path / "models.yaml"
    cfg.write_text(
        "request_cap: 200\n"
        "cooldown_seconds: 60\n"
        "roles:\n"
        "  analyst:\n"
        "    models:\n"
        "      - apodex/apodex-1.1-mini:free\n"
        "      - qwen/qwen3-8b:free\n"
    )
    router = ModelRouter(
        config_path=str(cfg),
        cache_dir=str(tmp_path / "cache"),
    )
    router.request_cap = cap
    return router


def _ok_resp(content: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    resp.raise_for_status = MagicMock()
    return resp


def _err_resp(status: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            str(status),
            request=httpx.Request("POST", "http://x"),
            response=httpx.Response(status),
        )
    )
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_happy_path(tmp_path):
    router = _router(tmp_path)
    with patch("httpx.Client.post", return_value=_ok_resp('{"result": "ok"}')):
        text, model = router.chat("analyst", [{"role": "user", "content": "hi"}])
    assert text == '{"result": "ok"}'
    assert model == "apodex/apodex-1.1-mini:free"


def test_rate_limit_failover(tmp_path):
    router = _router(tmp_path)
    with patch(
        "httpx.Client.post",
        side_effect=[_err_resp(429), _ok_resp("success")],
    ):
        text, model = router.chat("analyst", [{"role": "user", "content": "hi"}])
    assert text == "success"
    assert model == "qwen/qwen3-8b:free"


def test_credits_failover(tmp_path):
    router = _router(tmp_path)
    with patch(
        "httpx.Client.post",
        side_effect=[_err_resp(402), _ok_resp("credits ok")],
    ):
        text, model = router.chat("analyst", [{"role": "user", "content": "hi"}])
    assert text == "credits ok"
    assert model == "qwen/qwen3-8b:free"


def test_server_error_failover(tmp_path):
    router = _router(tmp_path)
    with patch(
        "httpx.Client.post",
        side_effect=[_err_resp(500), _ok_resp("server ok")],
    ):
        text, model = router.chat("analyst", [{"role": "user", "content": "hi"}])
    assert text == "server ok"
    assert model == "qwen/qwen3-8b:free"


def test_bad_json_twice_failover(tmp_path):
    """First model returns invalid JSON twice → router moves to second model."""
    router = _router(tmp_path)
    msgs = [{"role": "user", "content": "hi"}]
    responses = [
        _ok_resp("not json at all"),
        _ok_resp("still not json"),
        _ok_resp('{"valid": true}'),
    ]
    with patch("httpx.Client.post", side_effect=responses):
        data, model = router.chat_json("analyst", msgs)
    assert data == {"valid": True}
    assert model == "qwen/qwen3-8b:free"


def test_all_exhausted(tmp_path):
    router = _router(tmp_path)
    with patch("httpx.Client.post", return_value=_err_resp(429)):
        with pytest.raises(AllModelsExhausted):
            router.chat("analyst", [{"role": "user", "content": "hi"}])


def test_cache_hit(tmp_path):
    """Second identical call must not hit network."""
    router = _router(tmp_path)
    msgs = [{"role": "user", "content": "hi"}]
    with patch("httpx.Client.post", return_value=_ok_resp("cached")) as post_mock:
        t1, _ = router.chat("analyst", msgs)
        t2, _ = router.chat("analyst", msgs)
    assert t1 == t2 == "cached"
    assert post_mock.call_count == 1  # second call served from disk cache


def test_request_cap(tmp_path):
    """After cap is hit the router raises AllModelsExhausted immediately."""
    router = _router(tmp_path, cap=1)
    msgs = [{"role": "user", "content": "hi"}]
    with patch("httpx.Client.post", return_value=_ok_resp("ok")):
        router.chat("analyst", msgs)            # uses the 1 allowed request
    with patch("httpx.Client.post", return_value=_ok_resp("ok")):
        with pytest.raises(AllModelsExhausted, match="cap"):
            router.chat("analyst", [{"role": "user", "content": "bye"}])


def test_mock_model_no_network():
    """MockModel.as_router() never touches the network."""
    mock = MockModel('{"score": 0.9}')
    router = mock.as_router("reviewer")
    text, model = router.chat("reviewer", [{"role": "user", "content": "check"}])
    assert text == '{"score": 0.9}'
    assert model == "mock-model"
    assert len(mock.calls) == 1


def test_unauthorized_401_raises_immediately(tmp_path):
    router = _router(tmp_path)
    err_401 = MagicMock()
    err_401.status_code = 401
    err_401.text = "Unauthorized"
    with patch("httpx.Client.post", return_value=err_401):
        with pytest.raises(AllModelsExhausted, match="OPENROUTER_API_KEY"):
            router.chat("analyst", [{"role": "user", "content": "hi"}])
