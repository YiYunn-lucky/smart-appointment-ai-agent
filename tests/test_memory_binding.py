"""
M12 分层记忆：长期记忆写入/绑定/召回（离线降级路径）测试

覆盖（不依赖 LLM / 网络；embedding 一律以离线异常替代）：
1. 手机号提取与会话绑定
2. 记忆写入：类型默认重要度、同内容去重
3. 语义不可用时召回自动降级为时效+重要度并返回 Top-5
4. 软删除与按客户隔离
"""

import pytest

from services.memory_service import MemoryService, DEFAULT_IMPORTANCE


@pytest.fixture
def memory(tmp_path, monkeypatch) -> MemoryService:
    # 离线环境：任何 embedding 调用立即失败 → 走无语义降级路径
    def offline_embed(_text):
        raise RuntimeError("离线测试：禁止调用 embedding 服务")

    monkeypatch.setattr("services.text_embedding.embed_input", offline_embed)
    return MemoryService(db_path=f"sqlite:///{tmp_path / 'mem.db'}")


@pytest.fixture
def chat_svc(tmp_path):
    from services.chat_session_service import ChatSessionService
    return ChatSessionService(db_path=f"sqlite:///{tmp_path / 'mem.db'}")


class TestPhoneExtractAndBind:
    """手机号识别与会话绑定"""

    def test_extract_phone_returns_first_match(self):
        assert MemoryService.extract_phone("我的电话是13800138000，麻烦安排") == "13800138000"
        assert MemoryService.extract_phone("电话 13900139000") == "13900139000"
        assert MemoryService.extract_phone("没有留下号码") is None
        assert MemoryService.extract_phone(None) is None

    def test_maybe_bind_session_persists_user(self, memory, chat_svc):
        chat_svc.save(session_id="s1")
        phone = memory.maybe_bind_session("s1", "我叫王芳，手机13800138000")
        assert phone == "13800138000"
        row = chat_svc.load("s1")
        assert row["user_id"] == "13800138000"

    def test_maybe_bind_skips_without_phone(self, memory, chat_svc):
        chat_svc.save(session_id="s1")
        assert memory.maybe_bind_session("s1", "你好，我想咨询") is None
        assert chat_svc.load("s1")["user_id"] is None

    def test_maybe_bind_no_row_does_not_crash(self, memory):
        assert memory.maybe_bind_session("不存在", "手机13800138000") == "13800138000"


class TestMemoryWrite:
    """记忆写入：默认重要度 / 去重 / 隔离 / 软删除"""

    def test_default_importance_by_type(self, memory):
        memory.add_memory("13800138000", "报修：空调不制冷", memory_type='repair')
        memory.add_memory("13800138000", "咨询：保修几年", memory_type='consult')
        memory.add_memory("13800138000", "偏好：周六上午上门", memory_type='preference')
        rows = {r['memory_type']: r['importance'] for r in memory.list_memories("13800138000")}
        assert rows['repair'] == DEFAULT_IMPORTANCE['repair']
        assert rows['consult'] == DEFAULT_IMPORTANCE['consult']
        assert rows['preference'] == DEFAULT_IMPORTANCE['preference']
        # 离线环境 embedding 置空，语义召回降级不影响写入
        for r in memory.list_memories("13800138000"):
            assert r['embedding'] is None

    def test_duplicate_content_is_deduplicated(self, memory):
        memory.add_memory("13800138000", "报修：空调不制冷", memory_type='repair')
        memory.add_memory("13800138000", "报修：空调不制冷", memory_type='repair')
        rows = memory.list_memories("13800138000")
        assert len(rows) == 1

    def test_memories_isolated_by_user_and_soft_delete(self, memory):
        memory.add_memory("13800138000", "报修：空调不制冷", memory_type='repair')
        memory.add_memory("13900139000", "报修：冰箱漏水", memory_type='repair')

        assert len(memory.list_memories("13800138000")) == 1
        assert len(memory.list_memories("13900139000")) == 1
        assert memory.list_memories("13800138000")[0]['is_active'] is True

        # 软删除后不再召回
        row = memory.list_memories("13800138000")[0]
        memory.memory_repo.delete_memory(row['id'])
        assert memory.list_memories("13800138000") == []


class TestRecallDegradation:
    """无语义分时的召回：时效 + 重要度归一化，按分降序返回 Top-K"""

    def _seed(self, memory, user: str):
        memory.add_memory(user, "报修：空调不制冷已上门处理", memory_type='repair')
        memory.add_memory(user, "咨询：净水器滤芯更换", memory_type='consult')
        memory.add_memory(user, "偏好：喜欢周六上午时段", memory_type='preference')

    def test_recall_returns_top_k_sorted(self, memory):
        self._seed(memory, "13800138000")
        result = memory.recall("13800138000", "帮我查一下之前报修情况", top_k=5)
        assert len(result) == 3
        assert result == sorted(result, key=lambda r: r['score'], reverse=True)
        assert all('content' in r and 'score' in r and 'memory_type' in r for r in result)

    def test_recall_top_k_caps_result(self, memory):
        for i in range(8):
            memory.add_memory("13800138000", f"咨询：第{i}个问题", memory_type='consult')
        result = memory.recall("13800138000", "咨询一下", top_k=5)
        assert len(result) == 5

    def test_recall_empty_for_unknown_user(self, memory):
        assert memory.recall("19999999999", "随便问问") == []

    def test_repair_memory_outranks_fresh_consult_when_semantics_unavailable(self, memory):
        """无语义分时：repair（重要度0.8）压过同日写入的 consult（0.5）"""
        memory.add_memory("13800138000", "报修：空调不制冷", memory_type='repair')
        memory.add_memory("13800138000", "咨询：随便聊聊价格", memory_type='consult')
        result = memory.recall("13800138000", "帮我查一下之前报修情况", top_k=5)
        top = [r for r in result if r['content'].startswith("报修")][0]
        assert result.index(top) == 0
        assert result[0]['importance'] == 0.8
