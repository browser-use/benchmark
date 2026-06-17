"""z.ai chat model support for browser-use.

The z.ai OpenAI-compatible endpoint supports native function calling and
GLM-specific thinking controls. Browser-use's generic ChatOpenAI wrapper does
not pass the z.ai `thinking` object, and its structured-output path relies on
OpenAI JSON schema responses. This wrapper uses z.ai function calls for
structured output instead.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeVar, overload

import httpx
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError
from openai.types.chat.chat_completion import ChatCompletion
from pydantic import BaseModel

from browser_use.llm.base import BaseChatModel
from browser_use.llm.exceptions import ModelProviderError, ModelRateLimitError
from browser_use.llm.messages import BaseMessage
from browser_use.llm.openai.serializer import OpenAIMessageSerializer
from browser_use.llm.schema import SchemaOptimizer
from browser_use.llm.views import ChatInvokeCompletion, ChatInvokeUsage

T = TypeVar("T", bound=BaseModel)


@dataclass
class ChatZAI(BaseChatModel):
    model: str
    api_key: str | None = None
    base_url: str | httpx.URL | None = None
    timeout: float | httpx.Timeout | None = 180
    max_retries: int = 5
    temperature: float | None = 0.1
    top_p: float | None = None
    max_tokens: int | None = 8192
    thinking: Literal["enabled", "disabled"] = "enabled"
    reasoning_effort: Literal["max", "xhigh", "high", "medium", "low", "minimal", "none"] | None = "max"
    default_headers: Mapping[str, str] | None = None
    default_query: Mapping[str, object] | None = None
    http_client: httpx.AsyncClient | None = None

    @property
    def provider(self) -> str:
        return "zai"

    @property
    def name(self) -> str:
        return self.model

    def _client(self) -> AsyncOpenAI:
        params: dict[str, Any] = {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "default_headers": self.default_headers,
            "default_query": self.default_query,
        }
        if self.http_client is not None:
            params["http_client"] = self.http_client
        return AsyncOpenAI(**{k: v for k, v in params.items() if v is not None})

    def _model_params(self) -> dict[str, Any]:
        extra_body: dict[str, Any] = {"thinking": {"type": self.thinking}}
        if self.reasoning_effort is not None:
            extra_body["reasoning_effort"] = self.reasoning_effort
        params: dict[str, Any] = {"extra_body": extra_body}
        if self.temperature is not None:
            params["temperature"] = self.temperature
        if self.top_p is not None:
            params["top_p"] = self.top_p
        if self.max_tokens is not None:
            params["max_tokens"] = self.max_tokens
        return params

    def _usage(self, response: ChatCompletion) -> ChatInvokeUsage | None:
        if response.usage is None:
            return None
        prompt_details = response.usage.prompt_tokens_details
        return ChatInvokeUsage(
            prompt_tokens=response.usage.prompt_tokens,
            prompt_cached_tokens=prompt_details.cached_tokens if prompt_details else None,
            prompt_cache_creation_tokens=None,
            prompt_image_tokens=None,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        )

    def _thinking_from_response(self, response: ChatCompletion) -> str | None:
        if not response.choices:
            return None
        message = response.choices[0].message
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning is None and getattr(message, "model_extra", None):
            reasoning = message.model_extra.get("reasoning_content")
        return reasoning

    def _tool_for_output(self, output_format: type[BaseModel]) -> dict[str, Any]:
        schema = SchemaOptimizer.create_optimized_json_schema(
            output_format,
            remove_min_items=True,
            remove_defaults=True,
        )
        return {
            "type": "function",
            "function": {
                "name": "agent_output",
                "description": f"Return the browser-use action result as {output_format.__name__}.",
                "parameters": schema,
            },
        }

    def _parse_tool_arguments(self, arguments: Any, output_format: type[T]) -> T:
        if isinstance(arguments, str):
            data = json.loads(arguments)
        elif isinstance(arguments, dict):
            data = arguments
        else:
            raise TypeError(f"Unexpected function arguments type: {type(arguments).__name__}")
        return output_format.model_validate(data)

    def _parse_content_json(self, content: str, output_format: type[T]) -> T:
        try:
            return output_format.model_validate_json(content)
        except Exception:
            start = content.find("{")
            end = content.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise
            return output_format.model_validate_json(content[start : end + 1])

    @overload
    async def ainvoke(
        self, messages: list[BaseMessage], output_format: None = None, **kwargs: Any
    ) -> ChatInvokeCompletion[str]: ...

    @overload
    async def ainvoke(self, messages: list[BaseMessage], output_format: type[T], **kwargs: Any) -> ChatInvokeCompletion[T]: ...

    async def ainvoke(
        self, messages: list[BaseMessage], output_format: type[T] | None = None, **kwargs: Any
    ) -> ChatInvokeCompletion[T] | ChatInvokeCompletion[str]:
        openai_messages = OpenAIMessageSerializer.serialize_messages(messages)
        try:
            params = self._model_params()
            if output_format is None:
                response = await self._client().chat.completions.create(
                    model=self.model,
                    messages=openai_messages,
                    **params,
                )
                return ChatInvokeCompletion(
                    completion=response.choices[0].message.content or "",
                    thinking=self._thinking_from_response(response),
                    usage=self._usage(response),
                    stop_reason=response.choices[0].finish_reason if response.choices else None,
                )

            response = await self._client().chat.completions.create(
                model=self.model,
                messages=openai_messages,
                tools=[self._tool_for_output(output_format)],
                tool_choice="auto",
                **params,
            )
            message = response.choices[0].message
            if message.tool_calls:
                completion = self._parse_tool_arguments(message.tool_calls[0].function.arguments, output_format)
            elif message.content:
                completion = self._parse_content_json(message.content, output_format)
            else:
                raise ModelProviderError(
                    message="Expected z.ai function call or JSON content but got neither",
                    status_code=500,
                    model=self.name,
                )

            return ChatInvokeCompletion(
                completion=completion,
                thinking=self._thinking_from_response(response),
                usage=self._usage(response),
                stop_reason=response.choices[0].finish_reason if response.choices else None,
            )

        except RateLimitError as e:
            raise ModelRateLimitError(message=e.message, model=self.name) from e
        except APIConnectionError as e:
            raise ModelProviderError(message=str(e), model=self.name) from e
        except APIStatusError as e:
            raise ModelProviderError(message=e.message, status_code=e.status_code, model=self.name) from e
        except Exception as e:
            raise ModelProviderError(message=str(e), model=self.name) from e
