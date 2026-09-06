"""
M12 分层记忆：多会话隔离与运行时还原测试

覆盖（全部离线，不触碰 LLM / 网络）：
1. 两个会话的客户绑定 / 槽位 / 窗口 / 摘要互不串场（行级隔离）
2. 状态机值按会话还原（appointment / classify）
3. LRU 淘汰后的运行时按 DB 行重建（等价"进程重启恢复"）
4. AppointmentAgent 上下文模式：槽位直用 ctx、重置只清槽位保留窗口、
   解析历史由窗口重建
"""

import pytest

from agents.appointment_agent import AppointmentAgent
from agents.session.agent_session_registry import AgentSessionRegistry
from agents.session.session_context import SessionContext
from agents.session.session_window import extract_reply_text
from services.memory_service import MemoryService


@pytest.fixture
def registry(tmp_path) -> AgentSessionRegistry:
    return AgentSessionRegistry(db_path=f"sqlite:///{tmp_path / 'iso.db'}")


async def run_turn(reg: AgentSessionRegistry, session_id: str, text: str) -> str:
    """向会话发送一条消息（复用真实 Agent 图构建，但不驱动 LLM，仅产出协议文本；
    消息含手机号时按入口语义绑定客户）"""
    runtime = await reg.get(session_id)
    if runtime.ctx.user_id is None:
        phone = MemoryService.extract_phone(text)
        if phone:
            runtime.ctx.bind_user(phone)
    chunks = ["[REPLY][客服调度]模拟回复：" + text]
    runtime.ctx.append_turn(text, extract_reply_text("".join(chunks)))
    reg.persist(runtime)
    return "".join(chunks)


class TestContextIsolation:
    """多会话互不串场"""

    @pytest.mark.asyncio
    async def test_two_sessions_keep_their_own_rows(self, registry, tmp_path):
        """A 会话绑定客户并写窗口后，B 会话保持匿名且数据独立"""
        await run_turn(registry, "sA", "我要报修空调，手机号13800138000")
        await run_turn(registry, "sB", "我想问问冰箱怎么保养")

        row_a = registry._session_svc.load("sA")
        row_b = registry._session_svc.load("sB")

        assert row_a["user_id"] == "13800138000"
        assert row_b["user_id"] is None  # B 会话未出现手机号，不串场
        assert row_a["message_window"] != row_b["message_window"]
        assert len(row_a["message_window"]) == 2
        assert len(row_b["message_window"]) == 2

    @pytest.mark.asyncio
    async def test_contexts_do_not_share_slots_or_summary(self, registry):
        """同进程内两会话的 ctx 槽位/摘要对象彼此独立"""
        rt_a = await registry.get("sA")
        rt_b = await registry.get("sB")

        rt_a.ctx.appointment_slots["phone"] = "13800138000"
        rt_a.ctx.appointment_slots["product_type"] = "空调"
        rt_a.ctx.summary_text = "A 的早期摘要"

        assert rt_b.ctx.appointment_slots == {}
        assert rt_b.ctx.summary_text == ""
        assert rt_b.ctx.user_id is None

    @pytest.mark.asyncio
    async def test_state_value_restored_per_session(self, registry, tmp_path):
        """A 会话处于报修流程时落库 appointment，B 会话仍为 classify；重启后按会话还原"""
        rt_a = await registry.get("sA")
        rt_a.agent.state_manager.transition_to_appointment()
        registry.persist(rt_a)
        await run_turn(registry, "sB", "随便聊聊")

        reg2 = AgentSessionRegistry(db_path=f"sqlite:///{tmp_path / 'iso.db'}")
        rt_a2 = await reg2.get("sA")
        rt_b2 = await reg2.get("sB")

        assert rt_a2.agent.state_manager.get_current_state().value == "appointment"
        assert rt_b2.agent.state_manager.get_current_state().value == "classify"


class TestRegistryRecovery:
    """淘汰 / 重启后的运行时重建"""

    @pytest.mark.asyncio
    async def test_evicted_runtime_rebuilds_from_row(self, registry, tmp_path):
        """LRU 淘汰后再次 get：从 DB 行还原绑定/窗口/摘要，数据不丢"""
        registry.MAX_RUNTIMES = 2
        await run_turn(registry, "s1", "报修空调，手机号13800138000")
        await run_turn(registry, "s2", "问下收费，电话13900139000")
        await run_turn(registry, "s3", "其他问题")

        assert "s1" not in registry._runtimes  # 已被 LRU 淘汰

        rt = await registry.get("s1")  # 按行重建
        assert rt.ctx.user_id == "13800138000"
        assert len(rt.ctx.window_messages) == 2
        assert registry._session_svc.load("s2")["user_id"] == "13900139000"


class TestAppointmentAgentContextMode:
    """报修 Agent 挂载会话上下文后的行为"""

    def _agent_with_ctx(self, session_id: str = "s1") -> AppointmentAgent:
        agent = AppointmentAgent(session_id=session_id)
        ctx = SessionContext(session_id=session_id)
        ctx.appointment_slots["phone"] = "13800138000"
        ctx.append_turn("我要报修空调", "请您提供上门地址")
        ctx.append_turn("朝阳区望京某小区3号楼", "好的，请问几点上门方便？")
        agent.session_context = ctx
        return agent

    def test_working_slots_reference_context(self):
        """上下文模式下槽位直用 ctx 字典，改写即对会话可见"""
        agent = self._agent_with_ctx()
        slots = agent._current_slots()
        slots["address"] = "朝阳区望京某小区3号楼"
        assert agent.session_context.appointment_slots["address"] == "朝阳区望京某小区3号楼"
        # 原生（无上下文）Agent 仍走本地 appointment_history，向后兼容
        legacy = AppointmentAgent(session_id="legacy")
        assert legacy._current_slots() is legacy.appointment_history

    def test_reset_only_clears_slots_keeps_window(self):
        """报修完成后：只清槽位，窗口与客户绑定保留（供后续咨询/再次报修）"""
        agent = self._agent_with_ctx()
        agent.session_context.bind_user("13800138000")
        agent._reset_state_after_appointment()

        assert agent.session_context.appointment_slots == {}
        assert agent.session_context.user_id == "13800138000"
        assert len(agent.session_context.window_messages) == 4
        # 原生 reset() 语义不变
        legacy = AppointmentAgent(session_id="legacy")
        legacy.reset()
        assert legacy.appointment_history["phone"] is None
        assert legacy.finished is False

    def test_history_rebuilt_from_context_window(self):
        """解析历史由窗口最近轮次重建（用户/客服交替），不回放旧会话记忆"""
        agent = self._agent_with_ctx()
        history = agent._history_from_context()
        assert [m.type for m in history.messages] == ["human", "ai", "human", "ai"]
        assert history.messages[0].content == "我要报修空调"
        assert history.messages[1].content == "请您提供上门地址"

    def test_no_session_context_falls_back_to_legacy_history(self):
        """未挂载上下文时沿用成员 chat_history（存量测试语义不受影响）"""
        agent = AppointmentAgent(session_id="legacy")
        assert agent._in_context_mode is False
        assert agent._current_slots() is agent.appointment_history
