"""LLM router with failover, cooldown, disk cache, and request cap.

Usage:
    router = ModelRouter()                    # reads models.yaml and .env
    text, model = router.chat("analyst", messages)
    data, model = router.chat_json("reviewer", messages)
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import yaml

try:
    from dotenv import load_dotenv
    _HAS_DOTENV = True
except ImportError:
    _HAS_DOTENV = False


class AllModelsExhausted(Exception):
    """Raised when every model for a role is in cooldown or has failed."""


class AuthenticationError(AllModelsExhausted):
    """Raised when OpenRouter authentication fails (e.g. invalid API key)."""


class ModelRouter:
    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
    HEADERS_STATIC = {
        "HTTP-Referer": "https://github.com/verdict-ai",
        "X-Title": "Verdict AI",
        "Content-Type": "application/json",
    }

    def __init__(
        self,
        config_path: str | Path = "models.yaml",
        cache_dir: str | Path = ".cache/llm",
        env_path: str | Path = ".env",
    ) -> None:
        if _HAS_DOTENV:
            load_dotenv(env_path)
        self.api_key = os.getenv("OPENROUTER_API_KEY", "")

        config_file = Path(config_path)
        pkg_fallback = Path(__file__).resolve().parent / "models.yaml"
        if config_path == "models.yaml" and pkg_fallback.exists():
            config_file = pkg_fallback
        elif not config_file.exists() and pkg_fallback.exists():
            config_file = pkg_fallback

        if config_file.exists():
            with open(config_file, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        else:
            cfg = {}

        self.request_cap: int = cfg.get("request_cap", 200)
        self.cooldown_seconds: int = cfg.get("cooldown_seconds", 60)
        self.roles_config: dict[str, dict] = cfg.get("roles", {})

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # per-session state (not persisted)
        self._cooldowns: dict[str, datetime] = {}
        self._request_count: int = 0
        self._bad_json_streak: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(
        self,
        role: str,
        messages: list[dict],
        *,
        context: str = "",
    ) -> tuple[str, str]:
        """Return (raw_text, model_name). Raises AllModelsExhausted on total failure."""
        if self._request_count >= self.request_cap:
            raise AllModelsExhausted("Request cap reached.")

        models = self._available(role)
        if not models:
            raise AllModelsExhausted(f"All models for role '{role}' are in cooldown or unconfigured.")

        # Cache check (try first available model only — keyed by model+messages)
        for model in models:
            cached = self._cache_get(model, messages)
            if cached is not None:
                return cached, model

        last_exc: Exception | None = None
        for model in models:
            if self._request_count >= self.request_cap:
                raise AllModelsExhausted("Request cap reached during failover.")
            self._request_count += 1
            try:
                text = self._post(model, messages)
                self._cache_set(model, messages, text)
                return text, model
            except _RetryableError as e:
                self._cooldowns[model] = datetime.now() + timedelta(seconds=self.cooldown_seconds)
                last_exc = e

        raise AllModelsExhausted(
            f"All models exhausted for role '{role}'. Last: {last_exc}"
        )

    def chat_json(
        self,
        role: str,
        messages: list[dict],
        *,
        context: str = "",
    ) -> tuple[Any, str]:
        """Return (parsed_json, model_name). Retries once per model on bad JSON.
        Moves to next model after two bad JSON replies.
        """
        models = self._available(role)
        if not models:
            raise AllModelsExhausted(f"All models for role '{role}' are in cooldown or unconfigured.")

        last_exc: Exception | None = None
        for model in models:
            for _attempt in range(2):
                if self._request_count >= self.request_cap:
                    raise AllModelsExhausted("Request cap reached.")
                self._request_count += 1
                try:
                    text = self._post(model, messages)
                    parsed = _parse_json(text)
                    self._cache_set(model, messages, text)
                    self._bad_json_streak[model] = 0
                    return parsed, model
                except _RetryableError as e:
                    self._cooldowns[model] = datetime.now() + timedelta(seconds=self.cooldown_seconds)
                    last_exc = e
                    break  # HTTP error → skip to next model immediately
                except (json.JSONDecodeError, ValueError) as e:
                    streak = self._bad_json_streak.get(model, 0) + 1
                    self._bad_json_streak[model] = streak
                    last_exc = e
                    if streak >= 2:
                        # Two bad JSON replies → put in cooldown, move on
                        self._cooldowns[model] = datetime.now() + timedelta(seconds=self.cooldown_seconds)
                        break

        raise AllModelsExhausted(
            f"All models exhausted or failed JSON validation for role '{role}'. Last: {last_exc}"
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _available(self, role: str) -> list[str]:
        now = datetime.now()
        candidates = self.roles_config.get(role, {}).get("models", [])
        out = []
        for m in candidates:
            cd = self._cooldowns.get(m)
            if cd and now < cd:
                continue  # still cooling down
            elif cd:
                del self._cooldowns[m]
            out.append(m)
        return out

    def _post(self, model: str, messages: list[dict]) -> str:
        headers = {**self.HEADERS_STATIC, "Authorization": f"Bearer {self.api_key}"}
        payload = {"model": model, "messages": messages}
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(self.BASE_URL, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            raise _RetryableError(f"Timeout: {e}") from e
        except httpx.RequestError as e:
            raise _RetryableError(f"Request error: {e}") from e

        if getattr(self, "_auth_failed", False):
            raise AuthenticationError(getattr(self, "_auth_error_msg", "OpenRouter authentication failed (HTTP 401)."))

        if resp.status_code == 401:
            err_msg = (
                f"OpenRouter authentication failed (HTTP 401): {resp.text[:200]}. "
                "Check that OPENROUTER_API_KEY is set and valid."
            )
            self._auth_failed = True
            self._auth_error_msg = err_msg
            raise AuthenticationError(err_msg)
        if resp.status_code != 200:
            raise _RetryableError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _cache_key(self, model: str, messages: list[dict]) -> str:
        raw = json.dumps({"model": model, "messages": messages}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_get(self, model: str, messages: list[dict]) -> str | None:
        path = self.cache_dir / f"{self._cache_key(model, messages)}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))["content"]
            except Exception:
                pass
        return None

    def _cache_set(self, model: str, messages: list[dict], content: str) -> None:
        path = self.cache_dir / f"{self._cache_key(model, messages)}.json"
        try:
            path.write_text(json.dumps({"content": content}), encoding="utf-8")
        except Exception:
            pass


def _parse_json(text: str) -> Any:
    """Parse JSON from model output, stripping optional markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        # strip opening fence (```json or ```)
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return json.loads(text.strip())


