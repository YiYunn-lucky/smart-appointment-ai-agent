"""
M12 分层记忆：聊天入口（ProcessUserInput_stream）会话化测试

以真实 Agent 图 + 假分类流驱动完整入口链路（不触网）：
1. 消息内手机号 → 会话绑定客户并随快照落库
2. 未出现手机号 → 会话保持匿名
3. 同会话多轮：窗口累积；重建运行时后状态/窗口/绑定还原（重启恢复）
4. 不同会话交错：各行与 ctx 数据互不串场
5. 已绑定客户的召回注入：ctx.recalled 携带历史要点（供 Agent 组装背景）
"""

import pytest

from api import chat_handler
from agents.session.agent_session_registry import AgentSessionRegistry
from services.memory_service import MemoryService


@pytest.fixture
def env(tmp_path, monkeypatch):
    """把 chat_handler 的单例替换为临时库实例（隔离生产 data 库）"""
    db_path = f"sqlite:///{tmp_path / 'chat.db'}"
    registry = AgentSessionRegistry(db_path=db_path)
    memory = MemoryService(db_path=db_path)

    monkeypatch.setattr(chat_handler, "_registry", registry)
    monkeypatch.setattr(chat_handler, "_memory_service", memory)

    def offline_embed(_text):
        raise RuntimeError("离线测试：禁止调用 embedding 服务")

    monkeypatch.setattr("services.text_embedding.embed_input", offline_embed)
    return registry, memory, db_path


def _patch_classify(rt, reply: str):
    """以假流替换运行时 Agent 图的分类流（同会话后续轮次沿用）"""

    async def fake_classify(task):
        yield "[THOUGHT][客服调度] 模拟分类处理"
        yield f"[REPLY][客服调度]{reply}"

    rt.agent.classify_task_stream = fake_classify


async def _send(registry, session_id: str, text: str, reply: str = "好的，已为您登记。"):
    """向会话发送一条消息（先补假分类流再走真实入口编排）"""
    rt = await registry.get(session_id)
    _patch_classify(rt, reply)
    chunks = []
    async for token in chat_handler.ProcessUserInput_stream(text, session_id=session_id):
        chunks.append(token)
    return "".join(chunks)


class TestSessionPersistence:
    """入口链路：绑定 / 写穿 / 还原"""

    @pytest.mark.asyncio
    async def test_phone_message_binds_user_and_persists(self, env):
        registry, _, _ = env
        await _send(registry, "s1", "我要报修空调，手机号13800138000")
        row = registry._session_svc.load("s1")

        assert row["user_id"] == "13800138000"
        assert len(row["message_window"]) == 2
        assert [m["role"] for m in row["message_window"]] == ["user", "assistant"]
        assert row["state_value"] == "classify"

    @pytest.mark.asyncio
    async def test_message_without_phone_stays_anonymous(self, env):
        registry, _, _ = env
        await _send(registry, "s1", "你好，在吗？")
        row = registry._session_svc.load("s1")
        assert row["user_id"] is None

    @pytest.mark.asyncio
    async def test_window_accumulates_and_recovers_after_rebuild(self, env):
        registry, _, db_path = env
        await _send(registry, "s1", "我要报修空调，手机号13800138000")
        await _send(registry, "s1", "地址是朝阳区望京某小区3号楼")
        assert len(registry._session_svc.load("s1")["message_window"]) == 4

        # 模拟进程重启：新注册表按行重建图与上下文
        reg2 = AgentSessionRegistry(db_path=db_path)
        rt = await reg2.get("s1")
        assert rt.ctx.user_id == "13800138000"
        assert len(rt.ctx.window_messages) == 4
        assert rt.ctx.window_messages[0]["content"] == "我要报修空调，手机号13800138000"

        # 重启后继续对话：窗口继续累积
        await _send(reg2, "s1", "下午3点上门吧")
        assert len(reg2._session_svc.load("s1")["message_window"]) == 6

    @pytest.mark.asyncio
    async def test_interleaved_sessions_do_not_cross(self, env):
        registry, _, _ = env
        await _send(registry, "sA", "我要报修空调，手机号13800138000")
        await _send(registry, "sB", "我要报修冰箱，手机号13900139000")
        await _send(registry, "sA", "地址是朝阳区望京某小区3号楼")

        row_a = registry._session_svc.load("sA")
        row_b = registry._session_svc.load("sB")
        assert row_a["user_id"] == "13800138000"
        assert row_b["user_id"] == "13900139000"
        assert len(row_a["message_window"]) == 4
        assert len(row_b["message_window"]) == 2  # B 不受 A 后续消息影响


class TestRecallInjection:
    """已绑定客户的长期记忆召回注入"""

    @pytest.mark.asyncio
    async def test_recall_loaded_into_context_after_binding(self, env):
        registry, memory, _ = env
        memory.add_memory("13800138000", "报修：空调不制冷，已预约上门", memory_type='repair')

        await _send(registry, "s1", "帮我安排修一下洗衣机，手机号13800138000")
        rt = await registry.get("s1")

        # ctx.recalled 由入口在绑定后填充，供 Agent 组装"客户已知背景"
        assert rt.ctx.user_id == "13800138000"
        assert rt.ctx.recalled
        contents = [r["content"] for r in rt.ctx.recalled]
        assert any("空调" in c for c in contents)
        background = rt.ctx.background_text(rt.ctx.recalled)
        assert "空调" in background
        assert "客户已知背景" in background

    @pytest.mark.asyncio
    async def test_no_recall_for_anonymous_session(self, env):
        registry, memory, _ = env
        memory.add_memory("13800138000", "报修：空调不制冷，已预约上门", memory_type='repair')

        await _send(registry, "s1", "你好，请问在吗")
        rt = await registry.get("s1")
        assert rt.ctx.user_id is None
        assert rt.ctx.recalled is None or rt.ctx.recalled == []
