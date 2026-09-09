"""Provider-agnostic structured classification.

The service needs one thing from a language model: given a message, return
JSON that validates against a schema. Which vendor does that is a deployment
decision, not an architectural one - development runs on a free model, while
the client's real Slack content needs a route with a data-retention policy
they have signed off on (docs/open-questions.md Q7).

Two backends:
  anthropic  - the Anthropic SDK, schema enforced by the API.
  openai     - any OpenAI-compatible endpoint (OpenRouter, Together, a local
               server). Tries strict json_schema, falls back to json_object
               for models that do not support it.

Both validate the reply against the pydantic model before returning it, so a
model that ignores the schema fails loudly here rather than corrupting a task.
"""

from __future__ import annotations

import json
import logging
from typing import TypeVar

import httpx2 as httpx
from pydantic import BaseModel, ValidationError

from .config import settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class Refusal(LLMError):
    """The model declined to answer. Caller should route to a human."""


class Backend:
    def classify(self, system: str, user: str, schema: type[T]) -> T:
        raise NotImplementedError


class AnthropicBackend(Backend):
    def __init__(self) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def classify(self, system: str, user: str, schema: type[T]) -> T:
        import anthropic

        try:
            response = self._client.messages.parse(
                model=settings.classifier_model,
                max_tokens=4096,
                # The system prompt is long and never changes, so cache it.
                system=[
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                thinking={"type": "adaptive"},
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
        except anthropic.AuthenticationError:
            raise LLMError("ANTHROPIC_API_KEY is missing or invalid")
        except anthropic.RateLimitError:
            raise LLMError("rate limited by Anthropic — slow down or retry later")
        except anthropic.APIStatusError as error:
            raise LLMError(f"Anthropic → {error.status_code}: {error.message}")

        if response.stop_reason == "refusal":
            raise Refusal("model declined to classify this message")
        return response.parsed_output


class OpenAICompatBackend(Backend):
    """Any OpenAI-compatible /chat/completions endpoint."""

    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise LLMError("OPENAI_API_KEY (or OPENROUTER_API_KEY) is not set")
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        if "openrouter" in settings.openai_base_url:
            # OpenRouter attributes traffic with these; harmless elsewhere.
            headers["HTTP-Referer"] = "https://github.com/kapicoast-byte/karthik"
            headers["X-Title"] = "catalogbot"
        self._http = httpx.Client(
            base_url=settings.openai_base_url.rstrip("/"),
            headers=headers,
            timeout=90.0,
        )
        self._supports_json_schema = True

    def classify(self, system: str, user: str, schema: type[T]) -> T:
        raw = self._complete(system, user, schema)
        try:
            return schema.model_validate_json(raw)
        except ValidationError as first_error:
            # One repair attempt: hand the model its own mistake. Weaker models
            # usually fix a named field; if not, we fail rather than guess.
            log.warning("invalid classification, retrying once: %s", first_error)
            repaired = self._complete(
                system,
                f"{user}\n\nYour previous reply was rejected:\n{first_error}\n"
                f"Reply again with JSON that satisfies the schema exactly.",
                schema,
            )
            try:
                return schema.model_validate_json(repaired)
            except ValidationError as second_error:
                raise LLMError(f"model cannot satisfy the schema: {second_error}")

    def _complete(self, system: str, user: str, schema: type[T]) -> str:
        body: dict = {
            "model": settings.classifier_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": 2048,
        }
        if self._supports_json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            }
        else:
            body["response_format"] = {"type": "json_object"}
            body["messages"][0]["content"] += (
                "\n\nReply with JSON only, matching this schema:\n"
                + json.dumps(schema.model_json_schema())
            )

        try:
            response = self._http.post("/chat/completions", json=body)
        except Exception as error:
            raise LLMError(f"cannot reach {settings.openai_base_url}: {error}")

        # Not every model supports strict schemas. Downgrade once, permanently.
        if response.status_code == 400 and self._supports_json_schema:
            log.warning(
                "%s rejected json_schema; falling back to json_object",
                settings.classifier_model,
            )
            self._supports_json_schema = False
            return self._complete(system, user, schema)

        if response.status_code >= 400:
            raise LLMError(
                f"{settings.classifier_model} → {response.status_code}: "
                f"{response.text[:300]}"
            )

        payload = response.json()
        if error := payload.get("error"):
            # OpenRouter reports upstream failures inside a 200.
            raise LLMError(f"{error.get('message', error)}")

        choice = payload["choices"][0]
        if choice.get("finish_reason") == "content_filter":
            raise Refusal("upstream content filter declined this message")

        content = choice["message"]["content"] or ""
        return _strip_code_fence(content)

    def close(self) -> None:
        self._http.close()


def _strip_code_fence(text: str) -> str:
    """Smaller models wrap JSON in ```json fences despite being told not to."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def build_backend() -> Backend:
    provider = settings.llm_provider.lower()
    if provider == "anthropic":
        return AnthropicBackend()
    if provider in {"openai", "openrouter"}:
        return OpenAICompatBackend()
    raise LLMError(f"unknown LLM_PROVIDER {provider!r}")
