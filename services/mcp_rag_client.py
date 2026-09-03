"""
外部 RAG MCP 客户端

通过 MCP(stdio) 协议调用 MODULAR-RAG-MCP-SERVER 的检索工具
(query_knowledge_hub / list_collections)，把返回的 citations 规范化为
与本地 KnowledgeService.search 输出对齐的文档结构，供知识库链路统一消费。

开关与连接参数通过环境变量配置（见 .env.example）：
    RAG_MCP_ENABLED / RAG_MCP_COMMAND / RAG_MCP_ARGS / RAG_MCP_CWD / RAG_MCP_COLLECTION
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_RAG_MCP_CWD = r"C:/Users/Cloud/Desktop/RAG项目/MODULAR-RAG-MCP-SERVER-main"
DEFAULT_RAG_MCP_ARGS = ["-m", "src.mcp_server.server"]
QUERY_TOOL = "query_knowledge_hub"
LIST_COLLECTIONS_TOOL = "list_collections"
# 首次调用需拉起 RAG 子进程并预热 chroma/BM25，预留充足超时
CALL_TIMEOUT_SECONDS = 120.0


def is_rag_mcp_enabled() -> bool:
    """外部 RAG MCP 开关（实时读取环境变量，便于演示时切换）"""
    return os.getenv("RAG_MCP_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    return value if value not in (None, "") else default


class RagMcpClient:
    """封装 stdio 型 MCP 客户端（每次调用独立会话，进程内串行执行）"""

    def __init__(
        self,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        cwd: Optional[str] = None,
        collection: Optional[str] = None,
    ) -> None:
        resolved_command = command or _env("RAG_MCP_COMMAND") or sys.executable
        resolved_args = self._resolve_args(args)
        resolved_cwd = cwd or _env("RAG_MCP_CWD") or DEFAULT_RAG_MCP_CWD
        self._server_params = StdioServerParameters(
            command=resolved_command,
            args=resolved_args,
            cwd=resolved_cwd,
            env=None,
        )
        self._collection = collection or _env("RAG_MCP_COLLECTION") or None

        self._session_lock = asyncio.Lock()

    @staticmethod
    def _resolve_args(args: Optional[List[str]]) -> List[str]:
        """显式 args 优先；其次解析环境变量（支持 JSON 数组或空格分隔字符串）"""
        if args is not None:
            return list(args)
        raw_args = _env("RAG_MCP_ARGS")
        if not raw_args:
            return list(DEFAULT_RAG_MCP_ARGS)
        try:
            parsed = json.loads(raw_args)
            if isinstance(parsed, list):
                return [str(a) for a in parsed]
        except json.JSONDecodeError:
            pass
        return raw_args.split()

    @property
    def collection(self) -> Optional[str]:
        return self._collection

    async def _call_with_session(self, name: str, arguments: Dict[str, Any]) -> Any:
        """在一次完整 stdio 会话中调用 MCP 工具。

        mcp SDK 的 stdio 会话要求进入与退出发生在同一 task 内，无法跨请求常驻，
        因此每次调用建立独立子进程会话；进程内串行执行避免并发时重复拉起服务。
        首次调用需预热 RAG 服务（chroma/BM25），可能耗时较长。
        """
        async with self._session_lock:
            logger.info(
                f"调用外部 RAG MCP 工具 {name}: {self._server_params.command} "
                f"{' '.join(self._server_params.args)} (cwd={self._server_params.cwd})"
            )
            async with stdio_client(self._server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await asyncio.wait_for(
                        session.call_tool(name, arguments),
                        timeout=CALL_TIMEOUT_SECONDS,
                    )

    async def search(
        self, query: str, top_k: int = 3, collection: Optional[str] = None
    ) -> List[Dict]:
        """调用 query_knowledge_hub 语义检索，返回规范化文档列表。

        输出字段与 KnowledgeService.search 对齐：content/category/keywords/score/rank，
        另附 source/chunk_id/metadata 供溯源。
        解析失败或调用异常时返回空列表（由调用方决定回退），不向外抛。
        """
        if not query or not query.strip():
            return []
        arguments: Dict[str, Any] = {"query": query.strip(), "top_k": max(1, min(int(top_k), 20))}
        target_collection = collection or self._collection
        if target_collection:
            arguments["collection"] = target_collection

        try:
            result = await self._call_with_session(QUERY_TOOL, arguments)
        except Exception as e:
            logger.error(f"外部 RAG 检索失败: {e}")
            return []

        documents = self._parse_citations(result)
        if not documents:
            logger.warning("外部 RAG 未返回可解析的 citations 结果")
        return documents

    async def list_collections(self) -> List[str]:
        """调用 list_collections，返回各文本块内容（供联调确认集合名）"""
        try:
            result = await self._call_with_session(LIST_COLLECTIONS_TOOL, {"include_stats": True})
        except Exception as e:
            logger.error(f"外部 RAG list_collections 失败: {e}")
            return []
        texts = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                texts.append(text)
        return texts

    @staticmethod
    def _parse_citations(result: Any) -> List[Dict]:
        """从 CallToolResult 中提取 citations 并规范化。

        兼容两种内容形态：纯 JSON 文本块，或 Markdown 中 ```json 围栏包裹的
        JSON（RAG 服务端实际输出格式）。首个可解析块命中即返回。
        """
        for block in getattr(result, "content", []) or []:
            if getattr(block, "type", None) != "text":
                continue
            text = getattr(block, "text", None)
            if not text:
                continue
            data = RagMcpClient._extract_json(text)
            if not isinstance(data, dict):
                continue
            citations = data.get("citations")
            if not isinstance(citations, list):
                continue
            return [
                RagMcpClient._normalize_citation(c, idx + 1)
                for idx, c in enumerate(citations)
                if isinstance(c, dict)
            ]
        return []

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict]:
        """从文本中提取 JSON dict：先整体解析，失败则取首个 json 围栏块"""
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    @staticmethod
    def _normalize_citation(citation: Dict[str, Any], rank: int) -> Dict[str, Any]:
        metadata = citation.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        return {
            "content": str(citation.get("text_snippet") or ""),
            "category": str(metadata.get("title") or metadata.get("category") or "外部知识库"),
            "keywords": [],
            "score": float(citation.get("score") or 0.0),
            "rank": rank,
            "source": str(citation.get("source") or ""),
            "chunk_id": str(citation.get("chunk_id") or ""),
            "metadata": metadata,
        }


_client_singleton: Optional[RagMcpClient] = None
_client_lock = asyncio.Lock()


async def get_rag_mcp_client() -> RagMcpClient:
    """进程内单例获取 RagMcpClient（配置在首次构造时定格）"""
    global _client_singleton
    if _client_singleton is None:
        async with _client_lock:
            if _client_singleton is None:
                _client_singleton = RagMcpClient()
    return _client_singleton
