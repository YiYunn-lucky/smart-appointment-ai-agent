"""
聊天会话数据访问对象

会话级状态快照：状态机值 / 预约槽位 / 短期消息窗口 / 滚动摘要。
单行覆盖写（行粒度小），供进程重启与多会话隔离恢复。
"""

from typing import Any, Dict, List, Optional

from ..base.interfaces import BaseChatSessionRepository
from ..base.session_manager import SessionManager
from ..models import ChatSession


class ChatSessionRepository(BaseChatSessionRepository):

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    def upsert_session(self, session_id: str, state_value: Optional[str] = None,
                       user_id: Optional[str] = None,
                       appointment_slots: Optional[Dict[str, Any]] = None,
                       message_window: Optional[List[Dict[str, Any]]] = None,
                       summary_text: Optional[str] = None) -> bool:
        with self.session_manager.session_scope() as session:
            row = session.query(ChatSession).filter(
                ChatSession.session_id == session_id
            ).first()
            if row is None:
                row = ChatSession(session_id=session_id)
                session.add(row)
            row.state_value = state_value
            if user_id is not None:
                row.user_id = user_id
            row.appointment_slots = appointment_slots
            row.message_window = message_window
            row.summary_text = summary_text
            return True

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            row = session.query(ChatSession).filter(
                ChatSession.session_id == session_id
            ).first()
            return self._to_dict(row) if row else None

    def update_session_user(self, session_id: str, user_id: str) -> bool:
        with self.session_manager.session_scope() as session:
            row = session.query(ChatSession).filter(
                ChatSession.session_id == session_id
            ).first()
            if row is None:
                return False
            row.user_id = user_id
            return True

    def list_sessions(self, user_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            query = session.query(ChatSession)
            if user_id is not None:
                query = query.filter(ChatSession.user_id == user_id)
            rows = query.order_by(ChatSession.updated_at.desc()).limit(limit).all()
            return [self._to_dict(row) for row in rows]

    def _to_dict(self, row: ChatSession) -> Dict[str, Any]:
        return {
            'id': row.id,
            'session_id': row.session_id,
            'user_id': row.user_id,
            'state_value': row.state_value,
            'appointment_slots': row.appointment_slots or {},
            'message_window': row.message_window or [],
            'summary_text': row.summary_text or '',
            'created_at': row.created_at,
            'updated_at': row.updated_at,
        }
