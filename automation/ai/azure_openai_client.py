from __future__ import annotations

import json
import logging
import time
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from automation.ai.prompts import PROMPT_VERSION
from backend.config import Settings
from backend.services.ai_audit_service import ai_metrics_context
from backend.services.token_usage_service import record_token_usage

logger = logging.getLogger("autobi.ai")


class AIServiceError(RuntimeError):
    """A safe-to-display error from the optional AI planning service."""


class AzureOpenAIClient:
    def __init__(self, settings: Settings) -> None:
        if not (settings.azure_openai_api_key and settings.azure_ai_foundry_project_endpoint
                and settings.azure_openai_endpoint and settings.azure_openai_deployment):
            raise AIServiceError("Azure OpenAI is enabled but its server-side configuration is incomplete.")
        self.model = settings.azure_openai_deployment
        self.timeout = settings.ai_timeout_seconds
        # Azure AI Foundry exposes deployed models through its OpenAI-compatible
        # /openai/v1 inference endpoint. The project endpoint is retained in
        # configuration for project identity; it is not a completions endpoint.
        self._client = AsyncOpenAI(
            api_key=settings.azure_openai_api_key,
            base_url=settings.azure_openai_endpoint,
            timeout=self.timeout,
            max_retries=2,
        )

    async def generate_json(self, operation: str, prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        ai_metrics_context.set({"operation": operation, "prompt_version": PROMPT_VERSION})
        response = None
        outcome = "failed"
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=True, default=str)},
                ],
            )
            text = response.choices[0].message.content
            result = json.loads(text or "")
            if not isinstance(result, dict):
                raise ValueError("Expected a JSON object")
            outcome = "succeeded"
        except APITimeoutError as error:
            self._record_failure(operation, started, "timeout")
            raise AIServiceError("Azure OpenAI timed out. Please retry.") from error
        except (APIConnectionError, APIStatusError, ValueError, IndexError, TypeError) as error:
            self._record_failure(operation, started, type(error).__name__)
            raise AIServiceError("Azure OpenAI request failed. Please retry after checking the AI service configuration.") from error

        finally:
            record_token_usage(operation=operation, model=self.model, response=response,
                               outcome=outcome, latency_ms=round((time.perf_counter()-started)*1000, 2))

        usage = response.usage
        self._record_success(operation, started, usage.prompt_tokens if usage else None,
                             usage.completion_tokens if usage else None, usage.total_tokens if usage else None)
        return result

    async def test_connection(self) -> None:
        started = time.perf_counter()
        response = None
        outcome = "failed"
        try:
            response = await self._client.chat.completions.create(
                model=self.model, max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            outcome = "succeeded"
        except (APITimeoutError, APIConnectionError, APIStatusError) as error:
            raise AIServiceError("Azure OpenAI deployment connection test failed.") from error
        finally:
            record_token_usage(operation="connection_test", model=self.model, response=response,
                               outcome=outcome, latency_ms=round((time.perf_counter()-started)*1000, 2))

    def _record_failure(self, operation: str, started: float, exception_type: str) -> None:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        ai_metrics_context.set({"operation": operation, "prompt_version": PROMPT_VERSION,
                                "latency_ms": latency_ms, "retry_count": 2})
        logger.warning("AI operation failed", extra={"event": "ai.operation.failed", "operation": operation,
                       "model_name": self.model, "latency_ms": latency_ms, "success": False,
                       "retry_count": 2, "prompt_version": PROMPT_VERSION, "exception_type": exception_type})

    def _record_success(self, operation: str, started: float, input_tokens: int | None,
                        output_tokens: int | None, total_tokens: int | None) -> None:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        ai_metrics_context.set({"operation": operation, "prompt_version": PROMPT_VERSION,
                                "latency_ms": latency_ms, "input_tokens": input_tokens,
                                "output_tokens": output_tokens, "total_tokens": total_tokens, "retry_count": 2})
        logger.info("AI operation completed", extra={"event": "ai.operation.completed", "operation": operation,
                    "model_name": self.model, "latency_ms": latency_ms, "success": True,
                    "retry_count": 2, "prompt_version": PROMPT_VERSION, "input_tokens": input_tokens,
                    "output_tokens": output_tokens})
