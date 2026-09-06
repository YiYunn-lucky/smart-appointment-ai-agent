"""
聊天链路入口（会话化）

由 web/routes.py /chat/stream 调用。每个请求携带 session_id（无则后端生成），
经 AgentSessionRegistry 取/建会话运行时（每会话独立 Agent 图 + SessionContext），
处理期间持有 per-session 锁串行同会话并发，结束后把本轮写入短期窗口、
滚动摘要并落库 chat_sessions 表 —— 会话状态跨请求、跨进程重启可恢复。
"""

import logging
import uuid

from agents.session.agent_session_registry import AgentSessionRegistry
from agents.session.session_window import extract_reply_text

logger = logging.getLogger(__name__)

_registry: AgentSessionRegistry = None
_memory_service = None


def _get_registry() -> AgentSessionRegistry:
    """进程级会话注册表单例（惰性创建；测试可用 set 覆盖）"""
    global _registry
    if _registry is None:
        _registry = AgentSessionRegistry()
    return _registry


def _get_memory_service():
    """进程级记忆服务单例（惰性创建）"""
    global _memory_service
    if _memory_service is None:
        from services.memory_service import MemoryService

        _memory_service = MemoryService()
    return _memory_service


async def ProcessUserInput_stream(user_input: str, session_id: str = None):
    """
    流式处理用户输入（多会话隔离主入口）

    Args:
        user_input: 用户消息文本
        session_id: 会话 ID；None 时生成一次性 ID（遗留调用方）

    Yields:
        str: 令牌流（[THOUGHT]/[REPLY]/[SIGNAL]/[ERROR]，协议不变）
    """
    session_id = session_id or str(uuid.uuid4())
    registry = _get_registry()
    runtime = await registry.get(session_id)
    ctx = runtime.ctx

    async with runtime.lock:
        # 1. 客户绑定探测：消息内手机号，或补查上轮报修槽位中的手机号
        if ctx.user_id is None:
            phone = _get_memory_service().extract_phone(user_input)
            if not phone and isinstance(ctx.appointment_slots, dict):
                phone = ctx.appointment_slots.get("phone")
            if phone:
                ctx.bind_user(phone)

        # 2. 长期记忆召回（已绑定客户才有记忆）
        ctx.recalled = None
        if ctx.user_id:
            ctx.recalled = _get_memory_service().recall(ctx.user_id, user_input, top_k=5)

        # 3. 转发 Agent 图处理（Agent 内部从 session_context 读取背景/槽位/窗口）
        chunks = []
        try:
            async for token in runtime.agent.classify_task_stream(user_input):
                chunks.append(token)
                yield token
        finally:
            # 4. 写穿：窗口 + 滚动摘要 + 会话行
            try:
                reply_text = extract_reply_text("".join(chunks))
                if reply_text:
                    rolled = ctx.append_turn(user_input, reply_text)
                    if rolled:
                        await registry.merge_rolled_into_summary(runtime, rolled)
                registry.persist(runtime)
            except Exception as e:
                logger.error(f"会话写穿失败（不影响已输出内容）：{session_id}，{e}")
