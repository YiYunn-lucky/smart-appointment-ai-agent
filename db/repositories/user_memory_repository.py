"""
用户长期记忆数据访问对象

按客户（手机号）隔离的记忆条目，供分层记忆召回。
"""

from typing import Any, Dict, List, Optional

from ..base.interfaces import BaseUserMemoryRepository
from ..base.session_manager import SessionManager
from ..models import UserMemory


class UserMemoryRepository(BaseUserMemoryRepository):

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    def add_memory(self, user_id: str, content: str, memory_type: str = 'consult',
                   importance: Optional[float] = None, embedding: Optional[List[float]] = None,
                   source_session_id: Optional[str] = None) -> int:
        with self.session_manager.session_scope() as session:
            row = UserMemory(
                user_id=user_id,
                content=content,
                memory_type=memory_type,
                importance=importance if importance is not None else 0.5,
                embedding=embedding,
                source_session_id=source_session_id,
            )
            session.add(row)
            session.flush()
            return row.id

    def get_user_memories(self, user_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            query = session.query(UserMemory).filter(
                UserMemory.user_id == user_id,
                UserMemory.is_active == 1
            ).order_by(UserMemory.created_at.desc())
            if limit is not None:
                query = query.limit(limit)
            return [self._to_dict(row) for row in query.all()]

    def delete_memory(self, memory_id: int, soft_delete: bool = True) -> bool:
        with self.session_manager.session_scope() as session:
            row = session.query(UserMemory).filter(UserMemory.id == memory_id).first()
            if row is None:
                return False
            if soft_delete:
                row.is_active = 0
            else:
                session.delete(row)
            return True

    def _to_dict(self, row: UserMemory) -> Dict[str, Any]:
        return {
            'id': row.id,
            'user_id': row.user_id,
            'content': row.content,
            'memory_type': row.memory_type,
            'importance': row.importance,
            'embedding': row.embedding,
            'source_session_id': row.source_session_id,
            'created_at': row.created_at,
            'is_active': bool(row.is_active),
        }
