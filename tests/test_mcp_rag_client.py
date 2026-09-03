"""
外部 RAG MCP 客户端离线测试

不真实拉起 RAG 子进程：用 fake session 替代 MCP 会话层，
验证 citations 解析、异常降级、以及 KnowledgeService.search 的开关与回退链路。
"""

import json
import pytest

from mcp.types import CallToolResult, TextContent

from services.mcp_rag_client import RagMcpClient
from services.knowledge_service import KnowledgeService


def _text_block(text: str) -> TextContent:
    return TextContent(type="text", text=text)


def _citation_result(*citations: dict, markdown: str = "", raw_json: bool = False) -> CallToolResult:
    """构造 query_knowledge_hub 返回：Markdown 文本块 + citations 块。

    raw_json=True 时第二个块为纯 JSON（兼容形态）；默认镜像 RAG 服务端真实输出：
    Markdown 中 ```json 围栏包裹的 JSON。
    """
    blocks = []
    if markdown:
        blocks.append(_text_block(markdown))
    payload = json.dumps({"citations": list(citations), "has_images": False})
    if raw_json:
        blocks.append(_text_block(payload))
    else:
        blocks.append(_text_block(f"---\n**References (JSON):**\n```json\n{payload}\n```"))
    return CallToolResult(content=blocks, isError=False)


SAMPLE_CITATION = {
    "index": 1,
    "chunk_id": "doc_abc_001",
    "source": "docs/家电手册.pdf",
    "score": 0.9234,
    "text_snippet": "空调不制冷时先检查滤网是否积尘、模式是否制冷",
    "page": 3,
    "metadata": {"title": "家电故障排查手册"},
}


class FakeRagClient:
    """fake RagMcpClient：search 按注入结果返回（文档/异常/空）"""

    def __init__(self, result):
        self._result = result
        self.calls = []

    async def search(self, query, top_k=3, collection=None):
        self.calls.append((query, top_k, collection))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.fixture
def rag_mcp_disabled(monkeypatch):
    """确保开关关闭的默认环境"""
    monkeypatch.delenv("RAG_MCP_ENABLED", raising=False)
    monkeypatch.delenv("RAG_MCP_CWD", raising=False)
    monkeypatch.delenv("RAG_MCP_ARGS", raising=False)
    monkeypatch.delenv("RAG_MCP_COLLECTION", raising=False)


@pytest.mark.asyncio
async def test_search_parses_json_citations(rag_mcp_disabled, monkeypatch):
    """RAG 真实输出（Markdown + json 围栏块）可正确规范化"""
    client = RagMcpClient(command="python", args=[], cwd=".")
    monkeypatch.setattr(
        client,
        "_call_with_session",
        _fake_call(_citation_result(SAMPLE_CITATION, markdown="Result:\n[1] 空调…")),
    )
    docs = await client.search("空调不制冷怎么办", top_k=3)
    assert len(docs) == 1
    doc = docs[0]
    assert doc["content"] == SAMPLE_CITATION["text_snippet"]
    assert doc["category"] == "家电故障排查手册"
    assert doc["score"] == 0.9234
    assert doc["rank"] == 1
    assert doc["source"] == "docs/家电手册.pdf"
    assert doc["chunk_id"] == "doc_abc_001"


@pytest.mark.asyncio
async def test_search_parses_raw_json_block(rag_mcp_disabled, monkeypatch):
    """纯 JSON 块（兼容形态）同样可解析"""
    client = RagMcpClient(command="python", args=[], cwd=".")
    monkeypatch.setattr(
        client,
        "_call_with_session",
        _fake_call(_citation_result(SAMPLE_CITATION, raw_json=True)),
    )
    docs = await client.search("空调不制冷怎么办", top_k=3)
    assert len(docs) == 1
    assert docs[0]["chunk_id"] == "doc_abc_001"


