"""
聊天会话服务层

会话级状态快照的落库/恢复：状态机值、预约槽位、短期消息窗口与滚动摘要。
供多会话隔离与进程重启后的会话恢复使用。
"""

from typing import Any, Dict, List, Optional

from db.db_router import DatabaseRouter
import logging

logger = logging.getLogger(__name__)


class ChatSessionService:
    """聊天会话服务类"""

    def __init__(self, db_path: str = 'sqlite:///data/smart_appointment.db'):
        self.db_router = DatabaseRouter(db_path)
        self.session_repo = self.db_router.chat_sessions

    def load(self, session_id: str) -> Optional[Dict[str, Any]]:
        """按 session_id 读取会话行（不存在返回 None）"""
        try:
            return self.session_repo.get_session(session_id)
        except Exception as e:
            logger.error(f"加载会话失败：{session_id}，{e}")
            return None

    def save(self, session_id: str, state_value: Optional[str] = None,
             user_id: Optional[str] = None,
             appointment_slots: Optional[Dict[str, Any]] = None,
             message_window: Optional[List[Dict[str, str]]] = None,
             summary_text: Optional[str] = "") -> bool:
        """全量覆盖写会话快照（不存在则新建）"""
        try:
            return self.session_repo.upsert_session(
                session_id=session_id,
                state_value=state_value,
                user_id=user_id,
                appointment_slots=appointment_slots,
                message_window=message_window,
                summary_text=summary_text,
            )
        except Exception as e:
            logger.error(f"保存会话失败：{session_id}，{e}")
            return False

    def bind_user(self, session_id: str, user_id: str) -> bool:
        """把会话绑定到客户手机号"""
        try:
            return self.session_repo.update_session_user(session_id, user_id)
        except Exception as e:
            logger.error(f"会话绑定用户失败：{session_id} → {user_id}，{e}")
            return False

    def list_sessions(self, user_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """会话列表（可按客户过滤）"""
        try:
            return self.session_repo.list_sessions(user_id=user_id, limit=limit)
        except Exception as e:
            logger.error(f"列出会话失败：{e}")
            return []
