"""
Agent 会话运行时注册表

每会话惰性构建一张完整 Agent 图（TaskClassificationAgent + 子 Agent，session_id 透传），
状态与上下文在 SessionContext 中按会话隔离，杜绝单例时代的串场。
- 同会话并发请求以 per-session 锁串行；
- 内存保留 LRU（上限 8），淘汰不丢数据（每轮已写穿 chat_sessions 表）；
- 进程重启后按会话行重建图与上下文（状态机/槽位/窗口/摘要还原）。
"""

import asyncio
import logging
from collections import OrderedDict
from typing import Dict, Optional

from agents.task_classification_agent import TaskClassificationAgent
from agents.appointment_agent import AppointmentAgent
from agents.consultant_agent import ConsultantAgent
from config.constants import StateEnum
from .session_context import SessionContext
from .session_window import build_summary_prompt, fallback_summary

logger = logging.getLogger(__name__)


class SessionRuntime:
    """一个会话的运行单元：Agent 图 + 上下文 + 并发锁"""

    def __init__(self, session_id: str, agent: TaskClassificationAgent, ctx: SessionContext):
        self.session_id = session_id
        self.agent = agent
        self.ctx = ctx
        self.lock = asyncio.Lock()


class AgentSessionRegistry:
    """进程内会话运行时注册表（LRU）"""

    MAX_RUNTIMES = 8
    DEFAULT_DB_PATH = 'sqlite:///data/smart_appointment.db'

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        from services.chat_session_service import ChatSessionService

        self._session_svc = ChatSessionService(db_path)
        self._runtimes: "OrderedDict[str, SessionRuntime]" = OrderedDict()

    async def get(self, session_id: str) -> SessionRuntime:
        """获取（或构建）会话运行时；淘汰最久未用且已落库的运行时"""
        runtime = self._runtimes.get(session_id)
        if runtime is not None:
            self._runtimes.move_to_end(session_id)
            return runtime

        runtime = self._build_runtime(session_id)
        self._runtimes[session_id] = runtime
        while len(self._runtimes) > self.MAX_RUNTIMES:
            self._runtimes.popitem(last=False)
        return runtime

    # ---------- 构建与还原 ----------

    def _build_runtime(self, session_id: str) -> SessionRuntime:
        row = self._session_svc.load(session_id)
        ctx = SessionContext.from_row(row) if row else SessionContext(session_id=session_id)

        appointment_agent = AppointmentAgent(session_id=session_id)
        consultant_agent = ConsultantAgent(session_id=session_id)
        task_agent = TaskClassificationAgent(appointment_agent, consultant_agent)

        if row and row.get("state_value"):
            try:
                task_agent.state_manager.set_state(StateEnum(row["state_value"]))
            except ValueError:
                logger.warning(f"会话 {session_id} 状态值非法，重置为分类态")

        # 上下文注入整张 Agent 图（每会话一图，无共享可变状态）
        task_agent.session_context = ctx
        task_agent.appointment_agent.session_context = ctx
        task_agent.consultant_agent.session_context = ctx
        return SessionRuntime(session_id, task_agent, ctx)

    # ---------- 持久化与摘要 ----------

    def persist(self, runtime: SessionRuntime) -> bool:
        """把会话快照写穿 DB（状态机值/客户/槽位/窗口/摘要）"""
        try:
            state = runtime.agent.state_manager.get_current_state()
            return self._session_svc.save(
                session_id=runtime.session_id,
                state_value=state.value if state else None,
                user_id=runtime.ctx.user_id,
                appointment_slots=runtime.ctx.appointment_slots,
                message_window=runtime.ctx.window_messages,
                summary_text=runtime.ctx.summary_text,
            )
        except Exception as e:
            logger.error(f"持久化会话失败：{runtime.session_id}，{e}")
            return False

    async def merge_rolled_into_summary(self, runtime: SessionRuntime,
                                        rolled_messages) -> Optional[str]:
        """把滚出窗口的轮次并入滚动摘要（LLM 失败降级为截断拼接）"""
        if not rolled_messages:
            return None
        ctx = runtime.ctx
        from langchain_core.messages import HumanMessage

        prompt = build_summary_prompt(ctx.summary_text, rolled_messages)
        try:
            response = await runtime.agent.llm.ainvoke([HumanMessage(content=prompt)])
            new_summary = getattr(response, "content", None)
            if new_summary:
                ctx.summary_text = str(new_summary).strip()
                return ctx.summary_text
        except Exception as e:
            logger.warning(f"滚动摘要 LLM 失败，降级为截断拼接: {e}")
        ctx.summary_text = fallback_summary(rolled_messages, ctx.summary_text)
        return ctx.summary_text