class _RetryableError(Exception):
    """Internal: signals a failed model call that should trigger failover."""


# ------------------------------------------------------------------
# Mock helper for tests
# ------------------------------------------------------------------

class MockModel:
    """Returns a fixed response without any network call.

    Usage in tests:
        mock = MockModel('{"score": 0.9}')
        router = mock.as_router("reviewer")
        result, model = router.chat("reviewer", messages)
    """

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict] = []

    def as_router(self, role: str) -> ModelRouter:
        """Return a ModelRouter whose chat() always returns self.response for role."""
        router = ModelRouter.__new__(ModelRouter)
        router.request_cap = 1000
        router.cooldown_seconds = 60
        router.roles_config = {role: {"models": ["mock-model"]}}
        router.cache_dir = Path(".cache/llm-mock")
        router._cooldowns = {}
        router._request_count = 0
        router._bad_json_streak = {}

        mock = self  # capture

        def _mock_post(model: str, messages: list[dict]) -> str:  # noqa: ARG001
            mock.calls.append({"model": model, "messages": messages})
            return mock.response

        router._post = _mock_post  # type: ignore[method-assign]
        router._cache_get = lambda m, msgs: None  # type: ignore[method-assign]
        router._cache_set = lambda m, msgs, c: None  # type: ignore[method-assign]
        return router