@pytest.mark.asyncio
async def test_search_returns_empty_when_no_json_block(rag_mcp_disabled, monkeypatch):
    """只有纯文本无 JSON 时返回空列表不抛错"""
    client = RagMcpClient(command="python", args=[], cwd=".")
    result = CallToolResult(content=[_text_block("no structured data")], isError=False)
    monkeypatch.setattr(client, "_call_with_session", _fake_call(result))
    docs = await client.search("anything")
    assert docs == []


@pytest.mark.asyncio
async def test_search_returns_empty_on_exception(rag_mcp_disabled, monkeypatch):
    """会话异常时返回空列表不抛错"""
    client = RagMcpClient(command="python", args=[], cwd=".")
    monkeypatch.setattr(
        client, "_call_with_session", _fake_call(RuntimeError("connection lost"))
    )
    docs = await client.search("anything")
    assert docs == []


@pytest.mark.asyncio
async def test_normalize_citation_without_metadata(rag_mcp_disabled):
    """metadata 缺失时 category 使用默认值"""
    citation = {k: v for k, v in SAMPLE_CITATION.items() if k != "metadata"}
    doc = RagMcpClient._normalize_citation(citation, 2)
    assert doc["category"] == "外部知识库"
    assert doc["rank"] == 2


def _fake_call(result):
    """返回无参签名受限的 async 函数，monkeypatch 为实例属性 _call_with_session"""
    async def _call(name, arguments):
        if isinstance(result, Exception):
            raise result
        return result

    return _call


async def _patch_mcp_client(monkeypatch, result):
    """让 knowledge_service 内部延迟 import 到的 get_rag_mcp_client 返回 fake"""
    fake = FakeRagClient(result)

    async def fake_get_rag_mcp_client():
        return fake

    monkeypatch.setattr(
        "services.mcp_rag_client.get_rag_mcp_client", fake_get_rag_mcp_client
    )
    return fake


@pytest.mark.asyncio
async def test_knowledge_search_delegates_to_mcp_when_enabled(monkeypatch, temp_db_path):
    """开关开启且有结果时，KnowledgeService.search 委托外部 RAG 并原样返回"""
    monkeypatch.setenv("RAG_MCP_ENABLED", "true")
    expected = [{"content": "外部知识", "category": "外部知识库", "keywords": [],
                 "score": 0.9, "rank": 1}]
    fake = await _patch_mcp_client(monkeypatch, expected)

    service = KnowledgeService(db_path=temp_db_path)
    docs = await service.search("空调不制冷", top_k=3)

    assert docs == expected
    assert fake.calls == [("空调不制冷", 3, None)]


@pytest.mark.asyncio
async def test_knowledge_search_falls_back_when_mcp_empty(monkeypatch, temp_db_path):
    """开关开启但外部 RAG 无结果时回退本地检索（本地无索引 → 空，不抛错）"""
    monkeypatch.setenv("RAG_MCP_ENABLED", "true")
    await _patch_mcp_client(monkeypatch, [])

    service = KnowledgeService(db_path=temp_db_path)
    docs = await service.search("任意问题")

    assert docs == []


@pytest.mark.asyncio
async def test_knowledge_search_falls_back_when_mcp_errors(monkeypatch, temp_db_path):
    """开关开启但外部 RAG 异常时回退本地检索，不向外抛"""
    monkeypatch.setenv("RAG_MCP_ENABLED", "true")
    await _patch_mcp_client(monkeypatch, RuntimeError("server crashed"))

    service = KnowledgeService(db_path=temp_db_path)
    docs = await service.search("任意问题")

    assert docs == []


@pytest.mark.asyncio
async def test_knowledge_search_skips_mcp_when_disabled(monkeypatch, temp_db_path):
    """开关关闭时完全走本地路径，不触碰外部 RAG"""
    monkeypatch.delenv("RAG_MCP_ENABLED", raising=False)
    fake = await _patch_mcp_client(monkeypatch, [{"content": "不应被返回"}])

    service = KnowledgeService(db_path=temp_db_path)
    docs = await service.search("任意问题")

    assert docs == []
    assert fake.calls == []
