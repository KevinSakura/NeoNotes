"""One bounded request, no SDK, redirects, retries, tool execution or raw logs."""

import json
import re
import time

import httpx

from .model_settings import ModelError, ModelSettings

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("non-finite JSON")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


class DeepSeekAdapter:
    def __init__(self, settings: ModelSettings, transport: httpx.BaseTransport | None = None):
        self.settings, self.transport = settings, transport
        self.on_request = None

    def request_started(self, request):
        # HTTPX calls this immediately before dispatch. Never retain request or headers.
        if self.on_request:
            self.on_request()

    def generate(self, messages):
        settings = self.settings
        settings.ensure_available()
        if settings.mock:
            raise ModelError("模拟模式不会调用 DeepSeek。", code="mock")
        started = time.monotonic()
        key = settings.api_key.get_secret_value().strip()
        payload = {
            "model": settings.name,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "stream": False,
            "max_tokens": settings.max_tokens,
        }
        try:
            client = httpx.Client(
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(settings.timeout_seconds, connect=10, write=10, pool=10),
                event_hooks={"request": [self.request_started]},
            )
            with (
                client,
                client.stream(
                    "POST",
                    settings.base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + key},
                    json=payload,
                ) as response,
            ):
                if response.status_code != 200:
                    messages_by_code = {
                        400: "模型拒绝请求参数，请保留任务记录并检查适配配置。",
                        401: "DeepSeek 密钥无效或无权限，请在本地检查。",
                        402: "DeepSeek API 可用额度不足。",
                        429: "DeepSeek 请求受限，请稍后手动再试。",
                    }
                    raise ModelError(
                        messages_by_code.get(
                            response.status_code, "DeepSeek 服务未成功响应；未自动重试。"
                        ),
                        502,
                        "http_" + str(response.status_code),
                    )
                content = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() - started > settings.timeout_seconds:
                        raise ModelError("DeepSeek 请求超过时间上限；未自动重试。", 504, "timeout")
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise ModelError("模型响应超过大小上限；已有作品未改变。", 502, "oversize")
        except httpx.TimeoutException:
            raise ModelError(
                "DeepSeek 请求超时；未自动重试，已有作品保留。", 504, "timeout"
            ) from None
        except (httpx.HTTPError, OSError):
            raise ModelError(
                "无法连接 DeepSeek；请检查网络，未自动重试。", 502, "network"
            ) from None
        try:
            raw = content.decode("utf-8")
            if key in raw:
                raise ValueError("secret in response")
            envelope = strict_json(raw)
            if key in json.dumps(envelope, ensure_ascii=False):
                raise ValueError("escaped secret in response")
            choice = envelope["choices"][0]
            message = choice["message"]
            metadata = {
                "requested_model": settings.name,
                "returned_model": envelope["model"],
                "request_id": envelope["id"],
                "finish_reason": choice["finish_reason"],
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "usage": {},
            }
            for field in ("returned_model", "request_id"):
                if not isinstance(metadata[field], str) or not re.fullmatch(
                    r"[\w.:-]{1,150}", metadata[field]
                ):
                    raise ValueError("bad metadata")
            usage = envelope.get("usage") or {}
            for field in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "prompt_cache_hit_tokens",
                "prompt_cache_miss_tokens",
            ):
                value = usage.get(field)
                if value is not None:
                    if type(value) is not int or not 0 <= value <= 100_000_000:
                        raise ValueError("bad usage")
                    metadata["usage"][field] = value
            if choice["finish_reason"] != "stop":
                error = ModelError(
                    "模型输出被截断、拒绝或中断；不会采用不完整作品。", 502, "incomplete"
                )
                error.metadata = metadata
                raise error
            if (
                message.get("tool_calls")
                or message.get("refusal")
                or not isinstance(message.get("content"), str)
                or not message["content"].strip()
            ):
                error = ModelError("模型没有返回完整的音乐 JSON；已有作品保留。", 502, "empty")
                error.metadata = metadata
                raise error
            result = strict_json(message["content"])
            if not isinstance(result, dict):
                raise ValueError("not an object")
            if key in json.dumps(result, ensure_ascii=False):
                raise ValueError("escaped secret in music")
            return result, metadata
        except (ValueError, TypeError, KeyError, IndexError, UnicodeError, AttributeError):
            error = ModelError("模型响应格式不正确；未采用或自动修复。", 502, "response")
            # Only attach already validated metadata, never raw model text.
            if "metadata" in locals() and all(
                isinstance(metadata.get(field), str)
                and re.fullmatch(r"[\w.:-]{1,150}", metadata[field])
                for field in ("returned_model", "request_id")
            ):
                error.metadata = metadata
            raise error from None
