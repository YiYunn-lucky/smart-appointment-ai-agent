"""
AutoDream 沉淀检查点数据访问对象

每人一行：已回放到的行为事件ID（幂等 checkpoint）+ 任务锁状态。
回放按事件ID增量推进，进程中断后可从断点续跑，不重复累计偏好置信度。
"""

from typing import Any, Dict, List, Optional

from ..base.interfaces import BaseDreamCheckpointRepository
from ..base.session_manager import SessionManager
from ..models import DreamCheckpoint
from config.time_config import TimeConfig


class DreamCheckpointRepository(BaseDreamCheckpointRepository):

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    def get_checkpoint(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            row = session.query(DreamCheckpoint).filter(
                DreamCheckpoint.user_id == user_id
            ).first()
            return self._to_dict(row) if row else None

    def get_or_create(self, user_id: str) -> Dict[str, Any]:
        with self.session_manager.session_scope() as session:
            row = session.query(DreamCheckpoint).filter(
                DreamCheckpoint.user_id == user_id
            ).first()
            if row is None:
                row = DreamCheckpoint(user_id=user_id)
                session.add(row)
                session.flush()
            return self._to_dict(row)

    def try_acquire_lock(self, user_id: str, stale_minutes: int = 30) -> bool:
        """抢占任务锁；锁超时（如进程崩溃残留）时自动接管"""
        now = TimeConfig.naive_now()
        with self.session_manager.session_scope() as session:
            row = session.query(DreamCheckpoint).filter(
                DreamCheckpoint.user_id == user_id
            ).first()
            if row is None:
                row = DreamCheckpoint(user_id=user_id, is_running=1,
                                      running_started_at=now)
                session.add(row)
                session.flush()
                return True
            if row.is_running == 0:
                row.is_running = 1
                row.running_started_at = now
                return True
            # 已持锁：超时视为崩溃残留，接管继续跑
            started = row.running_started_at
            if started is not None and (now - started).total_seconds() > stale_minutes * 60:
                row.is_running = 1
                row.running_started_at = now
                return True
            return False

    def release_lock(self, user_id: str, processed_events: int = 0,
                     last_event_id: Optional[int] = None, status: str = "ok",
                     error: Optional[str] = None) -> bool:
        with self.session_manager.session_scope() as session:
            row = session.query(DreamCheckpoint).filter(
                DreamCheckpoint.user_id == user_id
            ).first()
            if row is None:
                return False
            now = TimeConfig.naive_now()
            row.is_running = 0
            row.running_started_at = None
            row.last_status = status
            if error:
                row.last_error = error[:2000]
            if processed_events > 0:
                row.run_count += 1
                row.total_events_processed += processed_events
                row.last_run_at = now
                if last_event_id is not None:
                    row.last_event_id = max(row.last_event_id, last_event_id)
            return True

    def list_checkpoints(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.session_manager.session_scope() as session:
            rows = session.query(DreamCheckpoint).order_by(
                DreamCheckpoint.updated_at.desc()
            ).limit(limit).all()
            return [self._to_dict(row) for row in rows]

    def _to_dict(self, row: DreamCheckpoint) -> Dict[str, Any]:
        return {
            'id': row.id,
            'user_id': row.user_id,
            'last_event_id': row.last_event_id or 0,
            'run_count': row.run_count or 0,
            'total_events_processed': row.total_events_processed or 0,
            'is_running': bool(row.is_running),
            'running_started_at': row.running_started_at,
            'last_run_at': row.last_run_at,
            'last_status': row.last_status,
            'last_error': row.last_error,
            'created_at': row.created_at,
            'updated_at': row.updated_at,
        }
