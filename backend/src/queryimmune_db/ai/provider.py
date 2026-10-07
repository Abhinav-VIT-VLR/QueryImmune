from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from queryimmune_db.api.config import Settings


class AIProviderError(RuntimeError):
    """Sanitized provider failure; never carries prompt or credential content."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LLMProvider(Protocol):
    @property
    def model_version(self) -> str: ...

    def complete(self, system_prompt: str, user_payload: dict[str, object], *, json_mode: bool) -> str: ...


@dataclass(frozen=True)
class OpenAICompatibleProvider:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float = 20
    max_response_bytes: int = 16384

    @property
    def model_version(self) -> str:
        return self.model

    def complete(self, system_prompt: str, user_payload: dict[str, object], *, json_mode: bool) -> str:
        body: dict[str, object] = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, separators=(",", ":"))},
            ],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            opener = build_opener(_NoRedirect)
            with opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
            if len(raw) > self.max_response_bytes:
                raise AIProviderError("model response exceeded configured size")
            envelope = json.loads(raw)
            content = envelope["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise AIProviderError("model response was empty or malformed")
            return content
        except AIProviderError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AIProviderError("model provider request failed") from None


def configured_provider(settings: Settings) -> LLMProvider | None:
    if settings.ai_provider == "disabled":
        return None
    if not settings.ai_base_url or not settings.ai_api_key or not settings.ai_model:
        raise AIProviderError("model provider configuration is incomplete")
    return OpenAICompatibleProvider(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        model=settings.ai_model,
        timeout_seconds=settings.ai_timeout_seconds,
        max_response_bytes=settings.ai_max_response_bytes,
    )
