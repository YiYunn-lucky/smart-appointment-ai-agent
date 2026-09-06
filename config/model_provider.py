"""Model provider factory for chat LLMs and embeddings.

Supports Azure OpenAI and OpenAI-compatible providers such as Qwen,
DeepSeek, Zhipu, and OpenAI by switching environment variables.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from dotenv import load_dotenv
from langchain_openai import (
    AzureChatOpenAI,
    AzureOpenAIEmbeddings,
    ChatOpenAI,
    OpenAIEmbeddings,
)
from pydantic import SecretStr

load_dotenv()


CHAT_PROVIDERS = {"openai", "qwen", "deepseek", "zhipu", "openai-compatible"}
EMBEDDING_PROVIDERS = {"openai", "qwen", "zhipu", "openai-compatible"}

# 模型分级通道：main = 大模型（生成/推理质量优先）；fast = 本地小模型/轻量模型
# （承接任务分类、相关性判定、槽位抽取等高频结构化短调用，成本与延迟更低）
MODEL_TIERS = ("main", "fast")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def get_model_provider() -> str:
    """Return configured provider, defaulting to Azure for backward compatibility."""
    return (_env("MODEL_PROVIDER", "azure") or "azure").strip().lower()


def resolve_channel(tier: str = "main") -> Dict[str, Any]:
    """解析某模型通道的环境配置（纯解析、不构造对象，供测试与日志断言）。

    - main：常规模型（生成质量优先）；
    - fast：轻量/本地小模型，未配置 fast 专用模型时透明回退到 main 配置
      （通道独立但等价，行为不变）。

    OpenAI-compatible 分支：LLM_MODEL（main）/ LLM_FAST_MODEL（fast，可选
    MODEL_FAST_PROVIDER 换提供商）；Azure 分支：AZURE_OPENAI_DEPLOYMENT /
    AZURE_FAST_DEPLOYMENT（可选）。
    """
    if tier not in MODEL_TIERS:
        raise ValueError(
            f"Unsupported model tier={tier!r}. Use one of {MODEL_TIERS}."
        )
    is_fast = tier == "fast"
    provider = get_model_provider()
    if is_fast:
        provider = _env("MODEL_FAST_PROVIDER") or provider
    provider = provider.strip().lower()

    cfg: Dict[str, Any] = {"tier": tier, "provider": provider,
                           "model": None, "deployment": None}
    if provider == "azure":
        deployment = _env("AZURE_FAST_DEPLOYMENT") if is_fast else None
        deployment = deployment or _env("AZURE_OPENAI_DEPLOYMENT")
        cfg["deployment"] = deployment
    elif provider in CHAT_PROVIDERS:
        model = _env("LLM_FAST_MODEL") if is_fast else None
        cfg["model"] = model or _env("LLM_MODEL", "qwen-plus") or "qwen-plus"
    return cfg


def channel_label(tier: str = "main") -> str:
    """通道的人类可读标签：fast:qwen/qwen-turbo（供启动日志展示实际通道）"""
    cfg = resolve_channel(tier)
    return f"{tier}:{cfg['provider']}/{cfg['model'] or cfg['deployment'] or '(default)'}"


def create_chat_model(temperature: float = 0, tier: str = "main"):
    """Create a chat model from environment configuration.

    Args:
        temperature: 采样温度。
        tier: 模型通道，'main'（默认，生成质量优先）或 'fast'（本地小模型/轻量模型，
            承接分类与槽位抽取等高频结构化任务；未配置 fast 专用模型时透明回退 main）。

    Azure-compatible env vars:
        MODEL_PROVIDER=azure
        AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT,
        AZURE_OPENAI_VERSION
        AZURE_FAST_DEPLOYMENT            # fast 通道专用部署（可选）

    OpenAI-compatible env vars:
        MODEL_PROVIDER=qwen|deepseek|zhipu|openai|openai-compatible
        LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
        LLM_FAST_MODEL                   # fast 通道专用模型（可选）
        MODEL_FAST_PROVIDER              # fast 通道提供商（可选，缺省同 MODEL_PROVIDER）
    """
    cfg = resolve_channel(tier)
    provider = cfg["provider"]

    if provider == "azure":
        return AzureChatOpenAI(
            azure_deployment=cfg["deployment"],
            api_version=_env("AZURE_OPENAI_VERSION"),
            temperature=temperature,
            azure_endpoint=_env("AZURE_OPENAI_ENDPOINT"),
            api_key=SecretStr(_env("AZURE_OPENAI_API_KEY", "") or ""),
        )

    if provider in CHAT_PROVIDERS:
        return ChatOpenAI(
            model=cfg["model"],
            api_key=SecretStr(_env("LLM_API_KEY", "") or ""),
            base_url=_env("LLM_BASE_URL"),
            temperature=temperature,
        )

    raise ValueError(
        f"Unsupported MODEL_PROVIDER={provider!r}. "
        "Use azure, qwen, deepseek, zhipu, openai, or openai-compatible."
    )


def create_embedding_model():
    """Create an embedding model from environment configuration."""
    provider = (_env("EMBEDDING_PROVIDER") or get_model_provider()).strip().lower()

    if provider == "azure":
        return AzureOpenAIEmbeddings(
            azure_deployment=_env("AZURE_OPENAI_DEPLOYMENT_EMBEDDING"),
            api_key=SecretStr(_env("AZURE_OPENAI_API_KEY", "") or ""),
            api_version=_env("AZURE_OPENAI_EMBEDDING_VERSION", "2023-05-15"),
            azure_endpoint=_env("AZURE_OPENAI_ENDPOINT_EMBEDDING"),
        )

    if provider in EMBEDDING_PROVIDERS:
        return OpenAIEmbeddings(
            model=_env("EMBEDDING_MODEL", "text-embedding-v3") or "text-embedding-v3",
            api_key=SecretStr(_env("EMBEDDING_API_KEY") or _env("LLM_API_KEY", "") or ""),
            base_url=_env("EMBEDDING_BASE_URL") or _env("LLM_BASE_URL"),
            # OpenAI-compatible providers like DashScope (Qwen) only accept raw
            # strings; disable token-id batching to send plain text.
            check_embedding_ctx_length=False,
        )

    raise ValueError(
        f"Unsupported EMBEDDING_PROVIDER={provider!r}. "
        "Use azure, qwen, zhipu, openai, or openai-compatible."
    )