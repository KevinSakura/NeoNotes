"""Only the backend loads credentials; public status is an explicit allowlist."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
FULL_SONG_MAX_TOKENS = 32768


class ModelError(Exception):
    def __init__(self, message: str, status: int = 422, code: str = "invalid"):
        super().__init__(message)
        self.status, self.code = status, code


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)
    mock: bool = True
    provider: str = "deepseek"
    base_url: str = Field(default="https://api.deepseek.com", repr=False)
    name: Literal["deepseek-flash", "deepseek-v4-pro"] = "deepseek-flash"
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    output_mode: Literal["json_object"] = "json_object"
    timeout_seconds: int = Field(default=180, ge=10, le=300)
    max_retries: int = Field(default=0, ge=0, le=0)
    max_tokens: int = Field(default=32768, ge=512, le=32768)

    def ensure_available(self):
        if self.mock:
            return
        if self.provider != "deepseek":
            raise ModelError("本阶段仅接入 DeepSeek；不会自动切换到 Qwen。", 503, "provider")
        try:
            url = urlsplit(self.base_url)
            port = url.port
        except ValueError:
            raise ModelError("MODEL_BASE_URL 格式不正确。", 503, "config") from None
        if (
            url.scheme != "https"
            or url.hostname != "api.deepseek.com"
            or port not in (None, 443)
            or url.path.rstrip("/") not in ("", "/v1")
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ModelError("MODEL_BASE_URL 须为官方 DeepSeek HTTPS 聊天地址。", 503, "config")
        key = self.api_key.get_secret_value().strip()
        if not key or key.startswith(("填写", "你的", "YOUR_", "<")):
            raise ModelError("DeepSeek 密钥尚未配置，请在本地 .env 填写。", 503, "key")
        if self.name not in ("deepseek-flash", "deepseek-v4-pro"):
            raise ModelError("MODEL_NAME 不在本阶段已核对的 DeepSeek 模型列表内。", 503, "model")

    def public(self):
        try:
            self.ensure_available()
            error = None
        except (ModelError, ValueError):
            error = "模型配置不可用，请核对本地 .env 的服务商、地址、模型名和密钥。"
        return {
            "mode": "mock" if self.mock else "real",
            "provider": "mock" if self.mock else "deepseek",
            "model": "deterministic-demo" if self.mock else self.name,
            "ready": error is None,
            "error": error,
            "max_tokens": self.max_tokens,
            "full_song_max_tokens": FULL_SONG_MAX_TOKENS,
            "requests_per_job": 0 if self.mock else 1,
            "automatic_retries": 0,
            "full_song_enabled": False,
            "full_song_available": True,
        }


class EnvModelSettings(BaseSettings, ModelSettings):
    model_config = SettingsConfigDict(
        env_prefix="MODEL_", extra="ignore", frozen=True, hide_input_in_errors=True
    )


def load_model_settings(path: Path | None = None) -> ModelSettings:
    """Called lazily by the running backend, never by imports or isolated tests."""
    try:
        return EnvModelSettings(_env_file=path or ROOT / ".env", _env_file_encoding="utf-8-sig")
    except ValidationError as error:
        allowed = {
            "mock",
            "provider",
            "base_url",
            "name",
            "api_key",
            "output_mode",
            "timeout_seconds",
            "max_retries",
            "max_tokens",
        }
        fields = sorted(
            {
                "MODEL_" + str(item["loc"][0]).upper()
                for item in error.errors(include_input=False, include_context=False)
                if item["loc"] and item["loc"][0] in allowed
            }
        )
        raise ModelError(
            "本地模型配置字段无效："
            + ("、".join(fields) or "配置格式")
            + "。请按 .env.example 核对，密钥请勿发到聊天。",
            503,
            "config",
        ) from None
    except (OSError, UnicodeError):
        # Validation messages and input values can contain secrets. Never forward them.
        raise ModelError(
            "本地模型配置无法加载；请检查 .env 格式、模拟开关、超时和输出上限。",
            503,
            "config",
        ) from None
