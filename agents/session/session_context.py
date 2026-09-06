"""
会话上下文（内存态，SessionRuntime 的核心）

会话级状态束：客户绑定、预约槽位（只增不覆盖）、短期消息窗口（一问一答为一轮）
与滚动摘要文本。窗口写穿 chat_sessions 表行（services/chat_session_service），
LangChain 历史对象由窗口按需重建，不重复存储。
"""

from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from .session_window import (
    WINDOW_CAPACITY_ROUNDS,
    ROLL_CHUNK,
    ROLLOVER_WATERMARK,
    should_roll,
    roll_oldest,
)

_ROLL_TRIGGER_ROUNDS = max(1, round(WINDOW_CAPACITY_ROUNDS * ROLLOVER_WATERMARK))  # 6 轮


class SessionContext:
    """单会话运行时状态束"""

    def __init__(self, session_id: str, user_id: Optional[str] = None,
                 appointment_slots: Optional[Dict[str, Any]] = None,
                 window_messages: Optional[List[Dict[str, str]]] = None,
                 summary_text: str = ""):
        self.session_id = session_id
        self.user_id = user_id
        self.appointment_slots = appointment_slots or {}
        self.window_messages = window_messages or []
        self.summary_text = summary_text or ""
        self.recalled = None  # 本轮召回 Top-5（handler 填充，Agent 组装背景用；不持久化）

    # ---------- 构造 ----------

    @classmethod
    def from_row(cls, row: Optional[Dict[str, Any]]) -> Optional["SessionContext"]:
        if row is None:
            return None
        return cls(
            session_id=row["session_id"],
            user_id=row.get("user_id"),
            appointment_slots=row.get("appointment_slots") or {},
            window_messages=row.get("message_window") or [],
            summary_text=row.get("summary_text") or "",
        )

    # ---------- 客户绑定 ----------

    def bind_user(self, phone: str) -> bool:
        if not phone or self.user_id == phone:
            return False
        self.user_id = phone
        return True

    # ---------- 窗口 ----------

    @property
    def round_count(self) -> int:
        return len(self.window_messages) // 2

    def append_turn(self, user_text: str, reply_text: str) -> List[Dict[str, str]]:
        """写入一问一答；窗口占用达到 60% 时先滚出最旧轮次，返回被滚出的消息块（供摘要）"""
        rolled: List[Dict[str, str]] = []
        if should_roll(self.round_count):
            rolled, self.window_messages = roll_oldest(
                self.window_messages, chunk=ROLL_CHUNK * 2
            )
        self.window_messages.append({"role": "user", "content": user_text})
        self.window_messages.append({"role": "assistant", "content": reply_text})
        return rolled

    def recent_messages(self, n_rounds: int = WINDOW_CAPACITY_ROUNDS) -> List[Dict[str, str]]:
        """窗口内最近 n 轮（按消息序返回）"""
        return self.window_messages[-n_rounds * 2:]

    # ---------- 上下文注入 ----------

    def background_text(self, recalled: Optional[List[Dict[str, Any]]] = None) -> str:
        """滚动摘要 + 召回 Top-5 合并为背景文本（无内容返回空串）"""
        parts = []
        if self.summary_text:
            parts.append("早期对话摘要：" + self.summary_text)
        if recalled:
            parts.append("客户已知背景：" + "；".join(
                f"{r['content']}" for r in recalled if r.get('content')
            ))
        return "\n".join(parts)

    def to_langchain_messages(self, recalled: Optional[List[Dict[str, Any]]] = None,
                              n_rounds: int = WINDOW_CAPACITY_ROUNDS) -> List[BaseMessage]:
        """重建 LangChain 历史：背景（摘要+召回）置顶 + 最近窗口消息"""
        messages: List[BaseMessage] = []
        background = self.background_text(recalled)
        if background:
            messages.append(HumanMessage(
                content="（以下为系统掌握的客户背景，仅供你了解，不必向客户复述）\n" + background
            ))
        for msg in self.recent_messages(n_rounds):
            if msg.get("role") == "user":
                messages.append(HumanMessage(content=msg["content"]))
            else:
                messages.append(AIMessage(content=msg["content"]))
        return messages

    # ---------- 槽位 ----------

    def reset_slots(self):
        """报修任务结束后清空槽位（窗口与摘要保留，供后续咨询/再次报修）"""
        self.appointment_slots = {}

    # ---------- 序列化 ----------

    def to_row(self, state_value: Optional[str]) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "state_value": state_value,
            "appointment_slots": self.appointment_slots,
            "message_window": self.window_messages,
            "summary_text": self.summary_text,
        }
