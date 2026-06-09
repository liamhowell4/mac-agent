"""OpenRouter client for the LLM judge (M3).

Cloud, via OpenRouter's OpenAI-compatible /chat/completions. The judge only scores the
*eval* (clarify / no_call quality) — it never ships in the product. Key + model come from
.env (OPENROUTER_API_KEY, OPENROUTER_MODEL). Different family from the models-under-test,
so it avoids same-family correlated blind spots.
"""

from __future__ import annotations

import os

import httpx

DEFAULT_HOST = "https://openrouter.ai/api/v1"


class OpenRouterError(RuntimeError):
    pass


class OpenRouterJudge:
    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        host: str = DEFAULT_HOST,
        temperature: float = 0.0,
    ):
        self.model = model or os.environ.get("OPENROUTER_MODEL")
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.host = host.rstrip("/")
        self.temperature = temperature
        self.name = f"openrouter:{self.model}"

    def complete(self, system: str, user: str, max_tokens: int = 512) -> str:
        if not self.api_key:
            raise OpenRouterError("OPENROUTER_API_KEY not set (put it in .env)")
        if not self.model:
            raise OpenRouterError("OPENROUTER_MODEL not set (put it in .env or run.yaml)")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # optional attribution headers OpenRouter recommends
            "HTTP-Referer": "https://github.com/liamhowell4/mac-agent",
            "X-Title": "tooleval",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        try:
            resp = httpx.post(
                f"{self.host}/chat/completions", json=payload, headers=headers, timeout=120.0
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise OpenRouterError(
                f"OpenRouter HTTP {e.response.status_code}: {e.response.text[:300]}"
            ) from e
        except httpx.HTTPError as e:
            raise OpenRouterError(f"OpenRouter request failed: {e}") from e
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise OpenRouterError(f"Unexpected OpenRouter response: {str(data)[:300]}") from e
